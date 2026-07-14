"""Admin email gate driven by config."""

from __future__ import annotations

from agent.utils.config import RepoConfig


def _admin_emails() -> frozenset[str]:
    raw = RepoConfig.CONFIGURED_ADMINS
    return frozenset(e.strip().lower() for e in raw.split(",") if e.strip())


def is_admin(email: str | None) -> bool:
    if not email:
        return False
    return email.strip().lower() in _admin_emails()
