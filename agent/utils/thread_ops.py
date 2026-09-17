"""Shared LangGraph thread helpers for webhooks and the dashboard."""

from __future__ import annotations

import asyncio
import logging

# import os
from typing import Any

from langgraph_sdk import get_client

from agent.utils.completion import signal_runtime_completion
from agent.utils.secrets import SecretsManager

logger = logging.getLogger(__name__)

TERMINAL_FAILURE_STATUSES = frozenset({"error", "interrupted"})


def langgraph_url() -> str:
    # return os.environ.get("LANGGRAPH_URL","http://localhost:2024")
    return SecretsManager.get("LANGGRAPH_URL", "http://localhost:2024")


def langgraph_client():
    return get_client(url=langgraph_url())


async def is_thread_active(thread_id: str) -> bool:
    """Return whether the thread currently has a running run."""
    try:
        thread = await langgraph_client().threads.get(thread_id)
        status = thread.get("status", "idle") if isinstance(thread, dict) else "idle"
        logger.info("Thread %s status check: status=%s", thread_id, status)
        return status == "busy"
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Failed to get thread status for %s: %s — assuming not active",
            thread_id,
            exc,
        )
        return False


async def queue_message_for_thread(
    thread_id: str, message_content: str | list[dict[str, Any]] | dict[str, Any]
) -> bool:
    """Queue a follow-up message for a busy thread (FIFO store namespace)."""
    client = langgraph_client()
    try:
        namespace = ("queue", thread_id)
        key = "pending_messages"
        new_message = {"content": message_content}

        existing_messages: list[dict[str, Any]] = []
        try:
            existing_item = await client.store.get_item(namespace, key)
            if existing_item and existing_item.get("value"):
                existing_messages = existing_item["value"].get("messages", [])
        except Exception:  # noqa: BLE001
            logger.debug("No existing queued messages for thread %s", thread_id)

        existing_messages.append(new_message)
        await client.store.put_item(namespace, key, {"messages": existing_messages})
        logger.info(
            "Queued message for thread %s (total queued: %d)",
            thread_id,
            len(existing_messages),
        )
        return True
    except Exception:
        logger.exception("Failed to queue message for thread %s", thread_id)
        return False


def _watcher_enabled() -> bool:
    """Kill-switch for the run watcher (default on)."""
    return SecretsManager.get("RUN_WATCHER_ENABLED", "true").lower() in (
        "1",
        "true",
        "on",
        "yes",
    )


async def watch_run_and_signal_failure(
    thread_id: str,
    run_id: str | None,
    *,
    source: str = "",
    signal_fn=None,
) -> None:
    """Block until a run terminates; signal `failed` if it ended in error.

    Success is a no-op (the in-graph `after_agent` hook owns `completed`).
    Best-effort: never raises except `asyncio.CancelledError` (re-raised so
    server shutdown does not produce a false `failed`).
    """
    if not thread_id or not run_id:
        logger.debug("Run watcher: missing thread_id/run_id (source=%s) — skipping", source)
        return
    if not _watcher_enabled():
        logger.debug("Run watcher disabled (RUN_WATCHER_ENABLED) for thread %s", thread_id)
        return
    signal = signal_fn or signal_runtime_completion
    prefix = f"Run watcher [{source or 'agent'}] thread={thread_id} run={run_id}"
    try:
        await langgraph_client().runs.join(thread_id, run_id)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("%s: join failed — signalling failed", prefix)
        await signal(thread_id, "failed")
        return
    try:
        run = await langgraph_client().runs.get(thread_id, run_id)
    except asyncio.CancelledError:
        raise
    except Exception:
        # Join succeeded so the run finished; a get() failure must not
        # fabricate a `failed` signal.
        logger.warning("%s: join ok but status fetch failed — not signalling", prefix)
        return
    status = run.get("status") if isinstance(run, dict) else getattr(run, "status", None)
    if status in TERMINAL_FAILURE_STATUSES:
        logger.warning("%s ended with status=%s — signalling failed", prefix, status)
        await signal(thread_id, "failed")
    else:
        logger.debug("%s ended with status=%s — no signal (success path owns it)", prefix, status)
