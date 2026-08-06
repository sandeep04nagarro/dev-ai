"""Shared pytest fixtures."""

from __future__ import annotations

import pytest

from agent import webapp

#: The unpatched gate, captured before the autouse fixture below replaces it, so
#: tests covering the gate itself can still exercise the real implementation.
_REAL_IS_REPO_ENABLED_FOR_REVIEW = webapp._is_repo_enabled_for_review


@pytest.fixture
def real_is_repo_enabled_for_review():
    """The real :func:`agent.webapp._is_repo_enabled_for_review`, unstubbed."""
    return _REAL_IS_REPO_ENABLED_FOR_REVIEW


@pytest.fixture(autouse=True)
def _default_enable_review_repos(monkeypatch: pytest.MonkeyPatch) -> None:
    """Treat every repo as enabled for review by default.

    The opt-in list is stored in the LangGraph Store, which is not running in the
    test environment, so :func:`agent.webapp._is_repo_enabled_for_review` would
    reject every repo and short-circuit the handlers under test.

    Tests targeting the opt-in gate itself should override this fixture with
    ``monkeypatch.setattr(webapp, "_is_repo_enabled_for_review", ...)`` (or stub
    ``list_enabled_review_repos``) to a stricter stub.
    """

    async def _enabled(_repo_config: dict[str, str]) -> bool:
        return True

    monkeypatch.setattr(webapp, "_is_repo_enabled_for_review", _enabled)
