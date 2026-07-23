from __future__ import annotations

import json
import logging
import subprocess

import docker

logger = logging.getLogger(__name__)


class AwsEcrRegistry:
    def __init__(self, registry_uri: str, region: str = "us-east-1"):
        self._registry_uri = registry_uri
        self._region = region
        self._docker = docker.from_env()
        self._ensure_auth()

    def _ensure_auth(self) -> None:
        try:
            result = subprocess.run(
                ["aws", "ecr", "get-login-password", "--region", self._region],
                capture_output=True,
                text=True,
                check=True,
            )
            password = result.stdout.strip()
            self._docker.login(
                username="AWS",
                password=password,
                registry=self._registry_uri,
            )
        except subprocess.CalledProcessError as e:
            logger.error("Failed to get ECR login password: %s", e.stderr)
            raise
        except docker.errors.APIError as e:
            logger.error("Docker login to ECR failed: %s", e)
            raise

    def push_image(self, thread_id: str, run_id: str) -> bool:
        repo_name = f"sandbox-{thread_id}"
        local_tag = f"{repo_name}:{run_id}"
        remote_tag_run = f"{self._registry_uri}/{repo_name}:{run_id}"
        remote_tag_latest = f"{self._registry_uri}/{repo_name}:latest"

        docker_api = self._docker.api
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
                    self._docker.images.remove(tag)
                except docker.errors.NotFound:
                    pass
                except Exception:
                    logger.exception("Failed to remove remote tag %s", tag)

    def pull_image(self, thread_id: str) -> str | None:
        repo_name = f"sandbox-{thread_id}"
        remote_latest = f"{self._registry_uri}/{repo_name}:latest"

        try:
            image = self._docker.images.pull(remote_latest)
            return image.id
        except docker.errors.NotFound:
            logger.info("ECR latest tag not found for %s, trying specific tags", repo_name)

        tags = self.list_tags(thread_id)
        if not tags:
            logger.warning("No ECR tags found for repo %s", repo_name)
            return None

        tag = tags[-1]
        remote_tag = f"{self._registry_uri}/{repo_name}:{tag}"
        try:
            image = self._docker.images.pull(remote_tag)
            return image.id
        except docker.errors.NotFound:
            logger.warning("ECR tag %s not found for repo %s", tag, repo_name)
            return None
        except docker.errors.APIError as e:
            logger.error("Docker API error pulling ECR image for thread %s: %s", thread_id, e)
            return None

    def list_tags(self, thread_id: str) -> list[str]:
        repo_name = f"sandbox-{thread_id}"
        try:
            result = subprocess.run(
                [
                    "aws", "ecr", "list-images",
                    "--repository-name", repo_name,
                    "--region", self._region,
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            data = json.loads(result.stdout)
            return [item["imageTag"] for item in data.get("imageIds", []) if "imageTag" in item]
        except subprocess.CalledProcessError as e:
            logger.error("Failed to list ECR images for %s: %s", repo_name, e.stderr)
            return []
        except Exception as e:
            logger.error("Error listing ECR images for %s: %s", repo_name, e)
            return []
