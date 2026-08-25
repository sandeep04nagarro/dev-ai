from unittest.mock import MagicMock, patch

import pytest

from agent.integrations.docker import (
    SNAPSHOT_ENABLED,
    DockerSandbox,
    _resolve_from_snapshot,
    create_docker_sandbox,
)


class _FakeContainer:
    def __init__(self, short_id="fake-ctr"):
        self.short_id = short_id
        self.status = "running"
        self.started = False

    def reload(self):
        return None

    def start(self):
        self.started = True


class _FakeClient:
    def __init__(self):
        self.containers = _FakeContainers()


class _FakeContainers:
    def get(self, container_id):
        if container_id == "existing-running":
            return _FakeContainer(container_id)
        if container_id == "existing-stopped":
            c = _FakeContainer(container_id)
            c.status = "exited"
            return c
        raise __import__("docker").errors.NotFound("not found")


@pytest.fixture(autouse=True)
def _ensure_snapshot_disabled():
    if SNAPSHOT_ENABLED:
        pytest.skip("SNAPSHOT_ENABLED is set in env, cannot test disable path")


def test_create_docker_sandbox_legacy_no_id():
    with (
        patch("agent.integrations.docker.docker.from_env"),
        patch("agent.integrations.docker._create_container") as mock_create,
    ):
        mock_create.return_value = _FakeContainer("fresh-ctr")
        sandbox = create_docker_sandbox(sandbox_id=None)
        assert isinstance(sandbox, DockerSandbox)
        mock_create.assert_called_once()


def test_create_docker_sandbox_legacy_reconnect():
    fake = _FakeContainer("existing-running")
    with patch("agent.integrations.docker.docker.from_env") as mock_from_env:
        mock_client = MagicMock()
        mock_client.containers.get.return_value = fake
        mock_from_env.return_value = mock_client
        sandbox = create_docker_sandbox(sandbox_id="existing-running")
        assert isinstance(sandbox, DockerSandbox)
        assert sandbox.id == "existing-running"


def test_resolve_from_snapshot_true_state_pulls_image():
    with (
        patch("agent.integrations.docker.get_snapshot_state", return_value=True),
        patch("agent.integrations.docker.create_registry") as mock_create_registry,
        patch("agent.integrations.docker._create_container") as mock_create,
        patch("agent.integrations.docker.docker.from_env"),
    ):
        mock_registry = MagicMock()
        mock_registry.pull_image.return_value = "sha256:pulled-image-id"
        mock_create_registry.return_value = mock_registry
        mock_create.return_value = _FakeContainer("pulled-ctr")

        sandbox = _resolve_from_snapshot("thread-1")
        assert isinstance(sandbox, DockerSandbox)
        mock_registry.pull_image.assert_called_once_with("thread-1")
        args, _ = mock_create.call_args
        assert args[0] == "sha256:pulled-image-id"


def test_resolve_from_snapshot_true_state_pull_fails_falls_through():
    with (
        patch("agent.integrations.docker.get_snapshot_state", return_value=True),
        patch("agent.integrations.docker.create_registry") as mock_create_registry,
        patch("agent.integrations.docker._create_container") as mock_create,
        patch("agent.integrations.docker.docker.from_env"),
    ):
        mock_registry = MagicMock()
        mock_registry.pull_image.return_value = None
        mock_create_registry.return_value = mock_registry
        mock_create.return_value = _FakeContainer("fallback-ctr")

        sandbox = _resolve_from_snapshot("thread-1")
        assert isinstance(sandbox, DockerSandbox)
        mock_registry.pull_image.assert_called_once_with("thread-1")
        mock_create.assert_called_once()


def test_resolve_from_snapshot_string_state_reconnects():
    with (
        patch("agent.integrations.docker.docker.from_env") as mock_from_env,
        patch("agent.integrations.docker.get_snapshot_state", return_value="existing-stopped"),
    ):
        mock_client = MagicMock()
        fake = _FakeContainer("existing-stopped")
        fake.status = "exited"
        mock_client.containers.get.return_value = fake
        mock_from_env.return_value = mock_client

        sandbox = _resolve_from_snapshot("thread-1")
        assert isinstance(sandbox, DockerSandbox)
        assert sandbox.id == "existing-stopped"
        assert fake.started is True


def test_resolve_from_snapshot_string_state_container_gone():
    with (
        patch("agent.integrations.docker.get_snapshot_state", return_value="nonexistent-ctr"),
        patch("agent.integrations.docker.clear_snapshot_state") as mock_clear,
        patch("agent.integrations.docker._create_container") as mock_create,
        patch("agent.integrations.docker.docker.from_env") as mock_from_env,
    ):
        mock_client = MagicMock()
        mock_client.containers.get.side_effect = __import__("docker").errors.NotFound("gone")
        mock_from_env.return_value = mock_client
        mock_create.return_value = _FakeContainer("fresh-ctr")

        sandbox = _resolve_from_snapshot("thread-1")
        assert isinstance(sandbox, DockerSandbox)
        mock_clear.assert_called_once_with("thread-1")
        mock_create.assert_called_once()


def test_resolve_from_snapshot_no_state_creates_fresh():
    with (
        patch("agent.integrations.docker.get_snapshot_state", return_value=None),
        patch("agent.integrations.docker._create_container") as mock_create,
        patch("agent.integrations.docker.docker.from_env"),
    ):
        mock_create.return_value = _FakeContainer("fresh-ctr")
        sandbox = _resolve_from_snapshot("thread-1")
        assert isinstance(sandbox, DockerSandbox)
        mock_create.assert_called_once()
