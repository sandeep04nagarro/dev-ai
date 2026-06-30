"""Multi-Repo Registry for mapping Jira Project Keys to a list of repositories."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TypedDict

from langgraph_sdk import get_client

from agent.utils.jira_project_repo_map import JIRA_PROJECT_TO_REPO

logger = logging.getLogger(__name__)

MULTI_REPO_NAMESPACE: list[str] = ["multi_repo_registry"]

class RepoConfig(TypedDict):
    owner: str
    name: str
    type: str  # frontend, backend, shared, mobile, docs, infra, etc.

def _client():
    return get_client()

async def get_project_repos(project_key: str) -> list[RepoConfig]:
    """Get the mapped repos for a Jira project key."""
    if not project_key:
        return []
    
    try:
        item = await _client().store.get_item(MULTI_REPO_NAMESPACE, project_key)
    except Exception as e:
        logger.debug("Multi-repo registry lookup failed for %s: %s", project_key, e)
        return _get_fallback_repos(project_key)
        
    if item is None:
        return _get_fallback_repos(project_key)
        
    value = item.get("value") if isinstance(item, dict) else getattr(item, "value", None)
    if not isinstance(value, dict):
        return _get_fallback_repos(project_key)
        
    repos = value.get("repos", [])
    if not repos:
        return _get_fallback_repos(project_key)
        
    return repos

def _get_fallback_repos(project_key: str) -> list[RepoConfig]:
    """Fallback to JIRA_PROJECT_TO_REPO if no registry entry exists."""
    fallback_repo = JIRA_PROJECT_TO_REPO.get(project_key)
    if fallback_repo:
        logger.info("Bootstrapping multi-repo registry for %s from JIRA_PROJECT_TO_REPO", project_key)
        return [{"owner": fallback_repo["owner"], "name": fallback_repo["name"], "type": "backend"}]
    return []

async def set_project_repos(project_key: str, repos: list[RepoConfig]) -> list[RepoConfig]:
    """Set the mapped repos for a Jira project key."""
    if not project_key:
        return []
        
    await _client().store.put_item(
        MULTI_REPO_NAMESPACE,
        project_key,
        {"repos": repos, "updated_at": datetime.now(UTC).isoformat()},
    )
    return repos
