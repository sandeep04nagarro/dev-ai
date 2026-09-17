"""Runtime completion signal to the runtime wrapper.

Single home for the best-effort ``POST {thread_id, status}`` to the wrapper's
``/internal/completions`` endpoint. Imported by the in-graph ``after_agent``
hook, the webapp run watcher, and the ``_safe_process_*`` failure paths.
"""

from __future__ import annotations

import asyncio
import logging

import httpx

from agent.utils.secrets import SecretsManager

logger = logging.getLogger(__name__)


async def signal_runtime_completion(thread_id: str, status: str) -> None:
    """POST {thread_id, status} to the runtime wrapper. Best-effort, never raises."""
    base_url = SecretsManager.get("RUNTIME_WRAPPER_URL", "")
    if not base_url or not thread_id:
        return
    url = base_url.rstrip("/") + "/internal/completions"
    payload = {"thread_id": thread_id, "status": status}
    for attempt in range(1, 4):
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(url, json=payload, timeout=10.0)
            if response.status_code < 400:  # noqa: PLR2004
                logger.info(
                    "Runtime completion signalled for thread %s (status=%s, attempt %d)",
                    thread_id,
                    status,
                    attempt,
                )
                return
        except Exception as exc:  # noqa: BLE001 — best-effort signal only
            logger.warning(
                "Runtime completion attempt %d for %s failed: %s", attempt, thread_id, exc
            )
        await asyncio.sleep(1.0)
    logger.error("Failed to signal runtime completion for thread %s after 3 attempts", thread_id)
