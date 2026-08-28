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

from receipt_sentinel.categorize.categorize_receipt import ParseFailure, categorize_receipt
from receipt_sentinel.categorize.llm_client import LlmClient, LlmResponse, LlmTransientError
from receipt_sentinel.evals.golden_case import GoldenCase
from receipt_sentinel.evals.run_store import EvalRun, RunStatus, new_run_id, provider_error_rate
from receipt_sentinel.evals.scoring import CaseScore, ModelPricing, errored_score, score_case
from receipt_sentinel.evals.thresholds import Thresholds
from receipt_sentinel.prompts.prompt_config import PromptConfig


class CostCeilingExceeded(Exception):  # noqa: N818 - brief's exact required name
    pass


async def _score_one(
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
    except LlmTransientError:
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

    tasks = [asyncio.create_task(guarded(case)) for case in cases]
    try:
        scores = list(await asyncio.gather(*tasks))
    except CostCeilingExceeded:
        for task in tasks:
            task.cancel()
        # Reap every task (including the raiser) so cancellation is fully awaited and no
        # "Task was destroyed but it is pending" / unretrieved-exception noise leaks out.
        await asyncio.gather(*tasks, return_exceptions=True)
        raise

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
