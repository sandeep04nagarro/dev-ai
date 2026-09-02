"""Tests for BranchEnforcementMiddleware — helpers and tool hook."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import agent.middleware.branch_enforcement as be
from agent.middleware.branch_enforcement import (
    build_enforced_branch_name,
    contains_jira_key,
    extract_branch_from_command,
    slugify,
)


def _req(tool_name: str, command: str, cid="call_1"):
    return MagicMock(
        tool_call={"name": tool_name, "args": {"command": command}, "id": cid}, tool_name=tool_name
    )


def test_slugify_basic():
    assert slugify("Fix Login Redirect!") == "fix-login-redirect"
    assert slugify("  Hello   World  ") == "hello-world"
    assert slugify("UPPER_case-123") == "upper-case-123"
    assert slugify("") == "task"
    assert slugify("---") == "task"


def test_slugify_truncate():
    assert len(slugify("a" * 100, max_len=40)) == 40
    assert not slugify("hello-world- trailing- ", max_len=10).endswith("-")
    assert (
        slugify("Fix login bug with very long title that exceeds limit", max_len=40)
        == "fix-login-bug-with-very-long-title-that"
    )


def test_contains_jira_key():
    assert contains_jira_key("devai-agent/PROJ-123-fix", "PROJ-123") is True
    assert contains_jira_key("devai-agent/proj-123-fix", "PROJ-123") is True
    assert contains_jira_key("devai-agent/fix-bug", "PROJ-123") is False


def test_build_enforced_branch_name():
    assert (
        build_enforced_branch_name("PROJ-123", "devai-agent/fix-login-bug")
        == "devai-agent/PROJ-123-fix-login-bug"
    )
    assert (
        build_enforced_branch_name("PROJ-123", "open-swe/fix-login-bug")
        == "devai-agent/PROJ-123-fix-login-bug"
    )
    assert build_enforced_branch_name("AB-1", "") == "devai-agent/AB-1-task"
    # idempotent: already contains key (case-insensitive) and correct prefix -> return as-is, preserve casing
    assert (
        build_enforced_branch_name("PROJ-123", "devai-agent/PROJ-123-already")
        == "devai-agent/PROJ-123-already"
    )
    assert (
        build_enforced_branch_name("PROJ-123", "devai-agent/proj-123-fix")
        == "devai-agent/proj-123-fix"
    )
    assert (
        build_enforced_branch_name("PROJ-123", "devai-agent/PROJ-123-fix")
        == "devai-agent/PROJ-123-fix"
    )


def test_extract_branch_checkout():
    assert (
        extract_branch_from_command("git checkout -b devai-agent/fix-bug") == "devai-agent/fix-bug"
    )
    assert (
        extract_branch_from_command("git checkout -b 'devai-agent/PROJ-123-fix'")
        == "devai-agent/PROJ-123-fix"
    )
    assert extract_branch_from_command("git checkout -B devai-agent/hotfix") == "devai-agent/hotfix"
    assert (
        extract_branch_from_command("git switch -c devai-agent/test origin/main")
        == "devai-agent/test"
    )
    # legacy open-swe prefix also extracted for migration
    assert extract_branch_from_command("git checkout -b open-swe/fix-bug") == "open-swe/fix-bug"
    # bare reuse (no -b/-c) — only for devai-agent/ (and legacy open-swe/) branches, not main/master
    assert extract_branch_from_command("git checkout devai-agent/fix-bug") == "devai-agent/fix-bug"
    assert extract_branch_from_command("git switch devai-agent/fix-bug") == "devai-agent/fix-bug"
    assert extract_branch_from_command("git checkout open-swe/fix-bug") == "open-swe/fix-bug"
    assert extract_branch_from_command("git checkout main") is None
    assert extract_branch_from_command("git switch main") is None


def test_extract_branch_push():
    assert (
        extract_branch_from_command("git push origin devai-agent/fix-bug") == "devai-agent/fix-bug"
    )
    assert (
        extract_branch_from_command("git push -u origin devai-agent/PROJ-123-foo")
        == "devai-agent/PROJ-123-foo"
    )
    assert extract_branch_from_command("git push origin HEAD:devai-agent/fix") == "devai-agent/fix"
    assert (
        extract_branch_from_command("git push origin local:devai-agent/remote")
        == "devai-agent/remote"
    )
    # legacy
    assert extract_branch_from_command("git push origin open-swe/fix-bug") == "open-swe/fix-bug"


def test_extract_branch_none():
    assert extract_branch_from_command("git status") is None
    assert extract_branch_from_command("gh repo clone owner/repo") is None
    assert extract_branch_from_command("ls -la") is None
    assert extract_branch_from_command("npm run build") is None


def test_extract_branch_ignores_non_branch_git():
    assert extract_branch_from_command("git branch -a") is None
    assert extract_branch_from_command("git log --oneline") is None


# ---- tool hook ----


def test_blocks_checkout_without_key():
    mw = be.BranchEnforcementMiddleware()
    mw._jira_key = "PROJ-123"
    req = _req("execute", "git checkout -b devai-agent/fix-bug")
    handler = MagicMock(return_value=MagicMock(content="ok"))
    result = mw.wrap_tool_call(req, handler)
    handler.assert_not_called()
    assert result.status == "error"
    payload = json.loads(result.content)
    assert payload["error"] == "branch_missing_jira_key"
    assert payload["corrected_branch"] == "devai-agent/PROJ-123-fix-bug"
    assert payload["corrected_command"] == "git checkout -b devai-agent/PROJ-123-fix-bug"
    assert payload["jira_key"] == "PROJ-123"


def test_allows_checkout_with_key():
    mw = be.BranchEnforcementMiddleware()
    mw._jira_key = "PROJ-123"
    req = _req("execute", "git checkout -b devai-agent/PROJ-123-fix-bug")
    handler = MagicMock(return_value=MagicMock(content="ok"))
    result = mw.wrap_tool_call(req, handler)
    handler.assert_called_once()
    assert result.content == "ok"


def test_blocks_push_without_key():
    mw = be.BranchEnforcementMiddleware()
    mw._jira_key = "PROJ-123"
    req = _req("execute", "git push origin devai-agent/fix")
    handler = MagicMock(return_value=MagicMock(content="ok"))
    result = mw.wrap_tool_call(req, handler)
    handler.assert_not_called()
    payload = json.loads(result.content)
    assert payload["corrected_branch"] == "devai-agent/PROJ-123-fix"
    assert payload["corrected_command"] == "git push origin devai-agent/PROJ-123-fix"


def test_blocks_bare_checkout_without_key():
    mw = be.BranchEnforcementMiddleware()
    mw._jira_key = "PROJ-123"
    req = _req("execute", "git checkout devai-agent/fix-bug")
    handler = MagicMock(return_value=MagicMock(content="ok"))
    result = mw.wrap_tool_call(req, handler)
    handler.assert_not_called()
    assert json.loads(result.content)["error"] == "branch_missing_jira_key"


def test_allows_push_with_key_case_insensitive():
    mw = be.BranchEnforcementMiddleware()
    mw._jira_key = "PROJ-123"
    req = _req("execute", "git push origin devai-agent/proj-123-fix")
    handler = MagicMock(return_value=MagicMock(content="ok"))
    mw.wrap_tool_call(req, handler)
    handler.assert_called_once()


def test_noop_no_jira():
    mw = be.BranchEnforcementMiddleware()
    mw._jira_key = None
    req = _req("execute", "git checkout -b devai-agent/fix")
    handler = MagicMock(return_value=MagicMock(content="ok"))
    with patch("agent.middleware.branch_enforcement.get_config", return_value={"configurable": {}}):
        mw.wrap_tool_call(req, handler)
    handler.assert_called_once()


def test_noop_non_shell_tool():
    mw = be.BranchEnforcementMiddleware()
    mw._jira_key = "PROJ-123"
    req = MagicMock(
        tool_call={"name": "read_file", "args": {"path": "foo.py"}, "id": "c1"},
        tool_name="read_file",
    )
    handler = MagicMock(return_value=MagicMock(content="ok"))
    mw.wrap_tool_call(req, handler)
    handler.assert_called_once()


def test_noop_non_git_execute():
    mw = be.BranchEnforcementMiddleware()
    mw._jira_key = "PROJ-123"
    req = _req("execute", "ls -la && npm test")
    handler = MagicMock(return_value=MagicMock(content="ok"))
    mw.wrap_tool_call(req, handler)
    handler.assert_called_once()


def test_resolves_jira_key_once_and_caches_positive():
    mw = be.BranchEnforcementMiddleware()
    cfg = {"configurable": {"jira_issue": {"key": "DEMO-42"}}, "metadata": {}}
    with patch("agent.middleware.branch_enforcement.get_config", return_value=cfg) as mock_cfg:
        req1 = _req("execute", "git checkout -b devai-agent/bad1")
        handler = MagicMock(return_value=MagicMock(content="ok"))
        mw.wrap_tool_call(req1, handler)
        assert mw._jira_key == "DEMO-42"
        mock_cfg.reset_mock()
        req2 = _req("execute", "git checkout -b devai-agent/bad2")
        mw.wrap_tool_call(req2, handler)
        mock_cfg.assert_not_called()


def test_does_not_cache_negative():
    mw = be.BranchEnforcementMiddleware()
    empty = {"configurable": {}, "metadata": {}}
    good = {"configurable": {"jira_issue": {"key": "PROJ-999"}}, "metadata": {}}
    handler = MagicMock(return_value=MagicMock(content="ok"))
    with patch("agent.middleware.branch_enforcement.get_config", return_value=empty):
        mw.wrap_tool_call(_req("execute", "git checkout -b devai-agent/foo"), handler)
        assert mw._jira_key is None
        handler.assert_called_once()
        handler.reset_mock()
    with patch("agent.middleware.branch_enforcement.get_config", return_value=good):
        result = mw.wrap_tool_call(_req("execute", "git checkout -b devai-agent/foo"), handler)
        assert mw._jira_key == "PROJ-999"
        handler.assert_not_called()
        assert json.loads(result.content)["corrected_branch"] == "devai-agent/PROJ-999-foo"


def test_async_blocks():
    import asyncio

    async def _inner():
        mw = be.BranchEnforcementMiddleware()
        mw._jira_key = "PROJ-123"
        req = _req("execute", "git checkout -b devai-agent/bad")
        handler = AsyncMock(return_value=MagicMock(content="ok"))
        result = await mw.awrap_tool_call(req, handler)
        handler.assert_not_awaited()
        assert result.status == "error"

    asyncio.run(_inner())


def test_e2e_jira_retry_flow():
    mw = be.BranchEnforcementMiddleware()
    with patch(
        "agent.middleware.branch_enforcement.get_config",
        return_value={"configurable": {"jira_issue": {"key": "DEMO-42"}}},
    ):
        req_bad = _req("execute", "git checkout -b devai-agent/dark-mode")
        handler = MagicMock(return_value=MagicMock(content="ok"))
        r1 = mw.wrap_tool_call(req_bad, handler)
        assert json.loads(r1.content)["corrected_branch"] == "devai-agent/DEMO-42-dark-mode"
        handler.assert_not_called()
        req_good = _req("execute", "git checkout -b devai-agent/DEMO-42-dark-mode")
        mw.wrap_tool_call(req_good, handler)
        handler.assert_called_once()
