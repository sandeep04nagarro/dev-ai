"""After-agent middleware that stops a Docker sandbox container.

Runs after every agent exit (success, error, or step-limit). Uses the
``SANDBOX_BACKENDS`` in-memory registry to locate the container for the
current thread and stops it gracefully.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from docker.errors import APIError, NotFound
from langchain.agents.middleware import AgentState, after_agent
from langgraph.config import get_config
from langgraph.runtime import Runtime

from ..integrations.docker import DockerSandbox
from ..utils.sandbox_state import SANDBOX_BACKENDS, unwrap_sandbox_backend

logger = logging.getLogger(__name__)

if os.getenv("DEBUG_MODE", "").lower() in ("on", "1", "true"):
    logger.setLevel(logging.DEBUG)


@after_agent
async def docker_cleanup_middleware(
    state: AgentState,
    runtime: Runtime,
) -> dict[str, Any] | None:
    """Stop the Docker container after the agent finishes.

    Fires on every agent exit (success, error, or step-limit).
    Handles already-stopped containers gracefully.
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

    container_id = current.id
    try:
        await asyncio.to_thread(current._container.stop, timeout=5)  # noqa: SLF001
        logger.info("Stopped container %s", container_id)
    except NotFound:
        logger.info("Container %s already stopped or removed", container_id)
    except APIError as e:
        logger.error("Docker API error stopping %s: %s", container_id, e)
    except Exception as e:
        logger.warning("Unexpected error stopping %s: %s", container_id, e)

    logger.info("Cleanup complete for container %s on thread %s", container_id, thread_id)
    return None
