from __future__ import annotations

import logging

import docker
import httpx

logger = logging.getLogger(__name__)


class LocalStackRegistry:
    """
    Local Stack Registry implementation:
    activates when SANDBOX_REGISTRY_TYPE is set to localstack
    Provides a connection to the localstack container registry services
    for pushing and pulling container snapshots.
    """
    def __init__(self, endpoint: str = "http://localhost:4566"):
        self._registry_uri = endpoint
        self._docker = None

    @property
    def _docker_client(self):
        if self._docker is None:
            self._docker = docker.from_env()
        return self._docker

    def push_image(self, thread_id: str, run_id: str) -> bool:
        repo_name = f"sandbox-{thread_id}"
        local_tag = f"{repo_name}:{run_id}"
        remote_tag_run = f"{self._registry_uri}/{repo_name}:{run_id}"
        remote_tag_latest = f"{self._registry_uri}/{repo_name}:latest"

        docker_api = self._docker_client.api
        try:
            docker_api.tag(local_tag, remote_tag_run)
            docker_api.tag(local_tag, remote_tag_latest)
            docker_api.push(remote_tag_run)
            docker_api.push(remote_tag_latest)
            return True
        except docker.errors.APIError as e:
            logger.error("Docker API error pushing image for thread %s: %s", thread_id, e)
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
        repo_name = f"sandbox-{thread_id}"
        remote_latest = f"{self._registry_uri}/{repo_name}:latest"

        try:
            image = self._docker_client.images.pull(remote_latest)
            return image.id
        except docker.errors.NotFound:
            logger.info("Latest tag not found for %s, trying specific tags", repo_name)

        tags = self.list_tags(thread_id)
        if not tags:
            logger.warning("No tags found for repo %s", repo_name)
            return None

        tag = tags[-1]
        remote_tag = f"{self._registry_uri}/{repo_name}:{tag}"
        try:
            image = self._docker_client.images.pull(remote_tag)
            return image.id
        except docker.errors.NotFound:
            logger.warning("Tag %s not found for repo %s", tag, repo_name)
            return None
        except docker.errors.APIError as e:
            logger.error("Docker API error pulling image for thread %s: %s", thread_id, e)
            return None

    def list_tags(self, thread_id: str) -> list[str]:
        url = f"{self._registry_uri}/v2/sandbox-{thread_id}/tags/list"
        try:
            with httpx.Client() as client:
                resp = client.get(url, timeout=10.0)
                resp.raise_for_status()
                data = resp.json()
                return data.get("tags", [])
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return []
            logger.error("HTTP error listing tags for thread %s: %s", thread_id, e)
            return []
        except Exception as e:
            logger.error("Error listing tags for thread %s: %s", thread_id, e)
            return []
