"""cli

Command-line entry for Receipt Sentinel:
`sentinel evals run|compare-models|promote-baseline|promote`, `sentinel ingest`,
`sentinel allowlist add`, `sentinel checklist propose|confirm`.

Status:
    None
Issues:
    None
Todo:
    None
"""

import argparse
import os
import re
import sys
from datetime import date, timedelta
from pathlib import Path

import anthropic

from receipt_sentinel.categorize.anthropic_llm_client import DEFAULT_MODEL_ID
from receipt_sentinel.checklist.checklist_commands import (
    command_checklist_confirm,
    command_checklist_propose,
)
from receipt_sentinel.checklist.receipt_store import DEFAULT_STORE_PATH
from receipt_sentinel.evals.dashboard import command_build_dashboard
from receipt_sentinel.evals.eval_commands import (
    REPO_ROOT,
    EvalPaths,
    command_compare_models,
    command_evals_run,
    command_promote_baseline,
    command_promote_case,
    command_record_skip,
)
from receipt_sentinel.extract.extracted_document import OcrUnavailable
from receipt_sentinel.ingest.eml_directory_source import EmlDirectorySource
from receipt_sentinel.ingest.gmail_source import GmailSetupError, GmailSource, build_gmail_service
from receipt_sentinel.ingest.inbound_message import MessageSource
from receipt_sentinel.ingest.ingest_commands import command_allowlist_add, command_ingest
from receipt_sentinel.telemetry import configure_tracing

DEFAULT_INGEST_DAYS = 30
DEFAULT_ALLOWLIST_PATH = REPO_ROOT / "config" / "allowlist.yaml"
DEFAULT_CHECKLIST_DIR = REPO_ROOT / "runs" / "checklists"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sentinel")
    top = parser.add_subparsers(dest="group", required=True)
    _add_ingest_parsers(top)
    _add_checklist_parsers(top)
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


def _add_ingest_parsers(top: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    ingest = top.add_parser("ingest", help="read receipt emails (allowlisted senders only)")
    source = ingest.add_mutually_exclusive_group(required=True)
    source.add_argument("--eml", type=Path, help="directory of .eml files")
    source.add_argument("--gmail", action="store_true", help="your Gmail, read-only")
    ingest.add_argument("--reauth", action="store_true", help="with --gmail: sign in again")
    ingest.add_argument("--gmail-credentials", type=Path, default=REPO_ROOT / "credentials.json")
    ingest.add_argument("--gmail-token", type=Path, default=REPO_ROOT / "token.json")
    ingest.add_argument(
        "--since",
        type=date.fromisoformat,
        default=date.today() - timedelta(days=DEFAULT_INGEST_DAYS),
        help=f"earliest message date, YYYY-MM-DD (default: {DEFAULT_INGEST_DAYS} days ago)",
    )
    ingest.add_argument("--fake", action="store_true", help="keyless golden-label model")
    ingest.add_argument("--provider", choices=("anthropic", "azure"), default="anthropic")
    ingest.add_argument("--max-messages", type=int, default=50, help="model spend bound")
    ingest.add_argument("--allowlist", type=Path, default=DEFAULT_ALLOWLIST_PATH)

    allowlist = top.add_parser("allowlist", help="trusted receipt senders").add_subparsers(
        dest="command", required=True
    )
    add = allowlist.add_parser("add", help="allow one exact address or exact domain")
    target = add.add_mutually_exclusive_group(required=True)
    target.add_argument("address", nargs="?", help="exact sender address")
    target.add_argument("--domain", help="exact sender domain (subdomains not included)")
    add.add_argument("--allowlist", type=Path, default=DEFAULT_ALLOWLIST_PATH)
    add.add_argument("--db", type=Path, default=DEFAULT_STORE_PATH, help="local receipt store")


def _month(value: str) -> str:
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", value):
        raise argparse.ArgumentTypeError(f"{value!r} is not a month like 2026-08")
    return value


def _add_checklist_parsers(top: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    checklist = top.add_parser("checklist", help="monthly checklist: propose, then confirm")
    commands = checklist.add_subparsers(dest="command", required=True)
    propose = commands.add_parser("propose", help="ingest a month and write a proposal")
    propose.add_argument("--month", type=_month, required=True, help="YYYY-MM")
    source = propose.add_mutually_exclusive_group(required=True)
    source.add_argument("--eml", type=Path, help="directory of .eml files")
    source.add_argument("--gmail", action="store_true", help="your Gmail, read-only")
    propose.add_argument("--reauth", action="store_true", help="with --gmail: sign in again")
    propose.add_argument("--gmail-credentials", type=Path, default=REPO_ROOT / "credentials.json")
    propose.add_argument("--gmail-token", type=Path, default=REPO_ROOT / "token.json")
    propose.add_argument("--fake", action="store_true", help="keyless golden-label model")
    propose.add_argument("--provider", choices=("anthropic", "azure"), default="anthropic")
    propose.add_argument("--max-messages", type=int, default=50, help="model spend bound")
    propose.add_argument("--allowlist", type=Path, default=DEFAULT_ALLOWLIST_PATH)
    confirm = commands.add_parser("confirm", help="store the receipts you accepted")
    confirm.add_argument("proposal_id")
    choice = confirm.add_mutually_exclusive_group(required=True)
    choice.add_argument("--decisions", type=Path, help="file saved by the review page")
    choice.add_argument("--accept", help="comma-separated item ids, e.g. r01,r03")
    for command in (propose, confirm):
        command.add_argument("--db", type=Path, default=DEFAULT_STORE_PATH, help="local store")
        command.add_argument("--out", type=Path, default=DEFAULT_CHECKLIST_DIR)


def _message_source(args: argparse.Namespace) -> MessageSource:
    if args.gmail:
        return GmailSource(
            build_gmail_service(args.gmail_credentials, args.gmail_token, reauth=args.reauth)
        )
    return EmlDirectorySource(args.eml)


def _dispatch_checklist(args: argparse.Namespace) -> int:
    if args.command == "confirm":
        accept = (
            tuple(i.strip() for i in args.accept.split(",") if i.strip()) if args.accept else None
        )
        return command_checklist_confirm(
            args.proposal_id,
            out_dir=args.out,
            store_path=args.db,
            decisions_path=args.decisions,
            accept=accept,
        )
    return command_checklist_propose(
        _message_source(args),
        month=args.month,
        allowlist_path=args.allowlist,
        store_path=args.db,
        out_dir=args.out,
        fake=args.fake,
        provider=args.provider,
        max_messages=args.max_messages,
    )


def _dispatch_ingest(args: argparse.Namespace) -> int:
    if args.group == "allowlist":
        return command_allowlist_add(
            args.allowlist, address=args.address, domain=args.domain, store_path=args.db
        )
    return command_ingest(
        _message_source(args),
        allowlist_path=args.allowlist,
        since=args.since,
        fake=args.fake,
        provider=args.provider,
        max_messages=args.max_messages,
    )


# Exit 1 means a CRITICAL regression; 2 means the run could not happen at all (provider
# rejected the request, OCR is not installed, or Gmail sign-in needs attention).
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
    except (OcrUnavailable, GmailSetupError) as err:
        print(str(err), file=sys.stderr)
        return EXIT_PROVIDER_REJECTED


def _dispatch(args: argparse.Namespace) -> int:
    if args.group in ("ingest", "allowlist"):
        return _dispatch_ingest(args)
    if args.group == "checklist":
        return _dispatch_checklist(args)
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
