import asyncio
import logging
from typing import Any

from langgraph.config import get_config

from agent.utils.github_app import get_github_app_installation_token
from agent.utils.sandbox_state import SANDBOX_BACKENDS

logger = logging.getLogger(__name__)


def refresh_github_token() -> dict[str, Any]:
    """Refresh the GitHub App installation token in the sandbox.

    Use this tool when the 'gh' CLI reports an authentication error or
    'Bad credentials', which typically happens if the GitHub token has expired
    (they have a 1-hour TTL). This will re-authenticate the gh CLI in your sandbox.

    Returns:
        Dictionary with 'success' (bool) key and an optional 'error' message.
    """
    try:
        config = get_config()
        configurable = config.get("configurable", {})
        thread_id = configurable.get("thread_id")

        if not thread_id:
            return {"success": False, "error": "No thread_id found in config."}

        return asyncio.run(_refresh_github_token_async(thread_id))
    except Exception as e:
        logger.exception("Failed to refresh GitHub token")
        return {"success": False, "error": str(e)}


async def _refresh_github_token_async(thread_id: str) -> dict[str, Any]:
    sandbox_backend = SANDBOX_BACKENDS.get(thread_id)
    if not sandbox_backend:
        return {"success": False, "error": f"No active sandbox found for thread {thread_id}."}

    installation_token = await get_github_app_installation_token()
    if not installation_token:
        return {
            "success": False,
            "error": "GitHub App is not configured or failed to generate token.",
        }

    await asyncio.to_thread(
        sandbox_backend.execute,
        f"printf '%s' '{installation_token}' | gh auth login --with-token",
    )
    return {"success": True}
