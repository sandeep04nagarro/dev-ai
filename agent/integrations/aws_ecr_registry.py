from __future__ import annotations

import base64
import logging

import boto3
import docker
from botocore.exceptions import ClientError

from agent.utils.config import DockerConfig

TIMEOUT = int(DockerConfig.TIMEOUT)

logger = logging.getLogger(__name__)


class AwsEcrRegistry:
    """
    AWS ECR Registry implementation:
    activates when SANDBOX_REGISTRY_TYPE is set to aws_ecr
    Provides a secure authorized connection to the AWS ECR
    for pushing and pulling container snapshots.
    """

    def __init__(self, registry_uri: str, repo_name: str, region: str = "us-east-1"):
        self._registry_uri = registry_uri
        self._repo_name = repo_name
        self._region = region
        self._docker = None

    @property
    def _docker_client(self):
        """
        private function used for lazy loading docker client
        to prevent blocking calls in the langgraph server.
        """
        if self._docker is None:
            self._docker = docker.from_env(timeout=TIMEOUT)
            self._ensure_auth()
        return self._docker

    def _ensure_auth(self) -> None:
        """
        Authorization helper function to support docker login
        with AWS credentials to log into AWS ECR
        """
        try:
            ecr_client = boto3.client("ecr", region_name=self._region)
            response = ecr_client.get_authorization_token()
            auth_data = response["authorizationData"][0]
            password = base64.b64decode(auth_data["authorizationToken"]).decode().split(":")[1]
            self._docker.login(
                username="AWS",
                password=password,
                registry=self._registry_uri,
            )
        except ClientError as e:
            logger.error("Failed to get ECR login password: %s", e)
            raise
        except docker.errors.APIError as e:
            logger.error("Docker login to ECR failed: %s", e)
            raise

    def push_image(self, thread_id: str, run_id: str) -> bool:
        local_tag = f"sandbox-{thread_id}:{run_id}"
        remote_tag_run = f"{self._registry_uri}/{self._repo_name}:{thread_id}-{run_id}"
        remote_tag_latest = f"{self._registry_uri}/{self._repo_name}:{thread_id}-latest"

        docker_api = self._docker_client.api
        try:
            docker_api.tag(local_tag, remote_tag_run)
            docker_api.tag(local_tag, remote_tag_latest)
            docker_api.push(remote_tag_run)
            docker_api.push(remote_tag_latest)
            return True
        except docker.errors.APIError as e:
            logger.error("Docker API error pushing ECR image for thread %s: %s", thread_id, e)
            return False
        finally:
            for tag in (remote_tag_run, remote_tag_latest):
                try:
                    self._docker_client.images.remove(tag)
                except docker.errors.NotFound:
                    pass
                except Exception:
                    logger.exception("Failed to remove remote tag %s", tag)

    def pull_image(self, thread_id: str) -> str | None:
        thread_latest = f"{self._registry_uri}/{self._repo_name}:{thread_id}-latest"

        try:
            image = self._docker_client.images.pull(thread_latest)
            return image.id
        except docker.errors.NotFound:
            logger.info("ECR latest tag not found for thread %s, trying specific tags", thread_id)

        tags = self.list_tags(thread_id)
        if not tags:
            logger.warning("No ECR tags found for thread %s", thread_id)
            return None

        tag = tags[-1]
        remote_tag = f"{self._registry_uri}/{self._repo_name}:{tag}"
        try:
            image = self._docker_client.images.pull(remote_tag)
            return image.id
        except docker.errors.NotFound:
            logger.warning("ECR tag %s not found for thread %s", tag, thread_id)
            return None
        except docker.errors.APIError as e:
            logger.error("Docker API error pulling ECR image for thread %s: %s", thread_id, e)
            return None

    def list_tags(self, thread_id: str) -> list[str]:
        try:
            ecr_client = boto3.client("ecr", region_name=self._region)
            response = ecr_client.list_images(repositoryName=self._repo_name)
            return sorted(
                item["imageTag"]
                for item in response.get("imageIds", [])
                if "imageTag" in item and item["imageTag"].startswith(f"{thread_id}-")
            )
        except ClientError as e:
            logger.error("Failed to list ECR images for %s: %s", self._repo_name, e)
            return []
        except Exception as e:
            logger.error("Error listing ECR images for %s: %s", self._repo_name, e)
            return []
