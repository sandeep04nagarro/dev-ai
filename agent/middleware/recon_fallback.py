"""After-agent middleware that injects a fallback JSON block when
the recon agent is stopped before emitting its own findings.

Detects stop markers injected by:

* ``ModelCallLimitMiddleware`` (step limit)
* ``ConsecutiveFailureBreakerMiddleware`` (repeated tool failures)
* ``SandboxCircuitBreakerMiddleware`` (unrecoverable sandbox)

on the **last** AI message.  When a marker is found, appends an
AIMessage containing a minimal ``status: "interrupted"`` JSON block
that ``parse_recon_output`` can extract naturally.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from typing import Any

from langchain.agents.middleware import AgentMiddleware, AgentState
from langchain_core.messages import AIMessage
from langgraph.runtime import Runtime

logger = logging.getLogger(__name__)

_LIMIT_MARKER = "Model call limits exceeded"
_CONSECUTIVE_FAILURE_MARKER = "Consecutive tool failures"
_CIRCUIT_BREAKER_MARKER = "Sandbox circuit breaker triggered"

_FALLBACK_FINDINGS = {
    "status": "interrupted",
    "files_touched": [],
    "modules": [],
    "scope": "uncertain",
    "complexity": "complex",
    "keywords": [],
    "steps_used": 0,
    "summary": "Reconnaissance run was interrupted before completion.",
}

_FALLBACK_JSON_BLOCK = "```json\n" + json.dumps(_FALLBACK_FINDINGS, indent=2) + "\n```"


def _content_to_text(content: object) -> str:
    """Flatten a message content value (str, list of blocks) to plain text."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content)
    parts: list[str] = []
    for block in content:
        if isinstance(block, Mapping):
            text = block.get("text", "")
            parts.append(text if isinstance(text, str) else str(text))
        else:
            parts.append(str(block))
    return " ".join(parts)


def _has_stop_marker(text: str) -> bool:
    """Return ``True`` if *text* contains any known stop marker."""
    return (
        _LIMIT_MARKER in text
        or _CONSECUTIVE_FAILURE_MARKER in text
        or _CIRCUIT_BREAKER_MARKER in text
    )


class ReconFallbackMiddleware(AgentMiddleware[AgentState, Any]):
    """Inject a fallback ``status: interrupted`` JSON when the recon agent
    is stopped mid-run by a circuit-breaker or step-limit middleware."""

    state_schema = AgentState

    async def aafter_agent(
        self,
        state: AgentState,
        runtime: Runtime,  # noqa: ARG002
    ) -> dict[str, Any] | None:
        messages = state.get("messages", [])
        if not messages:
            return None

        last_msg = messages[-1]
        content = _content_to_text(getattr(last_msg, "content", "") or "")
        if not _has_stop_marker(content):
            return None

        logger.info("Recon fallback triggered — last message contains stop marker")
        return {"messages": [AIMessage(content=_FALLBACK_JSON_BLOCK)]}
