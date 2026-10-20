"""checklist_commands

`sentinel checklist propose` (ingest the month, write a proposal and its review page; never
the store) and `sentinel checklist confirm` (the one path into the store, one transaction).

Status:
    None
Issues:
    None
Todo:
    None
"""

import json
import sqlite3
import sys
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Literal

from receipt_sentinel.checklist.checklist_proposal import (
    ChecklistItem,
    ChecklistProposal,
    build_checklist_proposal,
    load_proposal,
    save_proposal,
)
from receipt_sentinel.checklist.checklist_review_page import render_review_page
from receipt_sentinel.checklist.receipt_store import (
    checklist_exists,
    confirmed_receipt_dates_by_key,
    open_receipt_store,
    open_receipt_store_readonly,
)
from receipt_sentinel.ingest.inbound_message import MessageSource
from receipt_sentinel.ingest.ingest_commands import run_ingest
from receipt_sentinel.ingest.ingest_pipeline import OVER_MAX_MESSAGES
from receipt_sentinel.terminal_safe_text import render_one_line

EXIT_REFUSED = 2
# Receipts dated in the month can arrive by email up to a month later; mail after that is
# another month's (and would otherwise spend the --max-messages budget)
LATE_EMAIL_MONTHS = 2
PROPOSAL_FILE = "proposal.json"
REVIEW_PAGE_FILE = "review.html"
Confirmation = Literal["user-reviewed", "cli-ids"]


class ChecklistConfirmRefused(Exception):  # noqa: N818 - a refusal, not a crash
    """Confirm would write something wrong; nothing was written. str() says why."""


@dataclass(frozen=True)
class ChecklistDecisions:
    proposal_id: str
    accepted_ids: tuple[str, ...]
    rejected_ids: tuple[str, ...]
    confirmation: Confirmation


def command_checklist_propose(
    source: MessageSource,
    *,
    month: str,
    allowlist_path: Path,
    store_path: Path,
    out_dir: Path,
    fake: bool,
    provider: str,
    max_messages: int,
) -> int:
    """Propose the month's checklist: write proposal.json + review.html, print a summary.

    Reads the store (read-only, if it exists) only to flag already-confirmed receipts.
    """
    year, month_number = (int(part) for part in month.split("-"))
    since = date(year, month_number, 1)
    report = run_ingest(
        source,
        allowlist_path=allowlist_path,
        since=since,
        until=_first_day_months_later(since, LATE_EMAIL_MONTHS),
        fake=fake,
        provider=provider,
        max_messages=max_messages,
    )
    conn = open_receipt_store_readonly(store_path)
    try:
        proposal = build_checklist_proposal(report, month=month, conn=conn)
    finally:
        if conn is not None:
            conn.close()
    proposal_dir = out_dir / proposal.proposal_id
    save_proposal(proposal, proposal_dir / PROPOSAL_FILE)
    (proposal_dir / REVIEW_PAGE_FILE).write_text(render_review_page(proposal))
    print(format_proposal_summary(proposal, proposal_dir))
    return 0


def _first_day_months_later(day: date, months: int) -> date:
    index = day.year * 12 + day.month - 1 + months
    return date(index // 12, index % 12 + 1, 1)


def format_proposal_summary(proposal: ChecklistProposal, proposal_dir: Path) -> str:
    """Terminal summary; every model- or sender-derived value goes through render_one_line."""
    flagged = sum(1 for item in proposal.items if item.flags)
    lines = [
        f"{proposal.month} checklist proposal {proposal.proposal_id}: "
        f"{len(proposal.items)} receipt(s), {flagged} flagged (totals include flagged items)",
    ]
    lines += [
        f"  {render_one_line(t.category)} · {t.total} {render_one_line(t.currency)} "
        f"({len(t.item_ids)} receipt(s))"
        for t in proposal.category_totals
    ]
    lines += [
        f"  {item.item_id} {render_one_line(item.receipt.record.vendor)}: "
        + "; ".join(render_one_line(flag) for flag in item.flags)
        for item in proposal.items
        if item.flags
    ]
    lines.append(f"Unknown senders, never opened: {len(proposal.unknown_senders)}")
    lines.append(f"Skipped files: {len(proposal.skipped)}")
    over_cap = sum(1 for s in proposal.skipped if s.reason == OVER_MAX_MESSAGES)
    if over_cap:
        lines.append(
            f"  WARNING: {over_cap} message(s) not read: {OVER_MAX_MESSAGES} reached; "
            "this proposal may be missing receipts"
        )
    lines += [
        f"  {render_one_line(s.filename)} from {render_one_line(s.sender)}: "
        f"{render_one_line(s.reason)}"
        for s in proposal.skipped
        if s.reason != OVER_MAX_MESSAGES
    ]
    lines.append(f"Review: open {proposal_dir / REVIEW_PAGE_FILE}")
    lines.append(
        f"Then:   sentinel checklist confirm {proposal.proposal_id} --decisions <downloaded file>"
    )
    return "\n".join(lines)


def load_decisions(path: Path) -> ChecklistDecisions:
    """Read and shape-check a decisions file (from the review page or written by hand)."""
    try:
        data = json.loads(path.read_text())
        accepted, rejected = data["accepted_ids"], data["rejected_ids"]
        confirmation = data["confirmation"]
        valid = (
            isinstance(data["proposal_id"], str)
            and isinstance(accepted, list)
            and isinstance(rejected, list)
            and all(isinstance(i, str) for i in [*accepted, *rejected])
            and confirmation in ("user-reviewed", "cli-ids")
        )
    except (OSError, ValueError, TypeError, KeyError) as err:
        raise ChecklistConfirmRefused(f"{path} is not a valid decisions file") from err
    if not valid:
        raise ChecklistConfirmRefused(f"{path} is not a valid decisions file")
    return ChecklistDecisions(data["proposal_id"], tuple(accepted), tuple(rejected), confirmation)


def confirm_checklist(
    conn: sqlite3.Connection, proposal: ChecklistProposal, decisions: ChecklistDecisions
) -> int:
    """Store the accepted receipts and one checklist row, all or nothing.

    Returns:
        The new checklist's id.

    Raises:
        ChecklistConfirmRefused: The decisions don't fit this proposal, it was already
            confirmed, an accepted item is a flagged duplicate, or the store rejected the
            write. Nothing is written in any of these cases.
    """
    accepted = _check_decisions(conn, proposal, decisions)
    try:
        with conn:  # one transaction: commits on success, rolls back on any exception
            cursor = conn.execute(
                "INSERT INTO checklists (proposal_id, month, confirmed_at, confirmation,"
                " accepted_count, rejected_count) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    proposal.proposal_id,
                    proposal.month,
                    datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
                    decisions.confirmation,
                    len(accepted),
                    len(decisions.rejected_ids),
                ),
            )
            checklist_id = int(cursor.lastrowid or 0)
            for item in accepted:
                _insert_receipt(conn, checklist_id, item)
    except sqlite3.Error as err:
        raise ChecklistConfirmRefused(
            f"the store rejected the write ({err}); nothing saved"
        ) from err
    return checklist_id


def _check_decisions(
    conn: sqlite3.Connection, proposal: ChecklistProposal, decisions: ChecklistDecisions
) -> list[ChecklistItem]:
    if decisions.proposal_id != proposal.proposal_id:
        raise ChecklistConfirmRefused(
            f"decisions are for {decisions.proposal_id}, not {proposal.proposal_id}"
        )
    by_id = {item.item_id: item for item in proposal.items}
    unknown = sorted(set(decisions.accepted_ids + decisions.rejected_ids) - set(by_id))
    if unknown:
        raise ChecklistConfirmRefused(f"unknown item id(s): {', '.join(unknown)}")
    both = sorted(set(decisions.accepted_ids) & set(decisions.rejected_ids))
    if both:
        raise ChecklistConfirmRefused(f"both accepted and rejected: {', '.join(both)}")
    if checklist_exists(conn, proposal.proposal_id):
        raise ChecklistConfirmRefused(f"{proposal.proposal_id} is already confirmed")
    duplicates = [
        i
        for i in decisions.accepted_ids
        if any(f.startswith("duplicate of") for f in by_id[i].flags)
    ]
    if duplicates:
        raise ChecklistConfirmRefused(
            f"accepted item(s) flagged as duplicates: {', '.join(duplicates)}; reject them first"
        )
    # The store may have changed since propose ran (another proposal confirmed meanwhile)
    stored = confirmed_receipt_dates_by_key(
        conn, (by_id[i].dedup_key for i in decisions.accepted_ids)
    )
    now_duplicate = [i for i in decisions.accepted_ids if by_id[i].dedup_key in stored]
    if now_duplicate:
        raise ChecklistConfirmRefused(
            f"already stored since this proposal was made: {', '.join(now_duplicate)}; "
            "propose again"
        )
    return [by_id[i] for i in decisions.accepted_ids]


def _insert_receipt(conn: sqlite3.Connection, checklist_id: int, item: ChecklistItem) -> None:
    receipt = item.receipt
    record = receipt.record
    conn.execute(
        "INSERT INTO receipts (checklist_id, dedup_key, vendor, total, currency, receipt_date,"
        " category, line_items_json, confidence, model_id, prompt_version, source_message_id,"
        " source_filename, validation_passed) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            checklist_id,
            item.dedup_key,
            record.vendor,
            str(record.total),
            record.currency,
            record.date.isoformat(),
            record.category.value,
            json.dumps([li.model_dump(mode="json") for li in record.line_items]),
            record.confidence,
            record.model_id,
            record.prompt_version,
            receipt.message_id,
            receipt.filename,
            int(receipt.validation.passed),
        ),
    )


def command_checklist_confirm(
    proposal_id: str,
    *,
    out_dir: Path,
    store_path: Path,
    decisions_path: Path | None,
    accept: tuple[str, ...] | None,
) -> int:
    """Confirm a proposal from a decisions file or an explicit id list.

    Returns:
        0 when stored, 2 when refused (stderr says why; nothing written).
    """
    try:
        proposal_file = out_dir / proposal_id / PROPOSAL_FILE
        if not proposal_file.is_file():
            raise ChecklistConfirmRefused(f"no proposal at {proposal_file}: run checklist propose")
        proposal = load_proposal(proposal_file)
        if decisions_path is not None:
            decisions = load_decisions(decisions_path)
        else:
            accepted = tuple(accept or ())
            rejected = tuple(i.item_id for i in proposal.items if i.item_id not in accepted)
            decisions = ChecklistDecisions(proposal_id, accepted, rejected, "cli-ids")
        with closing(open_receipt_store(store_path)) as conn:
            checklist_id = confirm_checklist(conn, proposal, decisions)
    except ChecklistConfirmRefused as err:
        print(f"not confirmed: {render_one_line(err)}", file=sys.stderr)
        return EXIT_REFUSED
    print(
        f"confirmed {proposal_id}: {len(decisions.accepted_ids)} receipt(s) stored "
        f"(checklist #{checklist_id}) in {store_path}"
    )
    return 0
