"""E2E mock for 3-comment re-trigger + dedup scenario."""

from unittest.mock import AsyncMock, patch

import pytest

from agent.utils import github_comments


def test_3_comment_scenario_prompt_keeps_context_via_threads_block():
    # T0: 3 inline comments + R1 trigger
    comments_t0 = [
        {"author": "alice", "body": "Fix A", "type": "review_comment", "comment_id": 111, "path": "a.py", "line": 10},
        {"author": "alice", "body": "Fix B", "type": "review_comment", "comment_id": 222, "path": "b.py", "line": 20},
        {"author": "alice", "body": "Fix C - disputed", "type": "review_comment", "comment_id": 333, "path": "c.py", "line": 30},
        {"author": "alice", "body": "@dev-agent please address", "type": "review", "comment_id": 999},
    ]
    # Prompt at T0 includes all 3 ids
    prompt_t0 = github_comments.build_pr_prompt(comments_t0, "https://github.com/o/r/pull/5", repo_config={"owner": "o", "name": "r"})
    assert "111" in prompt_t0
    assert "222" in prompt_t0
    assert "333" in prompt_t0

    # T1: human replies to 333 (disputed, now wants fix) + new R2
    # fetch_pr_comments_since_last_tag would return only [human_reply_to_333, R2] slice
    human_reply = {"author": "alice", "body": "Actually fix C please", "type": "review_comment", "comment_id": 444, "path": "c.py", "line": 30}
    comments_t1_slice = [
        human_reply,
        {"author": "alice", "body": "@dev-agent please address again", "type": "review", "comment_id": 1000},
    ]
    # Without threads_block, original 333 body is lost
    prompt_t1_without = github_comments.build_pr_prompt(comments_t1_slice, "https://github.com/o/r/pull/5")
    assert "Fix C - disputed" not in prompt_t1_without

    # With threads_block (fetched via GraphQL), original context is preserved
    threads_block = """<pr_review_threads>
  <thread location=\"c.py:30\" status=\"resolved\">
    <comment author=\"alice\"><body>Fix C - disputed</body></comment>
    <comment author=\"open-swe[bot]\"><body>Dismissed: won't fix because X</body></comment>
    <comment author=\"alice\"><body>Actually fix C please</body></comment>
  </thread>
</pr_review_threads>"""
    prompt_t1_with = github_comments.build_pr_prompt(comments_t1_slice, "https://github.com/o/r/pull/5", threads_block=threads_block)
    assert "Fix C - disputed" in prompt_t1_with
    assert "Actually fix C please" in prompt_t1_with
    assert "Open/Resolved PR Review Threads" in prompt_t1_with


def test_github_reply_dedup_guard_skips_duplicate():
    """Second reply to same comment_id should be skipped via thread scan."""
    import asyncio
    from agent.tools.github_reply_to_review_thread import _async as reply_async

    mock_threads = [
        {"id": "thread-1", "comments": [{"id": 111, "author": "alice"}, {"id": 555, "author": "open-swe[bot]"}]},
    ]
    async def _run():
        with patch("agent.tools.github_reply_to_review_thread.fetch_pr_review_threads", new=AsyncMock(return_value=mock_threads)):
            with patch("agent.tools.github_reply_to_review_thread.reply_to_review_comment", new=AsyncMock()) as mock_reply:
                result = await reply_async(111, "Fix", "o", "r", 5, "tok")
                assert result["success"] is True
                assert result.get("skipped") is True
                mock_reply.assert_not_called()
    asyncio.run(_run())


def test_github_resolve_idempotent_when_already_resolved():
    """Resolving an already-resolved thread should return success with already_resolved."""
    import asyncio
    from agent.tools.github_resolve_review_thread import _async as resolve_async

    async def _run():
        with patch("agent.tools.github_resolve_review_thread.fetch_review_thread_id_for_comment", new=AsyncMock(return_value="thread-1")):
            with patch("agent.tools.github_resolve_review_thread.resolve_review_thread", new=AsyncMock(return_value=False)):
                with patch("agent.tools.github_resolve_review_thread.fetch_pr_review_threads", new=AsyncMock(return_value=[{"id": "thread-1", "is_resolved": True}])):
                    result = await resolve_async(111, "o", "r", 5, "tok")
                    assert result["success"] is True
                    assert result.get("already_resolved") is True
                    assert result["thread_id"] == "thread-1"
    asyncio.run(_run())
