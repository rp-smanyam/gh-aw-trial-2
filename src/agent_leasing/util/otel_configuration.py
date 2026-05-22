import logging
from typing import Sequence

import grpc
import structlog
from fastapi import FastAPI
from google.protobuf.json_format import MessageToJson
from opentelemetry import _events, _logs, metrics, trace
from opentelemetry.exporter.otlp.proto.common._internal.trace_encoder import encode_spans
from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
    OTLPSpanExporter as OTLPGrpcSpanExporter,
)
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.openai_agents import OpenAIAgentsInstrumentor
from opentelemetry.instrumentation.openai_v2 import OpenAIInstrumentor
from opentelemetry.instrumentation.system_metrics import SystemMetricsInstrumentor
from opentelemetry.sdk._events import EventLoggerProvider
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExportResult

from agent_leasing.settings import settings

logger = logging.getLogger(__name__)

# Idempotency flags to prevent duplicate setup
_otel_logging_configured = False
_otel_tracing_configured = False
_otel_metrics_configured = False

# Context fields injected into every OTel log record for central log correlation
# These are read from structlog's context variables
LIFECYCLE_CONTEXT_FIELDS = [
    "request_id",
    "openai_trace_id",
    "chat_session_id",
    "property_id",
    "prospect_id",
    "call_sid",
    "channel",
]


class LifecycleContextLoggingHandler(logging.Handler):
    """
    Wraps OTel LoggingHandler and injects agent-leasing lifecycle context
    fields from structlog's context variables into every log record before emitting.
    This integrates with the existing structlog.contextvars system used throughout
    the application (e.g., in middleware, agent_service, twilio_handler).
    """

    def __init__(self, otel_handler: LoggingHandler):
        super().__init__()
        self.otel_handler = otel_handler

    def emit(self, record: logging.LogRecord) -> None:
        try:
            # Read from structlog's context variables instead of maintaining separate context
            context = structlog.contextvars.get_contextvars()
            for field in LIFECYCLE_CONTEXT_FIELDS:
                if not hasattr(record, field) and field in context:
                    setattr(record, field, context[field])
            self.otel_handler.emit(record)
        except Exception:
            self.handleError(record)

    def setLevel(self, level) -> None:
        super().setLevel(level)
        self.otel_handler.setLevel(level)


class OTLPJsonSpanExporter(OTLPSpanExporter):
    """OTLPSpanExporter that sends JSON instead of protobuf."""

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        if self._shutdown:
            return SpanExportResult.FAILURE

        proto_message = encode_spans(spans)
        json_str = MessageToJson(proto_message)
        serialized_data = json_str.encode("utf-8")

        self._session.headers.update({"Content-Type": "application/json"})
        return self._export(serialized_data)


def _to_grpc_endpoint(endpoint: str) -> str:
    """
    Convert HTTPS URL to gRPC host:port format for Elastic APM.
    Elastic APM requires gRPC with TLS, so this always expects HTTPS URLs.

    Args:
        endpoint: URL like "https://host.com:443"

    Returns:
        host:port string (e.g., "host.com:443")
    """
    if not endpoint:
        return endpoint

    # Strip scheme (support both http:// and https:// but Elastic requires HTTPS)
    host_port = endpoint.replace("https://", "").replace("http://", "").rstrip("/")

    # Add default port 443 if not specified (Elastic APM default)
    if ":" not in host_port:
        host_port = f"{host_port}:443"

    return host_port


def setup_opentelemetry(app: FastAPI):
    """
    Set up OpenTelemetry for tracing, logging, and metrics.
    """
    if settings.otel_enabled:
        logging.getLogger("opentelemetry.attributes").setLevel(logging.ERROR)
        # Silence chatty third-party WARN/ERROR logs that aren't application errors
        # (KNCK-38040 follow-up). Each of these libraries logs at WARN/ERROR for
        # benign conditions, polluting the dashboard's error views.
        # - asgi_correlation_id: WARN every request without a valid X-Request-ID header
        # - agents.tracing.processors: BackendSpanExporter "[non-fatal] Tracing client error 429"
        # - opentelemetry.exporter: OTLP "Failed to export logs/metrics/traces" timeouts
        logging.getLogger("asgi_correlation_id").setLevel(logging.ERROR)
        logging.getLogger("agents.tracing.processors").setLevel(logging.ERROR)
        logging.getLogger("opentelemetry.exporter").setLevel(logging.ERROR)
        _setup_logging()
        _setup_tracing()
        _setup_metrics()

        # Instrument system metrics for Python runtime monitoring
        SystemMetricsInstrumentor().instrument()

        # Instrument FastAPI (exclude noisy receive/send spans)
        FastAPIInstrumentor.instrument_app(app, exclude_spans=["receive", "send"])

        # Instrument HTTP clients
        HTTPXClientInstrumentor().instrument()

        # Instrument OpenAI SDK
        OpenAIInstrumentor().instrument()

        # Instrument OpenAI Agents
        OpenAIAgentsInstrumentor().instrument()


def _setup_logging():
    """
    Set up the OpenTelemetry LoggerProvider and LogRecordProcessor.
    Sends logs to the Elastic endpoint with Bearer auth.
    """
    global _otel_logging_configured
    if _otel_logging_configured:
        return

    resource = Resource.create(
        {
            SERVICE_NAME: settings.app_name,
            "deployment.environment": settings.environment,
        }
    )

    logger_provider = LoggerProvider(resource=resource)

    elastic_endpoint = settings.elastic_endpoint
    elastic_token = settings.elastic_token

    if not elastic_endpoint:
        logger.error("Elastic logging disabled: ELASTIC_ENDPOINT missing")
    elif not elastic_token:
        logger.error("Elastic logging disabled: ELASTIC_TOKEN missing")
    else:
        try:
            log_exporter = OTLPLogExporter(
                endpoint=_to_grpc_endpoint(elastic_endpoint),
                credentials=grpc.ssl_channel_credentials(),
                headers=(("authorization", f"Bearer {elastic_token}"),),
                timeout=settings.otel_exporter_timeout_seconds,
            )
            # export_timeout_millis on BatchLogRecordProcessor is a no-op as of
            # opentelemetry-sdk 1.39.1 (upstream issue #4555); the deadline is
            # enforced by the exporter's `timeout` kwarg above.
            logger_provider.add_log_record_processor(BatchLogRecordProcessor(log_exporter))
            logger.info("Elastic log export configured")
        except Exception:
            logger.error("Elastic logging failed", exc_info=True)

    _logs.set_logger_provider(logger_provider)
    _events.set_event_logger_provider(EventLoggerProvider(logger_provider=logger_provider))

    otel_handler = LoggingHandler(logger_provider=logger_provider)
    logging.getLogger().addHandler(LifecycleContextLoggingHandler(otel_handler))

    _otel_logging_configured = True


def _setup_tracing():
    """
    Set up the OpenTelemetry TracerProvider and SpanProcessor.
    Exports to both Elastic (central tracing) and Agentic Backend when configured.
    """
    global _otel_tracing_configured
    if _otel_tracing_configured:
        return

    resource = Resource.create(
        {
            SERVICE_NAME: settings.app_name,
            "deployment.environment": settings.environment,
        }
    )
    trace_provider = TracerProvider(resource=resource)
    exporters_configured = 0

    # Central tracing (Elastic) direct export
    elastic_endpoint = settings.elastic_endpoint
    elastic_token = settings.elastic_token

    if elastic_endpoint:
        if not elastic_token:
            logger.error("Elastic tracing disabled: ELASTIC_TOKEN missing")
        else:
            try:
                elastic_exporter = OTLPGrpcSpanExporter(
                    endpoint=_to_grpc_endpoint(elastic_endpoint),
                    credentials=grpc.ssl_channel_credentials(),
                    headers=(("authorization", f"Bearer {elastic_token}"),),
                    timeout=settings.otel_exporter_timeout_seconds,
                )
                # export_timeout_millis on BatchSpanProcessor is a no-op as of
                # opentelemetry-sdk 1.39.1 (upstream issue #4555).
                elastic_processor = BatchSpanProcessor(elastic_exporter)
                trace_provider.add_span_processor(elastic_processor)
                exporters_configured += 1
            except Exception:
                logger.error("Elastic tracing failed", exc_info=True)

    # Agentic Backend direct export (when configured)
    agentic_endpoint = settings.agentic_evals_endpoint
    agentic_token = settings.agentic_evals_token

    if agentic_endpoint:
        if not agentic_token:
            logger.error("Agentic tracing disabled: AGENTIC_EVALS_TOKEN missing")
        else:
            try:
                agentic_exporter = OTLPJsonSpanExporter(
                    endpoint=agentic_endpoint,
                    headers={"X-API-Key": agentic_token},
                    timeout=settings.otel_exporter_timeout_seconds,
                )
                agentic_processor = BatchSpanProcessor(agentic_exporter)
                trace_provider.add_span_processor(agentic_processor)
                exporters_configured += 1
            except Exception:
                logger.error("Agentic tracing failed", exc_info=True)

    # Validate at least one exporter is configured
    if exporters_configured == 0:
        logger.error("No trace exporters configured - observability disabled")
    else:
        logger.info(f"Tracing active: {exporters_configured} exporter(s)")

    trace.set_tracer_provider(trace_provider)
    _otel_tracing_configured = True


def _setup_metrics():
    """
    Set up the OpenTelemetry MeterProvider and MetricReader.
    """
    global _otel_metrics_configured
    if _otel_metrics_configured:
        return

    elastic_endpoint = settings.elastic_endpoint
    elastic_token = settings.elastic_token

    if not elastic_endpoint or not elastic_token:
        logger.error("Elastic metrics disabled: ELASTIC_ENDPOINT or ELASTIC_TOKEN missing")
        return

    try:
        resource = Resource.create(
            {
                SERVICE_NAME: settings.app_name,
                "deployment.environment": settings.environment,
            }
        )

        metric_reader = PeriodicExportingMetricReader(
            OTLPMetricExporter(
                endpoint=_to_grpc_endpoint(elastic_endpoint),
                credentials=grpc.ssl_channel_credentials(),
                headers=(("authorization", f"Bearer {elastic_token}"),),
                timeout=settings.otel_exporter_timeout_seconds,
            ),
            export_timeout_millis=settings.otel_exporter_timeout_seconds * 1000,
        )
        meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
        metrics.set_meter_provider(meter_provider)
        _otel_metrics_configured = True
    except Exception:
        logger.error("Elastic metrics failed", exc_info=True)


def flush_traces(timeout_ms: int = 30000):
    """Force-flush all pending spans to the OTLP exporter."""
    try:
        provider = trace.get_tracer_provider()
        if hasattr(provider, "force_flush"):
            provider.force_flush(timeout_ms)
    except Exception:
        logger.warning("Failed to flush traces", exc_info=True)


def get_meter():
    """Get the OpenTelemetry meter instance."""
    return metrics.get_meter("agent-leasing")
