from __future__ import annotations

import logging
from typing import Any

from opentelemetry.sdk.trace import ReadableSpan, SpanProcessor

logger = logging.getLogger(__name__)


class _AttrsStore:
    _store: dict[str, Any] = {}

    @classmethod
    def set(cls, thread_id: str = "", **attrs: Any) -> None:
        cls._store = {"thread_id": str(thread_id[:200] if thread_id else ""), "attrs": attrs.copy()}

    @classmethod
    def get(cls) -> dict[str, Any]:
        return cls._store.get("attrs", {}).copy()


class LangfuseAttributesProcessor(SpanProcessor):
    LANGFUSE_ATTRS = {
        "session.id": "session_id",
        "user.id": "user_id",
        "trace.name": "trace_name",
    }

    def on_start(self, span, parent_context=None) -> None:
        attrs = _AttrsStore.get()
        if not attrs:
            return
        for attr_key, store_key in self.LANGFUSE_ATTRS.items():
            if attr_key not in span.attributes and store_key in attrs:
                span.set_attribute(attr_key, str(attrs[store_key]))

    def on_end(self, span: ReadableSpan) -> None:
        pass

    def shutdown(self) -> None:
        pass

    def force_flush(self, timeout_millis: int | None = None) -> bool:
        return True
