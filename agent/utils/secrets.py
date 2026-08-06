"""In-memory secrets store backed by AWS Secrets Manager."""

from __future__ import annotations

import json
import logging
import os

import boto3
from botocore.exceptions import BotoCoreError, ClientError

logger = logging.getLogger(__name__)

_SCOPE_TO_SECRET: dict[str, str] = {
    "dev": "dev-ai-agents-secret-manager",
    "staging": "dev-ai-agents-secret-manager",
    "prod": "dev-ai-agents-secret-manager",
}


class SecretsManager:
    """
    Singleton Secret Manager Class:
    Loads the secrets from AWS Secret MAnager once
    and stores them in an in-memory dict.
    """

    _secrets: dict[str, str] = {}
    _loaded: bool = False

    @classmethod
    def load(
        cls,
        *,
        secret_name: str | None = None,
        region: str | None = None,
        scope_override: str | None = None,
    ) -> None:

        if cls._loaded:
            logger.warning("SecretsManager.load() called more than once — skipping")
            return

        scope = scope_override or os.environ.get("SCOPE", "dev")
        resolved_name = secret_name or _SCOPE_TO_SECRET.get(scope)
        if not resolved_name:
            logger.warning(
                "No AWS secret name mapped for SCOPE=%r — falling back to os.environ only",
                scope,
            )
            cls._loaded = True
            return

        resolved_region = region or os.environ.get("AWS_REGION", "ap-south-1")

        try:
            client = boto3.client("secretsmanager", region_name=resolved_region)
            response = client.get_secret_value(SecretId=resolved_name)
            logger.info("%s is the response from the client", response)
        except (BotoCoreError, ClientError) as exc:
            logger.error(
                "Failed to fetch secret %r from AWS Secrets Manager (%s): %s — "
                "falling back to os.environ only",
                resolved_name,
                resolved_region,
                exc,
            )
            cls._loaded = True
            return

        secret_string: str | None = response.get("SecretString")
        if not secret_string:
            logger.warning("Secret %r has no SecretString — nothing to load", resolved_name)
            cls._loaded = True
            return

        try:
            payload: dict[str, str] = json.loads(secret_string)
        except json.JSONDecodeError:
            logger.error(
                "Secret %r is not valid JSON — cannot parse key-value pairs", resolved_name
            )
            cls._loaded = True
            return

        if not isinstance(payload, dict):
            logger.error(
                "Secret %r is a JSON %s, expected a JSON object — skipping",
                resolved_name,
                type(payload).__name__,
            )
            cls._loaded = True
            return

        cls._secrets.clear()
        cls._secrets.update((str(k), str(v)) for k, v in payload.items() if v is not None)
        cls._loaded = True

        logger.info(
            "Loaded %d secrets from AWS Secrets Manager (%s / %s)",
            len(cls._secrets),
            resolved_name,
            resolved_region,
        )

    @classmethod
    def get(cls, key: str, default: str | None = None) -> str | None:
        if not cls._loaded:
            cls.load()
        return cls._secrets.get(key) or os.environ.get(key, default)

    @classmethod
    def __getitem__(cls, key: str) -> str:
        val = cls.get(key)
        if val is None:
            msg = f"Secret {key!r} not found in SecretsManager or environment"
            raise KeyError(msg)
        return val

    @classmethod
    def __contains__(cls, key: str) -> bool:
        return key in cls._secrets or key in os.environ

    @classmethod
    def all(cls) -> dict[str, str]:
        return dict(cls._secrets)

    @classmethod
    def reset(cls) -> None:
        cls._secrets.clear()
        cls._loaded = False
