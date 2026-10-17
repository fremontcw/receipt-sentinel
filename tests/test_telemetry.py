import json

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from receipt_sentinel.categorize.llm_client import FakeLlmClient, LlmRequest
from receipt_sentinel.evals.golden_case import load_golden_cases
from receipt_sentinel.evals.runner import run_evals
from receipt_sentinel.evals.scoring import ModelPricing
from receipt_sentinel.evals.thresholds import Thresholds
from receipt_sentinel.prompts.prompt_config import PROMPTS_DIR, load_prompt_config
from receipt_sentinel.telemetry import TracingLlmClient

PROMPT = load_prompt_config(PROMPTS_DIR / "receipt_categorizer_v1.yaml")
CASES = load_golden_cases()[:3]

# The global provider can be set once per process; this is the only test module that does.
EXPORTER = InMemorySpanExporter()
_provider = TracerProvider()
_provider.add_span_processor(SimpleSpanProcessor(EXPORTER))
trace.set_tracer_provider(_provider)


def oracle(request: LlmRequest) -> str:
    for case in CASES:
        if case.receipt_text.strip() in request.user:
            return json.dumps(case.expected.model_dump(mode="json"))
    raise AssertionError("unknown case")


async def test_run_case_and_llm_spans_nest_and_carry_no_receipt_text() -> None:
    EXPORTER.clear()
    client = TracingLlmClient(FakeLlmClient(oracle), "fake")
    await run_evals(CASES, PROMPT, client, model_id="fake", pricing=ModelPricing(0.0, 0.0),
                    thresholds=Thresholds(), dataset_version="1", git_sha="s")
    spans = EXPORTER.get_finished_spans()
    by_name = {name: [s for s in spans if s.name == name] for name in {s.name for s in spans}}
    (run_span,) = by_name["eval_run"]
    assert len(by_name["eval_case"]) == len(by_name["chat fake"]) == len(CASES)
    assert all(s.parent and s.parent.span_id == run_span.context.span_id
               for s in by_name["eval_case"])
    case_span_ids = {s.context.span_id for s in by_name["eval_case"]}
    assert all(s.parent and s.parent.span_id in case_span_ids for s in by_name["chat fake"])
    assert all(s.attributes and s.attributes["gen_ai.usage.output_tokens"] > 0
               for s in by_name["chat fake"])
    # Receipts carry PII: no attribute may contain any receipt's text.
    values = [str(v) for s in spans for v in (s.attributes or {}).values()]
    for case in CASES:
        snippet = case.receipt_text.strip()[:20]
        assert not any(snippet in value for value in values)
