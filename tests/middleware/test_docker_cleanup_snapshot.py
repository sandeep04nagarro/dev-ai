from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.integrations.docker import DockerSandbox
from agent.middleware.docker_cleanup import _snapshot_and_cleanup


class _FakeContainer:
    def __init__(self, short_id="fake-ctr"):
        self.short_id = short_id
        self.stopped = False
        self.removed = False

    def reload(self):
        return None

    def stop(self, timeout=5):
        self.stopped = True

    def remove(self, force=True):
        self.removed = True


@pytest.mark.asyncio
async def test_snapshot_and_cleanup_push_succeeds():
    sandbox = DockerSandbox(_FakeContainer("ctr-1"))

    with (
        patch("agent.utils.snapshot.create_registry") as mock_create_registry,
        patch("agent.utils.snapshot_state.store_snapshot_status", new_callable=AsyncMock) as mock_store,
        patch("agent.middleware.docker_cleanup.docker.from_env") as mock_from_env,
    ):
        mock_registry = MagicMock()
        mock_registry.push_image.return_value = True
        mock_create_registry.return_value = mock_registry

        mock_client = MagicMock()
        mock_client.images.commit.return_value = MagicMock()
        mock_from_env.return_value = mock_client

        await _snapshot_and_cleanup(sandbox, "thread-1", {"langgraph_run_id": "run-abc"})

    assert sandbox._container.stopped is True
    mock_registry.push_image.assert_called_once_with("thread-1", "run-abc")
    mock_store.assert_awaited_once_with("thread-1", True)


@pytest.mark.asyncio
async def test_snapshot_and_cleanup_push_fails():
    sandbox = DockerSandbox(_FakeContainer("ctr-1"))

    with (
        patch("agent.utils.snapshot.create_registry") as mock_create_registry,
        patch("agent.utils.snapshot_state.store_snapshot_status", new_callable=AsyncMock) as mock_store,
        patch("agent.middleware.docker_cleanup.docker.from_env") as mock_from_env,
    ):
        mock_registry = MagicMock()
        mock_registry.push_image.return_value = False
        mock_create_registry.return_value = mock_registry

        mock_client = MagicMock()
        mock_client.images.commit.return_value = MagicMock()
        mock_from_env.return_value = mock_client

        await _snapshot_and_cleanup(sandbox, "thread-1", {"langgraph_run_id": "run-abc"})

    assert sandbox._container.removed is False
    mock_store.assert_awaited_once_with("thread-1", "ctr-1")


@pytest.mark.asyncio
async def test_snapshot_and_cleanup_commit_fails():
    sandbox = DockerSandbox(_FakeContainer("ctr-1"))

    with (
        patch("agent.utils.snapshot.create_registry") as mock_create_registry,
        patch("agent.utils.snapshot_state.store_snapshot_status", new_callable=AsyncMock) as mock_store,
        patch("agent.middleware.docker_cleanup.docker.from_env") as mock_from_env,
    ):
        mock_registry = MagicMock()
        mock_create_registry.return_value = mock_registry

        mock_client = MagicMock()
        mock_client.images.commit.side_effect = __import__("docker").errors.APIError(
            "commit failed"
        )
        mock_from_env.return_value = mock_client

        await _snapshot_and_cleanup(sandbox, "thread-1", {"langgraph_run_id": "run-abc"})

    mock_store.assert_awaited_once_with("thread-1", "ctr-1")
    mock_registry.push_image.assert_not_called()


@pytest.mark.asyncio
async def test_snapshot_and_cleanup_no_registry():
    sandbox = DockerSandbox(_FakeContainer("ctr-1"))

    with (
        patch("agent.utils.snapshot.create_registry", return_value=None),
    ):
        await _snapshot_and_cleanup(sandbox, "thread-1", {"langgraph_run_id": "run-abc"})

    assert sandbox._container.stopped is True


@pytest.mark.asyncio
async def test_snapshot_and_cleanup_no_run_id_generates_uuid():
    sandbox = DockerSandbox(_FakeContainer("ctr-1"))

    with (
        patch("agent.utils.snapshot.create_registry") as mock_create_registry,
        patch("agent.utils.snapshot_state.store_snapshot_status", new_callable=AsyncMock),
        patch("agent.middleware.docker_cleanup.docker.from_env") as mock_from_env,
    ):
        mock_registry = MagicMock()
        mock_registry.push_image.return_value = True
        mock_create_registry.return_value = mock_registry

        mock_client = MagicMock()
        mock_client.images.commit.return_value = MagicMock()
        mock_from_env.return_value = mock_client

        await _snapshot_and_cleanup(sandbox, "thread-1", {})

    mock_registry.push_image.assert_called_once()
    args, _ = mock_registry.push_image.call_args
    assert args[0] == "thread-1"
    assert isinstance(args[1], str)
