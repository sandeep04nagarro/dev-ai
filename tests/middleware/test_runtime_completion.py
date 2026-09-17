"""Tests for runtime_completion_middleware (delegates to utils.completion)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from agent.middleware import runtime_completion as rc_module
from agent.middleware.runtime_completion import runtime_completion_middleware


def _config(thread_id: str | None = "thread-1") -> dict:
    return {"configurable": {"thread_id": thread_id}}


async def test_signals_completion_to_wrapper() -> None:
    signal = AsyncMock()
    with (
        patch.object(rc_module, "signal_runtime_completion", signal),
        patch.object(rc_module, "get_config", return_value=_config()),
    ):
        result = await runtime_completion_middleware.aafter_agent({}, MagicMock())

    assert result is None
    signal.assert_awaited_once_with("thread-1", "completed")


async def test_noop_without_thread_id():
    signal = AsyncMock()
    with (
        patch.object(rc_module, "signal_runtime_completion", signal),
        patch.object(rc_module, "get_config", return_value=_config(None)),
    ):
        result = await runtime_completion_middleware.aafter_agent({}, MagicMock())
    assert result is None
    signal.assert_not_awaited()


async def test_noop_without_configurable_dict():
    signal = AsyncMock()
    with (
        patch.object(rc_module, "signal_runtime_completion", signal),
        patch.object(rc_module, "get_config", return_value={}),
    ):
        result = await runtime_completion_middleware.aafter_agent({}, MagicMock())
    assert result is None
    signal.assert_not_awaited()


async def test_never_raises_into_the_run():
    signal = AsyncMock(side_effect=RuntimeError("wrapper down"))
    with (
        patch.object(rc_module, "signal_runtime_completion", signal),
        patch.object(rc_module, "get_config", return_value=_config()),
    ):
        result = await runtime_completion_middleware.aafter_agent({}, MagicMock())
    assert result is None  # hook swallows signal failures so the run is unaffected
