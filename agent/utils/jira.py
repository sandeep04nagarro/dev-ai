"""Jira API utilities."""

from __future__ import annotations

import base64
import logging

# import os
import re
from typing import Any

import httpx

from agent.utils.secrets import SecretsManager

# from agent.utils.langsmith import get_langsmith_trace_url

logger = logging.getLogger(__name__)

# JIRA_API_TOKEN = os.environ.get("JIRA_API_TOKEN", "")
# JIRA_EMAIL = os.environ.get("JIRA_EMAIL", "")
# JIRA_DOMAIN = os.environ.get("JIRA_DOMAIN", "")  # e.g., your-domain.atlassian.net
JIRA_API_TOKEN = SecretsManager.get("JIRA_API_TOKEN", "")
JIRA_EMAIL = SecretsManager.get("JIRA_EMAIL", "")
JIRA_DOMAIN = SecretsManager.get("JIRA_DOMAIN", "")  # e.g., your-domain.atlassian.net

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


# ---------------------------------------------------------------------------
# Markdown → ADF converter
# ---------------------------------------------------------------------------
# Jira Cloud REST API v3 uses Atlassian Document Format (ADF) for rich text.
# Plain markdown strings are NOT rendered — every character is shown literally.
# We convert common markdown constructs to their ADF equivalents here so all
# callers (post_jira_comment / update_jira_comment) automatically produce
# properly formatted Jira comments.
#
# Supported patterns:
#   - Fenced code blocks  ```lang\n...\n```
#   - Headings            ## Title
#   - Horizontal rules    --- / ───…
#   - Markdown tables     | col | col |
#   - Bullet lists        * item / - item
#   - Bold inline         **text**
#   - Inline code         `code`
#   - Paragraphs          (everything else)
# ---------------------------------------------------------------------------


def _text_node(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text}


def _bold_node(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text, "marks": [{"type": "strong"}]}


def _inline_code_node(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text, "marks": [{"type": "code"}]}


def _link_node(text: str, href: str) -> dict[str, Any]:
    return {
        "type": "text",
        "text": text,
        "marks": [{"type": "link", "attrs": {"href": href}}],
    }


_INLINE_RE = re.compile(
    r"\*\*(.+?)\*\*"  # **bold**
    r"|`([^`]+)`"  # `inline code`
    r"|\[([^\]]+)\]\((https?://[^\)]+)\)"  # [text](url)
)


def _parse_inline(text: str) -> list[dict[str, Any]]:
    """Convert inline markdown (bold, code, links) into ADF inline nodes."""
    nodes: list[dict[str, Any]] = []
    pos = 0
    for m in _INLINE_RE.finditer(text):
        if m.start() > pos:
            nodes.append(_text_node(text[pos : m.start()]))
        if m.group(1) is not None:
            nodes.append(_bold_node(m.group(1)))
        elif m.group(2) is not None:
            nodes.append(_inline_code_node(m.group(2)))
        elif m.group(3) is not None:
            nodes.append(_link_node(m.group(3), m.group(4)))
        pos = m.end()
    if pos < len(text):
        nodes.append(_text_node(text[pos:]))
    return nodes or [_text_node(text)]


def _paragraph_node(text: str) -> dict[str, Any]:
    return {"type": "paragraph", "content": _parse_inline(text)}


def _heading_node(level: int, text: str) -> dict[str, Any]:
    return {
        "type": "heading",
        "attrs": {"level": min(level, 6)},
        "content": _parse_inline(text.strip()),
    }


def _code_block_node(language: str, code: str) -> dict[str, Any]:
    attrs: dict[str, Any] = {}
    if language:
        attrs["language"] = language
    return {
        "type": "codeBlock",
        "attrs": attrs,
        "content": [{"type": "text", "text": code}],
    }


def _rule_node() -> dict[str, Any]:
    return {"type": "rule"}


def _bullet_list_node(items: list[str]) -> dict[str, Any]:
    return {
        "type": "bulletList",
        "content": [
            {
                "type": "listItem",
                "content": [_paragraph_node(item.strip())],
            }
            for item in items
        ],
    }


def _table_node(lines: list[str]) -> dict[str, Any] | None:
    """Convert a markdown table to an ADF table node.

    Expects lines like:
        | Col A | Col B |
        |-------|-------|
        | val 1 | val 2 |
    """
    rows_raw = [line for line in lines if re.match(r"^\s*\|", line)]
    if len(rows_raw) < 2:  # noqa: PLR2004
        return None

    # Filter separator rows (---|---)
    data_rows = [r for r in rows_raw if not re.match(r"^\s*\|[\s\-:|]+\|\s*$", r)]
    if not data_rows:
        return None

    def _parse_row(line: str, is_header: bool) -> dict[str, Any]:
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        cell_type = "tableHeader" if is_header else "tableCell"
        return {
            "type": "tableRow",
            "content": [
                {
                    "type": cell_type,
                    "attrs": {},
                    "content": [_paragraph_node(c)],
                }
                for c in cells
            ],
        }

    table_rows = [_parse_row(data_rows[0], is_header=True)]
    for row in data_rows[1:]:
        table_rows.append(_parse_row(row, is_header=False))

    return {
        "type": "table",
        "attrs": {"isNumberColumnEnabled": False, "layout": "default"},
        "content": table_rows,
    }


_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")
_FENCE_START_RE = re.compile(r"^```(\w*)$")
_FENCE_END_RE = re.compile(r"^```\s*$")
_HR_RE = re.compile(r"^[\-\u2500]{3,}\s*$")
_BULLET_RE = re.compile(r"^[\*\-]\s+(.+)$")
_TABLE_ROW_RE = re.compile(r"^\s*\|")


def markdown_to_adf(markdown: str) -> dict[str, Any]:
    """Convert a markdown string to an Atlassian Document Format (ADF) document.

    Handles: headings, fenced code blocks, horizontal rules, markdown tables,
    bullet lists, bold/inline-code/link inline marks, and plain paragraphs.
    """
    lines = markdown.splitlines()
    doc_content: list[dict[str, Any]] = []

    i = 0
    bullet_buffer: list[str] = []
    table_buffer: list[str] = []

    def _flush_bullets() -> None:
        if bullet_buffer:
            doc_content.append(_bullet_list_node(list(bullet_buffer)))
            bullet_buffer.clear()

    def _flush_table() -> None:
        if table_buffer:
            node = _table_node(list(table_buffer))
            if node:
                doc_content.append(node)
            table_buffer.clear()

    while i < len(lines):
        line = lines[i]

        # --- Fenced code block ---
        m = _FENCE_START_RE.match(line)
        if m:
            _flush_bullets()
            _flush_table()
            lang = m.group(1)
            code_lines: list[str] = []
            i += 1
            while i < len(lines) and not _FENCE_END_RE.match(lines[i]):
                code_lines.append(lines[i])
                i += 1
            doc_content.append(_code_block_node(lang, "\n".join(code_lines)))
            i += 1
            continue

        # --- Heading ---
        m = _HEADING_RE.match(line)
        if m:
            _flush_bullets()
            _flush_table()
            level = len(m.group(1))
            doc_content.append(_heading_node(level, m.group(2)))
            i += 1
            continue

        # --- Horizontal rule (--- or ───…) ---
        if _HR_RE.match(line):
            _flush_bullets()
            _flush_table()
            doc_content.append(_rule_node())
            i += 1
            continue

        # --- Table row ---
        if _TABLE_ROW_RE.match(line):
            _flush_bullets()
            table_buffer.append(line)
            i += 1
            continue

        # Not a table row any more → flush accumulated table
        if table_buffer:
            _flush_table()

        # --- Bullet list item ---
        m = _BULLET_RE.match(line)
        if m:
            bullet_buffer.append(m.group(1))
            i += 1
            continue

        # Not a bullet → flush accumulated list
        if bullet_buffer:
            _flush_bullets()

        # --- Empty line → paragraph separator (skip) ---
        if not line.strip():
            i += 1
            continue

        # --- Plain paragraph ---
        doc_content.append(_paragraph_node(line))
        i += 1

    # Flush any trailing buffers
    _flush_bullets()
    _flush_table()

    # ADF requires at least one block node
    if not doc_content:
        doc_content.append({"type": "paragraph", "content": []})

    return {"type": "doc", "version": 1, "content": doc_content}


# ---------------------------------------------------------------------------
# Jira API calls
# ---------------------------------------------------------------------------


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


async def post_jira_comment(issue_id_or_key: str, comment_body: str) -> str | None:
    """Add a comment to a Jira issue.

    ``comment_body`` is accepted as Markdown. It is automatically converted to
    Atlassian Document Format (ADF) before being sent to the Jira API so that
    headings, bold text, code blocks, tables, and bullet lists all render
    correctly inside Jira Cloud.

    Args:
        issue_id_or_key: The Jira issue ID or Key
        comment_body: Markdown text of the comment

    Returns:
        The comment ID if successful, None otherwise
    """
    if not all([JIRA_API_TOKEN, JIRA_EMAIL, JIRA_DOMAIN]):
        return None

    url = f"{JIRA_BASE_URL}/issue/{issue_id_or_key}/comment"

    payload = {"body": markdown_to_adf(comment_body)}

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(url, headers=_headers(), json=payload)
            response.raise_for_status()
            if response.status_code == 201:
                return response.json().get("id")
            return None
        except Exception:
            logger.exception("Failed to post Jira comment to %s", issue_id_or_key)
            return None


async def update_jira_comment(issue_id_or_key: str, comment_id: str, comment_body: str) -> bool:
    """Update an existing comment on a Jira issue.

    ``comment_body`` is accepted as Markdown and converted to ADF before
    being sent (see ``post_jira_comment`` for details).

    Args:
        issue_id_or_key: The Jira issue ID or Key
        comment_id: The ID of the comment to update
        comment_body: Markdown text of the updated comment

    Returns:
        True if successful, False otherwise
    """
    if not all([JIRA_API_TOKEN, JIRA_EMAIL, JIRA_DOMAIN]):
        return False

    url = f"{JIRA_BASE_URL}/issue/{issue_id_or_key}/comment/{comment_id}"

    payload = {"body": markdown_to_adf(comment_body)}

    async with httpx.AsyncClient() as client:
        try:
            response = await client.put(url, headers=_headers(), json=payload)
            response.raise_for_status()
            return response.status_code == 200
        except Exception:
            logger.exception("Failed to update Jira comment %s on %s", comment_id, issue_id_or_key)
            return False


# async def post_jira_trace_comment(issue_id_or_key: str, thread_id: str) -> None:
#     """Post a trace URL comment on a Jira issue."""
#     trace_url = get_langsmith_trace_url(thread_id)
#     if trace_url:
#         await post_jira_comment(
#             issue_id_or_key,
#             f"On it! View trace: {trace_url}",
#         )
#     else:
#         await post_jira_comment(issue_id_or_key, "On it!")


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
