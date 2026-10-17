"""eval_commands

The explicit user actions behind `sentinel evals …`: run + diff + report + alert,
compare two models on the golden set, promote a run to baseline, promote a failed
receipt into the golden set.

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
import urllib.error
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from hashlib import sha1
from pathlib import Path

import yaml

from receipt_sentinel.categorize.anthropic_llm_client import DEFAULT_MODEL_ID, AnthropicLlmClient
from receipt_sentinel.categorize.azure_foundry_llm_client import (
    API_KEY_ENV_VAR,
    BASE_URL_ENV_VAR,
    FOUNDRY_PROVIDER_NAME,
    MODEL_ENV_VAR,
    RESOURCE_ENV_VAR,
    build_azure_foundry_llm_client,
)
from receipt_sentinel.categorize.llm_client import FakeLlmClient, LlmClient, LlmRequest
from receipt_sentinel.categorize.receipt_record import Category
from receipt_sentinel.evals.alerts import build_slack_payload, post_slack_alert
from receipt_sentinel.evals.diff import (
    DiffResult,
    compute_drift,
    diff_against_baseline,
    drift_from_pass_rates,
)
from receipt_sentinel.evals.golden_case import (
    GOLDEN_DIR,
    GoldenCase,
    load_dataset_version,
    load_golden_cases,
)
from receipt_sentinel.evals.report import render_report_html
from receipt_sentinel.evals.run_history import (
    HistoryRecord,
    append_history,
    comparable_pass_rates,
    load_history,
    scored_night_from,
    unscored_night,
)
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
from receipt_sentinel.telemetry import TracingLlmClient

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


def _label_oracle(cases: list[GoldenCase], *, noisy: bool) -> FakeLlmClient:
    """Fake client that answers from the golden labels — a perfect model, by construction.

    Args:
        cases: Golden cases whose labels supply the answers.
        noisy: When set, deterministically mis-categorises ~6% of cases, so the demo has a
            regression to catch against a baseline blessed from a clean run.
    """

    def respond(request: LlmRequest) -> str:
        for case in cases:
            if case.receipt_text.strip() in request.user:
                answer = case.expected.model_dump(mode="json")
                mis_categorise = (
                    int(sha1(case.id.encode()).hexdigest(), 16) % 17 == 0  # noqa: S324 - not security
                )
                if noisy and mis_categorise:
                    answer["category"] = Category.OTHER.value
                return json.dumps(answer)
        return "{}"

    return FakeLlmClient(respond)


def build_client(
    *,
    fake: bool,
    demo_noise: bool,
    cases: list[GoldenCase],
    provider: str = "anthropic",
    model_id: str | None = None,
) -> tuple[LlmClient, str]:
    """Pick the client for a run: the label oracle under --fake, else the real model.

    `provider` is "anthropic" (Anthropic API) or "azure" (the same model via Microsoft
    Foundry). `model_id` overrides the provider's default model. Both are ignored under
    --fake.
    """
    if fake:
        return TracingLlmClient(_label_oracle(cases, noisy=demo_noise), "fake"), "fake"
    if demo_noise:
        raise SystemExit("--demo-noise only applies to --fake runs")
    if provider == "azure":
        if not os.environ.get(API_KEY_ENV_VAR) or not (
            os.environ.get(RESOURCE_ENV_VAR) or os.environ.get(BASE_URL_ENV_VAR)
        ):
            raise SystemExit(f"set {API_KEY_ENV_VAR} and {RESOURCE_ENV_VAR} for --provider azure")
        foundry_model = model_id or os.environ.get(MODEL_ENV_VAR, DEFAULT_MODEL_ID)
        return (
            TracingLlmClient(build_azure_foundry_llm_client(), FOUNDRY_PROVIDER_NAME),
            foundry_model,
        )
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise SystemExit("ANTHROPIC_API_KEY is not set")
    return TracingLlmClient(AnthropicLlmClient(api_key), "anthropic"), model_id or DEFAULT_MODEL_ID


def _write_summary(
    path: Path, run: EvalRun, diff: DiffResult | None, *, drift_window_runs: int
) -> None:
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
    if diff and diff.drift:
        lines.append(
            f"- drift {diff.drift.window_runs}-night avg {diff.drift.moving_average_pass_rate:.1%} "
            f"({'DRIFTING' if diff.drift.drifting else 'steady'})"
        )
    elif diff:
        lines.append(
            f"- drift: not judged yet (needs {drift_window_runs} comparable nights against "
            "this baseline, model, and prompt)"
        )
    path.write_text("\n".join(lines) + "\n")


def _write_verdict(out_dir: Path, run: EvalRun, diff: DiffResult | None) -> None:
    """Machine-readable verdict for CI. Written last: its presence means the run finished,
    so a crash after scoring can never leave a PASS behind for the nightly to act on."""
    status = diff.status.value if diff else run.status.value
    (out_dir / "status").write_text(status + "\n")
    (out_dir / "drift").write_text("drifting\n" if _is_drifting(diff) else "steady\n")


def _is_drifting(diff: DiffResult | None) -> bool:
    return bool(diff and diff.drift and diff.drift.drifting)


def golden_set_is_all_synthetic(cases: list[GoldenCase]) -> bool:
    """True while no real (redacted) receipt is in the golden set — the only time a
    receipt-level report may leave this machine."""
    return all(c.source == "synthetic" for c in cases)


def _diff_for(
    run: EvalRun,
    paths: EvalPaths,
    cases: list[GoldenCase],
    thresholds: Thresholds,
    history: list[HistoryRecord] | None = None,
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
    if history is not None:
        # Published nightly history: only nights judged against this baseline count.
        prior = comparable_pass_rates(
            history,
            dataset_version=run.dataset_version,
            baseline_run_id=baseline.run_id,
            model_id=run.model_id,
            prompt_label=run.prompt_label,
        )
        drift = drift_from_pass_rates([run.pass_rate, *prior], baseline.pass_rate, thresholds)
    else:
        recent = list_recent_runs(
            paths.db_path, dataset_version=run.dataset_version, limit=thresholds.drift_window_runs
        )
        drift = compute_drift([run, *recent], baseline, thresholds)
    return diff_against_baseline(
        run, baseline, thresholds=thresholds, case_categories=case_categories, drift=drift
    )


def command_evals_run(
    paths: EvalPaths,
    *,
    fake: bool,
    save: bool,
    slack_webhook: str | None,
    report_url: str | None,
    demo_noise: bool = False,
    provider: str = "anthropic",
    client_override: LlmClient | None = None,
    history_path: Path | None = None,
    public: bool = False,
) -> int:
    """Run the golden evals, diff against the baseline, write the report, alert, store.

    Args:
        paths: Resolved locations for the golden set, baseline, run store, and report.
        fake: Use the label oracle instead of the real model (no API key needed).
        save: Store the run in the local history (never for PROVIDER_UNSTABLE runs).
        slack_webhook: Incoming-webhook URL, or None to skip alerting.
        report_url: URL linked from the Slack alert.
        demo_noise: With `fake`, mis-categorise ~6% of cases so the demo shows a regression.
        provider: "anthropic" or "azure" (Claude via Microsoft Foundry) for real runs.
        client_override: Test-only seam — an LlmClient to use instead of building one, so
            tests can exercise the provider-error and regression merge gates without a
            network call. Never set from the CLI.
        history_path: Published numbers-only nightly history: drift is computed from it and
            this night is appended to it (scored or not).
        public: Outputs leave this machine (CI artifacts): skip the receipt-level report
            unless every golden case is synthetic.

    Returns:
        Process exit code: 1 on a CRITICAL diff or when drifting, else 0.
    """
    cases = load_golden_cases(paths.golden_dir)
    # Read before spending anything: a corrupt history must fail the run up front.
    history = load_history(history_path) if history_path else None
    dataset_version = load_dataset_version(paths.golden_dir)
    thresholds = load_thresholds(paths.thresholds_path)
    client, model_id = build_client(
        fake=fake, demo_noise=demo_noise, cases=cases, provider=provider
    )
    if client_override is not None:
        client = client_override
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
        _write_summary(
            paths.report_path.parent / "summary.md",
            run,
            None,
            drift_window_runs=thresholds.drift_window_runs,
        )
        if history_path:
            reason = f"provider unstable (error rate {run.error_rate:.0%})"
            append_history(history_path, unscored_night(reason, run_url=report_url))
        _write_verdict(paths.report_path.parent, run, None)
        return 0
    diff = _diff_for(run, paths, cases, thresholds, history)
    _write_report(run, diff, cases, paths, public=public)
    _write_summary(
        paths.report_path.parent / "summary.md",
        run,
        diff,
        drift_window_runs=thresholds.drift_window_runs,
    )
    # Store before alerting: a Slack outage must never cost us the run. The stored status
    # is the diff verdict, not run_evals' PASS placeholder.
    if save:
        save_run(replace(run, status=diff.status), db_path=paths.db_path, json_dir=paths.runs_dir)
    if history_path:
        append_history(history_path, scored_night_from(run, diff, run_url=report_url))
    try:
        post_slack_alert(build_slack_payload(run, diff, report_url), slack_webhook)
    except (RuntimeError, urllib.error.URLError, OSError) as err:
        # Never print the webhook URL — it is a credential.
        print(f"slack alert failed ({type(err).__name__}); run was still saved and reported")
    print(
        f"{run.run_id}: {diff.status.value} pass {run.pass_rate:.1%} "
        f"({diff.pass_rate_delta_points:+.1f} pts)"
        + (" DRIFTING" if _is_drifting(diff) else "")
        + f" → {paths.report_path.parent}"
    )
    _write_verdict(paths.report_path.parent, run, diff)
    return 1 if diff.status is RunStatus.CRITICAL or _is_drifting(diff) else 0


def _write_report(
    run: EvalRun, diff: DiffResult, cases: list[GoldenCase], paths: EvalPaths, *, public: bool
) -> None:
    if public and not golden_set_is_all_synthetic(cases):
        print("golden set has real receipts: receipt-level report not written for publishing")
        return
    history_for_chart = list(
        reversed(list_recent_runs(paths.db_path, dataset_version=run.dataset_version, limit=30))
    ) + [run]
    paths.report_path.write_text(render_report_html(run, diff, cases, history_for_chart))


def command_record_skip(history_path: Path, *, reason: str, run_url: str | None) -> int:
    """Record a night that produced no score (no key, rejected key) so it is never a gap."""
    append_history(history_path, unscored_night(reason, run_url=run_url))
    print(f"recorded not-scored night: {reason}")
    return 0


def _pct_change(old: float, new: float) -> str:
    return f"{(new - old) / old:+.0%}" if old else "n/a"


def _write_comparison_summary(
    path: Path, incumbent: EvalRun, candidate: EvalRun, diff: DiffResult
) -> None:
    lines = [
        f"## Receipt Sentinel model comparison: **{diff.status.value}**",
        "",
        f"- incumbent `{incumbent.model_id}` → candidate `{candidate.model_id}` · "
        f"prompt `{candidate.prompt_label}` · dataset v{candidate.dataset_version}",
        f"- pass rate {incumbent.pass_rate:.1%} → {candidate.pass_rate:.1%} "
        f"({diff.pass_rate_delta_points:+.1f} pts)",
        f"- regressions {len(diff.regressions)} · improvements {len(diff.improvements)} · "
        f"adversarial failures {diff.adversarial_failures}",
        # Shown, never gated on: a cheaper-but-worse model still fails the quality gate.
        f"- cost ${incumbent.total_cost_usd:.3f} → ${candidate.total_cost_usd:.3f} "
        f"({_pct_change(incumbent.total_cost_usd, candidate.total_cost_usd)}) · "
        f"p50 {incumbent.p50_latency_ms} → {candidate.p50_latency_ms} ms",
    ]
    path.write_text("\n".join(lines) + "\n")


def _run_one_model(
    paths: EvalPaths,
    cases: list[GoldenCase],
    thresholds: Thresholds,
    client: LlmClient,
    model_id: str,
) -> EvalRun:
    return asyncio.run(
        run_evals(
            cases,
            load_active_prompt(paths.active_prompt_path),
            client,
            model_id=model_id,
            pricing=load_model_pricing(model_id, paths.pricing_path),
            thresholds=thresholds,
            dataset_version=load_dataset_version(paths.golden_dir),
            git_sha=_git_sha(),
        )
    )


def command_compare_models(
    paths: EvalPaths,
    *,
    incumbent_model: str = DEFAULT_MODEL_ID,
    candidate_model: str = DEFAULT_MODEL_ID,
    incumbent_provider: str = "anthropic",
    candidate_provider: str = "anthropic",
    fake: bool = False,
    demo_noise: bool = False,
) -> int:
    """Model migration gate: run two models on the golden set and diff candidate vs incumbent.

    The incumbent's fresh run stands in for the baseline, so the verdict uses the same
    thresholds as `evals run`. Cost and latency are reported but never gate. Neither run
    is stored — a comparison must not feed run history, drift, or the baseline.

    Args:
        paths: Resolved locations; the report and summary.md go next to `report_path`.
        incumbent_model: Model in use today (a model_pricing.yaml key).
        candidate_model: Model proposed to replace it (a model_pricing.yaml key).
        incumbent_provider: "anthropic" or "azure" for the incumbent.
        candidate_provider: "anthropic" or "azure" for the candidate.
        fake: Compare label oracles instead of real models (no API key needed).
        demo_noise: With `fake`, make the candidate mis-categorise ~6% of cases.

    Returns:
        Process exit code: 1 on a CRITICAL diff, else 0.
    """
    cases = load_golden_cases(paths.golden_dir)
    thresholds = load_thresholds(paths.thresholds_path)
    if not fake:
        # Fail on a missing price before the incumbent run spends any money.
        for model_id in (incumbent_model, candidate_model):
            try:
                load_model_pricing(model_id, paths.pricing_path)
            except KeyError as err:
                raise SystemExit(f"{err.args[0]}; add it before comparing") from err
    incumbent_client, incumbent_id = build_client(
        fake=fake,
        demo_noise=False,
        cases=cases,
        provider=incumbent_provider,
        model_id=incumbent_model,
    )
    candidate_client, candidate_id = build_client(
        fake=fake,
        demo_noise=demo_noise,
        cases=cases,
        provider=candidate_provider,
        model_id=candidate_model,
    )
    incumbent = _run_one_model(paths, cases, thresholds, incumbent_client, incumbent_id)
    candidate = _run_one_model(paths, cases, thresholds, candidate_client, candidate_id)
    paths.report_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path = paths.report_path.parent / "summary.md"
    unstable = [r for r in (incumbent, candidate) if r.status is RunStatus.PROVIDER_UNSTABLE]
    if unstable:
        # Same rule as `evals run`: a flaky provider says nothing about the model.
        message = ", ".join(f"{r.model_id} error rate {r.error_rate:.0%}" for r in unstable)
        summary_path.write_text(
            f"## Receipt Sentinel model comparison: **PROVIDER_UNSTABLE**\n\n- {message}\n"
        )
        print(f"PROVIDER_UNSTABLE ({message}) — no verdict")
        return 0
    diff = diff_against_baseline(
        candidate,
        incumbent,
        thresholds=thresholds,
        case_categories={c.id: c.expected.category.value for c in cases},
    )
    paths.report_path.write_text(
        render_report_html(candidate, diff, cases, [incumbent, candidate], incumbent=incumbent)
    )
    _write_comparison_summary(summary_path, incumbent, candidate, diff)
    print(
        f"{diff.status.value}: {incumbent.model_id} {incumbent.pass_rate:.1%} → "
        f"{candidate.model_id} {candidate.pass_rate:.1%} "
        f"({diff.pass_rate_delta_points:+.1f} pts) → {paths.report_path}"
    )
    return 1 if diff.status is RunStatus.CRITICAL else 0


def command_promote_baseline(paths: EvalPaths, run_id: str) -> int:
    source = paths.runs_dir / f"{run_id}.json"
    if not source.exists():
        print(f"no stored run {run_id} in {paths.runs_dir}")
        return 1
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
