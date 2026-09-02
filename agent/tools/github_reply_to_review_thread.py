from __future__ import annotations

import asyncio
from typing import Any

from langgraph.config import get_config

from ..reviewer_publish import fetch_pr_review_threads, reply_to_review_comment
from ..utils.github_token import get_github_token


def github_reply_to_review_thread(comment_id: int, body: str) -> dict[str, Any]:
    """Reply to a human GitHub review comment thread.

    Use the github_review_comment_id visible in the PR prompt's HTML comment
    (e.g. <!-- github_review_comment_id: 123 -->). Body should explain the fix
    and mention the commit SHA if applicable.
    Dedup: if open-swe[bot] already replied in this thread after the human comment, skip and return success with skipped:true.
    """
    if not isinstance(comment_id, int):
        return {"success": False, "error": "comment_id must be an int (github_review_comment_id)"}
    if not body.strip():
        return {"success": False, "error": "Reply body is required"}
    config = get_config()
    configurable = config.get("configurable", {}) if isinstance(config, dict) else {}
    repo_config = configurable.get("repo") if isinstance(configurable, dict) else None
    pr_number = configurable.get("pr_number") if isinstance(configurable, dict) else None
    if (
        not isinstance(repo_config, dict)
        or not repo_config.get("owner")
        or not repo_config.get("name")
        or not isinstance(pr_number, int)
    ):
        return {"success": False, "error": "Missing repo or PR info in run config"}
    token = get_github_token()
    if not token:
        return {"success": False, "error": "No GitHub token available"}
    return asyncio.run(
        _async(
            comment_id,
            body.strip(),
            str(repo_config["owner"]),
            str(repo_config["name"]),
            pr_number,
            token,
        )
    )


async def _async(comment_id: int, body: str, owner: str, repo: str, pr_number: int, token: str):
    # Dedup guard B: scan thread for existing bot reply
    try:
        threads = await fetch_pr_review_threads(owner=owner, repo=repo, pr_number=pr_number, token=token)
        for t in threads:
            cids = {c.get("id") for c in (t.get("comments") or []) if isinstance(c, dict)}
            if comment_id in cids:
                bot_replies = [c for c in t.get("comments", []) if c.get("author") == "open-swe[bot]"]
                if bot_replies:
                    return {"success": True, "skipped": True, "reason": "bot already replied in thread", "thread_id": t.get("id")}
    except Exception:
        pass  # best-effort guard; fall through to actual reply
    resp = await reply_to_review_comment(
        owner=owner, repo=repo, pr_number=pr_number, review_comment_id=comment_id, body=body, token=token
    )
    if resp is None:
        return {"success": False, "error": "GitHub did not accept the reply"}
    return {"success": True, "reply_id": resp.get("id"), "response": resp}
