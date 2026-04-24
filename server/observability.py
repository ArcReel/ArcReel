"""
Observability — OpenTelemetry tracing + Prometheus metrics cho ArcReel.

Kích hoạt bằng biến môi trường:
  OTLP_ENDPOINT=http://jaeger:4317     # gRPC OTLP endpoint (ví dụ: Jaeger, Tempo)

Để tắt hoàn toàn: không set OTLP_ENDPOINT (zero overhead).

Metrics Prometheus được expose tại GET /metrics (không cần auth).
"""

from __future__ import annotations

import logging
import os
import time
from typing import TYPE_CHECKING

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prometheus Metrics — always available, even without OTLP
# ---------------------------------------------------------------------------
try:
    from prometheus_client import (
        CONTENT_TYPE_LATEST,
        Counter,
        Gauge,
        Histogram,
        generate_latest,
    )
    _PROMETHEUS_AVAILABLE = True
except ImportError:
    _PROMETHEUS_AVAILABLE = False
    logger.debug("prometheus_client not installed — /metrics endpoint disabled")

if _PROMETHEUS_AVAILABLE:
    # Request metrics (supplement to FastAPI auto-instrumentation)
    HTTP_REQUEST_DURATION = Histogram(
        "arcreel_http_request_duration_seconds",
        "HTTP request duration",
        ["method", "endpoint", "status_code"],
        buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
    )

    # AI / Agent metrics
    TOOL_APPROVAL_DECISIONS = Counter(
        "arcreel_tool_approval_decisions_total",
        "Tool approval decisions made by user",
        ["tool_name", "decision"],  # decision: allow | deny
    )
    SESSION_CREATED = Counter(
        "arcreel_sessions_created_total",
        "Assistant sessions created",
    )
    SESSION_ACTIVE = Gauge(
        "arcreel_sessions_active",
        "Currently active (in-memory) assistant sessions",
    )

    # Generation pipeline metrics
    GENERATION_TASK_DURATION = Histogram(
        "arcreel_generation_task_duration_seconds",
        "Generation task duration",
        ["provider", "task_type"],  # task_type: image | video
        buckets=[1, 5, 15, 30, 60, 120, 300],
    )
    GENERATION_TASK_TOTAL = Counter(
        "arcreel_generation_tasks_total",
        "Total generation tasks",
        ["provider", "task_type", "status"],  # status: success | error
    )

    # Token usage
    TOKEN_USAGE = Counter(
        "arcreel_token_usage_total",
        "Total tokens consumed",
        ["model", "usage_type"],  # usage_type: input | output
    )

    # Context compression
    CONTEXT_COMPRESSIONS = Counter(
        "arcreel_context_compressions_total",
        "Number of times session history was compressed",
    )

# ---------------------------------------------------------------------------
# OpenTelemetry Tracing — optional
# ---------------------------------------------------------------------------
_OTEL_AVAILABLE = False

def setup_telemetry(app) -> None:  # type: ignore[type-arg]
    """
    Khởi tạo OpenTelemetry instrumentation.
    Gọi từ FastAPI lifespan *trước* khi yield.
    Nếu OTLP_ENDPOINT không được cấu hình, hàm này là no-op.
    """
    global _OTEL_AVAILABLE

    otlp_endpoint = os.environ.get("OTLP_ENDPOINT", "").strip()

    if not otlp_endpoint:
        logger.debug("OTLP_ENDPOINT not set — OpenTelemetry tracing disabled")
        return

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError as exc:
        logger.warning(
            "OpenTelemetry packages not installed — tracing disabled. "
            "Install with: uv add opentelemetry-sdk opentelemetry-exporter-otlp-proto-grpc "
            "opentelemetry-instrumentation-fastapi opentelemetry-instrumentation-sqlalchemy "
            "(%s)",
            exc,
        )
        return

    service_name = os.environ.get("OTEL_SERVICE_NAME", "arcreel")
    resource = Resource.create({"service.name": service_name})
    provider = TracerProvider(resource=resource)

    exporter = OTLPSpanExporter(endpoint=otlp_endpoint, insecure=True)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    FastAPIInstrumentor.instrument_app(app)
    SQLAlchemyInstrumentor().instrument()

    _OTEL_AVAILABLE = True
    logger.info(
        "OpenTelemetry tracing enabled → %s (service=%s)",
        otlp_endpoint,
        service_name,
    )


def get_tracer(name: str = "arcreel"):
    """Trả về tracer. No-op noop tracer nếu OTEL không được cấu hình."""
    if not _OTEL_AVAILABLE:
        # Return a noop-compatible object so callers don't need to check
        class _NoopTracer:
            def start_as_current_span(self, *a, **kw):
                from contextlib import contextmanager
                @contextmanager
                def _ctx():
                    yield None
                return _ctx()
        return _NoopTracer()

    from opentelemetry import trace
    return trace.get_tracer(name)


# ---------------------------------------------------------------------------
# Metric helper functions — safe even if prometheus_client not installed
# ---------------------------------------------------------------------------

def record_tool_approval(tool_name: str, decision: str) -> None:
    """Ghi nhận quyết định phê duyệt tool."""
    if _PROMETHEUS_AVAILABLE:
        TOOL_APPROVAL_DECISIONS.labels(tool_name=tool_name, decision=decision).inc()


def record_session_created() -> None:
    if _PROMETHEUS_AVAILABLE:
        SESSION_CREATED.inc()


def set_active_sessions(count: int) -> None:
    if _PROMETHEUS_AVAILABLE:
        SESSION_ACTIVE.set(count)


def record_generation_task(provider: str, task_type: str, status: str, duration_s: float) -> None:
    if _PROMETHEUS_AVAILABLE:
        GENERATION_TASK_DURATION.labels(provider=provider, task_type=task_type).observe(duration_s)
        GENERATION_TASK_TOTAL.labels(provider=provider, task_type=task_type, status=status).inc()


def record_token_usage(model: str, input_tokens: int, output_tokens: int) -> None:
    if _PROMETHEUS_AVAILABLE:
        TOKEN_USAGE.labels(model=model, usage_type="input").inc(input_tokens)
        TOKEN_USAGE.labels(model=model, usage_type="output").inc(output_tokens)


def record_context_compression() -> None:
    if _PROMETHEUS_AVAILABLE:
        CONTEXT_COMPRESSIONS.inc()


# ---------------------------------------------------------------------------
# Prometheus metrics endpoint handler
# ---------------------------------------------------------------------------

async def metrics_handler(request=None):  # type: ignore[assignment]
    """FastAPI route handler để expose /metrics cho Prometheus scrape."""
    from starlette.responses import Response

    if not _PROMETHEUS_AVAILABLE:
        return Response(
            "# prometheus_client not installed\n",
            media_type="text/plain",
            status_code=503,
        )
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
