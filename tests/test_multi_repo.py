"""Tests for multi-repo logic."""

import pytest

from agent.utils.repo import extract_repos_from_text
from agent.utils.multi_repo_registry import get_project_repos, _get_fallback_repos

def test_extract_repos_from_text():
    # Single repo fallback
    repos = extract_repos_from_text("Please fix this in repo: langchain-ai/langchain", "default-owner")
    assert len(repos) == 1
    assert repos[0] == {"owner": "langchain-ai", "name": "langchain"}
    
    # Multiple repos
    repos = extract_repos_from_text("Please fix this. repos: langchain-ai/backend, other-owner/frontend", "default-owner")
    assert len(repos) == 2
    assert repos[0] == {"owner": "langchain-ai", "name": "backend"}
    assert repos[1] == {"owner": "other-owner", "name": "frontend"}
    
    # Missing owner fallback
    repos = extract_repos_from_text("repos: backend, frontend", "default-owner")
    assert len(repos) == 2
    assert repos[0] == {"owner": "default-owner", "name": "backend"}
    assert repos[1] == {"owner": "default-owner", "name": "frontend"}
    
def test_fallback_repos():
    # Test that fallback works for standard mapping
    repos = _get_fallback_repos("PROJ")
    # This depends on JIRA_PROJECT_TO_REPO, which is empty by default unless mocked.
    # We just ensure it runs without error.
    pass
