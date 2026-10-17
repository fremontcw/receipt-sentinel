"""telemetry

OpenTelemetry tracing for eval runs: one span per run, one per golden case, and one per LLM
call using the GenAI semantic conventions. Off unless an exporter is configured by env var.

Status:
    None
Issues:
    None
Todo:
    None
"""

import os
from collections.abc import Callable
from dataclasses import dataclass

from opentelemetry import trace

from receipt_sentinel.categorize.llm_client import LlmClient, LlmRequest, LlmResponse

# ponytail: the API alone is a no-op tracer, so spans cost nothing until configure_tracing()
# installs the SDK. Proxy tracer: picks up a provider installed after import.
tracer = trace.get_tracer("receipt_sentinel")

CONSOLE_ENV_VAR = "SENTINEL_TRACE_CONSOLE"
OTLP_ENDPOINT_ENV_VAR = "OTEL_EXPORTER_OTLP_ENDPOINT"


@dataclass
class TracingLlmClient:
    """Wraps any LlmClient with a GenAI `chat` span per call.

    Records model, token usage and stop reason only. Prompt text, receipt text and model
    output are never put on spans: receipts carry PII, and trace backends are rarely
    access-controlled like the run store is.
    """

    inner: LlmClient
    provider_name: str

    async def complete(self, request: LlmRequest) -> LlmResponse:
        with tracer.start_as_current_span(f"chat {request.model_id}") as span:
            span.set_attribute("gen_ai.operation.name", "chat")
            span.set_attribute("gen_ai.provider.name", self.provider_name)
            span.set_attribute("gen_ai.request.model", request.model_id)
            span.set_attribute("gen_ai.request.max_tokens", request.max_tokens)
            try:
                response = await self.inner.complete(request)
            except Exception as err:
                span.set_attribute("error.type", type(err).__name__)
                raise
            span.set_attribute("gen_ai.response.model", response.model_id)
            span.set_attribute("gen_ai.usage.input_tokens", response.input_tokens)
            span.set_attribute("gen_ai.usage.output_tokens", response.output_tokens)
            if response.stop_reason:
                span.set_attribute("gen_ai.response.finish_reasons", [response.stop_reason])
            return response


def configure_tracing() -> Callable[[], None]:
    """Install a tracer provider when an exporter is requested by environment variable.

    `OTEL_EXPORTER_OTLP_ENDPOINT` sends spans over OTLP/HTTP (Jaeger, Grafana, Azure Monitor
    via a collector); `SENTINEL_TRACE_CONSOLE=1` prints them. Neither set: tracing stays off.

    Returns:
        A shutdown function that flushes pending spans; call it before the process exits.
    """
    use_otlp = bool(os.environ.get(OTLP_ENDPOINT_ENV_VAR))
    use_console = os.environ.get(CONSOLE_ENV_VAR) == "1"
    if not (use_otlp or use_console):
        return lambda: None
    try:
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
    except ImportError:
        print("tracing requested but the SDK is missing: uv sync --extra telemetry")
        return lambda: None
    provider = TracerProvider(resource=Resource.create({"service.name": "receipt-sentinel"}))
    if use_otlp:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        # Endpoint and auth headers come from the standard OTEL_EXPORTER_OTLP_* variables.
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    if use_console:
        provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
    trace.set_tracer_provider(provider)
    return provider.shutdown
