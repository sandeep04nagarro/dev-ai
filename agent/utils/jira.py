"""Jira API utilities."""

from __future__ import annotations

import base64
import logging
import os
from typing import Any

import httpx

from agent.utils.langsmith import get_langsmith_trace_url

logger = logging.getLogger(__name__)

JIRA_API_TOKEN = os.environ.get("JIRA_API_TOKEN", "")
JIRA_EMAIL = os.environ.get("JIRA_EMAIL", "")
JIRA_DOMAIN = os.environ.get("JIRA_DOMAIN", "")  # e.g., your-domain.atlassian.net

JIRA_BASE_URL = f"https://{JIRA_DOMAIN}/rest/api/3"


def _headers() -> dict[str, str]:
    """Build Jira API headers with Basic Auth."""
    auth_str = f"{JIRA_EMAIL}:{JIRA_API_TOKEN}"
    encoded_auth = base64.b64encode(auth_str.encode()).decode()
    return {
        "Authorization": f"Basic {encoded_auth}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


async def fetch_jira_issue_details(issue_id_or_key: str) -> dict[str, Any] | None:
    """Fetch full issue details from Jira API including description and comments.

    Args:
        issue_id_or_key: The Jira issue ID or Key (e.g., PROJ-123)

    Returns:
        Full issue data dict, or None if fetch failed
    """
    if not all([JIRA_API_TOKEN, JIRA_EMAIL, JIRA_DOMAIN]):
        logger.warning("Jira configuration is incomplete (missing token, email, or domain)")
        return None

    url = f"{JIRA_BASE_URL}/issue/{issue_id_or_key}"

    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(url, headers=_headers())
            response.raise_for_status()
            issue_data = response.json()
            
            # Fetch comments separately to ensure we get all of them
            comments_url = f"{url}/comment"
            comments_response = await client.get(comments_url, headers=_headers())
            if comments_response.status_code == 200:
                issue_data["comments"] = comments_response.json().get("comments", [])
            
            return issue_data
        except Exception:
            logger.exception("Failed to fetch Jira issue details for %s", issue_id_or_key)
            return None


async def post_jira_comment(issue_id_or_key: str, comment_body: str) -> bool:
    """Add a comment to a Jira issue.

    Args:
        issue_id_or_key: The Jira issue ID or Key
        comment_body: The plain text of the comment

    Returns:
        True if successful, False otherwise
    """
    if not all([JIRA_API_TOKEN, JIRA_EMAIL, JIRA_DOMAIN]):
        return False

    url = f"{JIRA_BASE_URL}/issue/{issue_id_or_key}/comment"
    
    # Jira API v3 uses ADF (Atlassian Document Format).
    # This is a simplified ADF for a single paragraph of text.
    payload = {
        "body": {
            "type": "doc",
            "version": 1,
            "content": [
                {
                    "type": "paragraph",
                    "content": [
                        {
                            "text": comment_body,
                            "type": "text"
                        }
                    ]
                }
            ]
        }
    }

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(url, headers=_headers(), json=payload)
            response.raise_for_status()
            return response.status_code == 201
        except Exception:
            logger.exception("Failed to post Jira comment to %s", issue_id_or_key)
            return False


async def post_jira_trace_comment(issue_id_or_key: str, thread_id: str) -> None:
    """Post a trace URL comment on a Jira issue."""
    trace_url = get_langsmith_trace_url(thread_id)
    if trace_url:
        await post_jira_comment(
            issue_id_or_key,
            f"On it! View trace: {trace_url}",
        )
    else:
        await post_jira_comment(issue_id_or_key, "On it!")


def extract_adf_text(adf: dict | str | None) -> str:
    """Recursively extract all text from an Atlassian Document Format (ADF) object.

    Args:
        adf: The ADF JSON object or a simple string.

    Returns:
        The extracted plain text.
    """
    if isinstance(adf, str):
        return adf
    if not isinstance(adf, dict):
        return ""

    text_parts = []

    # Check if this is a text node
    if adf.get("type") == "text" and "text" in adf:
        return adf["text"]

    # Recursively check all children in 'content'
    for item in adf.get("content", []):
        text = extract_adf_text(item)
        if text:
            text_parts.append(text)

    return " ".join(text_parts).strip()
