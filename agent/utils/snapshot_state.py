from __future__ import annotations

import logging

from langgraph.config import get_config
from langgraph_sdk import get_client

logger = logging.getLogger(__name__)

PERSISTED: dict[str, bool | str] = {}


def set_snapshot_state(thread_id: str, state: bool | str) -> None:
    PERSISTED[thread_id] = state


def get_snapshot_state(thread_id: str) -> bool | str | None:
    return PERSISTED.get(thread_id)


def clear_snapshot_state(thread_id: str) -> None:
    PERSISTED.pop(thread_id, None)


async def resolve_snapshot_status(thread_id: str) -> bool | str | None:
    cached = get_snapshot_state(thread_id)
    if cached is not None:
        return cached

    try:
        config = get_config()
    except Exception:
        logger.exception("Failed to read config for snapshot status")
        return None

    metadata = config.get("metadata", {})
    if not isinstance(metadata, dict):
        return None

    status = metadata.get("snapshot_status")
    if status is True:
        set_snapshot_state(thread_id, True)
        return True
    if isinstance(status, str):
        set_snapshot_state(thread_id, status)
        return status
    return None


async def store_snapshot_status(thread_id: str, status: bool | str) -> None:
    set_snapshot_state(thread_id, status)
    client = get_client()
    await client.threads.update(
        thread_id=thread_id,
        metadata={"snapshot_status": status},
    )
