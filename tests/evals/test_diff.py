import pytest

from receipt_sentinel.evals.diff import (
    compute_drift,
    diff_against_baseline,
    drift_from_pass_rates,
)
from receipt_sentinel.evals.golden_case import Difficulty
from receipt_sentinel.evals.run_store import EvalRun, RunStatus
from receipt_sentinel.evals.scoring import CaseScore
from receipt_sentinel.evals.thresholds import Thresholds

T = Thresholds()


def score(case_id: str, passed: bool, difficulty: Difficulty = Difficulty.EASY) -> CaseScore:
    return CaseScore(
        case_id=case_id,
        difficulty=difficulty,
        category_match=passed,
        total_match=passed,
        date_match=passed,
        vendor_match=passed,
        line_item_recall=1.0,
        validation_pass=True,
        injection_resisted=(passed if difficulty is Difficulty.ADVERSARIAL else None),
        passed=passed,
        errored=False,
        error_kind=None,
        latency_ms=1,
        input_tokens=1,
        output_tokens=1,
        cost_usd=0.0,
        actual=None,
    )


def run(run_id: str, scores: list[CaseScore], version: str = "1") -> EvalRun:
    return EvalRun(
        run_id=run_id,
        created_at="2026-08-28T00:00:00Z",
        prompt_label="p@1",
        model_id="m",
        dataset_version=version,
        scores=tuple(scores),
        status=RunStatus.PASS,
        git_sha="x",
    )


CATS = {"a": "groceries", "b": "dining", "c": "dining", "adv": "transport"}


def test_regressions_improvements_and_status() -> None:
    base = run("b", [score("a", True), score("b", True), score("c", False)])
    cur = run("c", [score("a", True), score("b", False), score("c", True), score("d", True)])
    d = diff_against_baseline(cur, base, thresholds=T, case_categories=CATS)
    assert [f.case_id for f in d.regressions] == ["b"]
    assert [f.case_id for f in d.improvements] == ["c"]
    assert d.new_cases == ("d",)
    assert d.status is RunStatus.PASS


def test_adversarial_failure_is_critical_even_when_pass_rate_improves() -> None:
    base = run("b", [score("a", False), score("adv", True, Difficulty.ADVERSARIAL)])
    cur = run("c", [score("a", True), score("adv", False, Difficulty.ADVERSARIAL)])
    d = diff_against_baseline(cur, base, thresholds=T, case_categories=CATS)
    assert d.adversarial_failures == 1 and d.status is RunStatus.CRITICAL


def test_per_group_deltas() -> None:
    base = run("b", [score("a", True), score("b", True), score("c", True)])
    cur = run("c", [score("a", True), score("b", False), score("c", False)])
    d = diff_against_baseline(cur, base, thresholds=T, case_categories=CATS)
    dining = next(g for g in d.by_category if g.name == "dining")
    assert dining.delta_points == pytest.approx(-100.0)
    assert d.pass_rate_delta_points == pytest.approx(-66.6667, abs=0.01)


def test_dataset_version_mismatch_raises() -> None:
    with pytest.raises(ValueError, match="dataset version"):
        diff_against_baseline(
            run("c", [], "2"), run("b", [], "1"), thresholds=T, case_categories={}
        )


def test_drift_needs_window_and_flags_gap() -> None:
    base = run("b", [score("a", True), score("b", True)])
    recent = [run(f"r{i}", [score("a", True), score("b", False)]) for i in range(7)]
    assert compute_drift(recent[:6], base, T) is None
    drift = compute_drift(recent, base, T)
    assert drift is not None and drift.gap_points == pytest.approx(50.0) and drift.drifting


def provider_errored(case_id: str, difficulty: Difficulty = Difficulty.EASY) -> CaseScore:
    return CaseScore(
        case_id=case_id,
        difficulty=difficulty,
        category_match=False,
        total_match=False,
        date_match=False,
        vendor_match=False,
        line_item_recall=0.0,
        validation_pass=False,
        injection_resisted=None,
        passed=False,
        errored=True,
        error_kind="provider",
        latency_ms=0,
        input_tokens=0,
        output_tokens=0,
        cost_usd=0.0,
        actual=None,
    )


def test_provider_errored_adversarial_case_is_not_a_critical_failure() -> None:
    base = run("b", [score("a", True), score("adv", True, Difficulty.ADVERSARIAL)])
    cur = run("c", [score("a", True), provider_errored("adv", Difficulty.ADVERSARIAL)])
    d = diff_against_baseline(cur, base, thresholds=T, case_categories=CATS)
    assert d.adversarial_failures == 0
    assert d.regressions == ()  # the case is neither pass nor fail in the current run
    assert d.status is not RunStatus.CRITICAL


def test_pass_rate_ignores_provider_errored_cases() -> None:
    # 1 of 2 scoreable cases passes; the provider error is not in the denominator.
    cur = run("c", [score("a", True), score("b", False), provider_errored("c")])
    assert cur.pass_rate == pytest.approx(0.5)


def test_drift_from_pass_rates_needs_full_window() -> None:
    assert drift_from_pass_rates([0.95] * 6, 0.982, T) is None
    drift = drift_from_pass_rates([0.95] * 7, 0.982, T)
    assert drift is not None and drift.drifting
    assert drift.gap_points == pytest.approx(3.2)
