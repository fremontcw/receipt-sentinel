import json
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from receipt_sentinel.evals.diff import diff_against_baseline
from receipt_sentinel.evals.run_history import (
    HISTORY_SCHEMA_VERSION,
    HistoryRecord,
    ScoredNight,
    UnscoredNight,
    append_history,
    comparable_pass_rates,
    load_history,
    scored_night_from,
    unscored_night,
)
from tests.evals.test_diff import T, run, score


def night(pass_rate: float, *, version: str = "1", baseline: str = "b1") -> ScoredNight:
    return ScoredNight(
        kind="scored", schema_version=HISTORY_SCHEMA_VERSION, created_at="2026-10-17T06:00:00Z",
        run_url=None, model_id="m", prompt_label="p@1", dataset_version=version,
        baseline_run_id=baseline, case_count=56, pass_rate=pass_rate, delta_points=0.0,
        regressions=0, adversarial_failures=0, cost_usd=0.19, p50_latency_ms=1500,
        status="PASS", drifting=False, drift_gap_points=None,
    )


def test_scored_night_is_numbers_only() -> None:
    leaky = replace(score("case_with_secret_id", False), actual={"vendor": "SECRET_RECEIPT_TEXT"})
    current = run("cur", [leaky, score("b", True)])
    baseline = run("base", [score("case_with_secret_id", True), score("b", True)])
    diff = diff_against_baseline(current, baseline, thresholds=T, case_categories={})
    record = scored_night_from(current, diff, run_url=None)
    published = json.dumps(asdict(record))
    assert "SECRET_RECEIPT_TEXT" not in published
    assert "case_with_secret_id" not in published
    assert record.delta_points == diff.pass_rate_delta_points
    assert record.baseline_run_id == "base"
    assert record.regressions == 1
    assert record.drifting is False and record.drift_gap_points is None


def test_round_trip_and_order(tmp_path: Path) -> None:
    path = tmp_path / "h.jsonl"
    records: list[HistoryRecord] = [night(0.98), unscored_night("no API key", run_url=None)]
    for record in records:
        append_history(path, record)
    assert load_history(path) == records
    assert isinstance(load_history(path)[1], UnscoredNight)


def test_missing_file_is_empty_history(tmp_path: Path) -> None:
    assert load_history(tmp_path / "nope.jsonl") == []


def test_malformed_line_raises_with_line_number(tmp_path: Path) -> None:
    path = tmp_path / "h.jsonl"
    append_history(path, night(0.98))
    with path.open("a") as handle:
        handle.write('{"kind": "scored"\n')
    with pytest.raises(ValueError, match="line 2"):
        load_history(path)
    future = {**asdict(night(0.98)), "schema_version": 99}
    path.write_text(json.dumps(future) + "\n")
    with pytest.raises(ValueError, match="line 1"):
        load_history(path)


def test_comparable_pass_rates_filters_baseline_and_dataset() -> None:
    history: list[HistoryRecord] = [
        night(0.9, baseline="b1"),
        night(0.8, baseline="b2"),
        night(0.7, version="2", baseline="b2"),
        unscored_night("provider unstable", run_url=None),
        night(0.6, baseline="b2"),
    ]
    assert comparable_pass_rates(history, dataset_version="1", baseline_run_id="b2") == [0.6, 0.8]
