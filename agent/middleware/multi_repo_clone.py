"""Middleware to inject multi-repo context into the system prompt.

When multiple repositories are selected for a Jira ticket, this middleware
appends a "Multi-Repository Workspace" section to the system prompt so the
agent knows which repos to clone and where to place them.

Actual cloning is handled by the agent itself via its normal Repository Setup
flow (which already has proper GitHub authentication).
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import SystemMessage
from langgraph.config import get_config

logger = logging.getLogger(__name__)


def _inject_multi_repo_prompt(request: ModelRequest, selected_repos: list[dict[str, Any]]) -> None:
    """Append multi-repo workspace context to the system prompt (in-place)."""
    if not request.messages or not isinstance(request.messages[0], SystemMessage):
        return

    original_sys = request.messages[0].content

    repo_context = "\n\n## Multi-Repository Workspace\n"
    repo_context += "You have access to multiple repositories for this task. "
    repo_context += "Clone each of them into your workspace using `gh repo clone`:\n"
    for repo in selected_repos:
        repo_context += (
            f"- **{repo['name']}** (Type: {repo.get('type', 'unknown')}): "
            f"`gh repo clone {repo['owner']}/{repo['name']}` → `/workspace/{repo['name']}`\n"
        )
    repo_context += (
        "\nMake sure to navigate to the correct repository directory "
        "when running commands or editing files."
    )

    request.messages[0].content = f"{original_sys}{repo_context}"


class MultiRepoCloneMiddleware(AgentMiddleware):
    """Middleware to inject multi-repo context into the system prompt.

    This runs inside ``awrap_model_call`` because the system prompt is only
    available via ``ModelRequest.messages[0]`` (it is NOT part of the agent
    ``state``), so hooks like ``abefore_agent`` cannot modify it.

    A ``_has_injected`` guard ensures the injection happens at most once per
    agent run, on the very first model call.
    """

    def __init__(self) -> None:
        self._has_injected = False

    def _try_inject(self, request: ModelRequest) -> None:
        """Inject multi-repo context into the system prompt exactly once."""
        if self._has_injected:
            return

        config = get_config()
        configurable = config.get("configurable", {})
        metadata = config.get("metadata", {})
        # Prefer configurable (per-run) over metadata (thread-level, possibly stale)
        selected_repos = configurable.get("selected_repos") or metadata.get("selected_repos")

        if not selected_repos:
            # Nothing to inject — mark as done so we don't re-check.
            self._has_injected = True
            return

        logger.info(
            "MultiRepoCloneMiddleware: Injecting %d repos into system prompt",
            len(selected_repos),
        )
        _inject_multi_repo_prompt(request, selected_repos)
        self._has_injected = True

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        self._try_inject(request)
        return handler(request)

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> Any:
        self._try_inject(request)
        return await handler(request)
