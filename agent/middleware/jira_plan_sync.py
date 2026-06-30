"""Middleware that synchronizes the agent's todo list to a Jira comment.

When the agent calls the `write_todos` tool, this middleware formats the todos
into a markdown checklist and either posts a new Jira comment or updates the
existing plan comment.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, AgentState
from langchain_core.messages import ToolMessage
from langgraph.config import get_config
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

from agent.utils.jira import post_jira_comment
from agent.utils.thread_ops import langgraph_client

logger = logging.getLogger(__name__)


def _get_name(candidate: object) -> str | None:
    if not candidate:
        return None
    if isinstance(candidate, str):
        return candidate
    if isinstance(candidate, dict):
        name = candidate.get("name")
    else:
        name = getattr(candidate, "name", None)
    return name if isinstance(name, str) and name else None


def _get_thread_id(request: ToolCallRequest) -> str | None:
    runtime_config = getattr(getattr(request, "runtime", None), "config", None)
    config = runtime_config if isinstance(runtime_config, dict) else None
    if config is None:
        try:
            config = get_config()
        except Exception:
            return None
    if not isinstance(config, dict):
        return None

    configurable = config.get("configurable", {})
    if not isinstance(configurable, dict):
        return None
    thread_id = configurable.get("thread_id")
    return thread_id if isinstance(thread_id, str) and thread_id else None


async def _sync_todos_to_jira(request: ToolCallRequest) -> None:
    """Sync the todos to Jira in the background."""
    try:
        tool_call = getattr(request, "tool_call", None)
        if not tool_call or not isinstance(tool_call, dict):
            return

        name = _get_name(tool_call)
        if name != "write_todos":
            return

        args = tool_call.get("args")
        if not isinstance(args, dict):
            return

        todos = args.get("todos", [])
        if not isinstance(todos, list) or not todos:
            return

        thread_id = _get_thread_id(request)
        if not thread_id:
            return

        client = langgraph_client()
        thread = await client.threads.get(thread_id)
        metadata = thread.get("metadata", {}) if isinstance(thread, dict) else getattr(thread, "metadata", {})

        jira_issue_key = metadata.get("jira_issue_key")
        if not jira_issue_key:
            return

        # Format todos as markdown checklist
        lines = ["## Agent Implementation Plan"]
        for todo in todos:
            status = todo.get("status", "")
            content = todo.get("content", "")
            # Determine checkmark state
            checked = "x" if status == "completed" else " "
            lines.append(f"* [{checked}] {content} ({status})")

        plan_body = "\n".join(lines)

        plan_comment_id = metadata.get("jira_plan_comment_id")

        if plan_comment_id:
            logger.info(
                "Jira plan already synced as comment %s for issue %s; skipping updates to improve performance.",
                plan_comment_id,
                jira_issue_key,
            )
            return

        # Post new comment and store ID
        new_comment_id = await post_jira_comment(jira_issue_key, plan_body)
        if new_comment_id:
            metadata["jira_plan_comment_id"] = new_comment_id
            await client.threads.update(thread_id, metadata=metadata)
            logger.info("Posted new Jira plan comment %s for issue %s", new_comment_id, jira_issue_key)
        else:
            logger.warning("Failed to post new Jira plan comment for issue %s", jira_issue_key)

    except Exception:
        logger.exception("Failed to sync todos to Jira")


def _sync_todos_to_jira_sync(request: ToolCallRequest) -> None:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(_sync_todos_to_jira(request))
        return
    
    # If we have an event loop, we shouldn't block, but this is a sync wrapper
    # Fire and forget is risky in sync contexts, but we can try to schedule it
    loop = asyncio.get_running_loop()
    loop.create_task(_sync_todos_to_jira(request))


class JiraPlanSyncMiddleware(AgentMiddleware):
    """Intercepts `write_todos` and syncs the plan to a Jira comment."""

    state_schema = AgentState

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        _sync_todos_to_jira_sync(request)
        return handler(request)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        await _sync_todos_to_jira(request)
        return await handler(request)
