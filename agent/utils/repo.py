"""Utilities for extracting repository configuration from text."""

from __future__ import annotations

import os
import re

_DEFAULT_REPO_OWNER = os.environ.get("DEFAULT_REPO_OWNER", "langchain-ai")


def extract_repo_from_text(text: str, default_owner: str | None = None) -> dict[str, str] | None:
    """Extract owner/name repo config from text containing repo: syntax or GitHub URLs.

    Checks for explicit ``repo:owner/name`` or ``repo owner/name`` first, then
    falls back to GitHub URL extraction.

    Returns:
        A dict with ``owner`` and ``name`` keys, or ``None`` if no repo found.
    """
    if default_owner is None:
        default_owner = _DEFAULT_REPO_OWNER
    owner: str | None = None
    name: str | None = None

    if "repo:" in text or "repo " in text:
        match = re.search(r"repo[: ]([a-zA-Z0-9_.\-/]+)", text)
        if match:
            value = match.group(1).rstrip("/")
            if "/" in value:
                owner, name = value.split("/", 1)
            else:
                owner = default_owner
                name = value

    if not owner or not name:
        github_match = re.search(r"github\.com/([a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+)", text)
        if github_match:
            owner, name = github_match.group(1).split("/", 1)

    if owner and name:
        return {"owner": owner, "name": name}
    return None

def extract_repos_from_text(text: str, default_owner: str | None = None) -> list[dict[str, str]]:
    """Extract multiple owner/name repo configs from text containing repos: syntax.
    
    Example: ``repos: owner/name1, owner/name2``
    """
    if default_owner is None:
        default_owner = _DEFAULT_REPO_OWNER
        
    repos: list[dict[str, str]] = []
    
    if "repos:" in text or "repos " in text:
        match = re.search(r"repos[: ]([a-zA-Z0-9_.\-/, ]+)", text)
        if match:
            value = match.group(1).strip()
            # Split by comma or space
            parts = [p.strip() for p in re.split(r'[, ]+', value) if p.strip()]
            for part in parts:
                if "/" in part:
                    o, n = part.split("/", 1)
                    repos.append({"owner": o, "name": n})
                else:
                    repos.append({"owner": default_owner, "name": part})
                    
    # If no "repos:" syntax found, fallback to single "repo:"
    if not repos:
        single_repo = extract_repo_from_text(text, default_owner)
        if single_repo:
            repos.append(single_repo)
            
    return repos
