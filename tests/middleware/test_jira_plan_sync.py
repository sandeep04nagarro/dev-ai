"""Tests for JiraPlanSyncMiddleware."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest

from agent.middleware.jira_plan_sync import JiraPlanSyncMiddleware, _sync_todos_to_jira


@pytest.mark.asyncio
@patch("agent.middleware.jira_plan_sync.post_jira_comment")
@patch("agent.middleware.jira_plan_sync.langgraph_client")
async def test_sync_todos_to_jira_first_time(
    mock_langgraph_client: MagicMock,
    mock_post_jira_comment: AsyncMock,
) -> None:
    # Setup mock client and thread
    mock_client = MagicMock()
    mock_langgraph_client.return_value = mock_client

    mock_thread = {
        "metadata": {
            "jira_issue_key": "PROJ-123",
        }
    }
    mock_client.threads.get = AsyncMock(return_value=mock_thread)
    mock_client.threads.update = AsyncMock()

    mock_post_jira_comment.return_value = "comment-999"

    # Construct request
    tool_call_mock = {
        "name": "write_todos",
        "args": {
            "todos": [
                {"status": "pending", "content": "Task 1"},
                {"status": "completed", "content": "Task 2"},
            ]
        }
    }
    request = MagicMock(spec=ToolCallRequest)
    request.tool_call = tool_call_mock
    request.runtime = MagicMock()
    request.runtime.config = {
        "configurable": {
            "thread_id": "thread-1"
        }
    }

    # Run sync
    await _sync_todos_to_jira(request)

    # Asserts
    mock_langgraph_client.assert_called_once()
    mock_client.threads.get.assert_called_once_with("thread-1")
    mock_post_jira_comment.assert_called_once_with(
        "PROJ-123",
        "## Agent Implementation Plan\n* [ ] Task 1 (pending)\n* [x] Task 2 (completed)"
    )
    # Check that metadata was updated with the comment ID
    assert mock_thread["metadata"]["jira_plan_comment_id"] == "comment-999"
    mock_client.threads.update.assert_called_once_with("thread-1", metadata=mock_thread["metadata"])


@pytest.mark.asyncio
@patch("agent.middleware.jira_plan_sync.post_jira_comment")
@patch("agent.middleware.jira_plan_sync.langgraph_client")
async def test_sync_todos_to_jira_already_posted(
    mock_langgraph_client: MagicMock,
    mock_post_jira_comment: AsyncMock,
) -> None:
    # Setup mock client and thread
    mock_client = MagicMock()
    mock_langgraph_client.return_value = mock_client

    mock_thread = {
        "metadata": {
            "jira_issue_key": "PROJ-123",
            "jira_plan_comment_id": "comment-999",
        }
    }
    mock_client.threads.get = AsyncMock(return_value=mock_thread)
    mock_client.threads.update = AsyncMock()

    # Construct request
    tool_call_mock = {
        "name": "write_todos",
        "args": {
            "todos": [
                {"status": "pending", "content": "Task 1"},
            ]
        }
    }
    request = MagicMock(spec=ToolCallRequest)
    request.tool_call = tool_call_mock
    request.runtime = MagicMock()
    request.runtime.config = {
        "configurable": {
            "thread_id": "thread-1"
        }
    }

    # Run sync
    await _sync_todos_to_jira(request)

    # Asserts
    mock_langgraph_client.assert_called_once()
    mock_client.threads.get.assert_called_once_with("thread-1")
    mock_post_jira_comment.assert_not_called()
    mock_client.threads.update.assert_not_called()


@pytest.mark.asyncio
@patch("agent.middleware.jira_plan_sync.post_jira_comment")
@patch("agent.middleware.jira_plan_sync.langgraph_client")
async def test_sync_todos_to_jira_no_issue_key(
    mock_langgraph_client: MagicMock,
    mock_post_jira_comment: AsyncMock,
) -> None:
    # Setup mock client and thread
    mock_client = MagicMock()
    mock_langgraph_client.return_value = mock_client

    mock_thread = {
        "metadata": {}
    }
    mock_client.threads.get = AsyncMock(return_value=mock_thread)

    # Construct request
    tool_call_mock = {
        "name": "write_todos",
        "args": {
            "todos": [{"status": "pending", "content": "Task 1"}]
        }
    }
    request = MagicMock(spec=ToolCallRequest)
    request.tool_call = tool_call_mock
    request.runtime = MagicMock()
    request.runtime.config = {
        "configurable": {
            "thread_id": "thread-1"
        }
    }

    # Run sync
    await _sync_todos_to_jira(request)

    # Asserts
    mock_langgraph_client.assert_called_once()
    mock_client.threads.get.assert_called_once_with("thread-1")
    mock_post_jira_comment.assert_not_called()


@pytest.mark.asyncio
@patch("agent.middleware.jira_plan_sync.post_jira_comment")
@patch("agent.middleware.jira_plan_sync.langgraph_client")
async def test_middleware_wrap_calls(
    mock_langgraph_client: MagicMock,
    mock_post_jira_comment: AsyncMock,
) -> None:
    mock_client = MagicMock()
    mock_langgraph_client.return_value = mock_client
    mock_thread = {
        "metadata": {
            "jira_issue_key": "PROJ-123",
        }
    }
    mock_client.threads.get = AsyncMock(return_value=mock_thread)
    mock_client.threads.update = AsyncMock()
    mock_post_jira_comment.return_value = "comment-999"

    # Construct request
    tool_call_mock = {
        "name": "write_todos",
        "args": {
            "todos": [
                {"status": "pending", "content": "Task 1"},
            ]
        }
    }
    request = MagicMock(spec=ToolCallRequest)
    request.tool_call = tool_call_mock
    request.runtime = MagicMock()
    request.runtime.config = {
        "configurable": {
            "thread_id": "thread-1"
        }
    }

    middleware = JiraPlanSyncMiddleware()

    # Test awrap_tool_call
    handler_mock = AsyncMock(return_value=MagicMock(spec=ToolMessage))
    await middleware.awrap_tool_call(request, handler_mock)
    handler_mock.assert_called_once_with(request)
