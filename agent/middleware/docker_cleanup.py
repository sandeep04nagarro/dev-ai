from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any
from uuid import uuid4

import docker
from docker.errors import APIError, NotFound
from langchain.agents.middleware import AgentState, after_agent
from langgraph.config import get_config
from langgraph.runtime import Runtime

from agent.integrations.docker import DockerSandbox
from agent.utils.sandbox_state import SANDBOX_BACKENDS, unwrap_sandbox_backend
from agent.utils.snapshot import create_registry
from agent.utils.snapshot_state import store_snapshot_status

logger = logging.getLogger(__name__)

if os.environ.get("DEBUG_MODE", False):
    logger.setLevel(logging.DEBUG)

SNAPSHOT_ENABLED = os.environ.get("SANDBOX_SNAPSHOT_ENABLED", "").lower() in (
    "1",
    "true",
    "on",
    "yes",
)


@after_agent
async def docker_cleanup_middleware(
    state: AgentState,
    runtime: Runtime,
) -> dict[str, Any] | None:
    """Stop the Docker container after the agent finishes.

    When ``SANDBOX_SNAPSHOT_ENABLED`` is set, commits the container to an image
    and pushes it to the configured registry before removing the container.
    Falls back to a simple stop when snapshot is disabled.
    """
    config = get_config()
    configurable = config.get("configurable", {})
    if not isinstance(configurable, dict):
        return None
    thread_id = configurable.get("thread_id")
    if not thread_id:
        return None

    sandbox_backend = SANDBOX_BACKENDS.get(thread_id)
    if not sandbox_backend:
        return None

    current = unwrap_sandbox_backend(sandbox_backend)
    if not isinstance(current, DockerSandbox):
        return None

    if SNAPSHOT_ENABLED:
        await _snapshot_and_cleanup(current, thread_id, configurable)
    else:
        container_id = current.id
        try:
            logger.debug("STOP begin %s at %f", container_id, time.time())
            await asyncio.to_thread(current.stop, timeout=5)
            logger.debug("STOP complete %s at %f", container_id, time.time())
            logger.info("Stopped container %s", container_id)
        except NotFound:
            logger.info("Container %s already stopped or removed", container_id)
        except APIError as e:
            logger.error("Docker API error stopping %s: %s", container_id, e)
        except Exception as e:
            logger.warning("Unexpected error stopping %s: %s", container_id, e)
        logger.info("Cleanup complete for container %s on thread %s", container_id, thread_id)

    return None


async def _snapshot_and_cleanup(
    sandbox: DockerSandbox,
    thread_id: str,
    configurable: dict,
) -> None:

    registry = create_registry()
    if not registry:
        logger.warning("Snapshot enabled but no registry configured, stopping only")
        await asyncio.to_thread(sandbox.stop, timeout=5)
        return

    run_id = configurable.get("langgraph_run_id") or str(uuid4())
    container_id = sandbox.id
    client = await asyncio.to_thread(docker.from_env)

    await asyncio.to_thread(sandbox.stop, timeout=5)

    try:
        await asyncio.to_thread(
            client.api.commit,
            container_id,
            repository=f"sandbox-{thread_id}",
            tag=run_id,
        )
    except docker.errors.APIError as e:
        logger.error("Docker commit failed for %s: %s", container_id, e)
        await store_snapshot_status(thread_id, container_id)
        return

    success = False
    for attempt in range(3):
        try:
            success = await asyncio.to_thread(registry.push_image, thread_id, run_id)
        except Exception as e:
            logger.error("Push attempt %d/3 failed for thread %s: %s", attempt + 1, thread_id, e)
        if success:
            break
        logger.warning("Push attempt %d/3 failed for thread %s", attempt + 1, thread_id)
        await asyncio.sleep(1)

    if success:
        try:
            await asyncio.to_thread(client.images.remove, f"sandbox-{thread_id}:{run_id}")
        except Exception:
            pass
        await asyncio.to_thread(sandbox.remove, force=True)
        await store_snapshot_status(thread_id, True)
        logger.info("Snapshot pushed, container+local image removed for thread %s", thread_id)
    else:
        await store_snapshot_status(thread_id, container_id)
        logger.warning(
            "All push attempts failed for thread %s. Container %s kept stopped.",
            thread_id,
            container_id,
        )
