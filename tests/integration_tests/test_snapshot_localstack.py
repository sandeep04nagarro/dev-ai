"""Full-cycle snapshot integration test against a local LocalStack instance.

Requires:
- Docker daemon running
- LocalStack running with ECR service (``docker compose up localstack``)

Set env vars:
    SANDBOX_SNAPSHOT_ENABLED=true
    SANDBOX_REGISTRY_TYPE=localstack
    SANDBOX_REGISTRY_URI=http://localhost:4566

Run with: ``pytest -vvs tests/integration_tests/test_snapshot_localstack.py -m integration``
"""

import os
import subprocess

import docker
import pytest

from agent.integrations.docker import DockerSandbox, _create_container, create_docker_sandbox
from agent.integrations.localstack_registry import LocalStackRegistry
from agent.utils.snapshot_state import PERSISTED, set_snapshot_state

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("SANDBOX_SNAPSHOT_ENABLED", "").lower()
        in ("1", "true", "on", "yes"),
        reason="SANDBOX_SNAPSHOT_ENABLED must be set",
    ),
    pytest.mark.skipif(
        os.environ.get("SANDBOX_REGISTRY_TYPE", "") != "localstack",
        reason="SANDBOX_REGISTRY_TYPE must be localstack",
    ),
]

THREAD_ID = "snapshot-int-test"
RUN_ID = "int-test-run"


@pytest.fixture(scope="module")
def docker_client():
    try:
        client = docker.from_env()
        client.ping()
        return client
    except docker.errors.DockerException:
        pytest.skip("Docker daemon not available")


@pytest.fixture(scope="module")
def localstack_repo():
    registry_uri = os.environ.get("SANDBOX_REGISTRY_URI", "http://localhost:4566")
    result = subprocess.run(
        [
            "aws", "--endpoint-url", registry_uri,
            "ecr", "create-repository",
            "--repository-name", f"sandbox-{THREAD_ID}",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 and "RepositoryAlreadyExistsException" not in result.stderr:
        pytest.skip(f"Cannot create repo in localstack: {result.stderr}")
    yield
    subprocess.run(
        [
            "aws", "--endpoint-url", registry_uri,
            "ecr", "delete-repository",
            "--repository-name", f"sandbox-{THREAD_ID}",
            "--force",
        ],
        capture_output=True,
    )


@pytest.fixture(autouse=True)
def _clear_state():
    PERSISTED.clear()
    yield
    PERSISTED.clear()


@pytest.mark.integration
def test_full_snapshot_cycle(docker_client, localstack_repo):
    registry = LocalStackRegistry(
        endpoint=os.environ.get("SANDBOX_REGISTRY_URI", "http://localhost:4566")
    )

    set_snapshot_state(THREAD_ID, None)

    sandbox = create_docker_sandbox(THREAD_ID)
    assert isinstance(sandbox, DockerSandbox)

    result = sandbox.execute("echo 'hello snapshot' > /workspace/data.txt && cat /workspace/data.txt")
    assert result.exit_code == 0
    assert "hello snapshot" in result.output

    sandbox.stop(timeout=5)

    container = docker_client.containers.get(sandbox.id)
    docker_client.images.commit(
        container.id,
        repository=f"sandbox-{THREAD_ID}",
        tag=RUN_ID,
    )

    push_ok = registry.push_image(THREAD_ID, RUN_ID)
    assert push_ok is True, "Push to localstack should succeed"

    container.remove(force=True)

    tags = registry.list_tags(THREAD_ID)
    assert len(tags) > 0

    pulled_tag = registry.pull_image(THREAD_ID)
    assert pulled_tag is not None

    fresh_container = _create_container(pulled_tag, docker_client)
    fresh_sandbox = DockerSandbox(fresh_container)

    result = fresh_sandbox.execute("cat /workspace/data.txt")
    assert result.exit_code == 0
    assert "hello snapshot" in result.output

    fresh_sandbox.remove(force=True)
