"""Tests for PRTitleEnforcementMiddleware — helpers only."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import agent.middleware.pr_title_enforcement as pe
from agent.middleware.pr_title_enforcement import (
    build_corrected_pr_title,
    contains_bracketed_jira_key,
    extract_pr_title_from_command,
)


def test_contains_bracketed():
    assert contains_bracketed_jira_key("feat: [RSGBW-193] foo", "RSGBW-193") is True
    assert contains_bracketed_jira_key("feat: [rsgbw-193] foo", "RSGBW-193") is True
    assert contains_bracketed_jira_key("feat: RSGBW-193 foo", "RSGBW-193") is False
    assert contains_bracketed_jira_key("feat: foo", "RSGBW-193") is False


def test_extract_pr_title():
    assert (
        extract_pr_title_from_command('gh pr create --draft --title "feat: foo" --body "x"')
        == "feat: foo"
    )
    assert (
        extract_pr_title_from_command("gh pr create --title 'fix: [RSGBW-193] bar' --body x")
        == "fix: [RSGBW-193] bar"
    )
    assert (
        extract_pr_title_from_command('GH_TOKEN=dummy gh pr create --title "feat: foo"')
        == "feat: foo"
    )
    assert extract_pr_title_from_command('gh pr edit 123 --title "feat: foo"') == "feat: foo"
    assert extract_pr_title_from_command("gh pr view 123 --json url") is None
    assert extract_pr_title_from_command("git push origin devai-agent/RSGBW-193-foo") is None


def test_build_corrected_pr_title_missing_jira():
    assert (
        build_corrected_pr_title("RSGBW-193", "feat: create organization")
        == "feat: [RSGBW-193] Create organization"
    )
    assert (
        build_corrected_pr_title("RSGBW-193", "create organization")
        == "feat: [RSGBW-193] Create organization"
    )
    assert (
        build_corrected_pr_title("RSGBW-193", "fix: create organization")
        == "fix: [RSGBW-193] Create organization"
    )


def test_build_corrected_idempotent():
    assert (
        build_corrected_pr_title("RSGBW-193", "feat: [RSGBW-193] Create org")
        == "feat: [RSGBW-193] Create org"
    )
    assert (
        build_corrected_pr_title("RSGBW-193", "feat: [rsgbw-193] Create org")
        == "feat: [RSGBW-193] Create org"
    )


def test_build_corrected_truncate():
    long = "feat: " + "a" * 100
    result = build_corrected_pr_title("RSGBW-193", long)
    assert len(result) <= 70
    assert "[RSGBW-193]" in result


def test_build_corrected_with_scope():
    assert (
        build_corrected_pr_title("RSGBW-193", "feat(auth): create org")
        == "feat(auth): [RSGBW-193] Create org"
    )


def test_build_corrected_strips_existing_jira():
    assert (
        build_corrected_pr_title("RSGBW-193", "feat: [RSGBW-193] create organization")
        == "feat: [RSGBW-193] Create organization"
    )
    assert (
        build_corrected_pr_title("RSGBW-193", "feat: RSGBW-193 create organization")
        == "feat: [RSGBW-193] Create organization"
    )


# ---- tool hook ----


def _req(cmd, cid="call_1"):
    return MagicMock(
        tool_call={"name": "execute", "args": {"command": cmd}, "id": cid}, tool_name="execute"
    )


def test_blocks_pr_create_missing_jira():
    mw = pe.PRTitleEnforcementMiddleware()
    mw._jira_key = "RSGBW-193"
    req = _req('gh pr create --draft --title "feat: create org" --body "x"')
    h = MagicMock(return_value=MagicMock(content="ok"))
    r = mw.wrap_tool_call(req, h)
    h.assert_not_called()
    assert r.status == "error"
    p = json.loads(r.content)
    assert p["error"] == "pr_title_missing_jira_key"
    assert p["corrected_title"] == "feat: [RSGBW-193] Create org"
    assert "RSGBW-193" in p["corrected_command"]


def test_allows_pr_create_with_jira():
    mw = pe.PRTitleEnforcementMiddleware()
    mw._jira_key = "RSGBW-193"
    req = _req('gh pr create --title "feat: [RSGBW-193] Create org" --body x')
    h = MagicMock(return_value=MagicMock(content="ok"))
    mw.wrap_tool_call(req, h)
    h.assert_called_once()


def test_blocks_missing_type():
    mw = pe.PRTitleEnforcementMiddleware()
    mw._jira_key = "RSGBW-193"
    req = _req('gh pr create --title "Create org" --body x')
    r = mw.wrap_tool_call(req, MagicMock(return_value=MagicMock(content="ok")))
    assert "feat: [RSGBW-193]" in json.loads(r.content)["corrected_title"]


def test_blocks_pr_edit():
    mw = pe.PRTitleEnforcementMiddleware()
    mw._jira_key = "RSGBW-193"
    req = _req('gh pr edit 123 --title "fix: foo" --body x')
    r = mw.wrap_tool_call(req, MagicMock(return_value=MagicMock(content="ok")))
    assert r.status == "error"


def test_noop_no_jira():
    mw = pe.PRTitleEnforcementMiddleware()
    mw._jira_key = None
    req = _req('gh pr create --title "feat: foo" --body x')
    h = MagicMock(return_value=MagicMock(content="ok"))
    with patch(
        "agent.middleware.pr_title_enforcement.get_config", return_value={"configurable": {}}
    ):
        mw.wrap_tool_call(req, h)
    h.assert_called_once()


def test_noop_no_title_flag():
    mw = pe.PRTitleEnforcementMiddleware()
    mw._jira_key = "RSGBW-193"
    req = _req('gh pr create --draft --body "x"')
    h = MagicMock(return_value=MagicMock(content="ok"))
    mw.wrap_tool_call(req, h)
    h.assert_called_once()


def test_positive_cache_and_negative_no_cache():
    mw = pe.PRTitleEnforcementMiddleware()
    cfg = {"configurable": {"jira_issue": {"key": "DEMO-1"}}}
    with patch("agent.middleware.pr_title_enforcement.get_config", return_value=cfg) as m:
        mw.wrap_tool_call(
            _req('gh pr create --title "feat: foo" --body x'),
            MagicMock(return_value=MagicMock(content="ok")),
        )
        assert mw._jira_key == "DEMO-1"
        m.reset_mock()
        mw.wrap_tool_call(
            _req('gh pr create --title "feat: foo" --body x'),
            MagicMock(return_value=MagicMock(content="ok")),
        )
        m.assert_not_called()
    mw2 = pe.PRTitleEnforcementMiddleware()
    with patch(
        "agent.middleware.pr_title_enforcement.get_config", return_value={"configurable": {}}
    ):
        mw2.wrap_tool_call(
            _req('gh pr create --title "feat: foo" --body x'),
            MagicMock(return_value=MagicMock(content="ok")),
        )
        assert mw2._jira_key is None
    with patch("agent.middleware.pr_title_enforcement.get_config", return_value=cfg):
        r = mw2.wrap_tool_call(
            _req('gh pr create --title "feat: foo" --body x'),
            MagicMock(return_value=MagicMock(content="ok")),
        )
        assert r.status == "error"


def test_async_blocks():
    import asyncio

    async def _inner():
        mw = pe.PRTitleEnforcementMiddleware()
        mw._jira_key = "RSGBW-193"
        req = _req('gh pr create --title "feat: foo" --body x')
        h = AsyncMock(return_value=MagicMock(content="ok"))
        r = await mw.awrap_tool_call(req, h)
        h.assert_not_awaited()
        assert r.status == "error"

    asyncio.run(_inner())


def test_e2e_retry():
    mw = pe.PRTitleEnforcementMiddleware()
    with patch(
        "agent.middleware.pr_title_enforcement.get_config",
        return_value={"configurable": {"jira_issue": {"key": "RSGBW-193"}}},
    ):
        req_bad = _req('gh pr create --title "feat: create org" --body x')
        h = MagicMock(return_value=MagicMock(content="ok"))
        r1 = mw.wrap_tool_call(req_bad, h)
        assert json.loads(r1.content)["corrected_title"] == "feat: [RSGBW-193] Create org"
        h.assert_not_called()
        req_good = _req('gh pr create --title "feat: [RSGBW-193] Create org" --body x')
        mw.wrap_tool_call(req_good, h)
        h.assert_called_once()
