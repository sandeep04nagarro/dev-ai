from __future__ import annotations

import gzip
import logging
import os
from collections import Counter
from collections.abc import Sequence
from typing import Any

from opentelemetry.exporter.otlp.proto.http.trace_exporter import encode_spans
from opentelemetry.sdk.trace import ReadableSpan, SpanProcessor
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult

logger = logging.getLogger(__name__)

DIAGNOSTICS_ENABLED = "OTEL_DIAGNOSTICS_ENABLED"
_OBSERVATION_TYPE = "langfuse.observation.type"
_OBSERVATION_MODEL = "langfuse.observation.model.name"

_LARGE_SPAN_THRESHOLD_BYTES = 100 * 1024


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


def _estimate_span_size(span: ReadableSpan) -> int:
    total = len(span.name or "")
    total += 32  # trace_id + span_id overhead
    attrs = span.attributes or {}
    for key, value in attrs.items():
        total += len(key)
        if isinstance(value, str):
            total += len(value)
        elif isinstance(value, bytes):
            total += len(value)
        elif isinstance(value, (int, float)):
            total += 16
        elif isinstance(value, bool):
            total += 4
        else:
            total += len(str(value))
    return total


def _classify_span(span: ReadableSpan) -> dict[str, str]:
    attrs = span.attributes or {}
    obs_type = attrs.get(_OBSERVATION_TYPE, "unknown")
    name = span.name or ""
    if obs_type in ("generation", "embedding"):
        model = attrs.get(_OBSERVATION_MODEL, "")
        label = f"model({model})" if model else "model"
    elif obs_type == "tool":
        label = f"tool({name})"
    elif obs_type == "chain":
        label = f"chain({name})"
    else:
        label = f"{obs_type}({name})"
    return {
        "type": obs_type,
        "label": label,
        "name": name,
        "model": attrs.get(_OBSERVATION_MODEL, ""),
    }


def _format_size(bytes_: int) -> str:
    mb = bytes_ / (1024 * 1024)
    if mb >= 1:
        return f"{mb:.1f}MB"
    kb = bytes_ / 1024
    if kb >= 1:
        return f"{kb:.1f}KB"
    return f"{bytes_}B"


class SizeDiagnosticsExporter(SpanExporter):
    def __init__(self, inner: SpanExporter):
        self._inner = inner

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        if not spans:
            return self._inner.export(spans)

        span_count = len(spans)
        type_counter: Counter[str] = Counter()
        type_size: dict[str, int] = {}
        largest_spans: list[tuple[int, ReadableSpan, dict[str, str]]] = []

        for span in spans:
            info = _classify_span(span)
            type_counter[info["type"]] += 1
            size = _estimate_span_size(span)
            type_size[info["type"]] = type_size.get(info["type"], 0) + size
            if size >= _LARGE_SPAN_THRESHOLD_BYTES:
                largest_spans.append((size, span, info))

        largest_spans.sort(key=lambda x: -x[0])

        try:
            serialized = encode_spans(spans).SerializePartialToString()
            raw_size = len(serialized)
            gzipped = gzip.compress(serialized)
            compressed_size = len(gzipped)
        except Exception:
            raw_size = sum(type_size.values())
            compressed_size = 0

        exceeds = raw_size > 20 * 1024 * 1024

        level = "WARNING" if exceeds else "INFO"
        logger.log(
            getattr(logging, level),
            "[SizeDiagnostics] Batch exported: spans=%d raw=%s gzip=%s exceeds_20MB=%s",
            span_count,
            _format_size(raw_size),
            _format_size(compressed_size) if compressed_size else "N/A",
            exceeds,
        )

        if type_counter:
            breakdown = " ".join(f"{t}={c}" for t, c in sorted(type_counter.items()))
            logger.log(
                getattr(logging, level),
                "[SizeDiagnostics]   by_type: %s",
                breakdown,
            )

        for rank, (size, span, info) in enumerate(largest_spans[:5], 1):
            attrs = sorted(
                span.attributes.items(),
                key=lambda kv: len(str(kv[1])) if isinstance(kv[1], str) else 0,
                reverse=True,
            )
            top_attrs = " ".join(
                f"{k}={_format_size(len(str(v)))}" for k, v in attrs[:5] if isinstance(v, str)
            )
            logger.log(
                getattr(logging, level),
                "[SizeDiagnostics]   large_span #%d: size=%s span=%s top_attrs: %s",
                rank,
                _format_size(size),
                info["label"],
                top_attrs,
            )

        if exceeds and compressed_size:
            ratio = raw_size / compressed_size if compressed_size else 0
            logger.log(
                logging.WARNING,
                "[SizeDiagnostics]   exceeds 20MB — gzip would reduce to %s (%.1fx compression)",
                _format_size(compressed_size),
                ratio,
            )

        return self._inner.export(spans)

    def shutdown(self) -> None:
        self._inner.shutdown()

    def force_flush(self, timeout_millis: int | None = None) -> bool:
        return self._inner.force_flush(timeout_millis)


def is_diagnostics_enabled() -> bool:
    return os.environ.get(DIAGNOSTICS_ENABLED, "").lower() in ("true", "1")


def wrap_exporter_if_enabled(exporter: SpanExporter) -> SpanExporter:
    if is_diagnostics_enabled():
        logger.info("Wrapping OTLP exporter with SizeDiagnosticsExporter")
        return SizeDiagnosticsExporter(exporter)
    return exporter
