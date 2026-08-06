from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def registry():
    with patch("agent.integrations.aws_ecr_registry.AwsEcrRegistry._ensure_auth"):
        from agent.integrations.aws_ecr_registry import AwsEcrRegistry

        return AwsEcrRegistry(
            registry_uri="123.dkr.ecr.us-east-1.amazonaws.com",
            repo_name="agent-snapshots",
            region="us-east-1",
        )


def test_push_image_success(registry):
    mock_docker_api = MagicMock()
    mock_docker_api.tag = MagicMock()
    mock_docker_api.push = MagicMock()

    mock_images_remove = MagicMock()
    mock_docker = MagicMock()
    mock_docker.api = mock_docker_api
    mock_docker.images.remove = mock_images_remove

    with patch.object(registry, "_docker", mock_docker):
        result = registry.push_image("thread-1", "run-abc")

    assert result is True
    assert mock_docker_api.tag.call_count == 2
    assert mock_docker_api.push.call_count == 2
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

    mock_docker = MagicMock()
    mock_docker.images.pull.return_value = mock_image

    with patch.object(registry, "_docker", mock_docker):
        result = registry.pull_image("thread-1")

    assert result == "sha256:pulled-id"


def test_pull_image_latest_not_found_falls_back(registry):
    mock_docker = MagicMock()
    mock_docker.images.pull = MagicMock()
    mock_docker.images.pull.side_effect = [
        __import__("docker").errors.NotFound("not found"),
        MagicMock(id="sha256:fallback-id"),
    ]

    with (
        patch.object(registry, "_docker", mock_docker),
        patch.object(registry, "list_tags", return_value=["tag-1", "tag-2"]),
    ):
        result = registry.pull_image("thread-1")

    assert result == "sha256:fallback-id"


def test_list_tags_success(registry):
    mock_ecr = MagicMock()
    mock_ecr.list_images.return_value = {
        "imageIds": [
            {"imageTag": "thread-1-run-b"},
            {"imageTag": "thread-1-latest"},
            {"imageTag": "thread-2-run-c"},
            {"imageDigest": "sha256:untagged"},
        ]
    }

    with patch("agent.integrations.aws_ecr_registry.boto3.client", return_value=mock_ecr):
        tags = registry.list_tags("thread-1")

    mock_ecr.list_images.assert_called_once_with(repositoryName="agent-snapshots")
    assert tags == ["thread-1-latest", "thread-1-run-b"]


def test_list_tags_client_error(registry):
    from botocore.exceptions import ClientError

    error = ClientError(
        {"Error": {"Code": "RepositoryNotFoundException", "Message": "missing"}},
        "ListImages",
    )
    mock_ecr = MagicMock()
    mock_ecr.list_images.side_effect = error

    with patch("agent.integrations.aws_ecr_registry.boto3.client", return_value=mock_ecr):
        tags = registry.list_tags("thread-1")

    assert tags == []
