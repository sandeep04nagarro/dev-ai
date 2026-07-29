from unittest.mock import MagicMock, patch

import pytest

from agent.integrations.localstack_registry import LocalStackRegistry


@pytest.fixture
def registry():
    return LocalStackRegistry(endpoint="localhost:4566")


def test_push_image_success(registry):
    mock_docker_api = MagicMock()
    docker_api_tag = MagicMock()
    docker_api_push = MagicMock()
    mock_docker_api.tag = docker_api_tag
    mock_docker_api.push = docker_api_push

    mock_images_remove = MagicMock()
    mock_docker = MagicMock()
    mock_docker.api = mock_docker_api
    mock_docker.images.remove = mock_images_remove

    with patch.object(registry, "_docker", mock_docker):
        result = registry.push_image("thread-1", "run-abc")

    assert result is True
    assert docker_api_tag.call_count == 2
    assert docker_api_push.call_count == 2
    assert mock_images_remove.call_count == 2


def test_push_image_api_error(registry):
    mock_docker_api = MagicMock()
    mock_docker_api.tag.side_effect = __import__("docker").errors.APIError("push failed")

    mock_docker = MagicMock()
    mock_docker.api = mock_docker_api

    with patch.object(registry, "_docker", mock_docker):
        result = registry.push_image("thread-1", "run-abc")

    assert result is False


def test_pull_image_latest_success(registry):
    mock_image = MagicMock()
    mock_image.id = "sha256:pulled-id"

    mock_images_pull = MagicMock(return_value=mock_image)
    mock_docker = MagicMock()
    mock_docker.images.pull = mock_images_pull

    with patch.object(registry, "_docker", mock_docker):
        result = registry.pull_image("thread-1")

    assert result == "sha256:pulled-id"
    mock_images_pull.assert_called_once()


def test_pull_image_latest_not_found_falls_back_to_list_tags(registry):
    mock_images_pull = MagicMock()
    mock_images_pull.side_effect = [
        __import__("docker").errors.NotFound("not found"),
        MagicMock(id="sha256:fallback-id"),
    ]

    mock_docker = MagicMock()
    mock_docker.images.pull = mock_images_pull

    with (
        patch.object(registry, "_docker", mock_docker),
        patch.object(registry, "list_tags", return_value=["tag-1", "tag-2"]) as mock_list_tags,
    ):
        result = registry.pull_image("thread-1")

    assert result == "sha256:fallback-id"
    mock_list_tags.assert_called_once_with("thread-1")


def test_pull_image_no_tags(registry):
    mock_images_pull = MagicMock()
    mock_images_pull.side_effect = __import__("docker").errors.NotFound("not found")

    mock_docker = MagicMock()
    mock_docker.images.pull = mock_images_pull

    with (
        patch.object(registry, "_docker", mock_docker),
        patch.object(registry, "list_tags", return_value=[]),
    ):
        result = registry.pull_image("thread-1")

    assert result is None


def test_list_tags_success(registry):
    with patch("agent.integrations.localstack_registry.httpx.Client") as mock_httpx:
        mock_response = MagicMock()
        mock_response.json.return_value = {"tags": ["tag-1", "tag-2"]}
        mock_httpx.return_value.__enter__.return_value.get.return_value = mock_response

        tags = registry.list_tags("thread-1")

    assert tags == ["tag-1", "tag-2"]


def test_list_tags_404(registry):
    import httpx

    with patch("agent.integrations.localstack_registry.httpx.Client") as mock_httpx:
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "404", request=MagicMock(), response=MagicMock(status_code=404)
        )
        mock_httpx.return_value.__enter__.return_value.get.return_value = mock_response

        tags = registry.list_tags("thread-1")

    assert tags == []
