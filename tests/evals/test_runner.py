import json
from unittest.mock import MagicMock

import anthropic
import pytest

from receipt_sentinel.categorize.llm_client import FakeLlmClient, LlmRequest, LlmTransientError
from receipt_sentinel.evals.golden_case import load_golden_cases
from receipt_sentinel.evals.run_store import RunStatus
from receipt_sentinel.evals.runner import CostCeilingExceeded, run_evals
from receipt_sentinel.evals.scoring import ModelPricing
from receipt_sentinel.evals.thresholds import Thresholds
from receipt_sentinel.prompts.prompt_config import PROMPTS_DIR, load_prompt_config

PROMPT = load_prompt_config(PROMPTS_DIR / "receipt_categorizer_v1.yaml")
CASES = load_golden_cases()[:10]
FREE = ModelPricing(0.0, 0.0)


def oracle(request: LlmRequest) -> str:
    """Answer from the golden label embedded in the case whose text is in the request."""
    for case in CASES:
        if case.receipt_text.strip() in request.user:
            return json.dumps(case.expected.model_dump(mode="json"))
    raise AssertionError("unknown case")


async def test_oracle_client_passes_everything() -> None:
    run = await run_evals(
        CASES,
        PROMPT,
        FakeLlmClient(oracle),
        model_id="fake",
        pricing=FREE,
        thresholds=Thresholds(),
        dataset_version="1",
        git_sha="s",
    )
    assert run.pass_rate == 1.0 and run.status is RunStatus.PASS
    assert {s.case_id for s in run.scores} == {c.id for c in CASES}


async def test_parse_failures_are_scored_not_raised() -> None:
    run = await run_evals(
        CASES,
        PROMPT,
        FakeLlmClient(lambda _r: "garbage"),
        model_id="fake",
        pricing=FREE,
        thresholds=Thresholds(),
        dataset_version="1",
        git_sha="s",
    )
    assert run.pass_rate == 0.0
    assert all(s.error_kind == "parse" for s in run.scores)
    assert run.status is RunStatus.PASS  # parse errors are model failures, not provider instability


async def test_provider_errors_make_run_unstable() -> None:
    class Flaky:
        async def complete(self, request: LlmRequest) -> object:
            raise LlmTransientError("down")

    run = await run_evals(
        CASES,
        PROMPT,
        Flaky(),
        model_id="fake",
        pricing=FREE,
        thresholds=Thresholds(),
        dataset_version="1",
        git_sha="s",
    )
    assert run.status is RunStatus.PROVIDER_UNSTABLE


async def test_cost_ceiling_aborts() -> None:
    pricey = ModelPricing(usd_per_million_input=1_000_000.0, usd_per_million_output=0.0)
    with pytest.raises(CostCeilingExceeded):
        await run_evals(
            CASES,
            PROMPT,
            FakeLlmClient(oracle),
            model_id="fake",
            pricing=pricey,
            thresholds=Thresholds(run_cost_ceiling_usd=0.5),
            dataset_version="1",
            git_sha="s",
        )


async def test_non_retryable_api_error_aborts_the_run() -> None:
    """A 401/400 is a broken setup (bad key, no workspace, unknown model), not a flaky
    provider: scoring it as PROVIDER_UNSTABLE made a misconfigured CI pass silently."""

    class Unauthorised:
        async def complete(self, request: LlmRequest) -> object:
            raise anthropic.AuthenticationError(
                message="bad key", response=MagicMock(status_code=401), body=None
            )

    with pytest.raises(anthropic.AuthenticationError):
        await run_evals(
            CASES,
            PROMPT,
            Unauthorised(),
            model_id="fake",
            pricing=FREE,
            thresholds=Thresholds(),
            dataset_version="1",
            git_sha="s",
        )


async def test_extract_error_scores_as_failure_without_model_call() -> None:
    fake = FakeLlmClient(oracle)
    run = await run_evals(
        CASES,
        PROMPT,
        fake,
        model_id="fake",
        pricing=FREE,
        thresholds=Thresholds(),
        dataset_version="1",
        git_sha="s",
        extract_errors={CASES[0].id: "the PDF is damaged or incomplete"},
    )
    failed = next(s for s in run.scores if s.case_id == CASES[0].id)
    assert failed.error_kind == "extract" and not failed.passed
    assert not any(CASES[0].receipt_text.strip() in r.user for r in fake.calls)
    assert len(fake.calls) == len(CASES) - 1
