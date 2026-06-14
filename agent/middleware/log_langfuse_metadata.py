from __future__ import annotations

import logging
from typing import Any

from langchain.agents.middleware import AgentState, before_model
from langgraph.config import get_config
from langgraph.runtime import Runtime

logger = logging.getLogger(__name__)


@before_model
async def log_langfuse_metadata(
    state: AgentState,
    runtime: Runtime,
) -> dict[str, Any] | None:
    try:
        config = get_config()
    except RuntimeError:
        return None

    metadata = config.get("metadata", {})
    configurable = config.get("configurable", {})

    langfuse_session_id = metadata.get("langfuse_session_id")
    langfuse_user_id = metadata.get("langfuse_user_id")
    langfuse_trace_name = metadata.get("langfuse_trace_name")

    configurable_session_id = configurable.get("langfuse_session_id")
    configurable_user_id = configurable.get("langfuse_user_id")

    msg_count = len(state.get("messages", []))

    logger.info(
        "=== LANGFUSE METADATA CHECK === msg_count=%d "
        "metadata.langfuse_session_id=%s "
        "metadata.langfuse_user_id=%s "
        "metadata.langfuse_trace_name=%s "
        "configurable.langfuse_session_id=%s "
        "configurable.langfuse_user_id=%s "
        "config.thread_id=%s "
        "config.source=%s",
        msg_count,
        langfuse_session_id,
        langfuse_user_id,
        langfuse_trace_name,
        configurable_session_id,
        configurable_user_id,
        configurable.get("thread_id"),
        configurable.get("source"),
    )

    return None
