"""Input sanitisation middleware.

This middleware defends against *indirect prompt injection*.  The first user
message handed to the agent is assembled from untrusted Jira / GitHub issue
content (title, description, comments) inside :mod:`agent.webapp`.  Because
that text is concatenated into the model's context window, a malicious issue
can try to override the system prompt, impersonate a system turn, or instruct
the agent to leak secrets.

``InputSanitizationMiddleware`` wraps every model call and sanitises all
:class:`HumanMessage` content *before* it reaches the LLM, using the pure
helpers in :mod:`agent.security.input_sanitizer`.  The transformation is
idempotent, so applying it on every model call is safe and cheap.

The middleware fails closed: when a harmful pattern fires, the message is
sanitised in place and the run is aborted with a :class:`RuntimeError` rather
than forwarded to the model.  On the async path a Jira comment is posted first
(when the thread is Jira-backed) so the reporter learns why the run stopped.

Only the Python standard library is used -- no third-party security package.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import BaseMessage, HumanMessage
from langgraph.config import get_config

from agent.security.input_sanitizer import sanitize_text_content
from agent.utils.jira import post_jira_comment
from agent.utils.thread_ops import langgraph_client

logger = logging.getLogger(__name__)

__all__ = ["InputSanitizationMiddleware"]


def _sanitize_messages(messages: list[BaseMessage]) -> list[str]:
    """Sanitise every :class:`HumanMessage` in *messages* in place.

    Returns a list of all redactions that fired across the messages.
    """
    all_redactions = []
    for message in messages:
        if not isinstance(message, HumanMessage):
            continue
        original_content = message.content
        new_content, redactions = sanitize_text_content(original_content)
        if not redactions:
            continue
        # ``sanitize_text_content`` returns the original object unchanged when
        # nothing fired, so a non-empty ``redactions`` list implies a real
        # change.
        message.content = new_content
        all_redactions.extend(redactions)
        logger.warning(
            "InputSanitizationMiddleware: redacted %s from a HumanMessage (preview=%.80r).",
            ", ".join(redactions),
            original_content if isinstance(original_content, str) else str(original_content)[:80],
        )
    return all_redactions


class InputSanitizationMiddleware(AgentMiddleware):
    """Neutralise prompt-injection payloads in user messages before model calls.

    Runs in both ``wrap_model_call`` (sync) and ``awrap_model_call`` (async)
    because the Open SWE agent drives the model asynchronously.  Non-human
    messages (system, AI, tool) are left untouched -- the system prompt is
    trusted infrastructure, and AI/tool output is already constrained by the
    framework.
    """

    def _sanitize(self, request: ModelRequest) -> list[str]:
        messages = getattr(request, "messages", None)
        if not messages:
            return []
        if not any(isinstance(m, HumanMessage) for m in messages):
            return []
        return _sanitize_messages(messages)

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        redactions = self._sanitize(request)
        if redactions:
            raise RuntimeError(
                f"Execution stopped due to harmful patterns: {', '.join(set(redactions))}"
            )
        return handler(request)

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> Any:
        redactions = self._sanitize(request)
        if redactions:
            try:
                config = get_config()
                thread_id = config.get("configurable", {}).get("thread_id")
                if thread_id:
                    client = langgraph_client()
                    thread = await client.threads.get(thread_id)
                    metadata = (
                        thread.get("metadata", {})
                        if isinstance(thread, dict)
                        else getattr(thread, "metadata", {})
                    )
                    jira_issue_key = metadata.get("jira_issue_key")
                    if jira_issue_key:
                        issues = ", ".join(set(redactions))
                        comment = f"Input sanitization detected harmful patterns: {issues}. Please handle them and then give the task again."
                        await post_jira_comment(jira_issue_key, comment)
                        logger.info(
                            "Posted Jira comment about harmful patterns to %s", jira_issue_key
                        )
            except Exception:
                logger.exception("Failed to post Jira comment for sanitization failure")
            raise RuntimeError(
                f"Execution stopped due to harmful patterns: {', '.join(set(redactions))}"
            )

        return await handler(request)
