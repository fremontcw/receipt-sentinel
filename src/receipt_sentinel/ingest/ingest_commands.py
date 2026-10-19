"""ingest_commands

The explicit user actions behind `sentinel ingest` (read-only: prints a report, stores
nothing) and `sentinel allowlist add` (the one write, to config/allowlist.yaml).

Status:
    None
Issues:
    None
Todo:
    None
"""

import asyncio
import re
import sys
from contextlib import closing
from datetime import date
from pathlib import Path

import yaml

from receipt_sentinel.checklist.receipt_store import open_receipt_store, record_allowlist_event
from receipt_sentinel.evals.eval_commands import REPO_ROOT, build_client
from receipt_sentinel.evals.golden_case import load_golden_cases
from receipt_sentinel.evals.golden_fixture_text import extract_fixture_cases
from receipt_sentinel.ingest.inbound_message import MessageSource
from receipt_sentinel.ingest.ingest_pipeline import IngestReport, ingest_messages
from receipt_sentinel.ingest.sender_allowlist import load_sender_allowlist, parse_single_sender
from receipt_sentinel.prompts.prompt_config import load_active_prompt
from receipt_sentinel.terminal_safe_text import render_one_line as _field
from receipt_sentinel.terminal_safe_text import render_terminal_safe

EXIT_BAD_INPUT = 2
_HOSTNAME = re.compile(r"(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}")
ALLOWLIST_FILE_HEADER = """\
# config/allowlist.yaml — senders whose receipt attachments may be opened (spec §4.1).
# Edit with `sentinel allowlist add ADDRESS` or `sentinel allowlist add --domain DOMAIN`.
# addresses: exact From addresses (preferred). domains: exact From domains only; a
#   subdomain must be listed on its own.
# trusted_authserv_id: the receiving server whose Authentication-Results header is
#   believed; an allowlisted sender also needs its DMARC pass for the From domain.
"""


def command_ingest(
    source: MessageSource,
    *,
    allowlist_path: Path,
    since: date,
    fake: bool,
    provider: str,
    max_messages: int,
) -> int:
    """Ingest messages and print the report. Writes nothing.

    Args:
        source: Message source (.eml directory or Gmail).
        allowlist_path: The sender allowlist YAML.
        since: Earliest message date.
        fake: Use the keyless golden-label model (knows only golden fixture attachments).
        provider: "anthropic" or "azure" for the real model.
        max_messages: Cap on allowed messages processed.

    Returns:
        0. Bad setup (missing key, missing Tesseract) raises and the CLI reports it.
    """
    report = run_ingest(
        source,
        allowlist_path=allowlist_path,
        since=since,
        fake=fake,
        provider=provider,
        max_messages=max_messages,
    )
    print(render_terminal_safe(format_ingest_report(report, since=since)))
    return 0


def run_ingest(
    source: MessageSource,
    *,
    allowlist_path: Path,
    since: date,
    until: date | None = None,
    fake: bool,
    provider: str,
    max_messages: int,
) -> IngestReport:
    """Ingest with the configured model: the shared core of `ingest` and `checklist propose`."""
    cases = extract_fixture_cases(load_golden_cases())[0] if fake else load_golden_cases()
    client, model_id = build_client(fake=fake, demo_noise=False, cases=cases, provider=provider)
    return asyncio.run(
        ingest_messages(
            source,
            load_sender_allowlist(allowlist_path),
            since=since,
            until=until,
            prompt=load_active_prompt(REPO_ROOT / "config" / "active_prompt.yaml"),
            client=client,
            model_id=model_id,
            max_messages=max_messages,
        )
    )


def format_ingest_report(report: IngestReport, *, since: date) -> str:
    """Plain-text report: receipts, skipped attachments, unknown senders (metadata only).
    Every sender-, file- or model-supplied value goes through _field (render_one_line)."""
    lines = [f"Ingested {len(report.receipts)} receipt(s) from messages since {since}"]
    for receipt in report.receipts:
        record = receipt.record
        verdict = (
            "valid"
            if receipt.validation.passed
            else "NEEDS REVIEW: "
            + "; ".join(_field(rule.detail) for rule in receipt.validation.rules if not rule.passed)
        )
        lines.append(
            f"  {_field(record.vendor)} · {record.total} {_field(record.currency)} · "
            f"{record.category} · {record.date} · {receipt.extraction_method} · {verdict}"
        )
        lines.append(
            f"    from {_field(receipt.sender)} · "
            f"{_field(receipt.message_id)}/{_field(receipt.filename)}"
        )
        if record.injection_detected:
            lines.append("    injection attempt flagged: instructions in the receipt were ignored")
    lines.append(f"Skipped {len(report.skipped)} attachment(s)")
    lines += [
        f"  {_field(s.message_id)}/{_field(s.filename)} from {_field(s.sender)}: {_field(s.reason)}"
        for s in report.skipped
    ]
    lines.append(f"Unknown senders, never opened: {len(report.unknown_senders)}")
    for unknown in report.unknown_senders:
        subjects = " | ".join(_field(subject) for subject in unknown.subjects)
        lines.append(
            f"  {_field(unknown.address)} · {unknown.message_count} message(s) · "
            f"{unknown.reason} · subjects: {subjects}"
        )
    return "\n".join(lines)


def command_allowlist_add(
    allowlist_path: Path, *, address: str | None, domain: str | None, store_path: Path
) -> int:
    """Add one exact address or exact domain to the allowlist file (an explicit user action).

    Records the addition in the store's allowlist_events table.

    Returns:
        0 when written (or already present), 2 when the entry is not a bare address or
        a valid hostname; the file and the store are then left untouched.
    """
    entry_kind, entry = ("addresses", address) if address is not None else ("domains", domain)
    value = (entry or "").strip().lower()
    valid = (
        parse_single_sender(value) == value if entry_kind == "addresses" else _is_hostname(value)
    )
    if not valid:
        hint = (
            "a bare address like receipts@shop.com"
            if entry_kind == "addresses"
            else ("a hostname like shop.com")
        )
        print(f"not added: {render_terminal_safe(entry or '')!r} is not {hint}", file=sys.stderr)
        return EXIT_BAD_INPUT
    allowlist = load_sender_allowlist(allowlist_path)
    entries = {"addresses": set(allowlist.addresses), "domains": set(allowlist.domains)}
    if value in entries[entry_kind]:
        print(f"{value} is already on the allowlist")
        return 0
    entries[entry_kind].add(value)
    data = {
        "addresses": sorted(entries["addresses"]),
        "domains": sorted(entries["domains"]),
        "trusted_authserv_id": allowlist.trusted_authserv_id,
    }
    allowlist_path.write_text(ALLOWLIST_FILE_HEADER + yaml.safe_dump(data, sort_keys=False))
    with closing(open_receipt_store(store_path)) as conn, conn:
        record_allowlist_event(conn, entry_kind=entry_kind, entry=value)
    print(f"added {value} to {entry_kind} in {allowlist_path}")
    return 0


def _is_hostname(value: str) -> bool:
    return _HOSTNAME.fullmatch(value) is not None
