"""diff

Compares a scored run against the blessed baseline for the same dataset version: pass-rate
deltas overall and per group, pass→fail / fail→pass flips, adversarial failures, run status,
and multi-run drift.

Status:
    Provider-errored cases are excluded from pass rates, flips, and adversarial failure
    counts — they measure the provider, not the model, and are gated by PROVIDER_UNSTABLE.
Issues:
    None
Todo:
    None
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass

from receipt_sentinel.evals.golden_case import Difficulty
from receipt_sentinel.evals.run_store import EvalRun, RunStatus, provider_error_rate
from receipt_sentinel.evals.scoring import CaseScore, is_provider_errored
from receipt_sentinel.evals.thresholds import Thresholds, classify_status


@dataclass(frozen=True)
class CaseFlip:
    case_id: str
    difficulty: Difficulty
    baseline: CaseScore | None
    current: CaseScore


@dataclass(frozen=True)
class GroupDelta:
    name: str
    baseline_pass_rate: float
    current_pass_rate: float

    @property
    def delta_points(self) -> float:
        return (self.current_pass_rate - self.baseline_pass_rate) * 100


@dataclass(frozen=True)
class DriftResult:
    window_runs: int
    moving_average_pass_rate: float
    baseline_pass_rate: float
    gap_points: float
    drifting: bool


@dataclass(frozen=True)
class DiffResult:
    baseline_run_id: str
    current_run_id: str
    dataset_version: str
    pass_rate_delta_points: float
    by_difficulty: tuple[GroupDelta, ...]
    by_category: tuple[GroupDelta, ...]
    regressions: tuple[CaseFlip, ...]
    improvements: tuple[CaseFlip, ...]
    new_cases: tuple[str, ...]
    missing_cases: tuple[str, ...]
    adversarial_failures: int
    status: RunStatus
    drift: DriftResult | None


def _pass_rate(scores: Iterable[CaseScore]) -> float:
    """Passed / scoreable — provider-errored cases never reached the model, so they are
    excluded from the denominator (same rule as EvalRun.pass_rate)."""
    listed = [s for s in scores if not is_provider_errored(s)]
    return sum(s.passed for s in listed) / len(listed) if listed else 0.0


def _group_deltas(
    current: EvalRun, baseline: EvalRun, key: Callable[[CaseScore], str]
) -> tuple[GroupDelta, ...]:
    names = sorted({key(s) for s in (*current.scores, *baseline.scores)})
    return tuple(
        GroupDelta(
            name=name,
            baseline_pass_rate=_pass_rate(s for s in baseline.scores if key(s) == name),
            current_pass_rate=_pass_rate(s for s in current.scores if key(s) == name),
        )
        for name in names
    )


def diff_against_baseline(
    current: EvalRun,
    baseline: EvalRun,
    *,
    thresholds: Thresholds,
    case_categories: dict[str, str],
    drift: DriftResult | None = None,
) -> DiffResult:
    if current.dataset_version != baseline.dataset_version:
        raise ValueError(
            f"dataset version mismatch: run={current.dataset_version} "
            f"baseline={baseline.dataset_version}"
        )
    base_by_id = {s.case_id: s for s in baseline.scores}
    cur_by_id = {s.case_id: s for s in current.scores}
    # A case that provider-errored in either run is neither a pass nor a fail there, so it
    # cannot have flipped in a way that says anything about the model.
    comparable = [
        s
        for s in current.scores
        if s.case_id in base_by_id
        and not is_provider_errored(s)
        and not is_provider_errored(base_by_id[s.case_id])
    ]
    regressions = tuple(
        CaseFlip(s.case_id, s.difficulty, base_by_id[s.case_id], s)
        for s in comparable
        if base_by_id[s.case_id].passed and not s.passed
    )
    improvements = tuple(
        CaseFlip(s.case_id, s.difficulty, base_by_id[s.case_id], s)
        for s in comparable
        if not base_by_id[s.case_id].passed and s.passed
    )
    adversarial_failures = sum(
        1
        for s in current.scores
        if s.difficulty is Difficulty.ADVERSARIAL and not s.passed and not is_provider_errored(s)
    )
    delta_points = (current.pass_rate - baseline.pass_rate) * 100
    status = classify_status(
        pass_rate_delta_points=delta_points,
        adversarial_failures=adversarial_failures,
        error_rate=provider_error_rate(current.scores),
        thresholds=thresholds,
    )
    return DiffResult(
        baseline_run_id=baseline.run_id,
        current_run_id=current.run_id,
        dataset_version=current.dataset_version,
        pass_rate_delta_points=delta_points,
        by_difficulty=_group_deltas(current, baseline, lambda s: s.difficulty.value),
        by_category=_group_deltas(
            current, baseline, lambda s: case_categories.get(s.case_id, "unknown")
        ),
        regressions=regressions,
        improvements=improvements,
        new_cases=tuple(sorted(set(cur_by_id) - set(base_by_id))),
        missing_cases=tuple(sorted(set(base_by_id) - set(cur_by_id))),
        adversarial_failures=adversarial_failures,
        status=status,
        drift=drift,
    )


def compute_drift(
    recent_runs: list[EvalRun], baseline: EvalRun, thresholds: Thresholds
) -> DriftResult | None:
    window = recent_runs[: thresholds.drift_window_runs]
    if len(window) < thresholds.drift_window_runs:
        return None
    moving_average = sum(r.pass_rate for r in window) / len(window)
    gap_points = (baseline.pass_rate - moving_average) * 100
    return DriftResult(
        window_runs=len(window),
        moving_average_pass_rate=moving_average,
        baseline_pass_rate=baseline.pass_rate,
        gap_points=gap_points,
        drifting=gap_points > thresholds.drift_floor_points,
    )
