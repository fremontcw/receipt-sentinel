from pathlib import Path

import pytest

from receipt_sentinel.evals.golden_case import Difficulty
from receipt_sentinel.evals.run_store import (
    EvalRun,
    RunStatus,
    list_recent_runs,
    load_run_json,
    new_run_id,
    run_from_dict,
    run_to_dict,
    save_run,
)
from receipt_sentinel.evals.scoring import CaseScore


def make_score(case_id: str, passed: bool, latency: int = 100) -> CaseScore:
    return CaseScore(
        case_id=case_id,
        difficulty=Difficulty.EASY,
        category_match=passed,
        total_match=passed,
        date_match=passed,
        vendor_match=passed,
        line_item_recall=1.0,
        validation_pass=True,
        injection_resisted=None,
        passed=passed,
        errored=False,
        error_kind=None,
        latency_ms=latency,
        input_tokens=10,
        output_tokens=5,
        cost_usd=0.001,
        actual=None,
    )


def make_run(run_id: str, passes: tuple[bool, ...], status: RunStatus = RunStatus.PASS) -> EvalRun:
    scores = tuple(make_score(f"c{i}", p, latency=100 * (i + 1)) for i, p in enumerate(passes))
    return EvalRun(
        run_id=run_id,
        created_at=f"2026-08-28T00:00:0{run_id[-1]}Z",
        prompt_label="p@1",
        model_id="fake",
        dataset_version="1",
        scores=scores,
        status=status,
        git_sha="abc",
    )


def test_pass_rate_and_p50() -> None:
    run = make_run("r1", (True, False, True))
    assert run.pass_rate == pytest.approx(2 / 3)
    assert run.p50_latency_ms == 200
    assert run.total_cost_usd == pytest.approx(0.003)


def test_json_round_trip() -> None:
    run = make_run("r1", (True, False))
    assert run_from_dict(run_to_dict(run)) == run


def test_save_and_list_recent_newest_first(tmp_path: Path) -> None:
    db = tmp_path / "runs.db"
    for i in range(3):
        save_run(make_run(f"r{i}", (True,)), db_path=db, json_dir=tmp_path / "runs")
    recent = list_recent_runs(db, dataset_version="1", limit=2)
    assert [r.run_id for r in recent] == ["r2", "r1"]
    assert load_run_json(tmp_path / "runs" / "r0.json").run_id == "r0"


def test_provider_unstable_is_never_saved(tmp_path: Path) -> None:
    run = make_run("r9", (True,), status=RunStatus.PROVIDER_UNSTABLE)
    with pytest.raises(ValueError, match="PROVIDER_UNSTABLE"):
        save_run(run, db_path=tmp_path / "runs.db", json_dir=tmp_path / "runs")


def test_new_run_id_shape() -> None:
    rid = new_run_id()
    assert len(rid) == 16 + 1 + 6 and rid[8] == "T" and rid[15] == "Z"
