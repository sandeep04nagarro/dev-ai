from __future__ import annotations

import asyncio
from typing import Any

from langgraph.config import get_config

from ..reviewer_publish import fetch_pr_review_threads, fetch_review_thread_id_for_comment, resolve_review_thread
from ..utils.github_token import get_github_token


def github_resolve_review_thread(comment_id: int) -> dict[str, Any]:
    """Resolve the GitHub review thread containing the given review comment.

    Call after github_reply_to_review_thread when the inline comment has been addressed.
    Always resolve, even when dismissing — human can reply to resolved thread and re-trigger @dev-agent.
    Idempotent: if already resolved, returns success with already_resolved:true.
    """
    if not isinstance(comment_id, int):
        return {"success": False, "error": "comment_id must be an int"}
    config = get_config()
    configurable = config.get("configurable", {}) if isinstance(config, dict) else {}
    repo_config = configurable.get("repo") if isinstance(configurable, dict) else None
    pr_number = configurable.get("pr_number") if isinstance(configurable, dict) else None
    if not isinstance(repo_config, dict) or not isinstance(pr_number, int):
        return {"success": False, "error": "Missing repo or PR info in run config"}
    token = get_github_token()
    if not token:
        return {"success": False, "error": "No GitHub token available"}
    return asyncio.run(_async(comment_id, str(repo_config["owner"]), str(repo_config["name"]), pr_number, token))


async def _async(comment_id: int, owner: str, repo: str, pr_number: int, token: str):
    thread_id = await fetch_review_thread_id_for_comment(
        owner=owner, repo=repo, pr_number=pr_number, review_comment_id=comment_id, token=token
    )
    if not thread_id:
        return {"success": False, "error": f"Could not resolve thread id for comment {comment_id}"}
    ok = await resolve_review_thread(thread_node_id=thread_id, token=token)
    if ok:
        return {"success": True, "thread_id": thread_id}
    # Idempotency: mutation can return False when thread was already resolved.
    try:
        threads = await fetch_pr_review_threads(owner=owner, repo=repo, pr_number=pr_number, token=token)
        for t in threads:
            if t.get("id") == thread_id and t.get("is_resolved"):
                return {"success": True, "thread_id": thread_id, "already_resolved": True}
    except Exception:
        pass
    return {"success": False, "error": "GitHub did not resolve the thread"}
