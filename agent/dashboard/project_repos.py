"""Dashboard API for managing multi-repo project mappings."""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel

from agent.utils.multi_repo_registry import RepoConfig, get_project_repos, set_project_repos

logger = logging.getLogger(__name__)

class ProjectRepoUpdate(BaseModel):
    repos: list[RepoConfig]

async def api_get_project_repos(project_key: str) -> dict[str, Any]:
    """Get repos for a project key."""
    repos = await get_project_repos(project_key)
    return {"repos": repos}

async def api_set_project_repos(project_key: str, update: ProjectRepoUpdate) -> dict[str, Any]:
    """Set repos for a project key."""
    repos = await set_project_repos(project_key, update.repos)
    return {"repos": repos}
