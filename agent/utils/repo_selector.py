"""Repo selection router to determine which repositories are needed for a ticket."""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from langchain_core.messages import SystemMessage

from agent.dashboard.options import DEFAULT_MODEL_ID
from agent.utils.model import make_model
from agent.utils.multi_repo_registry import RepoConfig, get_project_repos

logger = logging.getLogger(__name__)

MULTI_REPO_SELECTOR_MODEL_ID = os.environ.get("MULTI_REPO_SELECTOR_MODEL_ID") or DEFAULT_MODEL_ID
MULTI_REPO_SELECTOR_FALLBACK = os.environ.get("MULTI_REPO_SELECTOR_FALLBACK", "all")
MULTI_REPO_SELECTOR_ENABLED = os.environ.get("MULTI_REPO_SELECTOR_ENABLED", "false").lower() == "true"

REPO_SELECTION_PROMPT = """You are a repository selection assistant. Given a Jira ticket and available repositories,
determine which repositories are needed to complete this task.

## Available Repositories:
{repo_list}

## Jira Ticket:
- Issue Key: {issue_key}
- Summary: {summary}
- Description: {description}

## Repository Types Explained:
- frontend: UI/UX code, React/Vue/Angular apps, CSS, static assets
- backend: API servers, business logic, database models, services
- shared: Common libraries, types, utilities used by multiple repos
- infrastructure: Docker, CI/CD, deployment configs
- mobile: iOS/Android/React Native apps
- docs: Documentation only

## Rules:
1. Select ONLY repos that are necessary for this task.
2. If task involves UI changes -> include frontend.
3. If task involves API/logic changes -> include backend.
4. If task involves cross-cutting concerns -> include both frontend AND backend.
5. Always include 'shared' type repos if any other repo is selected (they contain common types/utils).
6. If unsure, prefer to include more repos than less (we can filter later).

## Output Format:
Return ONLY a JSON array of the required repository names. Do not include any markdown formatting, backticks, or other text.
Example: ["webapp-frontend", "webapp-backend"]
"""

async def select_repos_for_ticket(
    project_key: str,
    issue_key: str,
    summary: str,
    description: str,
) -> list[RepoConfig]:
    """Select the appropriate repositories for a given Jira ticket."""
    available_repos = await get_project_repos(project_key)
    
    if not available_repos:
        logger.info("No repos found in registry for project %s", project_key)
        return []
        
    if len(available_repos) == 1:
        logger.info("Only one repo available for project %s, selecting it automatically", project_key)
        return available_repos
        
    if not MULTI_REPO_SELECTOR_ENABLED:
        logger.info("Multi-repo selector is disabled, returning all repos or fallback")
        return available_repos if MULTI_REPO_SELECTOR_FALLBACK == "all" else []

    repo_list_str = "\\n".join(
        f"- {r['name']} ({r.get('type', 'unknown')}): {r['owner']}/{r['name']}" 
        for r in available_repos
    )
    
    system_prompt = REPO_SELECTION_PROMPT.format(
        repo_list=repo_list_str,
        issue_key=issue_key,
        summary=summary,
        description=description,
    )
    
    try:
        model = make_model(MULTI_REPO_SELECTOR_MODEL_ID, max_tokens=1000, temperature=0.0)
        messages = [SystemMessage(content=system_prompt)]
        
        response = await model.ainvoke(messages)
        content = str(response.content).strip()
        
        # Parse JSON
        if content.startswith("```json"):
            content = content[7:]
        if content.endswith("```"):
            content = content[:-3]
        content = content.strip()
        
        selected_repo_names = json.loads(content)
        if not isinstance(selected_repo_names, list):
            logger.warning("Invalid LLM response format: expected list, got %s", type(selected_repo_names))
            selected_repo_names = [r["name"] for r in available_repos] if MULTI_REPO_SELECTOR_FALLBACK == "all" else []
            
        selected_repos = [r for r in available_repos if r["name"] in selected_repo_names]
        
        # Always include shared repos if any other repo is selected
        if selected_repos:
            shared_repos = [r for r in available_repos if r.get("type") == "shared" and r not in selected_repos]
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
