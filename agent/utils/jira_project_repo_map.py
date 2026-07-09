"""Jira Project ID to GitHub Repository mapping."""

from __future__ import annotations

import json

from agent.utils import config as cfg

_JIRA_PROJECT_TO_REPO_RAW = cfg.JIRA_PROJECT_TO_REPO

try:
    JIRA_PROJECT_TO_REPO: dict[str, dict[str, str]] = json.loads(_JIRA_PROJECT_TO_REPO_RAW)
except Exception:
    JIRA_PROJECT_TO_REPO = {}
