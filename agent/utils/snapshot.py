from __future__ import annotations

import os
from typing import Protocol
from agent.integrations.localstack_registry import LocalStackRegistry
from agent.integrations.aws_ecr_registry import AwsEcrRegistry


class RegistryBackend(Protocol):
    def push_image(self, thread_id: str, run_id: str) -> bool: ...

    def pull_image(self, thread_id: str) -> str | None: ...

    def list_tags(self, thread_id: str) -> list[str]: ...


def _load_registry_backends() -> dict[str, type]:
    backends: dict[str, type] = {}
    try:
        backends["localstack"] = LocalStackRegistry
    except ImportError:
        pass
    try:
        backends["aws_ecr"] = AwsEcrRegistry
    except ImportError:
        pass
    return backends


def create_registry() -> RegistryBackend | None:
    registry_type = os.environ.get("SANDBOX_REGISTRY_TYPE", "").lower()
    if not registry_type:
        return None

    backends = _load_registry_backends()
    cls = backends.get(registry_type)
    if not cls:
        supported = ", ".join(sorted(backends)) if backends else "none available"
        raise ValueError(
            f"Unsupported registry type: {registry_type}. Supported types: {supported}"
        )

    if registry_type == "localstack":
        endpoint = os.environ.get("SANDBOX_REGISTRY_URI", "http://localhost:4566")
        return cls(endpoint=endpoint)
    if registry_type == "aws_ecr":
        registry_uri = os.environ.get("SANDBOX_REGISTRY_URI", "")
        if not registry_uri:
            raise ValueError("SANDBOX_REGISTRY_URI must be set for ECR registry")
        repo_name = os.environ.get("SANDBOX_REGISTRY_REPO", "")
        if not repo_name:
            raise ValueError("SANDBOX_REGISTRY_REPO must be set for ECR registry")
        region = os.environ.get("AWS_REGION", "us-east-1")
        return cls(registry_uri=registry_uri, repo_name=repo_name, region=region)

    raise ValueError(f"Unsupported registry type: {registry_type}")
