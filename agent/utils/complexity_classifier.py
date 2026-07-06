import hashlib
import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

if os.getenv("DEBUG_MODE", "").lower() in ("on", "1", "true"):
    logger.setLevel(logging.DEBUG)


def run_layer_0(fields: dict[str, Any]) -> str | None:
    """Check Jira metadata for obvious tier decisions.

    Returns:
        "light" if obvious simple fix,
        "heavy" if obviously complex,
        None if ambiguous (needs reconnaissance)
    """
    logger.debug("run_layer_0 — labels=%s, issuetype=%s",
                 fields.get("labels"), fields.get("issuetype", {}).get("name"))
    labels = {l.lower() for l in fields.get("labels", [])}
    issuetype = (fields.get("issuetype", {}) or {}).get("name", "").lower()

    light_signals = {"typo", "documentation", "docs", "chore"}
    heavy_signals = {"epic", "migration", "breaking-change", "refactor"}
    
    if labels & light_signals:
        logger.debug("run_layer_0 → light (label match: %s)", labels & light_signals)
        return "light"
    
    if labels & heavy_signals or issuetype == "epic":
        logger.debug("run_layer_0 → heavy (label/issuetype match)")
        return "heavy"

    logger.debug("run_layer_0 → None (ambiguous)")
    return None


def ticket_hash(description: str, comments: list[dict[str, Any]]) -> str:
    """Generate deterministic hash of ticket content.

    Handles ADF comment bodies (dict) and plain text bodies (str).
    Used to detect if recon-findings from a prior run are still valid.
    """
    from agent.utils.jira import extract_adf_text

    def _body_text(c: dict[str, Any]) -> str:
        body = c.get("body", "")
        if isinstance(body, dict):
            return extract_adf_text(body)
        return str(body) if body else ""

    content = (description or "") + "".join(
        _body_text(c) for c in (comments or [])
    )
    h = hashlib.sha256(content.encode()).hexdigest()[:16]
    logger.debug("ticket_hash — desc_len=%d, comment_count=%d → %s",
                 len(description or ""), len(comments or []), h)
    return h


def parse_recon_output(state: dict[str, Any]) -> dict[str, Any]:
    """Extract recon findings JSON from thread final state.

    Recon agent outputs a fenced JSON block as its final message.
    This function extracts and validates that structure.
    """
    values = state.get("values", {})
    messages = values.get("messages", [])
    logger.debug("parse_recon_output — messages_count=%d", len(messages))

    for idx, msg in enumerate(reversed(messages)):
        if not isinstance(msg, dict) and msg.get("type") == "ai":
            content = msg.get("content", "") or ""
            logger.debug("%d - %s", idx, content)
            continue
        content = msg.get("content", "") or ""
        logger.debug("%d - %s", idx, content)
        if "```json" in content:
            start = content.find("```json") + 7
            end = content.find("```", start)
            if end > start:
                try:
                    findings = json.loads(content[start:end].strip())
                    if isinstance(findings, dict) and "status" in findings:
                        logger.debug("parse_recon_output → status=%s, scope=%s",
                                     findings.get("status"), findings.get("scope"))
                        return findings
                    logger.warning("Recon output missing 'status' field")
                except json.JSONDecodeError:
                    logger.warning("Failed to parse recon JSON output")
        # Also try without fences (fallback)
        try:
            findings = json.loads(content.strip())
            if isinstance(findings, dict) and "status" in findings:
                logger.debug("parse_recon_output (fallback) → status=%s", findings.get("status"))
                return findings
        except (json.JSONDecodeError, TypeError):
            pass

    logger.debug("parse_recon_output → {} (no valid findings found)")
    return {}


def decide_tier(recon_findings: dict[str, Any] | None, jira_fields: dict[str, Any]) -> str:
    """Deterministic tier decision with aggressive light-default.

    Args:
        recon_findings: Parsed output from reconnaissance agent, or None
        jira_fields: Jira issue fields for additional context

    Returns:
        "light" or "heavy"
    """
    if recon_findings is None:
        return "light"

    scope = recon_findings.get("scope", "narrow")
    if scope in ("cross-cutting", "wide"):
        return "heavy"

    complexity = recon_findings.get("complexity", "simple")
    if complexity == "complex":
        return "heavy"

    files_touched = recon_findings.get("files_touched", [])
    if len(files_touched) > 5:
        return "heavy"

    keywords = {k.lower() for k in recon_findings.get("keywords", [])}
    heavy_keywords = {"global", "shared", "migration", "config", "session", "context", "state"}
    if keywords & heavy_keywords:
        return "heavy"

    steps_used = recon_findings.get("steps_used", 0)
    recon_step_limit = int(os.environ.get("RECON_STEP_LIMIT", "20"))
    if steps_used >= recon_step_limit - 2:
        return "heavy"

    return "light"
