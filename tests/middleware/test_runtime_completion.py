"""Tests for runtime_completion_middleware."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.middleware.runtime_completion import runtime_completion_middleware


def _config(thread_id: str | None = "thread-1") -> dict:
    return {"configurable": {"thread_id": thread_id}}


@pytest.mark.asyncio
@patch("agent.middleware.runtime_completion.httpx.AsyncClient")
@patch("agent.middleware.runtime_completion.SecretsManager")
async def test_signals_completion_to_wrapper(
    mock_secrets: MagicMock,
    mock_client_cls: MagicMock,
) -> None:
    mock_secrets.get.return_value = "http://wrapper:8001"
    response = MagicMock()
    response.status_code = 200
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.post = AsyncMock(return_value=response)
    mock_client_cls.return_value = client

    with patch("agent.middleware.runtime_completion.get_config", return_value=_config()):
        result = await runtime_completion_middleware.aafter_agent({}, MagicMock())

    assert result is None
    client.post.assert_called_once_with(
        "http://wrapper:8001/internal/completions",
        json={"thread_id": "thread-1", "status": "completed"},
        timeout=10.0,
    )


@pytest.mark.asyncio
@patch("agent.middleware.runtime_completion.httpx.AsyncClient")
@patch("agent.middleware.runtime_completion.SecretsManager")
async def test_noop_without_wrapper_url(mock_secrets: MagicMock, mock_client_cls: MagicMock):
    mock_secrets.get.return_value = ""
    with patch("agent.middleware.runtime_completion.get_config", return_value=_config()):
        result = await runtime_completion_middleware.aafter_agent({}, MagicMock())
    assert result is None
    mock_client_cls.assert_not_called()


@pytest.mark.asyncio
@patch("agent.middleware.runtime_completion.httpx.AsyncClient")
@patch("agent.middleware.runtime_completion.SecretsManager")
async def test_noop_without_thread_id(mock_secrets: MagicMock, mock_client_cls: MagicMock):
    mock_secrets.get.return_value = "http://wrapper:8001"
    with patch("agent.middleware.runtime_completion.get_config", return_value=_config(None)):
        result = await runtime_completion_middleware.aafter_agent({}, MagicMock())
    assert result is None
    mock_client_cls.assert_not_called()


@pytest.mark.asyncio
@patch("agent.middleware.runtime_completion.asyncio.sleep", new=AsyncMock())
@patch("agent.middleware.runtime_completion.httpx.AsyncClient")
@patch("agent.middleware.runtime_completion.SecretsManager")
async def test_retries_then_gives_up_without_raising(
    mock_secrets: MagicMock,
    mock_client_cls: MagicMock,
):
    mock_secrets.get.return_value = "http://wrapper:8001"
    response = MagicMock()
    response.status_code = 500
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.post = AsyncMock(return_value=response)
    mock_client_cls.return_value = client

    with patch("agent.middleware.runtime_completion.get_config", return_value=_config()):
        result = await runtime_completion_middleware.aafter_agent({}, MagicMock())

    assert result is None  # never raises into the agent run
    assert client.post.await_count == 3
