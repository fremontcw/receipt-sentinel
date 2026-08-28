"""cli

Command-line entry for Receipt Sentinel: `sentinel evals run|promote-baseline|promote`.

Status:
    None
Issues:
    None
Todo:
    None
"""

import argparse
import os
from pathlib import Path

from receipt_sentinel.evals.eval_commands import (
    EvalPaths,
    command_evals_run,
    command_promote_baseline,
    command_promote_case,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sentinel")
    top = parser.add_subparsers(dest="group", required=True)
    evals = top.add_parser("evals", help="regression harness").add_subparsers(
        dest="command", required=True
    )

    run = evals.add_parser("run", help="run golden evals, diff vs baseline, write report")
    run.add_argument("--fake", action="store_true", help="use FakeLlmClient (no API key)")
    run.add_argument("--no-save", action="store_true", help="do not store the run")
    run.add_argument("--out", type=Path, default=Path("runs"))
    run.add_argument("--report-url", default=None)

    promote = evals.add_parser("promote-baseline", help="bless a stored run as the baseline")
    promote.add_argument("run_id")
    promote.add_argument("--fake", action="store_true", help="bless into the fake baseline")
    promote.add_argument("--out", type=Path, default=Path("runs"))

    case = evals.add_parser("promote", help="turn a failed receipt into a stub golden case")
    case.add_argument("receipt_text", type=Path)
    case.add_argument("--id", required=True, dest="case_id")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "promote":
        default = EvalPaths.default(Path("runs"))
        return command_promote_case(default, args.receipt_text, args.case_id)
    paths = EvalPaths.default(args.out, fake=args.fake)
    if args.command == "run":
        return command_evals_run(
            paths,
            fake=args.fake,
            save=not args.no_save,
            slack_webhook=os.environ.get("SLACK_WEBHOOK_URL"),
            report_url=args.report_url,
        )
    return command_promote_baseline(paths, args.run_id)
