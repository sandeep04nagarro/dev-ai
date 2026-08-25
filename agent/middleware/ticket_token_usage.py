"""Middleware that accumulates LLM token usage and posts it to Jira.

Tracks prompt / completion / total token counts per agent run. When the
run finishes the middleware looks up the associated Jira issue (via
thread metadata) and either creates a new comment or updates an
existing one so the ticket always reflects the latest cumulative usage.

Phase-aware logging
-------------------
This middleware also reads the ``PhaseTokenLedger`` for the current issue key
and:

1. Logs the markdown table breakdown to the server logs.

2. Posts **only the grand-total line** to the Jira comment — no phase table
   appears in Jira.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, AgentState
from langchain_core.messages import AIMessage
from langgraph.config import get_config
from langgraph.runtime import Runtime
from langgraph_sdk import get_client

from agent.utils.config import TokenLogConfig
from agent.utils.jira import post_jira_comment, update_jira_comment
from agent.utils.secrets import SecretsManager
from agent.utils.token_profiler import PhaseTokenLedger

TOKEN_USAGE_LOG_FILE = TokenLogConfig.USAGE_LOG_FILE

logger = logging.getLogger(__name__)

if TOKEN_USAGE_LOG_FILE:
    _log_dir = os.path.dirname(os.path.abspath(TOKEN_USAGE_LOG_FILE))
    if _log_dir:
        os.makedirs(_log_dir, exist_ok=True)
    _handler = logging.FileHandler(TOKEN_USAGE_LOG_FILE)
    _handler.setLevel(logging.DEBUG)
    _handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(_handler)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

_USAGE_COMMENT_META_KEY = "jira_token_usage_comment_id"
_TICKET_META_KEY = "jira_issue_key"
_TICKET_TOTAL_META_KEY = "jira_token_usage"


class TicketTokenUsageMiddleware(AgentMiddleware):
    """Accumulate LLM token usage and surface it as a Jira issue comment.

    Reads the Jira issue key from the thread's ``configurable`` metadata
    (key ``jira_issue_key``). The usage totals are stored in
    ``jira_token_usage`` and the comment id in
    ``jira_token_usage_comment_id`` so subsequent runs update the same
    comment instead of creating duplicates.

    Phase-aware behaviour
    ----------------------
    At run end (``aafter_agent``) this middleware:

    * Looks up the ``PhaseTokenLedger`` for the ticket.
    * Logs the full phase breakdown to the server log.
    * Updates the configured Jira ticket with the usage counts.
    """

    state_schema = AgentState

    def __init__(self) -> None:
        """Initialise all usage counters to zero for a fresh run."""
        self._run_accum: dict[str, int] = {
            "prompt": 0,
            "cache_read": 0,
            "completion": 0,
            "total": 0,
        }

    def after_model(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        """Accumulate token usage from the latest model response (sync path)."""
        self._accumulate(state)
        return None

    async def aafter_model(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        """Accumulate token usage from the latest model response (async path)."""
        self._accumulate(state)
        return None

    def after_agent(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        return None

    async def aafter_agent(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        """Finalise the run: compute a new cumulative total and post to Jira.

        Steps
        -----
        1. Resolve the Jira issue key from thread metadata.
        2. Merge the per-middleware run accumulator with any tokens captured by
           the ``PhaseTokenProfilerCallback`` (held in ``PhaseTokenLedger``).
        3. Flush the phase-breakdown table to the profiling log file.
        4. Post / update the Jira comment with the cumulative **total only**
           (no phase breakdown is included in the Jira comment).
        5. Persist the new comment id and cumulative total back to thread
           metadata for the next run.
        """
        logger.debug("aafter_agent: entered with _run_accum=%s", self._run_accum)

        if not any(self._run_accum.values()):
            logger.debug("aafter_agent: no usage accumulated, skipping")
            return None

        try:
            config = get_config()
        except Exception:
            logger.exception("Failed to read runtime config in token usage middleware")
            return None

        configurable = config.get("configurable", {})
        if not isinstance(configurable, dict):
            logger.debug("aafter_agent: config has no configurable dict")
            return None

        thread_id = configurable.get("thread_id")
        if not isinstance(thread_id, str) or not thread_id:
            logger.debug(
                "aafter_agent: no thread_id in configurable (keys=%s)",
                list(configurable.keys()),
            )
            return None

        lg = get_client()
        try:
            thread = await lg.threads.get(thread_id)
            metadata = thread.get("metadata", {}) if isinstance(thread, dict) else {}
        except Exception:
            logger.warning("Failed to fetch thread metadata for token usage", exc_info=True)
            return None

        logger.debug("aafter_agent: metadata keys=%s", list(metadata.keys()))

        ticket_id = metadata.get(_TICKET_META_KEY)
        if not isinstance(ticket_id, str) or not ticket_id:
            logger.debug("aafter_agent: no %s in metadata, skipping", _TICKET_META_KEY)
            return None

        jira_env_ok = all(
            # os.environ.get(k) for k in ["JIRA_API_TOKEN", "JIRA_EMAIL", "JIRA_DOMAIN"]
            SecretsManager.get(k)
            for k in ["JIRA_API_TOKEN", "JIRA_EMAIL", "JIRA_DOMAIN"]
        )
        logger.debug("aafter_agent: ticket_id=%s jira_env_configured=%s", ticket_id, jira_env_ok)
        if not jira_env_ok:
            logger.warning("aafter_agent: Jira env vars are not fully configured")

        # ------------------------------------------------------------------
        # Phase-aware: log the breakdown to server logs
        # ------------------------------------------------------------------
        try:
            ledger = PhaseTokenLedger.get(ticket_id)

            # Log the markdown table to the logger as well (visible in
            # application logs / TOKEN_USAGE_LOG_FILE debug output).
            phase_table = ledger.build_markdown_table()
            logger.info(
                "Phase token breakdown for %s (thread=%s):\n%s",
                ticket_id,
                thread_id,
                phase_table,
            )

            # Use the ledger's grand totals as the canonical numbers; they
            # include tokens captured by PhaseTokenProfilerCallback as well as
            # any that the middleware accumulator saw directly.
            ledger_totals = ledger.get_totals()
            # Merge: take whichever is larger between the ledger and middleware
            # accumulator for each bucket (avoids double-counting).
            merged_prompt = max(self._run_accum["prompt"], ledger_totals["prompt"])
            merged_cache_read = max(
                self._run_accum.get("cache_read", 0), ledger_totals.get("cache_read", 0)
            )
            merged_completion = max(self._run_accum["completion"], ledger_totals["completion"])
            merged_total = merged_prompt + merged_completion
            run_total = {
                "prompt": merged_prompt,
                "cache_read": merged_cache_read,
                "completion": merged_completion,
                "total": merged_total,
            }
        except Exception:
            logger.exception("Failed to flush phase ledger; falling back to run accumulator")
            run_total = dict(self._run_accum)

        existing_comment_id = metadata.get(_USAGE_COMMENT_META_KEY)
        ticket_total_prior = _read_ticket_total(metadata)
        new_total = {
            "prompt": ticket_total_prior["prompt"] + run_total["prompt"],
            "cache_read": ticket_total_prior.get("cache_read", 0) + run_total.get("cache_read", 0),
            "completion": ticket_total_prior["completion"] + run_total["completion"],
            "total": ticket_total_prior["total"] + run_total["total"],
        }
        logger.debug(
            "aafter_agent: ticket_total_prior=%s run_total=%s new_total=%s comment_id=%s",
            ticket_total_prior,
            run_total,
            new_total,
            existing_comment_id,
        )

        # Include both grand totals and phase breakdown in the Jira comment.
        body = _build_comment_body(ticket_id, new_total, phase_table)
        comment_id = await _post_or_update(ticket_id, existing_comment_id, body)
        logger.debug("aafter_agent: post_or_update returned comment_id=%s", comment_id)

        if comment_id:
            try:
                await lg.threads.update(
                    thread_id=thread_id,
                    metadata={
                        _USAGE_COMMENT_META_KEY: comment_id,
                        _TICKET_TOTAL_META_KEY: new_total,
                    },
                )
                logger.debug("aafter_agent: persisted metadata for thread %s", thread_id)
            except Exception:
                logger.warning("Failed to persist token usage metadata", exc_info=True)

        self._run_accum = {"prompt": 0, "cache_read": 0, "completion": 0, "total": 0}
        logger.debug("aafter_agent: reset _run_accum and done")
        return None

    def _accumulate(self, state: AgentState) -> None:
        """Walk the message list and add any found token counts to ``_run_accum``.

        Token data may live on the ``usage_metadata`` attribute of the
        last ``AIMessage`` or inside ``response_metadata`` under the
        ``token_usage`` / ``usage`` keys. Both sync and async providers
        are handled.
        """
        msgs = state.get("messages", [])
        if not msgs:
            logger.debug("_accumulate: no messages in state")
            return

        # Search backwards for the last AIMessage. Other middleware (notably
        # ensure_no_empty_msg) may have appended ToolMessages to the state
        # after the model call, so msgs[-1] is not guaranteed to be an
        # AIMessage even though this hook fires after the model.
        last = None
        for msg in reversed(msgs):
            if isinstance(msg, AIMessage):
                last = msg
                break
        if last is None:
            logger.debug("_accumulate: no AIMessage found in %d messages", len(msgs))
            return

        usage_md = getattr(last, "usage_metadata", None)
        resp_meta = last.response_metadata or {}
        logger.debug(
            "_accumulate: type=%s usage_metadata=%s response_meta_keys=%s",
            type(last).__name__,
            usage_md,
            list(resp_meta.keys()),
        )

        prompt = 0
        cache_read = 0
        completion = 0
        total = 0

        if usage_md is not None and isinstance(usage_md, dict):
            prompt = usage_md.get("input_tokens", usage_md.get("prompt_tokens", 0))
            completion = usage_md.get("output_tokens", usage_md.get("completion_tokens", 0))
            total = usage_md.get("total_tokens", 0)

            input_details = usage_md.get("input_token_details") or usage_md.get(
                "prompt_tokens_details"
            )
            if isinstance(input_details, dict):
                cache_read = input_details.get("cache_read", input_details.get("cached_tokens", 0))
            else:
                cache_read = usage_md.get("cache_read_input_tokens", 0)

        if not total:
            for key in ("token_usage", "usage"):
                u = resp_meta.get(key)
                if isinstance(u, dict):
                    prompt = u.get("prompt_tokens", u.get("input_tokens", 0))
                    completion = u.get("completion_tokens", u.get("output_tokens", 0))
                    total = u.get("total_tokens", u.get("total", 0))

                    input_details = u.get("prompt_tokens_details") or u.get("input_token_details")
                    if isinstance(input_details, dict):
                        cache_read = input_details.get(
                            "cached_tokens", input_details.get("cache_read", 0)
                        )
                    else:
                        cache_read = u.get("cache_read_input_tokens", 0)

                if not total:
                    total = int(prompt) + int(completion)
                if total:
                    logger.debug(
                        "_accumulate: found usage via response_metadata[%s]=%s",
                        key,
                        u,
                    )
                    break

        if not total:
            logger.debug("_accumulate: no usage data found in any location")
            return

        self._run_accum["prompt"] += int(prompt)
        self._run_accum["cache_read"] += int(cache_read)
        self._run_accum["completion"] += int(completion)
        self._run_accum["total"] += int(total)
        logger.debug(
            "_accumulate: added prompt=%s cache_read=%s completion=%s total=%s → run_accum=%s",
            prompt,
            cache_read,
            completion,
            total,
            self._run_accum,
        )


def _read_ticket_total(metadata: dict[str, Any]) -> dict[str, int]:
    """Read the cumulative token total stored in thread metadata.

    Returns a dict of ``{"prompt":, "cache_read":, "completion":, "total":}`` with all
    values coerced to ``int``. Falls back to all-zero when the key is
    absent or malformed.
    """
    stored = metadata.get(_TICKET_TOTAL_META_KEY)
    if isinstance(stored, dict):
        return {
            "prompt": int(stored.get("prompt", 0)),
            "cache_read": int(stored.get("cache_read", 0)),
            "completion": int(stored.get("completion", 0)),
            "total": int(stored.get("total", 0)),
        }
    return {"prompt": 0, "cache_read": 0, "completion": 0, "total": 0}


def _build_comment_body(ticket_id: str, total: dict[str, int], phase_table: str) -> str:
    """Render the token-usage markdown body for a Jira comment."""
    p = total["prompt"]
    cr = total.get("cache_read", 0)
    c = total["completion"]
    t = total["total"]

    return (
        f"**Token Usage** · {ticket_id}\n"
        f"───────────────────────────\n\n"
        f"**Cumulative Totals:**\n"
        f"| | Tokens |\n"
        f"|---|---|\n"
        f"| Input | {p:,} |\n"
        f"| Cache Read | {cr:,} |\n"
        f"| Output | {c:,} |\n"
        f"| **Total** | **{t:,}** |\n\n"
        f"**Current Run Phase Breakdown:**\n\n"
        f"{phase_table}"
    )


async def _post_or_update(ticket_id: str, existing_comment_id: str | None, body: str) -> str | None:
    """Update an existing Jira comment or create a new one and return its id."""
    if existing_comment_id:
        ok = await update_jira_comment(ticket_id, existing_comment_id, body)
        if ok:
            return existing_comment_id
        logger.warning(
            "Failed to update existing Jira comment %s; falling back to creating a new comment",
            existing_comment_id,
        )
    new_id = await post_jira_comment(ticket_id, body)
    return new_id
