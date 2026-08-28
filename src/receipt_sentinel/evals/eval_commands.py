"""eval_commands

The explicit user actions behind `sentinel evals …`: run + diff + report + alert,
promote a run to baseline, promote a failed receipt into the golden set.

Status:
    None
Issues:
    None
Todo:
    None
"""

import asyncio
import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha1
from pathlib import Path

import yaml

from receipt_sentinel.categorize.anthropic_llm_client import DEFAULT_MODEL_ID, AnthropicLlmClient
from receipt_sentinel.categorize.llm_client import FakeLlmClient, LlmClient, LlmRequest
from receipt_sentinel.categorize.receipt_record import Category
from receipt_sentinel.evals.alerts import build_slack_payload, post_slack_alert
from receipt_sentinel.evals.diff import DiffResult, compute_drift, diff_against_baseline
from receipt_sentinel.evals.golden_case import (
    GOLDEN_DIR,
    GoldenCase,
    load_dataset_version,
    load_golden_cases,
)
from receipt_sentinel.evals.report import render_report_html
from receipt_sentinel.evals.run_store import (
    EvalRun,
    RunStatus,
    list_recent_runs,
    load_run_json,
    save_run,
)
from receipt_sentinel.evals.runner import run_evals
from receipt_sentinel.evals.scoring import load_model_pricing
from receipt_sentinel.evals.thresholds import Thresholds, load_thresholds
from receipt_sentinel.prompts.prompt_config import load_active_prompt

REPO_ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class EvalPaths:
    golden_dir: Path
    baseline_path: Path
    db_path: Path
    runs_dir: Path
    report_path: Path
    thresholds_path: Path
    pricing_path: Path
    active_prompt_path: Path

    @classmethod
    def default(cls, out_dir: Path, *, fake: bool = False) -> "EvalPaths":
        # Fake and real runs never share a baseline — a `--fake` CI smoke run must not
        # diff against a real-model baseline (or vice versa).
        baseline_name = "baseline_fake.json" if fake else "baseline.json"
        return cls(
            golden_dir=GOLDEN_DIR,
            baseline_path=GOLDEN_DIR.parent / baseline_name,
            db_path=out_dir / "runs.db",
            runs_dir=out_dir / "runs",
            report_path=out_dir / "report.html",
            thresholds_path=REPO_ROOT / "config" / "thresholds.yaml",
            pricing_path=REPO_ROOT / "config" / "model_pricing.yaml",
            active_prompt_path=REPO_ROOT / "config" / "active_prompt.yaml",
        )


def _git_sha() -> str:
    try:
        result = subprocess.run(  # noqa: S603,S607 - fixed argv, no user input
            ["git", "rev-parse", "--short", "HEAD"],  # noqa: S607
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


def _noisy_oracle(cases: list[GoldenCase]) -> FakeLlmClient:
    """Answers from labels but deterministically mis-categorises ~6% for demo regressions."""

    def respond(request: LlmRequest) -> str:
        for case in cases:
            if case.receipt_text.strip() in request.user:
                answer = case.expected.model_dump(mode="json")
                if int(sha1(case.id.encode()).hexdigest(), 16) % 17 == 0:  # noqa: S324 - not security
                    answer["category"] = Category.OTHER.value
                return json.dumps(answer)
        return "{}"

    return FakeLlmClient(respond)


def build_client(*, fake: bool, cases: list[GoldenCase]) -> tuple[LlmClient, str]:
    if fake:
        return _noisy_oracle(cases), "fake"
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise SystemExit("ANTHROPIC_API_KEY is not set")
    return AnthropicLlmClient(api_key), DEFAULT_MODEL_ID


def _write_summary(path: Path, run: EvalRun, diff: DiffResult | None) -> None:
    status = diff.status.value if diff else run.status.value
    lines = [
        f"## Receipt Sentinel evals: **{status}**",
        "",
        f"- prompt `{run.prompt_label}` · model `{run.model_id}` · dataset v{run.dataset_version}",
        f"- pass rate {run.pass_rate:.1%}"
        + (f" ({diff.pass_rate_delta_points:+.1f} pts vs baseline)" if diff else " (no baseline)"),
    ]
    if diff:
        lines.append(
            f"- regressions {len(diff.regressions)} · improvements {len(diff.improvements)} · "
            f"adversarial failures {diff.adversarial_failures}"
        )
    lines.append(f"- cost ${run.total_cost_usd:.3f} · p50 {run.p50_latency_ms} ms")
    path.write_text("\n".join(lines) + "\n")


def _diff_for(
    run: EvalRun, paths: EvalPaths, cases: list[GoldenCase], thresholds: Thresholds
) -> DiffResult:
    case_categories = {c.id: c.expected.category.value for c in cases}
    if not paths.baseline_path.exists():
        print(
            f"{run.run_id}: pass rate {run.pass_rate:.1%}; no baseline — run "
            f"`sentinel evals promote-baseline {run.run_id}` to bless it"
        )
        return diff_against_baseline(
            run, run, thresholds=thresholds, case_categories=case_categories
        )
    baseline = load_run_json(paths.baseline_path)
    history = list_recent_runs(
        paths.db_path, dataset_version=run.dataset_version, limit=thresholds.drift_window_runs
    )
    return diff_against_baseline(
        run,
        baseline,
        thresholds=thresholds,
        case_categories=case_categories,
        drift=compute_drift([run, *history], baseline, thresholds),
    )


def command_evals_run(
    paths: EvalPaths, *, fake: bool, save: bool, slack_webhook: str | None, report_url: str | None
) -> int:
    cases = load_golden_cases(paths.golden_dir)
    dataset_version = load_dataset_version(paths.golden_dir)
    thresholds = load_thresholds(paths.thresholds_path)
    client, model_id = build_client(fake=fake, cases=cases)
    run = asyncio.run(
        run_evals(
            cases,
            load_active_prompt(paths.active_prompt_path),
            client,
            model_id=model_id,
            pricing=load_model_pricing(model_id, paths.pricing_path),
            thresholds=thresholds,
            dataset_version=dataset_version,
            git_sha=_git_sha(),
        )
    )
    paths.report_path.parent.mkdir(parents=True, exist_ok=True)
    if run.status is RunStatus.PROVIDER_UNSTABLE:
        print(f"{run.run_id}: PROVIDER_UNSTABLE (error rate {run.error_rate:.0%}) — not stored")
        _write_summary(paths.report_path.parent / "summary.md", run, None)
        return 0
    diff = _diff_for(run, paths, cases, thresholds)
    history_for_chart = list(
        reversed(list_recent_runs(paths.db_path, dataset_version=dataset_version, limit=30))
    ) + [run]
    paths.report_path.write_text(render_report_html(run, diff, cases, history_for_chart))
    _write_summary(paths.report_path.parent / "summary.md", run, diff)
    post_slack_alert(build_slack_payload(run, diff, report_url), slack_webhook)
    if save:
        save_run(run, db_path=paths.db_path, json_dir=paths.runs_dir)
    print(
        f"{run.run_id}: {diff.status.value} pass {run.pass_rate:.1%} "
        f"({diff.pass_rate_delta_points:+.1f} pts) → {paths.report_path}"
    )
    return 1 if diff.status is RunStatus.CRITICAL else 0


def command_promote_baseline(paths: EvalPaths, run_id: str) -> int:
    source = paths.runs_dir / f"{run_id}.json"
    run = load_run_json(source)
    current_version = load_dataset_version(paths.golden_dir)
    if run.dataset_version != current_version:
        print(f"refusing: run is dataset v{run.dataset_version}, golden set is v{current_version}")
        return 1
    shutil.copyfile(source, paths.baseline_path)
    print(f"baseline ← {run_id} (pass rate {run.pass_rate:.1%}); commit {paths.baseline_path}")
    return 0


def command_promote_case(paths: EvalPaths, receipt_text_path: Path, case_id: str) -> int:
    today = datetime.now(UTC).date().isoformat()
    stub = {
        "id": case_id,
        "difficulty": "medium",
        "source": "real_redacted",
        "notes": "STUB LABEL — fill expected by hand, then run the redaction checklist",
        "email_date": today,
        "receipt_text": receipt_text_path.read_text(),
        "expected": {
            "vendor": "FILL_ME",
            "total": "0.00",
            "currency": "USD",
            "date": today,
            "category": "other",
            "line_items": [],
            "confidence": 0.0,
            "injection_detected": False,
        },
    }
    target = paths.golden_dir / "cases" / f"{case_id}.yaml"
    target.write_text(yaml.safe_dump(stub, sort_keys=False, allow_unicode=True))
    print(f"wrote {target}\nnext: label it, then follow scripts/redaction_checklist.md")
    return 0
