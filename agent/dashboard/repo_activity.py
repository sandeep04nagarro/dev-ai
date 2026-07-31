"""Repository and pull-request activity for the dashboard.

Split across two endpoints because the two halves have very different costs:

* Repository activity is aggregated from LangGraph threads plus the store and
  answers in milliseconds.
* Pull-request counts require one GitHub REST call per tracked repo, so they
  live behind their own endpoint and the UI fills them in after the table has
  already painted.

PR state is read from GitHub rather than from thread metadata on purpose:
``pr_number`` is passed on the *run config* for PR-triggered threads and is
never persisted onto the thread, so agent-opened PRs leave no trace in
metadata. Agent-authored PRs are instead identified by their branch prefix,
which ``open-swe`` controls when it pushes.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from agent.utils.github_app import get_github_app_installation_token
from agent.utils.multi_repo_registry import MULTI_REPO_NAMESPACE
from agent.utils.thread_ops import langgraph_client

from .enabled_repos import list_enabled_review_repos
from .thread_api import _metadata_repo, _run_status_to_agent_status

logger = logging.getLogger(__name__)

AGENT_BRANCH_PREFIX = "open-swe/"

_MAX_TRACKED_REPOS = 25
_MAX_PR_PAGES = 3
_PR_PAGE_SIZE = 100
_PR_CONCURRENCY = 5
_RECENT_PR_LIMIT = 10


def _empty_counts() -> dict[str, int]:
    return {"total": 0, "open": 0, "merged": 0, "closed": 0}


def _thread_repos(metadata: dict[str, Any]) -> list[tuple[str, str, str]]:
    """Every ``(owner, name, type)`` a thread touched.

    Multi-repo threads carry ``selected_repos``; single-repo webhook threads
    only carry the flat ``repo_owner``/``repo_name`` pair.
    """
    repos: list[tuple[str, str, str]] = []
    selected = metadata.get("selected_repos")
    if isinstance(selected, list):
        for entry in selected:
            if not isinstance(entry, dict):
                continue
            owner, name = entry.get("owner"), entry.get("name")
            if isinstance(owner, str) and isinstance(name, str) and owner and name:
                kind = entry.get("type")
                repos.append((owner, name, kind if isinstance(kind, str) else ""))
    if repos:
        return repos

    owner, name, full_name = _metadata_repo(metadata)
    return [(owner, name, "")] if full_name else []


def _thread_tokens(metadata: dict[str, Any]) -> int:
    for key in ("jira_token_usage", "token_usage"):
        usage = metadata.get(key)
        if isinstance(usage, dict):
            total = usage.get("total")
            if isinstance(total, int | float) and total > 0:
                return int(total)
    return 0


async def list_registered_projects() -> list[dict[str, Any]]:
    """Every Jira project key → repo mapping held in the multi-repo registry."""
    try:
        result = await langgraph_client().store.search_items(MULTI_REPO_NAMESPACE, limit=200)
    except Exception as exc:
        logger.warning("multi-repo registry search failed: %s", exc)
        return []

    items = result.get("items", []) if isinstance(result, dict) else []
    projects: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        value = item.get("value")
        if not isinstance(value, dict):
            continue
        repos = [r for r in value.get("repos", []) if isinstance(r, dict)]
        projects.append(
            {
                "project_key": item.get("key") or "",
                "repos": repos,
                "updated_at": value.get("updated_at") or item.get("updated_at"),
            }
        )
    return sorted(projects, key=lambda p: p["project_key"])


async def get_repository_activity(login: str) -> dict[str, Any]:
    """Per-repo thread aggregation, joined with the registry and review opt-in."""
    del login

    threads = await langgraph_client().threads.search(metadata={}, limit=1000)
    projects = await list_registered_projects()
    enabled = {r.lower() for r in await list_enabled_review_repos()}

    repos: dict[str, dict[str, Any]] = {}

    def entry_for(owner: str, name: str, kind: str) -> dict[str, Any]:
        full_name = f"{owner}/{name}"
        entry = repos.get(full_name)
        if entry is None:
            entry = repos[full_name] = {
                "full_name": full_name,
                "owner": owner,
                "name": name,
                "type": kind or None,
                "review_enabled": full_name.lower() in enabled,
                "project_keys": [],
                "threads": {"total": 0, "running": 0, "completed": 0, "error": 0},
                "tokens": 0,
                "last_active": None,
            }
        elif kind and not entry["type"]:
            entry["type"] = kind
        return entry

    for thread in threads:
        metadata = thread.get("metadata") if isinstance(thread, dict) else None
        metadata = metadata if isinstance(metadata, dict) else {}

        status = _run_status_to_agent_status(
            thread.get("status") if isinstance(thread, dict) else None,
            metadata.get("latest_run_status")
            if isinstance(metadata.get("latest_run_status"), str)
            else None,
        )
        bucket = {"running": "running", "error": "error"}.get(status, "completed")
        updated_at = thread.get("updated_at") if isinstance(thread, dict) else None
        tokens = _thread_tokens(metadata)

        for owner, name, kind in _thread_repos(metadata):
            entry = entry_for(owner, name, kind)
            entry["threads"]["total"] += 1
            entry["threads"][bucket] += 1
            entry["tokens"] += tokens
            if isinstance(updated_at, str) and (
                entry["last_active"] is None or updated_at > entry["last_active"]
            ):
                entry["last_active"] = updated_at

    # Registered repos with no thread activity yet still belong in the table —
    # "configured but idle" is a state an operator needs to be able to see.
    for project in projects:
        for repo in project["repos"]:
            owner, name = repo.get("owner"), repo.get("name")
            if not (isinstance(owner, str) and isinstance(name, str) and owner and name):
                continue
            kind = repo.get("type")
            entry = entry_for(owner, name, kind if isinstance(kind, str) else "")
            key = project["project_key"]
            if key and key not in entry["project_keys"]:
                entry["project_keys"].append(key)

    for full_name in enabled:
        if "/" not in full_name:
            continue
        owner, _, name = full_name.partition("/")
        entry_for(owner, name, "")

    ranked = sorted(
        repos.values(),
        key=lambda r: (r["threads"]["total"], r["last_active"] or ""),
        reverse=True,
    )

    return {
        "repositories": ranked,
        "projects": projects,
        "totals": {
            "repositories": len(ranked),
            "registered_projects": len(projects),
            "review_enabled": sum(1 for r in ranked if r["review_enabled"]),
            "threads": sum(r["threads"]["total"] for r in ranked),
        },
    }


def _tracked_repo_names(activity: dict[str, Any]) -> list[tuple[str, str]]:
    return [
        (repo["owner"], repo["name"])
        for repo in activity["repositories"][:_MAX_TRACKED_REPOS]
        if repo["owner"] and repo["name"]
    ]


async def _fetch_repo_pulls(
    client: httpx.AsyncClient,
    owner: str,
    name: str,
    headers: dict[str, str],
) -> list[dict[str, Any]] | None:
    pulls: list[dict[str, Any]] = []
    for page in range(1, _MAX_PR_PAGES + 1):
        response = await client.get(
            f"https://api.github.com/repos/{owner}/{name}/pulls",
            headers=headers,
            params={"state": "all", "per_page": _PR_PAGE_SIZE, "page": page},
        )
        if response.status_code != 200:
            logger.info("PR lookup for %s/%s returned %s", owner, name, response.status_code)
            return None
        batch = response.json()
        if not isinstance(batch, list):
            return None
        pulls.extend(batch)
        if len(batch) < _PR_PAGE_SIZE:
            break
    return pulls


def _pr_state(pull: dict[str, Any]) -> str:
    if pull.get("state") == "open":
        return "open"
    return "merged" if pull.get("merged_at") else "closed"


def _summarize_pulls(pulls: list[dict[str, Any]]) -> tuple[dict[str, int], dict[str, int], list[dict[str, Any]]]:
    overall, agent = _empty_counts(), _empty_counts()
    agent_pulls: list[dict[str, Any]] = []

    for pull in pulls:
        state = _pr_state(pull)
        overall["total"] += 1
        overall[state] += 1

        ref = (pull.get("head") or {}).get("ref") or ""
        if not ref.startswith(AGENT_BRANCH_PREFIX):
            continue
        agent["total"] += 1
        agent[state] += 1
        agent_pulls.append(
            {
                "number": pull.get("number"),
                "title": pull.get("title") or "",
                "state": state,
                "url": pull.get("html_url") or "",
                "branch": ref,
                "updated_at": pull.get("updated_at"),
            }
        )

    return overall, agent, agent_pulls


async def get_pull_request_activity(login: str) -> dict[str, Any]:
    """PR counts per tracked repo, read live from GitHub."""
    activity = await get_repository_activity(login)
    tracked = _tracked_repo_names(activity)

    if not tracked:
        return {
            "repositories": [],
            "recent": [],
            "totals": {"overall": _empty_counts(), "agent": _empty_counts()},
            "available": True,
            "reason": None,
        }

    token = await get_github_app_installation_token()
    if not token:
        return {
            "repositories": [],
            "recent": [],
            "totals": {"overall": _empty_counts(), "agent": _empty_counts()},
            "available": False,
            "reason": "No GitHub App installation token available",
        }

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    semaphore = asyncio.Semaphore(_PR_CONCURRENCY)

    async with httpx.AsyncClient(timeout=20.0) as client:

        async def one(owner: str, name: str) -> dict[str, Any] | None:
            async with semaphore:
                pulls = await _fetch_repo_pulls(client, owner, name, headers)
            if pulls is None:
                return None
            overall, agent, agent_pulls = _summarize_pulls(pulls)
            return {
                "full_name": f"{owner}/{name}",
                "overall": overall,
                "agent": agent,
                "agent_pulls": agent_pulls,
            }

        results = await asyncio.gather(
            *(one(owner, name) for owner, name in tracked), return_exceptions=True
        )

    repositories: list[dict[str, Any]] = []
    recent: list[dict[str, Any]] = []
    totals_overall, totals_agent = _empty_counts(), _empty_counts()
    unreadable = 0

    for result in results:
        if isinstance(result, BaseException) or result is None:
            unreadable += 1
            continue
        for key in totals_overall:
            totals_overall[key] += result["overall"][key]
            totals_agent[key] += result["agent"][key]
        for pull in result.pop("agent_pulls"):
            recent.append({**pull, "repo": result["full_name"]})
        repositories.append(result)

    repositories.sort(key=lambda r: r["agent"]["total"], reverse=True)
    recent.sort(key=lambda p: p.get("updated_at") or "", reverse=True)

    return {
        "repositories": repositories,
        "recent": recent[:_RECENT_PR_LIMIT],
        "totals": {"overall": totals_overall, "agent": totals_agent},
        "available": True,
        "reason": (
            f"{unreadable} repository(ies) could not be read with the current token"
            if unreadable
            else None
        ),
    }
