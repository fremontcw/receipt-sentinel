import shutil
from pathlib import Path

import pytest

from receipt_sentinel.categorize.llm_client import (
    LlmRequest,
    LlmResponse,
    LlmTransientError,
)
from receipt_sentinel.evals.eval_commands import (
    EvalPaths,
    command_compare_models,
    command_evals_run,
    command_promote_baseline,
    command_promote_case,
    command_record_skip,
)
from receipt_sentinel.evals.golden_case import GOLDEN_DIR, load_golden_cases
from receipt_sentinel.evals.run_history import (
    HISTORY_SCHEMA_VERSION,
    ScoredNight,
    UnscoredNight,
    append_history,
    load_history,
)
from receipt_sentinel.evals.run_store import RunStatus, list_recent_runs, load_run_json


def paths_in(tmp_path: Path) -> EvalPaths:
    default = EvalPaths.default(tmp_path / "out")
    return EvalPaths(**{**default.__dict__, "baseline_path": tmp_path / "baseline.json"})


def test_first_fake_run_without_baseline_saves_and_reports(tmp_path: Path) -> None:
    p = paths_in(tmp_path)
    assert command_evals_run(p, fake=True, save=True, slack_webhook=None, report_url=None) == 0
    runs = list_recent_runs(p.db_path, dataset_version="1", limit=5)
    summary_path = p.report_path.parent / "summary.md"
    assert len(runs) == 1 and p.report_path.exists() and summary_path.exists()


def test_promote_then_second_run_diffs_against_baseline(tmp_path: Path) -> None:
    p = paths_in(tmp_path)
    command_evals_run(p, fake=True, save=True, slack_webhook=None, report_url=None)
    run_id = list_recent_runs(p.db_path, dataset_version="1", limit=1)[0].run_id
    assert command_promote_baseline(p, run_id) == 0
    assert load_run_json(p.baseline_path).run_id == run_id
    code = command_evals_run(p, fake=True, save=True, slack_webhook=None, report_url=None)
    assert code == 0  # fake oracle is deterministic → identical run → PASS
    assert "Baseline" in p.report_path.read_text()


def test_promote_case_writes_stub(tmp_path: Path) -> None:
    golden = tmp_path / "golden"
    (golden / "cases").mkdir(parents=True)
    (golden / "dataset_version.txt").write_text("1")
    p = EvalPaths(**{**EvalPaths.default(tmp_path / "out").__dict__, "golden_dir": golden})
    receipt = tmp_path / "r.txt"
    receipt.write_text("ACME\nTOTAL 5.00\n")
    assert command_promote_case(p, receipt, "real_0099_acme") == 0
    stub = (golden / "cases" / "real_0099_acme.yaml").read_text()
    assert "FILL_ME" in stub and "ACME" in stub
    assert load_golden_cases(golden)[0].id == "real_0099_acme"


def test_provider_unstable_run_exits_zero_saves_nothing_but_writes_summary(
    tmp_path: Path,
) -> None:
    class Flaky:
        async def complete(self, request: LlmRequest) -> LlmResponse:
            raise LlmTransientError("down")

    p = paths_in(tmp_path)
    code = command_evals_run(p, fake=True, save=True, slack_webhook=None, report_url=None,
                             client_override=Flaky())
    assert code == 0
    assert list_recent_runs(p.db_path, dataset_version="1", limit=5) == []
    assert (p.report_path.parent / "summary.md").exists()
    assert "PROVIDER_UNSTABLE" in (p.report_path.parent / "summary.md").read_text()
    assert (p.report_path.parent / "status").read_text() == "PROVIDER_UNSTABLE\n"


def test_regression_against_clean_baseline_is_critical_and_exits_one(tmp_path: Path) -> None:
    p = paths_in(tmp_path)
    command_evals_run(p, fake=True, save=True, slack_webhook=None, report_url=None)
    run_id = list_recent_runs(p.db_path, dataset_version="1", limit=1)[0].run_id
    assert command_promote_baseline(p, run_id) == 0
    code = command_evals_run(p, fake=True, save=True, slack_webhook=None, report_url=None,
                             demo_noise=True)
    assert code == 1
    assert "CRITICAL" in (p.report_path.parent / "summary.md").read_text()
    # The nightly workflow branches on this file, not on parsing summary.md.
    assert (p.report_path.parent / "status").read_text() == "CRITICAL\n"


def test_promote_baseline_rejects_dataset_version_mismatch(tmp_path: Path) -> None:
    p = paths_in(tmp_path)
    command_evals_run(p, fake=True, save=True, slack_webhook=None, report_url=None)
    run_id = list_recent_runs(p.db_path, dataset_version="1", limit=1)[0].run_id
    stored = p.runs_dir / f"{run_id}.json"
    stored.write_text(stored.read_text().replace('"dataset_version": "1"',
                                                 '"dataset_version": "99"'))
    assert command_promote_baseline(p, run_id) == 1
    assert not p.baseline_path.exists()


def test_promote_baseline_rejects_unknown_run_id(tmp_path: Path) -> None:
    assert command_promote_baseline(paths_in(tmp_path), "20260828T000000Z-abcdef") == 1


def test_compare_models_identical_models_pass_and_store_nothing(tmp_path: Path) -> None:
    p = paths_in(tmp_path)
    assert command_compare_models(p, fake=True) == 0
    summary = (p.report_path.parent / "summary.md").read_text()
    assert "model comparison: **PASS**" in summary
    assert p.report_path.exists()
    # A comparison is not a run of record: it must not feed history, drift, or the baseline.
    assert not p.db_path.exists()
    assert not p.baseline_path.exists()


def test_compare_models_worse_candidate_is_critical_with_cost_and_latency(
    tmp_path: Path,
) -> None:
    p = paths_in(tmp_path)
    assert command_compare_models(p, fake=True, demo_noise=True) == 1
    summary = (p.report_path.parent / "summary.md").read_text()
    assert "CRITICAL" in summary
    assert "cost $" in summary and "p50" in summary
    assert "Incumbent" in p.report_path.read_text()


def test_compare_models_unpriced_candidate_fails_before_any_model_call(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="no pricing for model 'claude-unpriced'"):
        command_compare_models(paths_in(tmp_path), candidate_model="claude-unpriced")


def _bless_clean_baseline(p: EvalPaths) -> str:
    command_evals_run(p, fake=True, save=True, slack_webhook=None, report_url=None)
    run_id = list_recent_runs(p.db_path, dataset_version="1", limit=1)[0].run_id
    assert command_promote_baseline(p, run_id) == 0
    return run_id


def _out(p: EvalPaths, name: str) -> str:
    return (p.report_path.parent / name).read_text()


def test_stored_run_records_diff_verdict(tmp_path: Path) -> None:
    p = paths_in(tmp_path)
    _bless_clean_baseline(p)
    command_evals_run(p, fake=True, save=True, slack_webhook=None, report_url=None,
                      demo_noise=True)
    # Both runs land in the same second, so recency order is a coin toss: compare as a set.
    # Before the fix both were stored as the PASS placeholder.
    stored = {r.status for r in list_recent_runs(p.db_path, dataset_version="1", limit=2)}
    assert stored == {RunStatus.PASS, RunStatus.CRITICAL}


def test_history_run_appends_scored_night(tmp_path: Path) -> None:
    p = paths_in(tmp_path)
    baseline_id = _bless_clean_baseline(p)
    history = tmp_path / "h.jsonl"
    assert command_evals_run(p, fake=True, save=False, slack_webhook=None, report_url=None,
                             history_path=history) == 0
    [record] = load_history(history)
    assert isinstance(record, ScoredNight) and record.baseline_run_id == baseline_id
    assert _out(p, "drift") == "steady\n"


def _prefill(history: Path, baseline_id: str, pass_rate: float) -> None:
    for _ in range(7):
        append_history(history, ScoredNight(
            kind="scored", schema_version=HISTORY_SCHEMA_VERSION,
            created_at="2026-10-01T06:00:00Z", run_url=None, model_id="fake",
            prompt_label="p@1", dataset_version="1", baseline_run_id=baseline_id,
            case_count=56, pass_rate=pass_rate, delta_points=0.0, regressions=0,
            adversarial_failures=0, cost_usd=0.0, p50_latency_ms=1, status="PASS",
            drifting=False, drift_gap_points=None,
        ))


def test_drifting_history_exits_one_and_writes_drift(tmp_path: Path) -> None:
    p = paths_in(tmp_path)
    history = tmp_path / "h.jsonl"
    _prefill(history, _bless_clean_baseline(p), 0.80)
    assert command_evals_run(p, fake=True, save=False, slack_webhook=None, report_url=None,
                             history_path=history) == 1
    assert _out(p, "drift") == "drifting\n"
    assert _out(p, "status") == "PASS\n"
    newest = load_history(history)[-1]
    assert isinstance(newest, ScoredNight) and newest.drifting is True


def test_history_ignores_nights_from_an_old_baseline(tmp_path: Path) -> None:
    p = paths_in(tmp_path)
    _bless_clean_baseline(p)
    history = tmp_path / "h.jsonl"
    _prefill(history, "old", 0.80)
    assert command_evals_run(p, fake=True, save=False, slack_webhook=None, report_url=None,
                             history_path=history) == 0
    assert _out(p, "drift") == "steady\n"


def test_provider_unstable_with_history_appends_unscored(tmp_path: Path) -> None:
    class Flaky:
        async def complete(self, request: LlmRequest) -> LlmResponse:
            raise LlmTransientError("down")

    p = paths_in(tmp_path)
    history = tmp_path / "h.jsonl"
    command_evals_run(p, fake=True, save=False, slack_webhook=None, report_url=None,
                      client_override=Flaky(), history_path=history)
    [record] = load_history(history)
    assert isinstance(record, UnscoredNight) and record.reason.startswith("provider unstable")


def test_public_run_omits_report_when_golden_set_has_real_case(tmp_path: Path) -> None:
    golden = tmp_path / "golden"
    shutil.copytree(GOLDEN_DIR, golden)
    case = next((golden / "cases").glob("*.yaml"))
    case.write_text(case.read_text().replace("source: synthetic", "source: real_redacted"))
    p = EvalPaths(**{**paths_in(tmp_path).__dict__, "golden_dir": golden})
    command_evals_run(p, fake=True, save=False, slack_webhook=None, report_url=None, public=True)
    assert not p.report_path.exists()
    assert (p.report_path.parent / "summary.md").exists()

    synthetic = paths_in(tmp_path / "synthetic")
    command_evals_run(synthetic, fake=True, save=False, slack_webhook=None, report_url=None,
                      public=True)
    assert synthetic.report_path.exists()


def test_record_skip_appends_reason(tmp_path: Path) -> None:
    history = tmp_path / "h.jsonl"
    assert command_record_skip(history, reason="no API key", run_url=None) == 0
    [record] = load_history(history)
    assert isinstance(record, UnscoredNight) and record.reason == "no API key"
