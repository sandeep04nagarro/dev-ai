from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler
from langfuse import propagate_attributes

logger = logging.getLogger(__name__)


class MetadataLoggerHandler(BaseCallbackHandler):
    """Logs metadata and enters `propagate_attributes` for OTEL context propagation.

    Sits alongside the Langfuse handler in the callbacks list. LangGraph/Pregel
    distributes graph nodes across a pool of asyncio tasks, so contextvars set
    in one task are invisible to others. This handler enters
    `propagate_attributes` on *every* callback event so that whichever task
    fires the callback gets the context set *before* the langfuse handler
    reads it (since this handler is added to the callbacks list first).
    """

    _pa_entered: bool = False
    _pa_attrs: dict[str, str] = {}

    def _ensure_pa(self, md: dict[str, Any], run_id: UUID) -> None:
        if not self._pa_entered:
            self._pa_entered = True
            session_id = md.get("langfuse_session_id") or str(run_id)
            user_id = md.get("langfuse_user_id") or "unknown"
            trace_name = md.get("langfuse_trace_name")
            self._pa_attrs = {
                "session_id": str(session_id)[:200],
                "user_id": str(user_id)[:200],
            }
            if trace_name:
                self._pa_attrs["trace_name"] = str(trace_name)[:200]
        self._pa_cm = propagate_attributes(**self._pa_attrs)
        self._pa_cm.__enter__()

    def on_chain_start(
        self,
        serialized: dict[str, Any] | None,
        inputs: Any,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        md = metadata or {}
        self._ensure_pa(md, run_id)
        logger.info(
            "CB on_chain_start run_id=%s parent_run_id=%s "
            "langfuse_session_id=%s langfuse_user_id=%s "
            "langfuse_trace_name=%s",
            run_id,
            parent_run_id,
            md.get("langfuse_session_id"),
            md.get("langfuse_user_id"),
            md.get("langfuse_trace_name"),
        )

    def on_chat_model_start(
        self,
        serialized: dict[str, Any] | None,
        messages: list[list[Any]],
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        md = metadata or {}
        self._ensure_pa(md, run_id)
        logger.info(
            "CB on_chat_model_start run_id=%s parent_run_id=%s "
            "langfuse_session_id=%s langfuse_user_id=%s",
            run_id,
            parent_run_id,
            md.get("langfuse_session_id"),
            md.get("langfuse_user_id"),
        )

    def on_llm_start(
        self,
        serialized: dict[str, Any] | None,
        prompts: list[str],
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        md = metadata or {}
        self._ensure_pa(md, run_id)
        logger.info(
            "CB on_llm_start run_id=%s parent_run_id=%s "
            "langfuse_session_id=%s langfuse_user_id=%s",
            run_id,
            parent_run_id,
            md.get("langfuse_session_id"),
            md.get("langfuse_user_id"),
        )

    def on_tool_start(
        self,
        serialized: dict[str, Any] | None,
        input_str: str,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        md = metadata or {}
        self._ensure_pa(md, run_id)
        logger.info(
            "CB on_tool_start run_id=%s parent_run_id=%s "
            "langfuse_session_id=%s langfuse_user_id=%s",
            run_id,
            parent_run_id,
            md.get("langfuse_session_id"),
            md.get("langfuse_user_id"),
        )
