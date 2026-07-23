from unittest.mock import AsyncMock, patch

import pytest

from agent.utils.snapshot_state import (
    PERSISTED,
    clear_snapshot_state,
    get_snapshot_state,
    resolve_snapshot_status,
    set_snapshot_state,
    store_snapshot_status,
)


@pytest.fixture(autouse=True)
def _clear_persisted():
    PERSISTED.clear()
    yield
    PERSISTED.clear()


def test_set_get_clear():
    set_snapshot_state("thread-1", True)
    assert get_snapshot_state("thread-1") is True

    set_snapshot_state("thread-1", "container-abc")
    assert get_snapshot_state("thread-1") == "container-abc"

    clear_snapshot_state("thread-1")
    assert get_snapshot_state("thread-1") is None


def test_clear_nonexistent():
    clear_snapshot_state("nonexistent")
    PERSISTED.clear()


@pytest.mark.asyncio
async def test_resolve_snapshot_status_returns_cached():
    set_snapshot_state("thread-1", True)
    result = await resolve_snapshot_status("thread-1")
    assert result is True


@pytest.mark.asyncio
async def test_resolve_snapshot_status_from_metadata_true():
    with patch("agent.utils.snapshot_state.get_config") as mock_get_config:
        mock_get_config.return_value = {
            "metadata": {"snapshot_status": True},
            "configurable": {},
        }
        result = await resolve_snapshot_status("thread-1")
    assert result is True
    assert get_snapshot_state("thread-1") is True


@pytest.mark.asyncio
async def test_resolve_snapshot_status_from_metadata_string():
    with patch("agent.utils.snapshot_state.get_config") as mock_get_config:
        mock_get_config.return_value = {
            "metadata": {"snapshot_status": "container-abc"},
            "configurable": {},
        }
        result = await resolve_snapshot_status("thread-1")
    assert result == "container-abc"
    assert get_snapshot_state("thread-1") == "container-abc"


@pytest.mark.asyncio
async def test_resolve_snapshot_status_no_state():
    with patch("agent.utils.snapshot_state.get_config") as mock_get_config:
        mock_get_config.return_value = {"metadata": {}, "configurable": {}}
        result = await resolve_snapshot_status("thread-1")
    assert result is None


@pytest.mark.asyncio
async def test_resolve_snapshot_status_config_exception():
    with patch("agent.utils.snapshot_state.get_config", side_effect=Exception("no config")):
        result = await resolve_snapshot_status("thread-1")
    assert result is None


@pytest.mark.asyncio
async def test_store_snapshot_status():
    mock_client = AsyncMock()
    with patch("agent.utils.snapshot_state.get_client", return_value=mock_client):
        await store_snapshot_status("thread-1", True)

    assert get_snapshot_state("thread-1") is True
    mock_client.threads.update.assert_awaited_once_with(
        thread_id="thread-1",
        metadata={"snapshot_status": True},
    )
