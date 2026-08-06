"""Phase-based token profiling for the Open-SWE agent.

This module provides:

1. ``PhaseTokenProfilerCallback`` — a LangChain ``BaseCallbackHandler`` that
   categorises every LLM call into one of four phases:

   * ``multi_repo_selection``  — the repo-selector LLM call
   * ``planning``              — agent *thinking* calls (no tool results in context)
   * ``tool_execution``        — agent calls that follow at least one tool result
   * ``review``                — calls made inside the reviewer graph

   The callback keeps an in-process ledger that is logged to the server logger
   on every agent completion.

2. ``PhaseTokenLedger`` — an in-process singleton that accumulates per-phase
   token counts for a single issue run and exposes a helper to build the
   final summary row for the Jira comment.

Usage
-----
**In ``repo_selector.py``** — capture the single LLM call::

    from agent.utils.token_profiler import record_repo_selection_tokens

    response = await model.ainvoke(messages)
    record_repo_selection_tokens(issue_key, response)

**In ``server.py``** — inject the callback into the agent config::

    from agent.utils.token_profiler import PhaseTokenProfilerCallback

    callback = PhaseTokenProfilerCallback(issue_key=issue_key, thread_id=thread_id)
    callbacks = config.get("callbacks") or []
    config["callbacks"] = callbacks + [callback]

**In ``ticket_token_usage.py``** — flush the ledger to the server logs and
return total numbers for the Jira comment::

    from agent.utils.token_profiler import PhaseTokenLedger

    ledger = PhaseTokenLedger.get(issue_key)
    total = ledger.get_totals()  # {"prompt": …, "completion": …, "total": …}
    # phase breakdown is automatically logged to stdout
"""

from __future__ import annotations

import logging
import threading
from typing import Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Phase definitions
# ---------------------------------------------------------------------------

PHASE_MULTI_REPO_SELECTION = "multi_repo_selection"
PHASE_PLANNING = "planning"
PHASE_TOOL_EXECUTION = "tool_execution"
PHASE_REVIEW = "review"

ALL_PHASES = [
    PHASE_MULTI_REPO_SELECTION,
    PHASE_PLANNING,
    PHASE_TOOL_EXECUTION,
    PHASE_REVIEW,
]

_EMPTY_PHASE: dict[str, int] = {
    "prompt_tokens": 0,
    "cache_read_tokens": 0,
    "completion_tokens": 0,
    "total_tokens": 0,
}


def _new_phase_breakdown() -> dict[str, dict[str, int]]:
    return {phase: dict(_EMPTY_PHASE) for phase in ALL_PHASES}


# ---------------------------------------------------------------------------
# PhaseTokenLedger — per-issue singleton
# ---------------------------------------------------------------------------


class PhaseTokenLedger:
    """Accumulate token counts by phase for a single Jira/Linear issue.

    Thread-safe; designed to be created once per issue key and kept alive
    for the duration of the background run.
    """

    _lock: threading.Lock = threading.Lock()
    _registry: dict[str, PhaseTokenLedger] = {}

    def __init__(self, issue_key: str) -> None:
        self._issue_key = issue_key
        self._lock = threading.Lock()
        self._phases: dict[str, dict[str, int]] = _new_phase_breakdown()

    # ------------------------------------------------------------------
    # Registry helpers
    # ------------------------------------------------------------------

    @classmethod
    def get(cls, issue_key: str) -> PhaseTokenLedger:
        """Return (or create) the ledger for *issue_key*."""
        with cls._lock:
            if issue_key not in cls._registry:
                cls._registry[issue_key] = PhaseTokenLedger(issue_key)
            return cls._registry[issue_key]

    @classmethod
    def clear(cls, issue_key: str) -> None:
        """Remove the ledger for *issue_key* from the registry."""
        with cls._lock:
            cls._registry.pop(issue_key, None)

    # ------------------------------------------------------------------
    # Accumulation
    # ------------------------------------------------------------------

    def add(
        self, phase: str, prompt: int, cache_read: int, completion: int, total: int | None = None
    ) -> None:
        """Add token counts to a specific phase bucket."""
        if phase not in ALL_PHASES:
            logger.warning("Unknown profiling phase '%s', defaulting to tool_execution", phase)
            phase = PHASE_TOOL_EXECUTION
        if total is None:
            total = prompt + completion
        with self._lock:
            self._phases[phase]["prompt_tokens"] += int(prompt)
            self._phases[phase]["cache_read_tokens"] += int(cache_read)
            self._phases[phase]["completion_tokens"] += int(completion)
            self._phases[phase]["total_tokens"] += int(total)
        logger.debug(
            "Profiler [%s][%s] += input=%d cache_read=%d output=%d total=%d",
            self._issue_key,
            phase,
            prompt,
            cache_read,
            completion,
            total,
        )

    def get_phase_snapshot(self) -> dict[str, dict[str, int]]:
        """Return a deep copy of the current per-phase breakdown."""
        with self._lock:
            return {p: dict(v) for p, v in self._phases.items()}

    def get_totals(self) -> dict[str, int]:
        """Return {prompt, cache_read, completion, total} summed across all phases."""
        with self._lock:
            prompt = sum(v["prompt_tokens"] for v in self._phases.values())
            cache_read = sum(v["cache_read_tokens"] for v in self._phases.values())
            completion = sum(v["completion_tokens"] for v in self._phases.values())
            return {
                "prompt": prompt,
                "cache_read": cache_read,
                "completion": completion,
                "total": prompt + completion,
            }

    def build_markdown_table(self) -> str:
        """Build a markdown table showing the per-phase token breakdown."""
        phases = self.get_phase_snapshot()
        totals = self.get_totals()

        header = "| Phase | Input | Cache Read | Output | Total |\n"
        sep = "|-------|-------|------------|--------|-------|\n"
        rows: list[str] = []

        phase_labels = {
            PHASE_MULTI_REPO_SELECTION: "Repo Selection",
            PHASE_PLANNING: "Planning",
            PHASE_TOOL_EXECUTION: "Tool Execution (Search/Edit)",
            PHASE_REVIEW: "Review / Verification",
        }

        for phase_key in ALL_PHASES:
            label = phase_labels.get(phase_key, phase_key)
            d = phases[phase_key]
            p = d["prompt_tokens"]
            cr = d["cache_read_tokens"]
            c = d["completion_tokens"]
            t = d["total_tokens"]
            rows.append(f"| {label} | {p:,} | {cr:,} | {c:,} | {t:,} |\n")

        grand_p = totals["prompt"]
        grand_cr = totals["cache_read"]
        grand_c = totals["completion"]
        grand_t = totals["total"]
        rows.append(
            f"| **Total** | **{grand_p:,}** | **{grand_cr:,}** | **{grand_c:,}** | **{grand_t:,}** |\n"
        )

        return header + sep + "".join(rows)


# ---------------------------------------------------------------------------
# Convenience helper for repo_selector.py
# ---------------------------------------------------------------------------


def record_repo_selection_tokens(
    issue_key: str,
    response: Any,
    thread_id: str = "",
) -> None:
    """Extract usage from a LangChain response object and record it.

    Handles both ``usage_metadata`` (new-style) and
    ``response_metadata["token_usage"]`` (old-style) formats.

    Args:
        issue_key:  Jira/Linear issue key, e.g. ``PROJ-123``.
        response:   The return value of ``model.ainvoke(messages)``.
        thread_id:  Optional LangGraph thread id for log correlation.
    """
    prompt, cache_read, completion, total = _extract_usage(response)
    if not total:
        logger.debug("record_repo_selection_tokens: no usage found for %s", issue_key)
        return

    ledger = PhaseTokenLedger.get(issue_key)
    ledger.add(PHASE_MULTI_REPO_SELECTION, prompt, cache_read, completion, total)
    logger.info(
        "PhaseProfiler RepoSelection issue=%s input=%d cache_read=%d output=%d total=%d",
        issue_key,
        prompt,
        cache_read,
        completion,
        total,
    )


def _extract_usage(response: Any) -> tuple[int, int, int, int]:
    """Extract (prompt, cache_read, completion, total) token counts from a LangChain response.

    Tries ``usage_metadata`` first, then falls back to
    ``response_metadata["token_usage"]`` / ``["usage"]``.
    """
    prompt = 0
    cache_read = 0
    completion = 0
    total = 0

    usage_md = getattr(response, "usage_metadata", None)
    if isinstance(usage_md, dict):
        prompt = int(usage_md.get("input_tokens", usage_md.get("prompt_tokens", 0)))
        completion = int(usage_md.get("output_tokens", usage_md.get("completion_tokens", 0)))
        total = int(usage_md.get("total_tokens", 0))

        input_details = usage_md.get("input_token_details") or usage_md.get("prompt_tokens_details")
        if isinstance(input_details, dict):
            cache_read = int(input_details.get("cache_read", input_details.get("cached_tokens", 0)))
        else:
            cache_read = int(usage_md.get("cache_read_input_tokens", 0))

        if not total:
            total = prompt + completion

    if not total:
        resp_meta = getattr(response, "response_metadata", None) or {}
        for key in ("token_usage", "usage"):
            u = resp_meta.get(key)
            if isinstance(u, dict):
                prompt = int(u.get("prompt_tokens", u.get("input_tokens", 0)))
                completion = int(u.get("completion_tokens", u.get("output_tokens", 0)))
                total = int(u.get("total_tokens", u.get("total", 0)))

                input_details = u.get("prompt_tokens_details") or u.get("input_token_details")
                if isinstance(input_details, dict):
                    cache_read = int(
                        input_details.get("cached_tokens", input_details.get("cache_read", 0))
                    )
                else:
                    cache_read = int(u.get("cache_read_input_tokens", 0))

                if not total:
                    total = prompt + completion
                if total:
                    break

    return prompt, cache_read, completion, total


# ---------------------------------------------------------------------------
# PhaseTokenProfilerCallback — LangChain callback handler
# ---------------------------------------------------------------------------


class PhaseTokenProfilerCallback(BaseCallbackHandler):
    """LangChain callback that categorises each LLM call into a phase.

    Instantiate once per agent run and pass it via the ``callbacks`` list in
    the ``RunnableConfig``.  The callback inspects the *tags* and *metadata*
    attached to each ``on_llm_end`` / ``on_chat_model_end`` call to determine
    which phase produced those tokens.

    Phase detection logic:

    * If the tag ``"reviewer"`` is present → ``review``
    * Otherwise, inspect the ``messages`` kwarg passed to
      ``on_chat_model_start``: if ANY message with role ``"tool"`` exists,
      the call follows a tool result → ``tool_execution``; otherwise → ``planning``

    The ``multi_repo_selection`` phase is recorded directly via
    ``record_repo_selection_tokens`` (no callback needed there) because the
    repo selector invokes the model outside the deepagents graph.
    """

    raise_error: bool = False  # required by LangChain metaclass

    def __init__(
        self,
        issue_key: str,
        thread_id: str = "",
        is_reviewer: bool = False,
    ) -> None:
        super().__init__()
        self._issue_key = issue_key
        self._thread_id = thread_id
        self._is_reviewer = is_reviewer
        # Map run_id → bool (True if tool messages were seen in context)
        self._run_has_tool_context: dict[UUID, bool] = {}

    # ------------------------------------------------------------------
    # Start — detect context composition
    # ------------------------------------------------------------------

    def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[Any]],
        *,
        run_id: UUID,
        tags: list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        """Record whether the messages include at least one tool-result message."""
        has_tool = self._detect_tool_context(messages)
        self._run_has_tool_context[run_id] = has_tool
        logger.debug(
            "PhaseProfiler on_chat_model_start run_id=%s issue=%s has_tool_context=%s is_reviewer=%s",
            run_id,
            self._issue_key,
            has_tool,
            self._is_reviewer,
        )

    def on_llm_start(
        self,
        serialized: dict[str, Any],
        prompts: list[str],
        *,
        run_id: UUID,
        tags: list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        """Fallback for non-chat models (rare)."""
        self._run_has_tool_context.setdefault(run_id, False)

    # ------------------------------------------------------------------
    # End — extract usage and record
    # ------------------------------------------------------------------

    def on_llm_end(
        self,
        response: LLMResult,
        *,
        run_id: UUID,
        tags: list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        """Extract token usage and route to the correct phase bucket."""
        phase = self._determine_phase(run_id, tags or [])
        prompt, cache_read, completion, total = self._extract_from_llm_result(response)
        self._run_has_tool_context.pop(run_id, None)

        if not total:
            logger.debug("PhaseProfiler on_llm_end run_id=%s: no usage found, skipping", run_id)
            return

        ledger = PhaseTokenLedger.get(self._issue_key)
        ledger.add(phase, prompt, cache_read, completion, total)
        logger.info(
            "PhaseProfiler on_llm_end run_id=%s issue=%s phase=%s input=%d cache_read=%d output=%d total=%d",
            run_id,
            self._issue_key,
            phase,
            prompt,
            cache_read,
            completion,
            total,
        )

    # Alias — chat models fire on_llm_end via the same hook in LangChain core
    on_chat_model_end = on_llm_end

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _determine_phase(self, run_id: UUID, tags: list[str]) -> str:
        """Map a run to a phase label."""
        if self._is_reviewer or "reviewer" in tags:
            return PHASE_REVIEW
        has_tool_ctx = self._run_has_tool_context.get(run_id, False)
        return PHASE_TOOL_EXECUTION if has_tool_ctx else PHASE_PLANNING

    @staticmethod
    def _detect_tool_context(messages: list[list[Any]]) -> bool:
        """Return True if any message has role ``"tool"`` or type ``ToolMessage``."""
        for batch in messages:
            for msg in batch:
                # LangChain message objects
                role = getattr(msg, "type", None) or getattr(msg, "role", None)
                if role in ("tool", "tool_use"):
                    return True
                # Dict-style messages (from some providers)
                if isinstance(msg, dict) and msg.get("role") == "tool":
                    return True
        return False

    @staticmethod
    def _extract_from_llm_result(response: LLMResult) -> tuple[int, int, int, int]:
        """Pull (prompt, cache_read, completion, total) out of an ``LLMResult``."""
        prompt = 0
        cache_read = 0
        completion = 0
        total = 0

        # New-style: llm_output["usage_metadata"] or ["token_usage"]
        llm_out = response.llm_output or {}
        for key in ("usage_metadata", "token_usage", "usage"):
            u = llm_out.get(key)
            if isinstance(u, dict):
                prompt = int(u.get("input_tokens", u.get("prompt_tokens", 0)))
                completion = int(u.get("output_tokens", u.get("completion_tokens", 0)))
                total = int(u.get("total_tokens", u.get("total", 0)))

                input_details = u.get("input_token_details") or u.get("prompt_tokens_details")
                if isinstance(input_details, dict):
                    cache_read = int(
                        input_details.get("cache_read", input_details.get("cached_tokens", 0))
                    )
                else:
                    cache_read = int(u.get("cache_read_input_tokens", 0))

                if not total:
                    total = prompt + completion
                if total:
                    break

        # Fall back to per-generation usage metadata
        if not total and response.generations:
            for gen_list in response.generations:
                for gen in gen_list:
                    msg = getattr(gen, "message", None)
                    if msg is None:
                        continue
                    usage_md = getattr(msg, "usage_metadata", None)
                    if isinstance(usage_md, dict):
                        p = int(usage_md.get("input_tokens", usage_md.get("prompt_tokens", 0)))
                        c = int(usage_md.get("output_tokens", usage_md.get("completion_tokens", 0)))
                        t = int(usage_md.get("total_tokens", 0)) or (p + c)

                        input_details = usage_md.get("input_token_details") or usage_md.get(
                            "prompt_tokens_details"
                        )
                        cr = 0
                        if isinstance(input_details, dict):
                            cr = int(
                                input_details.get(
                                    "cache_read", input_details.get("cached_tokens", 0)
                                )
                            )
                        else:
                            cr = int(usage_md.get("cache_read_input_tokens", 0))

                        prompt += p
                        cache_read += cr
                        completion += c
                        total += t

        return prompt, cache_read, completion, total
