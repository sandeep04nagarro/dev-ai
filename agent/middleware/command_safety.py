"""Command-safety middleware for the Docker sandbox.

The Open SWE agent asks the sandbox to run shell commands through the
``execute`` / ``bash`` / ``shell`` / ``run_terminal_cmd`` tools.  Those
commands originate from LLM output that has been influenced by untrusted
Jira / GitHub issue text, so they must be treated as untrusted before they
reach ``DockerSandbox.execute()`` (which does ``sh -c <command>`` inside the
container).

``CommandSafetyMiddleware`` wraps every tool call.  When the tool is a shell
tool it runs the command through :class:`agent.security.command_guard.CommandGuard`:

* **allow** -> the call proceeds to the real handler (the sandbox runs it).
* **block** -> the call is *not* forwarded; instead a ``ToolMessage`` with
  ``status="error"`` is returned so the LLM sees the rejection and can
  self-correct with a safer command.

Only the Python standard library is used -- no third-party security package.
This middleware is the *agent-level* layer; :meth:`DockerSandbox.execute`
provides a second, sandbox-level layer of defence in depth.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import AgentState
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

from agent.security.command_guard import (
    BLOCKED_COMMAND_MESSAGE,
    CommandDecision,
    evaluate_command,
)

logger = logging.getLogger(__name__)

__all__ = ["CommandSafetyMiddleware", "SHELL_TOOL_NAMES"]

# Tool names that hand an arbitrary command string to a shell.  Mirrors the
# set recognised by the dashboard message adapter so every shell-style tool is
# covered regardless of which sub-agent emits it.
SHELL_TOOL_NAMES: frozenset[str] = frozenset(
    {"execute", "bash", "shell", "run_terminal_cmd", "terminal", "run_command"}
)


def _tool_call_name(request: ToolCallRequest) -> str | None:
    tool_call = getattr(request, "tool_call", None)
    if isinstance(tool_call, dict):
        name = tool_call.get("name")
        if isinstance(name, str) and name:
            return name
    name = getattr(request, "tool_name", None) or getattr(request, "name", None)
    if isinstance(name, str) and name:
        return name
    return None


def _tool_call_id(request: ToolCallRequest) -> str | None:
    tool_call = getattr(request, "tool_call", None)
    if isinstance(tool_call, dict):
        call_id = tool_call.get("id")
        if isinstance(call_id, str) and call_id:
            return call_id
    return None


def _extract_command(request: ToolCallRequest) -> str | None:
    tool_call = getattr(request, "tool_call", None)
    if not isinstance(tool_call, dict):
        return None
    args = tool_call.get("args", {})
    if not isinstance(args, dict):
        return None
    command = args.get("command")
    if isinstance(command, str) and command:
        return command
    # Some shell tools use "cmd" instead of "command".
    command = args.get("cmd")
    if isinstance(command, str) and command:
        return command
    return None


def _blocked_tool_message(
    decision: CommandDecision,
    request: ToolCallRequest,
    tool_name: str,
    command: str,
) -> ToolMessage:
    payload = {
        "status": "error",
        "error": "command_blocked_by_security_middleware",
        "name": tool_name,
        "message": BLOCKED_COMMAND_MESSAGE,
        "category": decision.category,
        "reason": decision.reason,
        "pattern": decision.pattern,
        # Truncate so a huge command cannot blow up the message history.
        "command": command if len(command) <= 512 else command[:512] + "...[truncated]",
    }
    return ToolMessage(
        content=json.dumps(payload),
        tool_call_id=_tool_call_id(request) or "unknown",
        status="error",
    )


class CommandSafetyMiddleware(AgentMiddleware):
    """Block destructive / exfiltration-class shell commands before they run.

    Hooks ``wrap_tool_call`` / ``awrap_tool_call``.  Non-shell tools pass
    through untouched.  Shell tools are classified by
    :func:`agent.security.command_guard.evaluate_command`; blocked commands are
    returned as error ``ToolMessage``\\ s without ever reaching the sandbox.
    """

    state_schema = AgentState

    def __init__(self, *, tool_names: frozenset[str] | None = None) -> None:
        self._tool_names = tool_names if tool_names is not None else SHELL_TOOL_NAMES

    def _evaluate(self, request: ToolCallRequest) -> tuple[CommandDecision, str, str] | None:
        """Inspect *request*; return ``(decision, tool_name, command)`` if it
        is a shell tool call, else ``None`` (caller should pass through)."""
        tool_name = _tool_call_name(request)
        if tool_name is None or tool_name.lower() not in self._tool_names:
            return None
        command = _extract_command(request)
        if command is None:
            # Shell tool but no command string -- let the handler produce its
            # own validation error rather than inventing a block reason.
            return None
        decision = evaluate_command(command)
        return decision, tool_name, command

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        inspected = self._evaluate(request)
        if inspected is None:
            return handler(request)
        decision, tool_name, command = inspected
        if decision.allowed:
            return handler(request)
        logger.warning(
            "CommandSafetyMiddleware: blocked %s command (category=%s reason=%s): %.200r",
            tool_name,
            decision.category,
            decision.reason,
            command,
        )
        return _blocked_tool_message(decision, request, tool_name, command)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        inspected = self._evaluate(request)
        if inspected is None:
            return await handler(request)
        decision, tool_name, command = inspected
        if decision.allowed:
            return await handler(request)
        logger.warning(
            "CommandSafetyMiddleware: blocked %s command (category=%s reason=%s): %.200r",
            tool_name,
            decision.category,
            decision.reason,
            command,
        )
        return _blocked_tool_message(decision, request, tool_name, command)
