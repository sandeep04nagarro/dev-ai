"""Docker sandbox backend integration.
This is a light weight docker sandbox implementation. A custom sandbox was implemented using docker
as a sandbox provider. The Docker sandbox provider gives each coding-agent task its own isolated
Linux container. When a task starts, a fresh container is created. When the task finishes, the
container is destroyed. If the same task receives follow-up messages, the agent reconnects to the
same container with all files intact."""

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

from agent.utils.config import DockerConfig
from agent.utils.snapshot import create_registry
from agent.utils.snapshot_state import clear_snapshot_state, get_snapshot_state

logger = logging.getLogger(__name__)

if os.environ.get("DEBUG_MODE",False):
    logger.setLevel(logging.DEBUG)


class DockerSandbox(BaseSandbox):
    """Sandbox backed by a Docker container.

    Wraps a pre-baked ``open-swe-sandbox`` image that already has git
    and the GitHub CLI installed, avoiding any per-run apt-get overhead.
    """

    def __init__(self, container: docker.models.containers.Container) -> None:
        """Wrap a Docker container. Stores a reference to the live container object."""
        self._container = container
        self._container_short_id = container.short_id
        self._container.reload()

    @property
    def id(self) -> str:
        """Short container ID used as the sandbox identifier."""
        return self._container_short_id

    def stop(self, timeout: int = 5) -> None:
        """Stop the container gracefully."""
        try:
            self._container.stop(timeout=timeout)
        except docker.errors.NotFound:
            logger.info("Container %s already stopped", self._container_short_id)
        except docker.errors.APIError as e:
            logger.error("Docker API error stopping %s: %s", self._container_short_id, e)

    def remove(self, force: bool = True) -> None:
        """Remove the container."""
        try:
            self._container.remove(force=force)
        except docker.errors.NotFound:
            logger.info("Container %s already removed", self._container_short_id)
        except docker.errors.APIError as e:
            logger.error("Docker API error removing %s: %s", self._container_short_id, e)

    def execute(
        self,
        command: str,
        *,
        timeout: int | None = None,
    ) -> ExecuteResponse:
        """Run a shell command inside the container via exec. Returns output and exit code.

        A final defence-in-depth command check runs here so that a destructive
        or secret-exfiltration command can never reach ``exec_run`` even if a
        caller bypasses the agent-level :class:`CommandSafetyMiddleware`.
        The check can be disabled with ``SANDBOX_DISABLE_COMMAND_GUARD=1`` for
        debugging only.
        """
        _enforce_command_guard(command)
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
            "Exec cmd='%.100s' exit=%s output=%.100s",
            command,
            exec_result.exit_code,
            output,
        )

        return ExecuteResponse(
            output=output,
            exit_code=exec_result.exit_code,
            truncated=False,
        )

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        """Upload files into the container root filesystem via tar archive. Returns per-file result objects."""
        tar_buffer = io.BytesIO()
        with tarfile.open(fileobj=tar_buffer, mode="w") as tar:
            for path, content in files:
                info = tarfile.TarInfo(name=path.lstrip("/"))
                info.size = len(content)
                info.mtime = int(time.time())
                tar.addfile(info, io.BytesIO(content))
            tar_buffer.seek(0)
        upload_error = ""
        try:
            self._container.put_archive("/", tar_buffer)
            logger.debug("Uploaded %d files to container %s", len(files), self._container_short_id)
        except Exception as e:
            upload_error = str(e)
            logger.warning(
                "Upload of %d files to container %s failed: %s",
                len(files),
                self._container_short_id,
                upload_error,
            )
        return [FileUploadResponse(path=p, error=upload_error) for p, _ in files]

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        """Download files from the container by path, returning per-path content or error."""
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
    """Extract and return the first regular file from a tar byte stream."""
    with tarfile.open(fileobj=io.BytesIO(tar_bytes)) as tar:
        for member in tar:
            if member.isfile():
                f = tar.extractfile(member)
                if f:
                    return f.read()
    return b""

class CommandBlockedError(SandboxClientError):
    """Raised when a command is rejected by the security guard before exec.

    Subclasses :class:`SandboxClientError` so existing error handling in
    :mod:`agent.middleware.tool_error_handler` treats it as a recoverable
    sandbox failure rather than an unhandled crash.
    """


def _enforce_command_guard(command: str) -> None:
    """Defence-in-depth check run immediately before ``container.exec_run``.

    The agent-level :class:`CommandSafetyMiddleware` already blocks dangerous
    commands, but this second layer guarantees that *no* code path -- internal
    helpers, subagents, or future callers -- can bypass it and reach the shell.
    Set ``SANDBOX_DISABLE_COMMAND_GUARD=1`` to disable (debugging only).
    """
    if os.getenv("SANDBOX_DISABLE_COMMAND_GUARD", "").lower() in ("1", "true", "on", "yes"):
        return
    # Imported lazily to avoid an import cycle at module load time.
    from agent.security.command_guard import evaluate_command

    decision = evaluate_command(command)
    if decision.allowed:
        return
    logger.warning(
        "DockerSandbox command guard blocked command (category=%s reason=%s): %.200r",
        decision.category,
        decision.reason,
        command,
    )
    raise CommandBlockedError(
        f"Command blocked by sandbox security guard: {decision.reason} "
        f"(category={decision.category})."
    )


SNAPSHOT_ENABLED = os.environ.get("SANDBOX_SNAPSHOT_ENABLED", "").lower() in (
    "1", "true", "on", "yes",
)


def _create_container(image: str, client: docker.DockerClient | None = None) -> docker.models.containers.Container:
    if client is None:
        client = docker.from_env()
    mem_limit = DockerConfig.MEM_LIMIT
    cpu_count = DockerConfig.CPU_COUNT
    network = DockerConfig.NETWORK
    seccomp_profile = DockerConfig.SECCOMP_PROFILE

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

    _env_to_forward = ["GITHUB_TOKEN", "GH_TOKEN"]
    container_env = {k: os.environ[k] for k in _env_to_forward if k in os.environ}

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
        environment=container_env,
    )

    logger.info("Container %s created from image %s", container.short_id, image)
    return container


def _resolve_from_snapshot(thread_id: str) -> DockerSandbox:

    state = get_snapshot_state(thread_id)
    client = docker.from_env()

    if state is True:
        registry = create_registry()
        if registry:
            tag = registry.pull_image(thread_id)
            if tag:
                return DockerSandbox(_create_container(tag, client))
            logger.warning("Failed to pull snapshot for thread %s, creating fresh", thread_id)
        else:
            logger.warning("Snapshot enabled but no registry configured for thread %s", thread_id)
    elif isinstance(state, str):
        try:
            container = client.containers.get(state)
            container.reload()
            logger.info("Reconnecting to stopped container %s for thread %s", state, thread_id)
            if container.status != "running":
                container.start()
            return DockerSandbox(container)
        except docker.errors.NotFound:
            logger.warning("Container %s for thread %s not found, creating fresh", state, thread_id)
            clear_snapshot_state(thread_id)

    return DockerSandbox(_create_container(DockerConfig.IMAGE, client))


def create_docker_sandbox(sandbox_id: str | None = None) -> DockerSandbox:
    """Create or reconnect to a Docker container sandbox.

    When *sandbox_id* is ``None`` a new container is started from the
    ``DOCKER_SANDBOX_IMAGE`` image (defaults to ``open-swe-sandbox:latest``).
    When an id is supplied the function reconnects to an existing container,
    starting it first if it is stopped.

    When ``SANDBOX_SNAPSHOT_ENABLED`` is set, *sandbox_id* is treated as a
    ``thread_id`` and the function resolves the sandbox from snapshot state:
    either pulling a previously pushed image from the registry, reconnecting
    to a stopped container, or creating a fresh container as fallback.

    The ``GITHUB_TOKEN`` environment variable (if set) is forwarded into the
    container so the in-container GitHub CLI can authenticate without extra
    setup.

    Args:
        sandbox_id: Optional existing container ID to reconnect to.
            If ``None``, creates a new container.

    Returns:
        DockerSandbox instance implementing SandboxBackendProtocol.
    """
    if SNAPSHOT_ENABLED and sandbox_id is not None:
        return _resolve_from_snapshot(sandbox_id)

    client = docker.from_env()

    if sandbox_id:
        try:
            container = client.containers.get(sandbox_id)
            container.reload()
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

    return DockerSandbox(_create_container(DockerConfig.IMAGE, client))
