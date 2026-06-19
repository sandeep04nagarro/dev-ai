"""Docker sandbox backend integration."""

from __future__ import annotations

import io
import logging
import os
import tarfile
import time

import docker
from deepagents.backends.protocol import (
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
)
from deepagents.backends.sandbox import BaseSandbox
from langsmith.sandbox import SandboxClientError

logger = logging.getLogger(__name__)

if os.getenv("DEBUG_MODE", "").lower() in ("on", "1", "true"):
    logger.setLevel(logging.DEBUG)

DEFAULT_IMAGE = "python:3.12-slim"
DEFAULT_MEM_LIMIT = "2g"
DEFAULT_CPU_COUNT = "2"
DEFAULT_NETWORK = "bridge"


class DockerSandbox(BaseSandbox):
    """Sandbox backed by a Docker container."""

    def __init__(self, container: docker.models.containers.Container) -> None:
        self._container = container
        self._container_short_id = container.short_id
        self._container.reload()

    @property
    def id(self) -> str:
        return self._container_short_id

    def execute(
        self,
        command: str,
        *,
        timeout: int | None = None,
    ) -> ExecuteResponse:
        try:
            exec_result = self._container.exec_run(
                cmd=["sh", "-c", command],
                workdir="/workspace",
            )
        except docker.errors.NotFound as e:
            logger.warning("Container %s unreachable: %s", self._container_short_id, e)
            raise SandboxClientError(f"Container {self._container_short_id} not found: {e}") from e
        except docker.errors.APIError as e:
            logger.warning("Container %s API error: %s", self._container_short_id, e)
            raise SandboxClientError(
                f"Docker API error for container {self._container_short_id}: {e}"
            ) from e

        output = exec_result.output
        if isinstance(output, bytes):
            output = output.decode("utf-8", errors="replace")
        else:
            output = str(output)

        logger.debug(
            "Exec cmd='%.100s' exit=%s output_bytes=%d",
            command,
            exec_result.exit_code,
            len(output),
        )

        return ExecuteResponse(
            output=output,
            exit_code=exec_result.exit_code,
            truncated=False,
        )

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        tar_buffer = io.BytesIO()
        with tarfile.open(fileobj=tar_buffer, mode="w") as tar:
            for path, content in files:
                info = tarfile.TarInfo(name=path.lstrip("/"))
                info.size = len(content)
                info.mtime = int(time.time())
                tar.addfile(info, io.BytesIO(content))
        tar_buffer.seek(0)
        try:
            self._container.put_archive("/", tar_buffer)
            logger.debug("Uploaded %d files to container %s", len(files), self._container_short_id)
        except Exception as e:
            logger.warning(
                "Upload of %d files to container %s failed: %s",
                len(files),
                self._container_short_id,
                e,
            )
            return [FileUploadResponse(path=p, error=str(e)) for p, _ in files]
        return [FileUploadResponse(path=p) for p, _ in files]

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        responses: list[FileDownloadResponse] = []
        for path in paths:
            try:
                tar_stream, _ = self._container.get_archive(path)
                content = b"".join(chunk for chunk in tar_stream)
                extracted = _extract_first_file_from_tar(content)
                responses.append(FileDownloadResponse(path=path, content=extracted))
            except docker.errors.NotFound:
                logger.debug(
                    "Download file %s not found in container %s", path, self._container_short_id
                )
                responses.append(FileDownloadResponse(path=path, error="file_not_found"))
            except Exception as e:
                logger.warning(
                    "Download of file %s from container %s failed: %s",
                    path,
                    self._container_short_id,
                    e,
                )
                responses.append(FileDownloadResponse(path=path, error=str(e)))
        logger.debug(
            "Downloaded %d files from container %s", len(responses), self._container_short_id
        )
        return responses


def _extract_first_file_from_tar(tar_bytes: bytes) -> bytes:
    with tarfile.open(fileobj=io.BytesIO(tar_bytes)) as tar:
        for member in tar:
            if member.isfile():
                f = tar.extractfile(member)
                if f:
                    return f.read()
    return b""


def create_docker_sandbox(sandbox_id: str | None = None) -> DockerSandbox:
    """Create or reconnect to a Docker container sandbox.

    Args:
        sandbox_id: Optional existing container ID to reconnect to.
            If None, creates a new container.

    Returns:
        DockerSandbox instance implementing SandboxBackendProtocol.
    """
    client = docker.from_env()

    if sandbox_id:
        try:
            container = client.containers.get(sandbox_id)
            logger.info(
                "Reconnecting to existing container %s (status=%s)", sandbox_id, container.status
            )
        except docker.errors.NotFound as e:
            logger.warning("Existing container %s not found", sandbox_id)
            raise RuntimeError(f"Existing container {sandbox_id} not found") from e
        if container.status != "running":
            logger.info("Starting stopped container %s", sandbox_id)
            container.start()
        return DockerSandbox(container)

    image = os.getenv("DOCKER_SANDBOX_IMAGE", DEFAULT_IMAGE)
    mem_limit = os.getenv("DOCKER_SANDBOX_MEM_LIMIT", DEFAULT_MEM_LIMIT)
    cpu_count = os.getenv("DOCKER_SANDBOX_CPU_COUNT", DEFAULT_CPU_COUNT)
    network = os.getenv("DOCKER_SANDBOX_NETWORK", DEFAULT_NETWORK)
    seccomp_profile = os.getenv("DOCKER_SANDBOX_SECCOMP_PROFILE", "")

    security_opt: list[str] = []
    if seccomp_profile:
        security_opt.append(f"seccomp={seccomp_profile}")

    nano_cpus = int(cpu_count) * 1_000_000_000

    cap_add_list = [
        "CHOWN",
        "DAC_OVERRIDE",
        "FOWNER",
        "SETUID",
        "SETGID",
        "SETPCAP",
        "NET_RAW",
        "SYS_CHROOT",
        "KILL",
    ]

    logger.info(
        "Creating container image=%s mem=%s cpu=%s network=%s "
        "caps_dropped=ALL caps_added=%s seccomp=%s",
        image,
        mem_limit,
        cpu_count,
        network,
        cap_add_list,
        seccomp_profile or "default",
    )

    container = client.containers.run(
        image=image,
        command="tail -f /dev/null",
        detach=True,
        auto_remove=False,
        network=network,
        mem_limit=mem_limit,
        nano_cpus=nano_cpus,
        cap_drop=["ALL"],
        cap_add=cap_add_list,
        security_opt=security_opt,
        labels={"open-swe-task": "true"},
    )

    container.exec_run("mkdir -p /workspace")
    container.exec_run("apt-get update -qq")
    container.exec_run("apt-get install -y -qq git ca-certificates")
    container.exec_run(
        "type gh >/dev/null 2>&1 || ("
        "curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg "
        "| dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg 2>/dev/null "
        "&& echo 'deb [signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] "
        "https://cli.github.com/packages stable main' "
        "| tee /etc/apt/sources.list.d/github-cli.list >/dev/null "
        "&& apt-get update -qq "
        "&& apt-get install -y -qq gh)"
    )

    logger.info("Pre-flight setup complete for container %s (git+gh installed)", container.short_id)

    return DockerSandbox(container)
