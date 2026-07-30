"""Langfuse tracing integration.

Provides a singleton Langfuse CallbackHandler that is injected into the
RunnableConfig callbacks of each graph factory, enabling Langfuse to capture
all LangChain/LangGraph events (model calls, tool calls, chain steps, etc.)
"""

from __future__ import annotations

import base64
import logging

# import os
from agent.utils.secrets import SecretsManager

logger = logging.getLogger(__name__)

_langfuse_handler: object | None = None


def _build_otlp_exporter(*, compression: str | None = None) -> object | None:
    """Build an OTLP span exporter configured from Langfuse env vars.

    Returns ``None`` when Langfuse credentials are missing.

    This mirrors the exporter construction the Langfuse SDK performs internally.
    Passing ``compression="gzip"`` enables gzip compression on the payload body
    (gzip typically reduces protobuf payloads by 10–20×).
    """
    # secret_key = os.environ.get("LANGFUSE_SECRET_KEY")
    # public_key = os.environ.get("LANGFUSE_PUBLIC_KEY")
    secret_key = SecretsManager.get("LANGFUSE_SECRET_KEY")
    public_key = SecretsManager.get("LANGFUSE_PUBLIC_KEY")
    if not secret_key or not public_key:
        return None

    # base_url = os.environ.get("LANGFUSE_BASE_URL", "https://cloud.langfuse.com").rstrip("/")
    # timeout = float(os.environ.get("LANGFUSE_TIMEOUT", "5"))
    # traces_export_path = os.environ.get("LANGFUSE_OTEL_TRACES_EXPORT_PATH")
    base_url = (SecretsManager.get("LANGFUSE_BASE_URL") or "https://cloud.langfuse.com").rstrip("/")
    timeout = float(SecretsManager.get("LANGFUSE_TIMEOUT") or "5")
    traces_export_path = SecretsManager.get("LANGFUSE_OTEL_TRACES_EXPORT_PATH")

    endpoint = (
        f"{base_url}/{traces_export_path}"
        if traces_export_path
        else f"{base_url}/api/public/otel/v1/traces"
    )

    basic_auth = "Basic " + base64.b64encode(f"{public_key}:{secret_key}".encode()).decode("ascii")

    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    except ImportError:
        logger.warning("OTLP exporter not available, skipping custom exporter")
        return None

    kwargs: dict[str, object] = {
        "endpoint": endpoint,
        "headers": {
            "Authorization": basic_auth,
            "x-langfuse-sdk-name": "python",
            "x-langfuse-public-key": public_key,
        },
        "timeout": timeout,
    }
    if compression:
        from opentelemetry.exporter.otlp.proto.http import Compression as OtlpCompression

        if compression == "gzip":
            kwargs["compression"] = OtlpCompression.Gzip
        elif compression == "deflate":
            kwargs["compression"] = OtlpCompression.Deflate
        else:
            kwargs["compression"] = compression

    return OTLPSpanExporter(**kwargs)


def _wrap_internal_exporter_with_diagnostics() -> None:
    """Replace the Langfuse SDK's internal OTLP exporter with ``SizeDiagnosticsExporter``.

    Only effective when the global tracer provider has already been populated
    by the Langfuse SDK (i.e. after at least one ``Langfuse`` instantiation).
    Safe no-op when no ``BatchSpanProcessor`` is found.
    """
    from opentelemetry import trace as otel_trace
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    from .tracing_diagnostics import SizeDiagnosticsExporter, is_diagnostics_enabled

    if not is_diagnostics_enabled():
        return

    provider = otel_trace.get_tracer_provider()
    active = provider._active_span_processor  # SynchronousMultiSpanProcessor
    replaced = False
    for sp in list(active._span_processors):
        if not isinstance(sp, BatchSpanProcessor):
            continue
        if not hasattr(sp, "_batch_processor"):
            continue
        bp = sp._batch_processor
        old = bp._exporter
        if isinstance(old, SizeDiagnosticsExporter):
            continue
        logger.info("Wrapping OTLP exporter with SizeDiagnosticsExporter")
        bp._exporter = SizeDiagnosticsExporter(old)
        replaced = True
    if not replaced:
        logger.debug("No BatchSpanProcessor found to wrap for diagnostics")


def get_langfuse_handler() -> object | None:
    global _langfuse_handler
    if _langfuse_handler is not None:
        return _langfuse_handler

    # if not bool(os.environ.get("LANGFUSE_SECRET_KEY") and os.environ.get("LANGFUSE_PUBLIC_KEY")):
    if not bool(SecretsManager.get("LANGFUSE_SECRET_KEY") and SecretsManager.get("LANGFUSE_PUBLIC_KEY")):
        return None

    try:
        from langfuse.langchain import CallbackHandler

        _langfuse_handler = CallbackHandler()
        logger.info("Langfuse tracing enabled (CallbackHandler)")

        from opentelemetry import trace as otel_trace

        provider = otel_trace.get_tracer_provider()
        if hasattr(provider, "add_span_processor"):
            from .tracing_diagnostics import LangfuseAttributesProcessor

            provider.add_span_processor(LangfuseAttributesProcessor())
            logger.info("LangfuseAttributesProcessor added to tracer provider")

        _wrap_internal_exporter_with_diagnostics()
    except Exception as exc:
        logger.warning("Failed to initialize Langfuse handler: %s", exc)
        _langfuse_handler = None

    return _langfuse_handler
