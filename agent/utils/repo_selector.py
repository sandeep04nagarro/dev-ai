"""Repo selection router to determine which repositories are needed for a ticket."""

from __future__ import annotations

import json
import logging

from langchain_core.messages import HumanMessage, SystemMessage

from agent.utils.model import make_model
from agent.utils.multi_repo_registry import RepoConfig, get_project_repos
from agent.utils.secrets import SecretsManager
from agent.utils.token_profiler import record_repo_selection_tokens

logger = logging.getLogger(__name__)

MULTI_REPO_SELECTOR_MODEL_ID = SecretsManager.get("MULTI_REPO_SELECTOR_MODEL_ID")
MULTI_REPO_SELECTOR_FALLBACK = SecretsManager.get("MULTI_REPO_SELECTOR_FALLBACK")
MULTI_REPO_SELECTOR_ENABLED = SecretsManager.get("MULTI_REPO_SELECTOR_ENABLED")

REPO_SELECTION_SYSTEM_PROMPT = """You are a repository selection assistant. Given a Jira ticket and available repositories,
determine which repositories are needed to complete this task.

Every repo you select gets fully cloned into the agent's workspace. Selecting an
unnecessary repo is a real cost — wasted clone time and a much larger context for
the coding agent to search through — so treat over-selection as a mistake, not a
safe default. Under-selection is easy to notice and correct; over-selection is not.

## About the `type` label:
The `type` next to each repo (e.g. "frontend", "backend", "BFF", "shared") is a
project-specific tag, not a fixed universal category — different projects use it
differently. Don't select a repo just because the ticket contains a word that
resembles its type label (e.g. the ticket mentioning "API" or "GraphQL" is not by
itself a reason to include a repo typed "BFF"). Judge relevance from the repo's
actual name and from what the ticket's acceptance criteria say will change.

## Resolving names that don't exactly match a repo:
Tickets often mention an internal service, module, or package name that is NOT the
exact name of any listed repository (for example the ticket says work happens in
"rsg-core-services" but no repo is literally named that). When this happens:
- Do NOT select every repo whose name loosely resembles the mentioned name.
- Look for explicit containment clues in the ticket — "is a monorepo", "lives
  inside", "part of", "a service within" — to find the ONE listed repo that
  actually contains the mentioned service, and select only that one.
- Background/context sentences that merely explain how the codebase is organized
  (e.g. "X is a monorepo containing services A, B, C") are orientation, not a
  request to touch every repo named in that sentence. Decide what to select from
  the acceptance criteria (routes, files, UI, endpoints actually being built),
  not from every proper noun mentioned in the description.

## Rules:
1. Select ONLY repos that are strictly necessary for this task.
2. If the ticket explicitly names a specific repo (by its exact listed name) or
   repo type, select ONLY that repo (plus any 'shared' repos it needs).
3. If the task is UI-only, select only the frontend repo(s) actually involved.
4. If the task is API/business-logic-only, select only the backend repo(s)
   actually involved.
5. Select multiple repos only when the acceptance criteria genuinely require
   coordinated changes across them (e.g. a new backend endpoint AND the frontend
   code that calls it in the same ticket).
6. Always include 'shared' type repos if any other non-shared repo is selected —
   they typically contain common types/utils other repos depend on.
7. When uncertain between two candidates, prefer the smaller set: pick the repo
   most directly implicated by the concrete technical details (routes, files,
   mutations, components), not every repo that is thematically related.

## Output Format:
Respond with a single JSON object with two keys:
- "reasoning": 1-3 short sentences — (a) any repo/service names explicitly
  mentioned in the ticket and whether each is an exact match to a listed repo,
  and (b) which listed repo(s) are actually required based on the acceptance
  criteria.
- "repos": a JSON array of the required repository names, using names exactly as
  they appear in the Available Repositories list above.

Return ONLY this JSON object. Do not include markdown formatting, backticks, or
any other text.

Example (ticket says "rsg-vtex-core is a monorepo; this work lives inside
rsg-core-services, a VTEX IO Node service" and asks only for a new backend
endpoint + GraphQL client, with no UI or BFF changes requested):
{{"reasoning": "rsg-core-services is described as living inside the rsg-vtex-core monorepo, not as a separate repo, so it maps to rsg-vtex-core. The ticket only adds a backend endpoint and GraphQL client; no frontend or BFF changes are requested.", "repos": ["rsg-vtex-core"]}}
"""

REPO_SELECTION_USER_PROMPT = """## Available Repositories:
{repo_list}

## Jira Ticket:
- Issue Key: {issue_key}
- Summary: {summary}
- Description: {description}
{triggering_comment_section}
Select the required repositories now, following the rules and output format above."""


def _dedupe_repos(repos: list[RepoConfig]) -> list[RepoConfig]:
    """Collapse repeated (owner, name) entries into one, merging their `type` tags.

    The registry can contain the same physical repo listed more than once under
    different `type` tags (e.g. a repo tagged both "backend" and "VTEX"). Showing
    duplicate lines for the same repo to the selector LLM makes it look like there
    are more distinct candidates than there really are, biasing it toward
    over-selection.
    """
    merged: dict[tuple[str, str], RepoConfig] = {}
    order: list[tuple[str, str]] = []
    for r in repos:
        key = (r["owner"], r["name"])
        if key not in merged:
            merged[key] = {"owner": r["owner"], "name": r["name"], "type": r.get("type", "unknown")}
            order.append(key)
        else:
            existing_types = [t.strip() for t in merged[key]["type"].split(",")]
            new_type = r.get("type", "unknown")
            if new_type not in existing_types:
                merged[key]["type"] = f"{merged[key]['type']}, {new_type}"
    return [merged[key] for key in order]


async def select_repos_for_ticket(
    project_key: str,
    issue_key: str,
    summary: str,
    description: str,
    triggering_comment: str = "",
) -> list[RepoConfig]:
    """Select the appropriate repositories for a given Jira ticket."""
    available_repos = await get_project_repos(project_key)

    if not available_repos:
        logger.info("No repos found in registry for project %s", project_key)
        return []

    return await select_repos_from_candidates(
        available_repos, issue_key, summary, description, triggering_comment
    )


async def select_repos_from_candidates(
    available_repos: list[RepoConfig],
    issue_key: str,
    summary: str,
    description: str,
    triggering_comment: str = "",
) -> list[RepoConfig]:
    """Run repo selection against a fixed candidate list.

    Split out from :func:`select_repos_for_ticket` so the selector prompt/logic can
    be exercised directly (e.g. from a script or test) without a live registry.
    """
    available_repos = _dedupe_repos(available_repos)

    if not available_repos:
        return []

    if len(available_repos) == 1:
        logger.info("Only one repo available for %s, selecting it automatically", issue_key)
        return available_repos

    if not MULTI_REPO_SELECTOR_ENABLED:
        logger.info("Multi-repo selector is disabled, returning all repos or fallback")
        return available_repos if MULTI_REPO_SELECTOR_FALLBACK == "all" else []

    repo_list_str = "\n".join(
        f"- {r['name']} ({r.get('type', 'unknown')}): {r['owner']}/{r['name']}"
        for r in available_repos
    )

    # Build the triggering comment section only if we have one
    triggering_comment_section = ""
    if triggering_comment:
        triggering_comment_section = (
            f"## Triggering Comment (most recent user request):\n"
            f"{triggering_comment}\n\n"
            f"**IMPORTANT**: The triggering comment is the most recent user request "
            f"and takes priority over the issue summary/description for repo selection. "
            f"If the user mentions a specific repo or type of repo in their comment, "
            f"select ONLY that repo.\n\n"
        )

    user_prompt = REPO_SELECTION_USER_PROMPT.format(
        repo_list=repo_list_str,
        issue_key=issue_key,
        summary=summary,
        description=description,
        triggering_comment_section=triggering_comment_section,
    )

    try:
        model = make_model(MULTI_REPO_SELECTOR_MODEL_ID, max_tokens=1200, temperature=0.0)
        messages = [
            SystemMessage(content=REPO_SELECTION_SYSTEM_PROMPT),
            HumanMessage(content=user_prompt),
        ]

        response = await model.ainvoke(messages)
        # Record token usage for the multi_repo_selection phase.
        record_repo_selection_tokens(issue_key, response)
        content = str(response.content).strip()

        # Strip markdown code fences if the model added them anyway.
        if content.startswith("```"):
            content = content.strip("`")
            if content.startswith("json"):
                content = content[4:]
            content = content.strip()

        parsed = json.loads(content)

        reasoning = None
        if isinstance(parsed, dict):
            selected_repo_names = parsed.get("repos", [])
            reasoning = parsed.get("reasoning")
        elif isinstance(parsed, list):
            # Backward-compat: model returned a bare array instead of the object format.
            selected_repo_names = parsed
        else:
            logger.warning(
                "Invalid LLM response format: expected object or list, got %s", type(parsed)
            )
            selected_repo_names = []

        if not isinstance(selected_repo_names, list):
            logger.warning(
                "Invalid LLM response format: expected list under 'repos', got %s",
                type(selected_repo_names),
            )
            selected_repo_names = []

        if reasoning:
            logger.info("Repo selection reasoning for %s: %s", issue_key, reasoning)

        # Defend against the model returning "owner/name" instead of just "name".
        normalized_names = {
            name.split("/")[-1] if isinstance(name, str) else name for name in selected_repo_names
        }

        selected_repos = [r for r in available_repos if r["name"] in normalized_names]

        # Always include shared repos if any other repo is selected
        if selected_repos:
            shared_repos = [
                r for r in available_repos if r.get("type") == "shared" and r not in selected_repos
            ]
            for r in shared_repos:
                if r not in selected_repos:
                    selected_repos.append(r)

        if not selected_repos:
            selected_repos = available_repos if MULTI_REPO_SELECTOR_FALLBACK == "all" else []

        logger.info("LLM selected repos for %s: %s", issue_key, [r["name"] for r in selected_repos])
        return selected_repos

    except Exception as e:
        logger.exception("Failed to run repo selection LLM for %s: %s", issue_key, e)
        return available_repos if MULTI_REPO_SELECTOR_FALLBACK == "all" else []
