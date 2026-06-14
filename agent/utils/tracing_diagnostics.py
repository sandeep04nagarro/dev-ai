from __future__ import annotations

import logging

from opentelemetry.sdk.trace import ReadableSpan, SpanProcessor

logger = logging.getLogger(__name__)


class SessionIdDiagnosticProcessor(SpanProcessor):
    def on_start(self, span, parent_context=None):
        pass

    def on_end(self, span: ReadableSpan) -> None:
        attrs = span.attributes
        session_id = attrs.get("session.id", "<MISSING>")
        user_id = attrs.get("user.id", "<MISSING>")
        parent_id = span.parent.span_id if span.parent else None
        logger.info(
            "OTEL_SPAN name=%s kind=%s session.id=%s user.id=%s parent=%s",
            span.name,
            span.kind,
            session_id,
            user_id,
            parent_id,
        )

    def shutdown(self) -> None:
        pass

    def force_flush(self, timeout_millis: int | None = None) -> bool:
        return True
