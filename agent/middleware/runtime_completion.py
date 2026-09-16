"""After-agent middleware that signals run completion to the runtime wrapper.

When a thread is executed by an open-swe worker container managed by the
runtime wrapper service, the wrapper learns about completion only from this
signal: it relays it to the router (releasing the tag lease) and tears the
container down. No-op unless ``RUNTIME_WRAPPER_URL`` is configured.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx
from langchain.agents.middleware import AgentState, after_agent
from langgraph.config import get_config
from langgraph.runtime import Runtime

from agent.utils.secrets import SecretsManager

logger = logging.getLogger(__name__)

ATTEMPTS = 3
RETRY_SLEEP_SECONDS = 1.0
POST_TIMEOUT_SECONDS = 10.0


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

    base_url = SecretsManager.get("RUNTIME_WRAPPER_URL", "")
    if not base_url:
        logger.debug("RUNTIME_WRAPPER_URL not set — skipping runtime completion signal")
        return None

    url = base_url.rstrip("/") + "/internal/completions"
    payload = {"thread_id": thread_id, "status": "completed"}

    for attempt in range(1, ATTEMPTS + 1):
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(url, json=payload, timeout=POST_TIMEOUT_SECONDS)
            if response.status_code < 400:  # noqa: PLR2004
                logger.info(
                    "Runtime completion signalled for thread %s (attempt %d, %s)",
                    thread_id,
                    attempt,
                    response.status_code,
                )
                return None
            logger.warning(
                "Runtime completion attempt %d for %s got %s",
                attempt,
                thread_id,
                response.status_code,
            )
        except Exception as exc:  # noqa: BLE001 — best-effort signal only
            logger.warning(
                "Runtime completion attempt %d for %s failed: %s", attempt, thread_id, exc
            )
        await asyncio.sleep(RETRY_SLEEP_SECONDS)

    logger.error(
        "Failed to signal runtime completion for thread %s after %d attempts", thread_id, ATTEMPTS
    )
    return None
