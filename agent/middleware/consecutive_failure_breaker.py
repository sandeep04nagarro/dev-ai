"""Consecutive failure circuit-breaker middleware for agents.

Tracks per-tool consecutive failures incrementally and stops the agent
when any tool exceeds its configured threshold, then injects an AIMessage
with a marker that ``notify_step_limit_reached`` detects and uses to post
a notification across all configured channels.
"""

from __future__ import annotations

import json
import logging
from typing import Annotated, Any, NotRequired

from langchain.agents.middleware import (
    AgentMiddleware,
    AgentState,
    hook_config,
)
from langchain_core.messages import AIMessage, BaseMessage
from langgraph.channels.untracked_value import UntrackedValue
from langgraph.runtime import Runtime
from typing_extensions import override

logger = logging.getLogger(__name__)

_FAILURE_MARKER = "Consecutive tool failures"
_DEFAULT_THRESHOLD = 5


class _ConsecutiveFailureState(AgentState):
    consecutive_failures: NotRequired[Annotated[dict[str, int], UntrackedValue]]
    _cf_processed_count: NotRequired[int]


def _is_tool_error(msg: BaseMessage) -> bool:
    """Return ``True`` if *msg* represents a tool-result error.

    A message is considered an error when it carries ``status="error"``,
    its text content contains a shell-style ``[Command failed with exit
    code`` marker, or its ``content`` is a JSON object with an ``error``
    key.
    """
    if getattr(msg, "status", None) == "error":
        return True
    content = getattr(msg, "content", "")
    if not isinstance(content, str):
        return False
    if "[Command failed with exit code" in content:
        return True
    try:
        data = json.loads(content)
        if isinstance(data, dict) and "error" in data:
            return True
    except (json.JSONDecodeError, ValueError):
        pass
    return False


def _build_message(
    triggered: dict[str, int],
    thresholds: dict[str, int],
    default_threshold: int,
) -> str:
    """Format the breaker-trigger message listing every tool that exceeded its threshold."""
    parts: list[str] = []
    for tool_name, count in sorted(triggered.items()):
        threshold = thresholds.get(tool_name, default_threshold)
        parts.append(f"'{tool_name}' failed {count} consecutive time(s) (threshold: {threshold})")
    return f"{_FAILURE_MARKER}: {'; '.join(parts)}. Stopping for human intervention."


class ConsecutiveFailureBreakerMiddleware(AgentMiddleware[_ConsecutiveFailureState, Any]):
    """Stop the agent when a tool fails too many times in a row.

    Per-tool thresholds are configurable via ``thresholds`` (dict of
    ``tool_name → max_consecutive_failures``).  Tools not listed use
    ``default_threshold`` (5 by default).

    Tracks failures incrementally across rounds — an AIMessage between
    two failure groups does **not** reset the counter; only a successful
    tool call for that tool does.

    When triggered the middleware:

    1. Injects an ``AIMessage`` with a ``_FAILURE_MARKER`` marker into
       the conversation so the user sees which tool failed.
    2. Jumps to the graph ``end`` node via ``{"jump_to": "end"}``.
    3. ``notify_step_limit_reached`` picks up the marker and posts a
       notification to all configured channels (Slack, Linear, GitHub).
    """

    state_schema = _ConsecutiveFailureState

    def __init__(
        self,
        *,
        thresholds: dict[str, int] | None = None,
        default_threshold: int = _DEFAULT_THRESHOLD,
    ) -> None:
        """Configure per-tool failure thresholds.

        Args:
            thresholds: Mapping of tool name → max consecutive failures
                before breaking. Tools not listed fall back to
                ``default_threshold``.
            default_threshold: Consecutive failure count that applies to
                any tool not explicitly listed in *thresholds*. Must be
                at least ``1``.
        """
        self.thresholds = thresholds or {}
        self.default_threshold = max(1, default_threshold)

    @hook_config(can_jump_to=["end"])
    @override
    def before_model(
        self,
        state: _ConsecutiveFailureState[Any],
        runtime: Runtime[Any],  # noqa: ARG002
    ) -> dict[str, Any] | None:
        """Inspect new messages for consecutive tool failures.

        Increments per-tool failure streaks only for error messages. A
        successful (non-error) tool message for a tool clears its
        streak. When any streak reaches its threshold the middleware
        injects an ``AIMessage`` explaining which tools failed and jumps
        the graph to ``end`` via ``{"jump_to": "end"}``.
        """
        messages = state.get("messages", [])
        if not messages:
            return None

        streaks = dict(state.get("consecutive_failures", {}))
        prev_processed = state.get("_cf_processed_count", 0)
        new_messages = messages[prev_processed:]
        triggered: dict[str, int] = {}

        for msg in new_messages:
            if isinstance(msg, AIMessage):
                continue
            tool_name: str = getattr(msg, "name", None) or "unknown"
            if _is_tool_error(msg):
                streaks[tool_name] = streaks.get(tool_name, 0) + 1
                threshold = self.thresholds.get(tool_name, self.default_threshold)
                if streaks[tool_name] >= threshold and tool_name not in triggered:
                    triggered[tool_name] = streaks[tool_name]
            else:
                streaks.pop(tool_name, None)

        if not triggered:
            return {
                "consecutive_failures": streaks,
                "_cf_processed_count": len(messages),
            }

        content = _build_message(triggered, self.thresholds, self.default_threshold)
        logger.warning("Consecutive failure breaker triggered: %s", content)
        return {
            "consecutive_failures": streaks,
            "_cf_processed_count": len(messages),
            "jump_to": "end",
            "messages": [AIMessage(content=content)],
        }

    @hook_config(can_jump_to=["end"])
    @override
    async def abefore_model(
        self,
        state: _ConsecutiveFailureState[Any],
        runtime: Runtime[Any],
    ) -> dict[str, Any] | None:
        """Async variant of :meth:`before_model`; delegates to the sync implementation."""
        return self.before_model(state, runtime)
