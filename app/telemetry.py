import os
from opentelemetry import trace, metrics
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter


def setup(service="adk-agent-demo"):
    """Set global OTel providers BEFORE importing ADK so ADK's built-in spans
    (invoke_agent, generate_content, execute_tool) flow through the same exporter.
    Endpoint/headers come from OTEL_EXPORTER_OTLP_* env vars.
    Dynatrace: OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE=delta"""
    res = Resource.create({"service.name": service,
                           "deployment.environment": os.getenv("ENV", "demo"),
                           "k8s.pod.name": os.getenv("HOSTNAME", "local")})
    tp = TracerProvider(resource=res)
    tp.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(tp)
    mr = PeriodicExportingMetricReader(OTLPMetricExporter(), export_interval_millis=15000)
    metrics.set_meter_provider(MeterProvider(resource=res, metric_readers=[mr]))
