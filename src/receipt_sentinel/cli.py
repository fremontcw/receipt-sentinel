"""cli

Command-line entry for Receipt Sentinel:
`sentinel evals run|compare-models|promote-baseline|promote`.

Status:
    None
Issues:
    None
Todo:
    None
"""

import argparse
import os
import sys
from pathlib import Path

import anthropic

from receipt_sentinel.categorize.anthropic_llm_client import DEFAULT_MODEL_ID
from receipt_sentinel.evals.dashboard import command_build_dashboard
from receipt_sentinel.evals.eval_commands import (
    EvalPaths,
    command_compare_models,
    command_evals_run,
    command_promote_baseline,
    command_promote_case,
    command_record_skip,
)
from receipt_sentinel.telemetry import configure_tracing


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sentinel")
    top = parser.add_subparsers(dest="group", required=True)
    evals = top.add_parser("evals", help="regression harness").add_subparsers(
        dest="command", required=True
    )

    run = evals.add_parser("run", help="run golden evals, diff vs baseline, write report")
    run.add_argument("--fake", action="store_true", help="use FakeLlmClient (no API key)")
    run.add_argument(
        "--demo-noise",
        action="store_true",
        help="with --fake: mis-categorise ~6%% of cases so the demo shows a regression",
    )
    run.add_argument(
        "--provider",
        choices=("anthropic", "azure"),
        default="anthropic",
        help="where the real model runs: Anthropic API, or Claude via Microsoft Foundry",
    )
    run.add_argument("--no-save", action="store_true", help="do not store the run")
    run.add_argument("--out", type=Path, default=Path("runs"))
    run.add_argument("--report-url", default=None)
    run.add_argument(
        "--history",
        type=Path,
        default=None,
        help="numbers-only nightly history (JSON Lines): drift from it, append this night",
    )
    run.add_argument(
        "--public",
        action="store_true",
        help="outputs get published: no receipt-level report unless the golden set is synthetic",
    )

    dash = evals.add_parser("dashboard", help="render the public run-history page")
    dash.add_argument("--history", type=Path, required=True)
    dash.add_argument("--out", type=Path, required=True)
    dash.add_argument("--repo-url", required=True)

    skip = evals.add_parser("record-skip", help="record a night that produced no score")
    skip.add_argument("--history", type=Path, required=True)
    skip.add_argument("--reason", required=True)
    skip.add_argument("--run-url", default=None)

    compare = evals.add_parser(
        "compare-models", help="model migration gate: diff a candidate model vs the incumbent"
    )
    compare.add_argument("--incumbent", default=DEFAULT_MODEL_ID, help="model in use today")
    compare.add_argument("--candidate", default=DEFAULT_MODEL_ID, help="proposed replacement")
    for role in ("incumbent", "candidate"):
        compare.add_argument(
            f"--{role}-provider", choices=("anthropic", "azure"), default="anthropic"
        )
    compare.add_argument("--fake", action="store_true", help="compare label oracles (no API key)")
    compare.add_argument(
        "--demo-noise",
        action="store_true",
        help="with --fake: the candidate mis-categorises ~6%% of cases",
    )
    compare.add_argument("--out", type=Path, default=Path("runs/compare"))

    promote = evals.add_parser("promote-baseline", help="bless a stored run as the baseline")
    promote.add_argument("run_id")
    promote.add_argument("--fake", action="store_true", help="bless into the fake baseline")
    promote.add_argument("--out", type=Path, default=Path("runs"))

    case = evals.add_parser("promote", help="turn a failed receipt into a stub golden case")
    case.add_argument("receipt_text", type=Path)
    case.add_argument("--id", required=True, dest="case_id")
    return parser


# Exit 1 means a CRITICAL regression; 2 means the run could not happen at all.
EXIT_PROVIDER_REJECTED = 2


def main(argv: list[str] | None = None) -> int:
    try:
        return _dispatch(_build_parser().parse_args(argv))
    except anthropic.APIStatusError as err:
        # The API's message names the problem (key, workspace, model) and never the key.
        print(
            f"provider rejected the request (HTTP {err.status_code}): {err.message}",
            file=sys.stderr,
        )
        return EXIT_PROVIDER_REJECTED


def _dispatch(args: argparse.Namespace) -> int:
    if args.command == "dashboard":
        return command_build_dashboard(args.history, args.out, repo_url=args.repo_url)
    if args.command == "record-skip":
        return command_record_skip(args.history, reason=args.reason, run_url=args.run_url)
    if args.command == "promote":
        default = EvalPaths.default(Path("runs"))
        return command_promote_case(default, args.receipt_text, args.case_id)
    paths = EvalPaths.default(args.out, fake=args.fake)
    if args.command in ("run", "compare-models"):
        shutdown_tracing = configure_tracing()
        try:
            if args.command == "compare-models":
                return command_compare_models(
                    paths,
                    incumbent_model=args.incumbent,
                    candidate_model=args.candidate,
                    incumbent_provider=args.incumbent_provider,
                    candidate_provider=args.candidate_provider,
                    fake=args.fake,
                    demo_noise=args.demo_noise,
                )
            return command_evals_run(
                paths,
                fake=args.fake,
                save=not args.no_save,
                slack_webhook=os.environ.get("SLACK_WEBHOOK_URL"),
                report_url=args.report_url,
                demo_noise=args.demo_noise,
                provider=args.provider,
                history_path=args.history,
                public=args.public,
            )
        finally:
            shutdown_tracing()
    return command_promote_baseline(paths, args.run_id)
