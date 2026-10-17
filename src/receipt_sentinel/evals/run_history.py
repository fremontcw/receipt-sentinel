"""run_history

Numbers-only nightly history: one JSON line per nightly run, published on the gh-pages
branch and read back to compute drift. Never holds receipt text, model output, labels, or
case ids — those stay in the private EvalRun JSON.

Status:
    None
Issues:
    None
Todo:
    None
"""

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from receipt_sentinel.evals.diff import DiffResult
from receipt_sentinel.evals.run_store import EvalRun

HISTORY_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class ScoredNight:
    kind: Literal["scored"]
    schema_version: int
    created_at: str
    run_url: str | None
    model_id: str
    prompt_label: str
    dataset_version: str
    baseline_run_id: str
    case_count: int
    pass_rate: float
    delta_points: float
    regressions: int
    adversarial_failures: int
    cost_usd: float
    p50_latency_ms: int
    status: str
    drifting: bool
    drift_gap_points: float | None


@dataclass(frozen=True)
class UnscoredNight:
    kind: Literal["not_scored"]
    schema_version: int
    created_at: str
    run_url: str | None
    reason: str


HistoryRecord = ScoredNight | UnscoredNight


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def scored_night_from(run: EvalRun, diff: DiffResult, *, run_url: str | None) -> ScoredNight:
    """Reduce a scored run and its diff to the numbers that are safe to publish."""
    return ScoredNight(
        kind="scored",
        schema_version=HISTORY_SCHEMA_VERSION,
        created_at=run.created_at,
        run_url=run_url,
        model_id=run.model_id,
        prompt_label=run.prompt_label,
        dataset_version=run.dataset_version,
        baseline_run_id=diff.baseline_run_id,
        case_count=len(run.scores),
        pass_rate=run.pass_rate,
        delta_points=diff.pass_rate_delta_points,
        regressions=len(diff.regressions),
        adversarial_failures=diff.adversarial_failures,
        cost_usd=run.total_cost_usd,
        p50_latency_ms=run.p50_latency_ms,
        status=diff.status.value,
        drifting=bool(diff.drift and diff.drift.drifting),
        drift_gap_points=diff.drift.gap_points if diff.drift else None,
    )


def unscored_night(
    reason: str, *, run_url: str | None, created_at: str | None = None
) -> UnscoredNight:
    """A night that produced no score (outage, rejected key, no key) — never a silent gap."""
    return UnscoredNight(
        kind="not_scored",
        schema_version=HISTORY_SCHEMA_VERSION,
        created_at=created_at or _utc_now(),
        run_url=run_url,
        reason=reason,
    )


def _parse_record(data: object) -> HistoryRecord:
    if not isinstance(data, dict):
        raise ValueError("not a JSON object")
    if data.get("schema_version") != HISTORY_SCHEMA_VERSION:
        raise ValueError(f"unsupported schema_version {data.get('schema_version')!r}")
    kind = data.get("kind")
    try:
        if kind == "scored":
            return ScoredNight(**data)
        if kind == "not_scored":
            return UnscoredNight(**data)
    except TypeError as err:
        raise ValueError(f"bad fields: {err}") from err
    raise ValueError(f"unknown kind {kind!r}")


def load_history(path: Path) -> list[HistoryRecord]:
    """Read the history file, oldest first. Missing file = empty history; a bad line raises.

    Raises:
        ValueError: A line is not valid JSON or not a known record, naming the line number.
    """
    if not path.exists():
        return []
    records: list[HistoryRecord] = []
    for number, line in enumerate(path.read_text().splitlines(), start=1):
        if not line.strip():
            continue
        try:
            records.append(_parse_record(json.loads(line)))
        except (json.JSONDecodeError, ValueError) as err:
            raise ValueError(f"{path} line {number}: {err}") from err
    return records


def append_history(path: Path, record: HistoryRecord) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        handle.write(json.dumps(asdict(record)) + "\n")


def comparable_pass_rates(
    history: list[HistoryRecord],
    *,
    dataset_version: str,
    baseline_run_id: str,
    model_id: str,
    prompt_label: str,
) -> list[float]:
    """Pass rates of scored nights comparable to this one, newest first.

    A promoted baseline, a new dataset version, a different model, or a different prompt
    restarts the drift window, so nights before the change never raise a false alarm.
    """
    return [
        r.pass_rate
        for r in reversed(history)
        if isinstance(r, ScoredNight)
        and r.dataset_version == dataset_version
        and r.baseline_run_id == baseline_run_id
        and r.model_id == model_id
        and r.prompt_label == prompt_label
    ]
