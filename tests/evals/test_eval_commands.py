from pathlib import Path

from receipt_sentinel.categorize.llm_client import (
    LlmRequest,
    LlmResponse,
    LlmTransientError,
)
from receipt_sentinel.evals.eval_commands import (
    EvalPaths,
    command_evals_run,
    command_promote_baseline,
    command_promote_case,
)
from receipt_sentinel.evals.golden_case import load_golden_cases
from receipt_sentinel.evals.run_store import list_recent_runs, load_run_json


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


def test_regression_against_clean_baseline_is_critical_and_exits_one(tmp_path: Path) -> None:
    p = paths_in(tmp_path)
    command_evals_run(p, fake=True, save=True, slack_webhook=None, report_url=None)
    run_id = list_recent_runs(p.db_path, dataset_version="1", limit=1)[0].run_id
    assert command_promote_baseline(p, run_id) == 0
    code = command_evals_run(p, fake=True, save=True, slack_webhook=None, report_url=None,
                             demo_noise=True)
    assert code == 1
    assert "CRITICAL" in (p.report_path.parent / "summary.md").read_text()


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
