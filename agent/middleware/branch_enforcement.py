"""Deterministic Jira branch enforcement — tool hook only, no prompt editing."""

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
ENFORCED_BRANCH_PREFIX = "devai-agent/"
SHELL_TOOL_NAMES = frozenset(
    {"execute", "bash", "shell", "run_terminal_cmd", "terminal", "run_command"}
)

_CHECKOUT_RE = re.compile(r"git\s+checkout\s+-[bB]\s+['\"]?([^\s'\";|&]+)['\"]?")
_SWITCH_RE = re.compile(r"git\s+switch\s+-c\s+['\"]?([^\s'\";|&]+)['\"]?")
_CHECKOUT_PLAIN_RE = re.compile(r"git\s+checkout\s+['\"]?(devai-agent/[^\s'\";|&]+)['\"]?")
_SWITCH_PLAIN_RE = re.compile(r"git\s+switch\s+['\"]?(devai-agent/[^\s'\";|&]+)['\"]?")
# backward-compat: also catch legacy open-swe/ branches so they get corrected to devai-agent/
_CHECKOUT_PLAIN_LEGACY_RE = re.compile(r"git\s+checkout\s+['\"]?(open-swe/[^\s'\";|&]+)['\"]?")
_SWITCH_PLAIN_LEGACY_RE = re.compile(r"git\s+switch\s+['\"]?(open-swe/[^\s'\";|&]+)['\"]?")
_PUSH_RE = re.compile(r"git\s+push\b[^;|&]*?\borigin\s+(?:HEAD:)?([^\s'\";|&]+)")


def slugify(text: str, max_len: int = 40) -> str:
    if not text or not text.strip():
        return "task"
    if "/" in text:
        text = text.rsplit("/", 1)[-1]
    s = text.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = s.strip("-")
    s = re.sub(r"-{2,}", "-", s)
    if not s:
        return "task"
    if len(s) > max_len:
        s = s[:max_len].rstrip("-") or "task"
    return s or "task"


def contains_jira_key(branch: str, jira_key: str) -> bool:
    return jira_key.lower() in branch.lower()


def build_enforced_branch_name(jira_key: str, attempted_branch: str) -> str:
    if (
        attempted_branch
        and attempted_branch.startswith(ENFORCED_BRANCH_PREFIX)
        and contains_jira_key(attempted_branch, jira_key)
    ):
        return attempted_branch
    # legacy prefix with key -> just swap prefix, preserve rest and casing
    if (
        attempted_branch
        and attempted_branch.lower().startswith("open-swe/")
        and contains_jira_key(attempted_branch, jira_key)
    ):
        suffix = attempted_branch.split("/", 1)[-1]
        return f"{ENFORCED_BRANCH_PREFIX}{suffix}"
    raw = attempted_branch or "task"
    if "/" in raw:
        raw = raw.rsplit("/", 1)[-1]
    slug = slugify(raw, max_len=40)
    if slug.lower().startswith(jira_key.lower() + "-"):
        return f"{ENFORCED_BRANCH_PREFIX}{slug}"
    return f"{ENFORCED_BRANCH_PREFIX}{jira_key}-{slug}"


def extract_branch_from_command(command: str) -> str | None:
    if "git " not in command:
        return None
    if not ("checkout" in command or "switch" in command or "push" in command):
        return None
    m = _CHECKOUT_RE.search(command)
    if m:
        return m.group(1).strip("'\"")
    m = _SWITCH_RE.search(command)
    if m:
        return m.group(1).strip("'\"")
    m = _CHECKOUT_PLAIN_RE.search(command)
    if m:
        return m.group(1).strip("'\"")
    m = _SWITCH_PLAIN_RE.search(command)
    if m:
        return m.group(1).strip("'\"")
    m = _CHECKOUT_PLAIN_LEGACY_RE.search(command)
    if m:
        return m.group(1).strip("'\"")
    m = _SWITCH_PLAIN_LEGACY_RE.search(command)
    if m:
        return m.group(1).strip("'\"")
    if "push" in command and "origin" in command:
        m = _PUSH_RE.search(command)
        if m:
            branch = m.group(1).strip("'\"")
            if ":" in branch:
                branch = branch.rsplit(":", 1)[-1]
            return branch
    return None


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
    jira_key: str,
    attempted: str,
    corrected: str,
    corrected_command: str,
    tool_name: str,
    call_id: str,
) -> ToolMessage:
    payload = {
        "status": "error",
        "error": "branch_missing_jira_key",
        "name": tool_name,
        "message": f"Branch '{attempted}' does not contain Jira key '{jira_key}'. Use '{corrected}' instead. Run: {corrected_command}",
        "jira_key": jira_key,
        "attempted_branch": attempted,
        "corrected_branch": corrected,
        "corrected_command": corrected_command,
    }
    return ToolMessage(
        content=json.dumps(payload), tool_call_id=call_id or "unknown", status="error"
    )


class BranchEnforcementMiddleware(AgentMiddleware):
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
        jira_key = self._get_key()
        if not jira_key:
            return None
        branch = extract_branch_from_command(command)
        if not branch:
            return None
        if branch.startswith(ENFORCED_BRANCH_PREFIX) and contains_jira_key(branch, jira_key):
            return None
        corrected = build_enforced_branch_name(jira_key, branch)
        corrected_command = command.replace(branch, corrected, 1)
        return branch, corrected, corrected_command

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
        attempted, corrected, corrected_command = blocked
        logger.warning(
            "BranchEnforcementMiddleware: blocked '%s' missing %s, suggest %s",
            attempted,
            self._jira_key,
            corrected,
        )
        return _blocked(
            self._jira_key or "",
            attempted,
            corrected,
            corrected_command,
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
        attempted, corrected, corrected_command = blocked
        logger.warning(
            "BranchEnforcementMiddleware: blocked '%s' missing %s, suggest %s",
            attempted,
            self._jira_key,
            corrected,
        )
        return _blocked(
            self._jira_key or "",
            attempted,
            corrected,
            corrected_command,
            name,
            _tool_id(request) or "unknown",
        )
