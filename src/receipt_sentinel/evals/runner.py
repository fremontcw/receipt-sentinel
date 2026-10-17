"""runner

Runs the categorizer over the golden set concurrently, scores every case, enforces the
per-run cost ceiling, and flags provider instability.

Status:
    None
Issues:
    None
Todo:
    None
"""

import asyncio
from datetime import UTC, datetime

import anthropic

from receipt_sentinel.categorize.categorize_receipt import ParseFailure, categorize_receipt
from receipt_sentinel.categorize.llm_client import LlmClient, LlmResponse, LlmTransientError
from receipt_sentinel.evals.golden_case import GoldenCase
from receipt_sentinel.evals.run_store import EvalRun, RunStatus, new_run_id, provider_error_rate
from receipt_sentinel.evals.scoring import CaseScore, ModelPricing, errored_score, score_case
from receipt_sentinel.evals.thresholds import Thresholds
from receipt_sentinel.prompts.prompt_config import PromptConfig
from receipt_sentinel.telemetry import tracer


class CostCeilingExceeded(Exception):  # noqa: N818 - brief's exact required name
    pass


async def _score_one(
    case: GoldenCase,
    prompt: PromptConfig,
    client: LlmClient,
    model_id: str,
    pricing: ModelPricing,
) -> CaseScore:
    with tracer.start_as_current_span("eval_case") as span:
        score = await _categorize_and_score(case, prompt, client, model_id, pricing)
        span.set_attribute("receipt_sentinel.case.id", case.id)
        span.set_attribute("receipt_sentinel.case.difficulty", score.difficulty.value)
        span.set_attribute("receipt_sentinel.case.passed", score.passed)
        span.set_attribute("receipt_sentinel.case.cost_usd", score.cost_usd)
        if score.error_kind:
            span.set_attribute("error.type", score.error_kind)
        return score


async def _categorize_and_score(
    case: GoldenCase,
    prompt: PromptConfig,
    client: LlmClient,
    model_id: str,
    pricing: ModelPricing,
) -> CaseScore:
    try:
        result = await categorize_receipt(case.receipt_text, prompt, client, model_id=model_id)
    except ParseFailure as err:
        # ponytail: ParseFailure only carries raw text, not the LlmResponse, so the real
        # token counts are lost here and cost accounting under-counts parse failures.
        # Rare in practice; attach LlmResponse to ParseFailure if this needs to be exact.
        raw = LlmResponse(
            text=err.raw_text, input_tokens=0, output_tokens=0, latency_ms=0, model_id=model_id
        )
        return errored_score(case, "parse", raw, pricing)
    except (LlmTransientError, anthropic.APIStatusError):
        # APIStatusError covers the non-retryable statuses the client never retries (401,
        # 403, 404, 400). They are provider problems, not model failures — one must not
        # crash the whole run.
        return errored_score(case, "provider", None, pricing)
    return score_case(case, result.record, result.response, pricing)


async def run_evals(
    cases: list[GoldenCase],
    prompt: PromptConfig,
    client: LlmClient,
    *,
    model_id: str,
    pricing: ModelPricing,
    thresholds: Thresholds,
    dataset_version: str,
    git_sha: str,
    run_id: str | None = None,
) -> EvalRun:
    """Score every golden case concurrently, enforcing the per-run cost ceiling.

    Args:
        cases: Golden cases to score.
        prompt: Prompt version under test.
        client: LLM client (real or fake) to call.
        model_id: Model identifier passed to the client and recorded on the run.
        pricing: Per-token pricing used for cost accounting.
        thresholds: Concurrency, cost ceiling, and error-rate gates.
        dataset_version: Golden dataset version recorded on the run.
        git_sha: Commit under test, recorded on the run.
        run_id: Explicit run id, or a generated one when omitted.

    Returns:
        The completed EvalRun, with status PROVIDER_UNSTABLE when provider errors exceed
        the threshold, else PASS (a placeholder the regression diff replaces).

    Raises:
        CostCeilingExceeded: Accumulated cost passed thresholds.run_cost_ceiling_usd.
    """
    semaphore = asyncio.Semaphore(thresholds.max_concurrency)
    spent = 0.0

    async def guarded(case: GoldenCase) -> CaseScore:
        nonlocal spent
        async with semaphore:
            score = await _score_one(case, prompt, client, model_id, pricing)
            spent += score.cost_usd
            if spent > thresholds.run_cost_ceiling_usd:
                raise CostCeilingExceeded(
                    f"spent ${spent:.4f} > ceiling ${thresholds.run_cost_ceiling_usd}"
                )
            return score

    with tracer.start_as_current_span("eval_run") as span:
        span.set_attribute("receipt_sentinel.run.prompt_label", prompt.label)
        span.set_attribute("gen_ai.request.model", model_id)
        span.set_attribute("receipt_sentinel.run.dataset_version", dataset_version)
        span.set_attribute("receipt_sentinel.run.case_count", len(cases))
        # Tasks copy the current context when created, so each case span nests under this one.
        scores = await _gather_cancelling_on_error(
            [asyncio.create_task(guarded(case)) for case in cases]
        )
        span.set_attribute("receipt_sentinel.run.cost_usd", round(spent, 6))

    ordered = tuple(sorted(scores, key=lambda s: s.case_id))
    unstable = provider_error_rate(ordered) > thresholds.max_error_rate
    return EvalRun(
        run_id=run_id or new_run_id(),
        created_at=datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        prompt_label=prompt.label,
        model_id=model_id,
        dataset_version=dataset_version,
        scores=ordered,
        status=RunStatus.PROVIDER_UNSTABLE if unstable else RunStatus.PASS,
        git_sha=git_sha,
    )


async def _gather_cancelling_on_error(tasks: list[asyncio.Task[CaseScore]]) -> list[CaseScore]:
    try:
        return list(await asyncio.gather(*tasks))
    except BaseException:
        # Any escaping exception (cost ceiling, cancellation, an unforeseen client error)
        # leaves siblings running. Cancel and reap every task — including the raiser — so
        # cancellation is fully awaited and no "Task was destroyed but it is pending" /
        # unretrieved-exception noise leaks out.
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
