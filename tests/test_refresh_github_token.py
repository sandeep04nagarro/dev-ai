from unittest.mock import MagicMock, patch

import pytest
from deepagents.backends.protocol import SandboxBackendProtocol

from agent.tools.refresh_github_token import refresh_github_token


@pytest.fixture
def mock_sandbox_backend():
    backend = MagicMock(spec=SandboxBackendProtocol)
    backend.execute = MagicMock()
    return backend


@patch("agent.tools.refresh_github_token.get_config")
@patch("agent.tools.refresh_github_token.SANDBOX_BACKENDS")
@patch("agent.tools.refresh_github_token.get_github_app_installation_token")
def test_refresh_github_token_success(
    mock_get_token, mock_sandbox_backends, mock_get_config, mock_sandbox_backend
):
    # Setup mocks
    mock_get_config.return_value = {"configurable": {"thread_id": "test_thread_123"}}
    mock_sandbox_backends.get.return_value = mock_sandbox_backend

    # get_github_app_installation_token is an async function
    mock_get_token.return_value = "fake_github_token_xyz"

    # Call the tool
    result = refresh_github_token()

    # Assertions
    assert result == {"success": True}

    mock_sandbox_backends.get.assert_called_once_with("test_thread_123")
    mock_get_token.assert_called_once()

    # Verify that the sandbox execute method was called with the correct bash command
    mock_sandbox_backend.execute.assert_called_once_with(
        "printf '%s' 'fake_github_token_xyz' | gh auth login --with-token"
    )


@patch("agent.tools.refresh_github_token.get_config")
def test_refresh_github_token_no_thread_id(mock_get_config):
    # No thread_id in config
    mock_get_config.return_value = {"configurable": {}}

    result = refresh_github_token()

    assert result == {"success": False, "error": "No thread_id found in config."}


@patch("agent.tools.refresh_github_token.get_config")
@patch("agent.tools.refresh_github_token.SANDBOX_BACKENDS")
def test_refresh_github_token_no_sandbox(mock_sandbox_backends, mock_get_config):
    mock_get_config.return_value = {"configurable": {"thread_id": "test_thread_123"}}
    # Sandbox not found
    mock_sandbox_backends.get.return_value = None

    result = refresh_github_token()

    assert result == {
        "success": False,
        "error": "No active sandbox found for thread test_thread_123.",
    }


@patch("agent.tools.refresh_github_token.get_config")
@patch("agent.tools.refresh_github_token.SANDBOX_BACKENDS")
@patch("agent.tools.refresh_github_token.get_github_app_installation_token")
def test_refresh_github_token_no_token(
    mock_get_token, mock_sandbox_backends, mock_get_config, mock_sandbox_backend
):
    mock_get_config.return_value = {"configurable": {"thread_id": "test_thread_123"}}
    mock_sandbox_backends.get.return_value = mock_sandbox_backend
    # Failed to generate token (e.g. env vars not set)
    mock_get_token.return_value = None

    result = refresh_github_token()

    assert result == {
        "success": False,
        "error": "GitHub App is not configured or failed to generate token.",
    }
