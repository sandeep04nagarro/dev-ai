"""Tests for the Layer-B run watcher.

`watch_run_and_signal_failure` lives in `agent.utils.thread_ops`;
`spawn_run_watcher` / `run_id_from_create_response` live in `agent.webapp`;
the signal POST itself lives in `agent.utils.completion`.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from agent import webapp
from agent.utils import thread_ops
from agent.utils.thread_ops import watch_run_and_signal_failure
from agent.webapp import run_id_from_create_response, spawn_run_watcher


def _client_mock(*, join_effect=None, get_result=None):
    client = MagicMock()
    client.runs.join = AsyncMock(side_effect=join_effect)
    client.runs.get = AsyncMock(return_value=get_result)
    return client


async def test_join_error_signals_failed():
    signal = AsyncMock()
    client = _client_mock(join_effect=RuntimeError("boom"))
    with (
        patch.object(thread_ops, "langgraph_client", return_value=client),
        patch.object(thread_ops, "_watcher_enabled", return_value=True),
    ):
        await watch_run_and_signal_failure("t-1", "r-1", source="jira", signal_fn=signal)
    signal.assert_awaited_once_with("t-1", "failed")


async def test_join_ok_success_status_no_signal():
    signal = AsyncMock()
    client = _client_mock(join_effect=None, get_result={"status": "success"})
    with (
        patch.object(thread_ops, "langgraph_client", return_value=client),
        patch.object(thread_ops, "_watcher_enabled", return_value=True),
    ):
        await watch_run_and_signal_failure("t-1", "r-1", source="jira", signal_fn=signal)
    signal.assert_not_awaited()


async def test_join_ok_error_status_signals_failed():
    signal = AsyncMock()
    client = _client_mock(join_effect=None, get_result={"status": "error"})
    with (
        patch.object(thread_ops, "langgraph_client", return_value=client),
        patch.object(thread_ops, "_watcher_enabled", return_value=True),
    ):
        await watch_run_and_signal_failure("t-1", "r-1", source="github-issue", signal_fn=signal)
    signal.assert_awaited_once_with("t-1", "failed")


async def test_join_ok_interrupted_status_signals_failed():
    signal = AsyncMock()
    client = _client_mock(join_effect=None, get_result={"status": "interrupted"})
    with (
        patch.object(thread_ops, "langgraph_client", return_value=client),
        patch.object(thread_ops, "_watcher_enabled", return_value=True),
    ):
        await watch_run_and_signal_failure("t-1", "r-1", signal_fn=signal)
    signal.assert_awaited_once_with("t-1", "failed")


async def test_join_ok_get_fails_does_not_signal():
    signal = AsyncMock()
    client = _client_mock(join_effect=None)
    client.runs.get = AsyncMock(side_effect=RuntimeError("store down"))
    with (
        patch.object(thread_ops, "langgraph_client", return_value=client),
        patch.object(thread_ops, "_watcher_enabled", return_value=True),
    ):
        await watch_run_and_signal_failure("t-1", "r-1", signal_fn=signal)
    signal.assert_not_awaited()


async def test_cancelled_join_reraises_without_signal():
    signal = AsyncMock()
    client = _client_mock(join_effect=asyncio.CancelledError())
    with (
        patch.object(thread_ops, "langgraph_client", return_value=client),
        patch.object(thread_ops, "_watcher_enabled", return_value=True),
    ):
        try:
            await watch_run_and_signal_failure("t-1", "r-1", signal_fn=signal)
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError("expected CancelledError")
    signal.assert_not_awaited()


async def test_missing_ids_noop():
    signal = AsyncMock()
    await watch_run_and_signal_failure("", "r-1", signal_fn=signal)
    await watch_run_and_signal_failure("t-1", None, signal_fn=signal)
    signal.assert_not_awaited()


async def test_disabled_watcher_noop():
    signal = AsyncMock()
    client = _client_mock(join_effect=RuntimeError("boom"))
    with (
        patch.object(thread_ops, "langgraph_client", return_value=client),
        patch.object(thread_ops, "_watcher_enabled", return_value=False),
    ):
        await watch_run_and_signal_failure("t-1", "r-1", signal_fn=signal)
    signal.assert_not_awaited()
    client.runs.join.assert_not_awaited()


def test_run_id_from_create_response_shapes():
    assert run_id_from_create_response({"run_id": "r-1"}) == "r-1"
    assert run_id_from_create_response({"run_id": "<unknown>"}) is None
    assert run_id_from_create_response({}) is None
    assert run_id_from_create_response(MagicMock(run_id="r-2")) == "r-2"


async def test_spawn_run_watcher_spawns_task():
    seen = {}

    async def fake_watch(thread_id, run_id, *, source=""):
        seen["args"] = (thread_id, run_id, source)

    with patch.object(webapp, "watch_run_and_signal_failure", side_effect=fake_watch):
        spawn_run_watcher({"run_id": "r-9"}, "t-9", source="jira")
        await asyncio.sleep(0)  # let the created task run
    assert seen["args"] == ("t-9", "r-9", "jira")


async def test_spawn_run_watcher_skips_without_run_id():
    with patch.object(webapp, "watch_run_and_signal_failure") as mock_watch:
        spawn_run_watcher({}, "t-9", source="jira")
        await asyncio.sleep(0)
    mock_watch.assert_not_called()
