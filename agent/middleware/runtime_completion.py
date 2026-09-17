"""After-agent middleware that signals run completion to the runtime wrapper.

When a thread is executed by an open-swe worker container managed by the
runtime wrapper service, the wrapper learns about completion only from this
signal: it relays it to the router (releasing the tag lease) and tears the
container down. No-op unless ``RUNTIME_WRAPPER_URL`` is configured.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain.agents.middleware import AgentState, after_agent
from langgraph.config import get_config
from langgraph.runtime import Runtime

from agent.utils.completion import signal_runtime_completion

logger = logging.getLogger(__name__)


@after_agent
async def runtime_completion_middleware(
    state: AgentState,
    runtime: Runtime,
) -> dict[str, Any] | None:
    """POST {thread_id, status} to the wrapper's /internal/completions endpoint."""
    config = get_config()
    configurable = config.get("configurable", {})
    if not isinstance(configurable, dict):
        return None

    thread_id = configurable.get("thread_id")
    if not isinstance(thread_id, str) or not thread_id:
        return None

    try:
        await signal_runtime_completion(thread_id, "completed")
    except Exception:  # noqa: BLE001 — completion signal must never fail the run
        logger.exception("Runtime completion signal failed for thread %s", thread_id)
    return None
