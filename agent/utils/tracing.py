"""Langfuse tracing integration.

Provides a singleton Langfuse CallbackHandler that is injected into the
RunnableConfig callbacks of each graph factory, enabling Langfuse to capture
all LangChain/LangGraph events (model calls, tool calls, chain steps, etc.)
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

_langfuse_handler: object | None = None


def get_langfuse_handler() -> object | None:
    global _langfuse_handler
    if _langfuse_handler is not None:
        return _langfuse_handler

    if not bool(os.environ.get("LANGFUSE_SECRET_KEY") and os.environ.get("LANGFUSE_PUBLIC_KEY")):
        return None

    try:
        from langfuse.langchain import CallbackHandler

        _langfuse_handler = CallbackHandler()
        logger.info("Langfuse tracing enabled (CallbackHandler)")
    except Exception as exc:
        logger.warning("Failed to initialize Langfuse handler: %s", exc)
        _langfuse_handler = None

    return _langfuse_handler
