"""After-agent middleware that stops and removes the Docker container."""

from __future__ import annotations

import logging
from typing import Any

from langchain.agents.middleware import AgentState, after_agent
from langgraph.config import get_config
from langgraph.runtime import Runtime

from ..integrations.docker import DockerSandbox
from ..utils.sandbox_state import (
    SANDBOX_BACKENDS,
    clear_sandbox_backend,
    unwrap_sandbox_backend,
)

logger = logging.getLogger(__name__)


@after_agent
async def docker_cleanup_middleware(
    state: AgentState,
    runtime: Runtime,
) -> dict[str, Any] | None:
    """Stop and remove the Docker container after the agent finishes.

    Fires on every agent exit (success, error, or step-limit).
    Handles already-stopped or already-removed containers gracefully.
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
        current._container.stop(timeout=5)  # noqa: SLF001
        logger.info("Stopped container %s", container_id)
    except Exception:
        logger.warning(
            "Could not stop container %s (may already be stopped)", container_id
        )
    try:
        current._container.remove(force=True)  # noqa: SLF001
        logger.info("Removed container %s", container_id)
    except Exception:
        logger.warning(
            "Could not remove container %s (may already be removed)", container_id
        )

    clear_sandbox_backend(thread_id)
    return None
