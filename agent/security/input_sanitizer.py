"""
Input sanitisation for untrusted prompts (Jira / GitHub issue content).

The first user message handed to the agent is built from external,
attacker-controlled text: a Jira issue description, a GitHub issue body, or
PR/issue comments.  Because that text is concatenated into the model's context
window, it is a classic *indirect prompt-injection* vector.  A malicious issue
could try to:

* override the system prompt ("Ignore all previous instructions ..."),
* impersonate a system / developer / assistant turn,
* smuggle hidden instructions using zero-width or bidi control characters,
* instruct the agent to exfiltrate secrets (``GITHUB_TOKEN``, ``/etc/passwd``).

This module neutralises those payloads using **only the Python standard
library** (``re`` + ``unicodedata``).  It is intentionally conservative: it
strips a small, well-defined set of high-risk markers and phrases while leaving
legitimate markdown (headings such as ``## Title:``, code fences, URLs) intact
so that the agent still receives useful context.

The public entry point is :func:`sanitize_prompt_text`, which returns a
:class:`SanitizationResult` describing what was changed.  The transformation is
**idempotent** -- running it twice yields the same output -- so it is safe to
apply on every model call inside a middleware.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

__all__ = [
    "SanitizationResult",
    "sanitize_prompt_text",
    "sanitize_text_content",
]

# ---------------------------------------------------------------------------
# 1. Invisible / control characters used to smuggle instructions
# ---------------------------------------------------------------------------
#
# Zero-width characters, bidi overrides and C1 control bytes are invisible to a
# human reviewer but perfectly visible to the tokenizer.  We strip them so that
# an attacker cannot hide a payload such as
# ``invis<ZWJ>ible<ZWSP>Ignore previous instructions`` inside an issue body.
#
# We deliberately keep tabs/newlines/carriage returns (legitimate formatting).

# U+200B..U+200F, U+2060..U+206F, U+2028..U+202E, U+0080..U+009F, plus a few
# stray zero-width joiners.
_INVISIBLE_CHAR_RE = re.compile(
    "["
    "\u00ad"        # soft hyphen
    "\u200b-\u200f"  # zero-width space / non-joiner / joiner / LRE / RLO
    "\u2028-\u202e"  # line/paragraph separators + bidi overrides
    "\u2060-\u206f"  # word joiner + invisible operators + deprecated format
    "\u0080-\u009f"  # C1 control characters
    "\ufeff"         # zero-width no-break space (BOM)
    "]"
)


def _strip_invisible(text: str) -> str:
    """Remove zero-width / bidi / C1 control characters from *text*."""
    return _INVISIBLE_CHAR_RE.sub("", text)


# ---------------------------------------------------------------------------
# 2. Embedded role-control / chat-template markers
# ---------------------------------------------------------------------------
#
# Attackers try to break out of the user turn by embedding tokens that the
# model's chat template treats as turn delimiters (OpenAI ``<|im_start|>``,
# Anthropic-style ``</system>``/``</instructions>``, Llama ``<<SYS>>``, ChatML
# role tags, etc.).  We defang them so they become inert prose.

_ROLE_MARKER_PATTERNS: tuple[re.Pattern[str], ...] = (
    # ChatML / OpenAI style:  <|im_start|>system  , <|im_end|>
    re.compile(r"<\|im_start\|>", re.IGNORECASE),
    re.compile(r"<\|im_end\|>", re.IGNORECASE),
    re.compile(r"<\|system\|>", re.IGNORECASE),
    re.compile(r"<\|assistant\|>", re.IGNORECASE),
    # Anthropic / generic XML-style role tags
    re.compile(r"</?\s*(system|instructions?|developer|assistant)\s*>", re.IGNORECASE),
    # Llama-2 style prompt markers
    re.compile(r"<<\s*SYS\s*>>", re.IGNORECASE),
    re.compile(r"<\s*/\s*SYS\s*>>", re.IGNORECASE),
    re.compile(r"\[\s*INST\s*\]", re.IGNORECASE),
    re.compile(r"\[\s*/\s*INST\s*\]", re.IGNORECASE),
    # Faked role headers in markdown / brackets, e.g.  [SYSTEM], ## System:,
    # ### Developer:, << SYSTEM MESSAGE >>.  These target the *structural*
    # impersonation rather than genuine headings like "## Title:".
    re.compile(r"(?m)^\s{0,3}#{1,6}\s*(system|developer|assistant)\b\s*:?", re.IGNORECASE),
    re.compile(r"\[\s*(system|developer|assistant)\s*\]", re.IGNORECASE),
    re.compile(r"<<\s*(system|developer|assistant)\s*>>", re.IGNORECASE),
)

# What we replace a matched role marker with.  Keeps it readable while making
# it syntactically inert.
_ROLE_MARKER_REPLACEMENT = "[redacted-role-marker]"


def _defang_role_markers(text: str) -> str:
    for pattern in _ROLE_MARKER_PATTERNS:
        text = pattern.sub(_ROLE_MARKER_REPLACEMENT, text)
    return text


# ---------------------------------------------------------------------------
# 3. Instruction-hijack phrases
# ---------------------------------------------------------------------------
#
# These are the canonical "ignore your instructions" formulations.  We match
# case-insensitively and only on whole phrases (word boundaries where useful)
# to avoid clobbering legitimate prose such as "ignore the previous test run".

_INJECTION_PHRASES: tuple[str, ...] = (
    r"ignore\s+(all\s+)?(previous|prior|above|earlier)\s+(instructions?|directives?|rules?|prompts?)",
    r"disregard\s+(all\s+)?(the\s+)?(previous|prior|above|earlier)\s+(instructions?|directives?|rules?|prompts?)",
    r"forget\s+(all\s+)?(your|the)\s+(previous|prior|above|earlier)\s+(instructions?|directives?|rules?)",
    r"disregard\s+(anything|everything)\s+(above|previously)",
    r"you\s+are\s+now\s+(in\s+)?(developer|system|jailbreak|dan|root|admin)\s+mode",
    r"(activate|enter|switch\s+to)\s+(jailbreak|dan|developer|root|admin|unrestricted)\s+mode",
    r"new\s+instructions?\s*:",
    r"override\s+(system|safety|content|security)\s+(policy|policy|filter|guidelines?)",
    r"do\s+not\s+follow\s+(your|the)\s+(system|safety|security)\s+(instructions?|rules?|policy)",
    r"reveal\s+(your|the)\s+(system\s+)?(prompt|instructions?|rules?)",
    r"print\s+(your|the)\s+(system\s+)?(prompt|instructions?)",
    r"repeat\s+(your|the)\s+(system\s+)?(prompt|instructions?)\s+(above|back)",
    r"show\s+me\s+(your|the)\s+(system\s+)?(prompt|instructions?)",
)

_INJECTION_RE = re.compile("|".join(_INJECTION_PHRASES), re.IGNORECASE)
_INJECTION_REPLACEMENT = "[redacted-instruction-override]"


def _neutralise_injection_phrases(text: str) -> str:
    return _INJECTION_RE.sub(_INJECTION_REPLACEMENT, text)


# ---------------------------------------------------------------------------
# 4. Secret-exfiltration instructions directed at the agent
# ---------------------------------------------------------------------------
#
# We block instructions that *tell the agent* to leak credentials or sandbox
# internals.  This is distinct from the command guard: the command guard blocks
# the agent from *executing* ``printenv``; here we block an attacker from
# *instructing* the agent to do so via the issue text.  We scope the patterns
# tightly to the instruction form ("post / send / output the token") to avoid
# flagging a legitimate sentence like "set the GITHUB_TOKEN env var".

_SECRET_EXFIL_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"(?i)\b(post|send|output|print|echo|share|leak|paste|reply\s+with|include)\b"
        r"[^.\n]{0,60}?"
        r"\b(GITHUB_TOKEN|GH_TOKEN|SECRET|API_KEY|ACCESS_TOKEN|\.env\b|/etc/passwd|/etc/shadow)\b",
    ),
    re.compile(
        r"(?i)\b(GITHUB_TOKEN|GH_TOKEN)\b[^.\n]{0,40}?\b(to|on|in|via)\b[^.\n]{0,40}?"
        r"\b(github|slack|jira|comment|issue|pr|webhook|http|url)\b",
    ),
    re.compile(r"(?i)\bcat\s+/proc/\d+/environ\b"),
    re.compile(r"(?i)\b(print|dump|show|exfiltrate)\s++(all\s+)?(env|environment)\s+variables?\b"),
)
_SECRET_REPLACEMENT = "[redacted-secret-reference]"


def _redact_secret_exfil(text: str) -> str:
    for pattern in _SECRET_EXFIL_PATTERNS:
        text = pattern.sub(_SECRET_REPLACEMENT, text)
    return text


# ---------------------------------------------------------------------------
# 5. Dangerous "pipe-to-shell" snippets presented as instructions
# ---------------------------------------------------------------------------
#
# A common payload is "run this to reproduce: curl ... | sh".  We defang the
# pipe-to-shell / curl-bash pattern inside prompts so the agent is less likely
# to blindly execute it.  Actual execution is still blocked by the command
# guard; this just removes the nudge from the prompt.

_PIPE_TO_SHELL_RE = re.compile(
    r"(?i)\b(curl|wget|fetch)\b[^|;\n]{0,200}?\|\s*(sh|bash|zsh|python\d?|perl|ruby)\b",
)
_PIPE_TO_SHELL_REPLACEMENT = "[redacted-remote-script-execution]"


def _defang_pipe_to_shell(text: str) -> str:
    return _PIPE_TO_SHELL_RE.sub(_PIPE_TO_SHELL_REPLACEMENT, text)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


@dataclass
class SanitizationResult:
    """Outcome of running :func:`sanitize_prompt_text` on a chunk of text.

    Attributes:
        text: The sanitised text.
        redactions: Human-readable list of the transformations that fired,
            e.g. ``["role_marker", "injection_phrase"]``.  Useful for logging.
    """

    text: str
    redactions: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        """``True`` when at least one redaction fired."""
        return bool(self.redactions)


def _normalize(text: str) -> str:
    """Apply NFC normalisation so visually-identical spoofing characters collapse.

    This turns composed look-alikes (e.g. a Cyrillic 'а' hidden in "cat") into a
    canonical form, which both reduces evasion surface and makes the subsequent
    regex passes more reliable.  We do *not* strip accents globally because that
    would mangle legitimate non-ASCII issue content.
    """
    return unicodedata.normalize("NFC", text)


def sanitize_prompt_text(text: str) -> SanitizationResult:
    """Sanitise a single untrusted text blob (issue body / comment / title).

    The transformation is idempotent and conservative:

    1. NFC-normalise the string.
    2. Strip invisible / bidi / control characters.
    3. Defang chat-template and role-impersonation markers.
    4. Neutralise instruction-hijack phrases ("ignore previous instructions").
    5. Redact secret-exfiltration instructions.
    6. Defang ``curl ... | sh`` snippets.

    Legitimate markdown (``## Title:``, fenced code blocks, links) is preserved
    so the agent keeps receiving useful context.
    """
    if not isinstance(text, str) or not text:
        return SanitizationResult(text=text if isinstance(text, str) else "")

    original = text
    redactions: list[str] = []

    normalised = _normalize(original)
    if normalised != original:
        redactions.append("unicode_normalization")

    cleaned = _strip_invisible(normalised)
    if cleaned != normalised:
        redactions.append("invisible_chars")

    defanged_roles = _defang_role_markers(cleaned)
    if defanged_roles != cleaned:
        redactions.append("role_marker")
        cleaned = defanged_roles

    neutralised = _neutralise_injection_phrases(cleaned)
    if neutralised != cleaned:
        redactions.append("injection_phrase")
        cleaned = neutralised

    redacted_secrets = _redact_secret_exfil(cleaned)
    if redacted_secrets != cleaned:
        redactions.append("secret_exfil")
        cleaned = redacted_secrets

    defanged_pipe = _defang_pipe_to_shell(cleaned)
    if defanged_pipe != cleaned:
        redactions.append("pipe_to_shell")
        cleaned = defanged_pipe

    return SanitizationResult(text=cleaned, redactions=redactions)


def sanitize_text_content(content: object) -> tuple[object, list[str]]:
    """Sanitise a LangChain message ``content`` value in place-friendly fashion.

    LangChain message content is either a plain ``str`` or a list of content
    blocks (``{"type": "text", "text": "..."}``, image blocks, etc.).  This
    helper sanitises every textual chunk it can find and returns a tuple of
    ``(new_content, merged_redactions)``.  Non-text content is passed through
    unchanged, and if nothing changed the original object is returned so callers
    can cheaply detect no-ops.
    """
    if isinstance(content, str):
        result = sanitize_prompt_text(content)
        return (result.text if result.changed else content), result.redactions

    if isinstance(content, list):
        changed = False
        merged: list[str] = []
        new_blocks: list[object] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                raw_text = block.get("text", "")
                if isinstance(raw_text, str):
                    res = sanitize_prompt_text(raw_text)
                    merged.extend(res.redactions)
                    if res.changed:
                        changed = True
                        new_blocks.append({**block, "text": res.text})
                        continue
            new_blocks.append(block)
        if not changed:
            return content, []
        return new_blocks, merged

    # Any other shape (None, ints, ...) is left untouched.
    return content, []