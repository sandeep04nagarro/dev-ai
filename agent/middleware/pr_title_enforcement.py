"""Deterministic PR title enforcement — tool hook only, no prompt editing."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Awaitable, Callable

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import AgentState
from langchain_core.messages import ToolMessage
from langgraph.config import get_config
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

logger = logging.getLogger(__name__)

JIRA_KEY_RE = re.compile(r"\b([A-Z][A-Z0-9]+-\d+)\b")
ALLOWED_TYPES = frozenset(
    {"feat", "fix", "chore", "ci", "docs", "refactor", "test", "perf", "build", "revert"}
)
TYPE_RE = re.compile(
    r"^\s*(feat|fix|chore|ci|docs|refactor|test|perf|build|revert)(\([^\)]+\))?:\s*", re.I
)

_TITLE_RE_QUOTED_DQ = re.compile(r'--title\s*=?\s*"([^"]+)"')
_TITLE_RE_QUOTED_SQ = re.compile(r"--title\s*=?\s*'([^']+)'")
_TITLE_RE_EQ = re.compile(r"--title\s*=\s*([^\s]+)")
SHELL_TOOL_NAMES = frozenset(
    {"execute", "bash", "shell", "run_terminal_cmd", "terminal", "run_command"}
)


def contains_bracketed_jira_key(title: str, jira_key: str) -> bool:
    return f"[{jira_key.lower()}]" in title.lower()


def extract_pr_title_from_command(command: str) -> str | None:
    if "gh " not in command and "gh" not in command:
        return None
    if "pr" not in command or not ("create" in command or "edit" in command):
        return None
    for pat in (_TITLE_RE_QUOTED_DQ, _TITLE_RE_QUOTED_SQ, _TITLE_RE_EQ):
        m = pat.search(command)
        if m:
            return m.group(1).strip()
    return None


def build_corrected_pr_title(jira_key: str, attempted: str) -> str:
    t = attempted.strip()
    # normalize idempotent: if already correct up to casing, return normalized
    m = TYPE_RE.match(t)
    if m and contains_bracketed_jira_key(t, jira_key):
        type_norm = m.group(1).lower() + (m.group(2) or "")
        desc_raw = t[m.end() :]
        desc_raw = re.sub(rf"\[{re.escape(jira_key)}\]\s*", "", desc_raw, flags=re.I).strip(" -:[]")
        if desc_raw and desc_raw[0].islower():
            desc_raw = desc_raw[0].upper() + desc_raw[1:]
        elif not desc_raw:
            desc_raw = "Update"
        candidate = f"{type_norm}: [{jira_key}] {desc_raw}"
        # if normalized candidate equals attempted ignoring case for key, consider idempotent
        if candidate.lower() == t.lower():
            return candidate
        if t == candidate:
            return t
        # if attempted already has correct prefix but wrong key case, normalize
        if contains_bracketed_jira_key(t, jira_key) and m:
            # check if lowercased versions match
            if candidate.lower() == t.lower():
                return candidate
            # if t is already candidate with lower key, return normalized upper
            if t.lower() == candidate.lower():
                return candidate

    # general rebuild
    m = TYPE_RE.match(t)
    if m:
        type_part = m.group(1).lower() + (m.group(2) or "")
        desc = t[m.end() :]
    else:
        type_part = "feat"
        desc = t
    desc = re.sub(rf"\[{re.escape(jira_key)}\]\s*", "", desc, flags=re.I)
    desc = re.sub(rf"\b{re.escape(jira_key)}\b\s*[-:]?\s*", "", desc, flags=re.I)
    desc = desc.strip(" -:[]")
    if desc:
        desc = desc[0].upper() + desc[1:]
    else:
        desc = "Update"
    prefix = f"{type_part}: [{jira_key}] "
    max_desc = 70 - len(prefix)
    if max_desc < 10:
        max_desc = 10
    if len(desc) > max_desc:
        desc = desc[:max_desc].rstrip(" -")
    return f"{prefix}{desc}"


def _tool_name(req: ToolCallRequest) -> str | None:
    tc = getattr(req, "tool_call", None)
    if isinstance(tc, dict):
        n = tc.get("name")
        if isinstance(n, str) and n:
            return n
    n = getattr(req, "tool_name", None) or getattr(req, "name", None)
    return n if isinstance(n, str) and n else None


def _tool_id(req: ToolCallRequest) -> str | None:
    tc = getattr(req, "tool_call", None)
    if isinstance(tc, dict):
        cid = tc.get("id")
        if isinstance(cid, str) and cid:
            return cid
    return None


def _cmd(req: ToolCallRequest) -> str | None:
    tc = getattr(req, "tool_call", None)
    if not isinstance(tc, dict):
        return None
    args = tc.get("args", {})
    if not isinstance(args, dict):
        return None
    for k in ("command", "cmd"):
        v = args.get(k)
        if isinstance(v, str) and v.strip():
            return v
    return None


def _blocked(
    jira_key: str, attempted: str, corrected: str, corrected_cmd: str, tool_name: str, call_id: str
) -> ToolMessage:
    payload = {
        "status": "error",
        "error": "pr_title_missing_jira_key",
        "name": tool_name,
        "message": f"PR title '{attempted}' does not contain '[{jira_key}]' or valid type. Use '{corrected}' instead. Run: {corrected_cmd}",
        "jira_key": jira_key,
        "attempted_title": attempted,
        "corrected_title": corrected,
        "corrected_command": corrected_cmd,
    }
    return ToolMessage(
        content=json.dumps(payload), tool_call_id=call_id or "unknown", status="error"
    )


class PRTitleEnforcementMiddleware(AgentMiddleware):
    state_schema = AgentState

    def __init__(self) -> None:
        self._jira_key: str | None = None

    def _get_key(self) -> str | None:
        if self._jira_key is not None:
            return self._jira_key
        try:
            cfg = get_config()
        except Exception:
            return None
        if not isinstance(cfg, dict):
            return None
        conf = cfg.get("configurable", {})
        meta = cfg.get("metadata", {})
        found: str | None = None
        if isinstance(conf, dict):
            ji = conf.get("jira_issue")
            if isinstance(ji, dict):
                k = ji.get("key")
                if isinstance(k, str) and JIRA_KEY_RE.search(k):
                    found = k.strip()
            if found is None:
                k = conf.get("jira_issue_key")
                if isinstance(k, str) and JIRA_KEY_RE.search(k):
                    found = k.strip()
        if found is None and isinstance(meta, dict):
            k = meta.get("jira_issue_key")
            if isinstance(k, str) and JIRA_KEY_RE.search(k):
                found = k.strip()
        if found is not None:
            self._jira_key = found
        return found

    def _should_block(self, tool_name: str, command: str) -> tuple[str, str, str] | None:
        if tool_name.lower() not in SHELL_TOOL_NAMES:
            return None
        if "gh " not in command or "pr" not in command:
            return None
        if "create" not in command and "edit" not in command:
            return None
        jira_key = self._get_key()
        if not jira_key:
            return None
        title = extract_pr_title_from_command(command)
        if title is None:
            return None
        has_bracket = contains_bracketed_jira_key(title, jira_key)
        has_type = bool(TYPE_RE.match(title))
        if has_bracket and has_type:
            corrected_norm = build_corrected_pr_title(jira_key, title)
            if corrected_norm == title:
                return None
            corrected_cmd = command.replace(title, corrected_norm, 1)
            return title, corrected_norm, corrected_cmd
        corrected = build_corrected_pr_title(jira_key, title)
        corrected_cmd = command.replace(title, corrected, 1)
        return title, corrected, corrected_cmd

    def wrap_tool_call(
        self, request: ToolCallRequest, handler: Callable[[ToolCallRequest], ToolMessage | Command]
    ) -> ToolMessage | Command:
        cmd = _cmd(request)
        if cmd is None:
            return handler(request)
        name = _tool_name(request) or ""
        blocked = self._should_block(name, cmd)
        if blocked is None:
            return handler(request)
        attempted, corrected, corrected_cmd = blocked
        logger.warning(
            "PRTitleEnforcementMiddleware: blocked '%s' missing [%s], suggest '%s'",
            attempted,
            self._jira_key,
            corrected,
        )
        return _blocked(
            self._jira_key or "",
            attempted,
            corrected,
            corrected_cmd,
            name,
            _tool_id(request) or "unknown",
        )

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        cmd = _cmd(request)
        if cmd is None:
            return await handler(request)
        name = _tool_name(request) or ""
        blocked = self._should_block(name, cmd)
        if blocked is None:
            return await handler(request)
        attempted, corrected, corrected_cmd = blocked
        logger.warning(
            "PRTitleEnforcementMiddleware: blocked '%s' missing [%s], suggest '%s'",
            attempted,
            self._jira_key,
            corrected,
        )
        return _blocked(
            self._jira_key or "",
            attempted,
            corrected,
            corrected_cmd,
            name,
            _tool_id(request) or "unknown",
        )
