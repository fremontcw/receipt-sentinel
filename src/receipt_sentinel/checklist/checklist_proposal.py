"""checklist_proposal

A month's ingested receipts as a proposed checklist: items in date order with flags
(needs review, low confidence, duplicate, injection), totals per category and currency,
and the unknown senders and skipped files to know about. A proposal is a file, never a
database row; only `checklist confirm` stores anything.

Status:
    None
Issues:
    None
Todo:
    None
"""

import hashlib
import json
import sqlite3
from dataclasses import asdict, dataclass, replace
from decimal import Decimal
from pathlib import Path

from receipt_sentinel.categorize.receipt_record import ReceiptRecord
from receipt_sentinel.checklist.receipt_store import (
    confirmed_receipt_dates_by_key,
    receipt_dedup_key,
)
from receipt_sentinel.ingest.ingest_pipeline import (
    IngestedReceipt,
    IngestReport,
    SkippedAttachment,
    UnknownSender,
)
from receipt_sentinel.validate.receipt_validation import ValidationResult, ValidationRuleResult

LOW_CONFIDENCE_THRESHOLD = 0.7
PROPOSAL_ID_HASH_CHARS = 8


@dataclass(frozen=True)
class ChecklistItem:
    item_id: str
    dedup_key: str
    receipt: IngestedReceipt
    flags: tuple[str, ...]


@dataclass(frozen=True)
class CategoryTotal:
    category: str
    currency: str
    total: Decimal
    item_ids: tuple[str, ...]


@dataclass(frozen=True)
class ChecklistProposal:
    proposal_id: str
    month: str
    items: tuple[ChecklistItem, ...]
    category_totals: tuple[CategoryTotal, ...]
    unknown_senders: tuple[UnknownSender, ...]
    skipped: tuple[SkippedAttachment, ...]


def build_checklist_proposal(
    report: IngestReport, *, month: str, conn: sqlite3.Connection | None
) -> ChecklistProposal:
    """Propose the month's checklist from an ingest report.

    Args:
        report: What ingest found (receipts, skipped files, unknown senders).
        month: "YYYY-MM"; only receipts whose receipt date is in it are kept.
        conn: The store, read-only, to flag receipts already confirmed; None when there
            is no store yet (duplicates are then checked within the proposal only).
    """
    in_month = sorted(
        (r for r in report.receipts if r.record.date.strftime("%Y-%m") == month),
        key=lambda r: (r.record.date, r.record.vendor, r.message_id, r.filename),
    )
    keys = [receipt_dedup_key(r.record.vendor, r.record.total, r.record.date) for r in in_month]
    confirmed = confirmed_receipt_dates_by_key(conn, keys) if conn is not None else {}
    first_item_by_key: dict[str, str] = {}
    items: list[ChecklistItem] = []
    for index, (receipt, key) in enumerate(zip(in_month, keys, strict=True), start=1):
        item_id = f"r{index:02d}"
        flags = _flags(receipt, key, confirmed, first_item_by_key)
        first_item_by_key.setdefault(key, item_id)
        items.append(ChecklistItem(item_id, key, receipt, flags))
    draft = ChecklistProposal(
        proposal_id="",
        month=month,
        items=tuple(items),
        category_totals=_category_totals(items),
        unknown_senders=report.unknown_senders,
        skipped=report.skipped,
    )
    # The id covers every item's number, key, record and flags: if a re-run renumbers or
    # changes anything, decisions saved against the old proposal no longer match it
    content = json.dumps(proposal_to_json(draft)["items"], sort_keys=True)
    digest = hashlib.sha256(f"{month}\n{content}".encode()).hexdigest()
    return replace(draft, proposal_id=f"{month}-{digest[:PROPOSAL_ID_HASH_CHARS]}")


def _flags(
    receipt: IngestedReceipt,
    key: str,
    confirmed: dict[str, str],
    first_item_by_key: dict[str, str],
) -> tuple[str, ...]:
    flags = [f"needs review: {r.detail}" for r in receipt.validation.rules if not r.passed]
    if receipt.record.confidence < LOW_CONFIDENCE_THRESHOLD:
        flags.append(f"low confidence {receipt.record.confidence:.2f}")
    if key in confirmed:
        flags.append(f"duplicate of confirmed receipt from {confirmed[key]}")
    if key in first_item_by_key:
        flags.append(f"duplicate of {first_item_by_key[key]} in this proposal")
    if receipt.record.injection_detected:
        flags.append("injection attempt flagged")
    return tuple(flags)


def _category_totals(items: list[ChecklistItem]) -> tuple[CategoryTotal, ...]:
    # Never add different currencies together: one row per (category, currency)
    groups: dict[tuple[str, str], list[ChecklistItem]] = {}
    for item in items:
        record = item.receipt.record
        groups.setdefault((record.category.value, record.currency), []).append(item)
    return tuple(
        CategoryTotal(
            category=category,
            currency=currency,
            total=sum((i.receipt.record.total for i in group), Decimal("0")),
            item_ids=tuple(i.item_id for i in group),
        )
        for (category, currency), group in groups.items()
    )


def save_proposal(proposal: ChecklistProposal, path: Path) -> None:
    """Write the proposal as JSON (Decimals as strings, dates as ISO)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(proposal_to_json(proposal), indent=2, ensure_ascii=False))


def proposal_to_json(proposal: ChecklistProposal) -> dict[str, object]:
    return {
        "proposal_id": proposal.proposal_id,
        "month": proposal.month,
        "items": [
            {
                "item_id": item.item_id,
                "dedup_key": item.dedup_key,
                "flags": list(item.flags),
                "receipt": {
                    **{
                        k: v
                        for k, v in asdict(item.receipt).items()
                        if k not in ("record", "validation")
                    },
                    "record": _record_json(item.receipt.record),
                    "validation": [asdict(rule) for rule in item.receipt.validation.rules],
                },
            }
            for item in proposal.items
        ],
        "category_totals": [
            {**asdict(t), "total": _plain(t.total), "item_ids": list(t.item_ids)}
            for t in proposal.category_totals
        ],
        "unknown_senders": [asdict(u) for u in proposal.unknown_senders],
        "skipped": [asdict(s) for s in proposal.skipped],
    }


def _record_json(record: ReceiptRecord) -> dict[str, object]:
    # Plain decimal strings: pydantic writes Decimal("1E+1") as "1E+1", which the page and
    # people both misread
    data = record.model_dump(mode="json")
    data["total"] = _plain(record.total)
    data["line_items"] = [
        {**item, "amount": _plain(line.amount)}
        for item, line in zip(data["line_items"], record.line_items, strict=True)
    ]
    return data


def _plain(amount: Decimal) -> str:
    return format(amount, "f")


def load_proposal(path: Path) -> ChecklistProposal:
    """Read a proposal written by save_proposal."""
    data = json.loads(path.read_text())
    return ChecklistProposal(
        proposal_id=str(data["proposal_id"]),
        month=str(data["month"]),
        items=tuple(_item_from_json(item) for item in data["items"]),
        category_totals=tuple(
            CategoryTotal(t["category"], t["currency"], Decimal(t["total"]), tuple(t["item_ids"]))
            for t in data["category_totals"]
        ),
        unknown_senders=tuple(
            UnknownSender(u["address"], u["message_count"], tuple(u["subjects"]), u["reason"])
            for u in data["unknown_senders"]
        ),
        skipped=tuple(SkippedAttachment(**s) for s in data["skipped"]),
    )


def _item_from_json(item: dict[str, object]) -> ChecklistItem:
    receipt = item["receipt"]
    assert isinstance(receipt, dict)  # noqa: S101 - shape written by save_proposal
    record = ReceiptRecord.model_validate(receipt["record"])
    return ChecklistItem(
        item_id=str(item["item_id"]),
        # Recomputed, never read back: confirm trusts this key to keep duplicates out
        dedup_key=receipt_dedup_key(record.vendor, record.total, record.date),
        flags=tuple(str(flag) for flag in item["flags"]),  # type: ignore[attr-defined]
        receipt=IngestedReceipt(
            message_id=receipt["message_id"],
            sender=receipt["sender"],
            subject=receipt["subject"],
            filename=receipt["filename"],
            record=record,
            validation=ValidationResult(
                tuple(ValidationRuleResult(**rule) for rule in receipt["validation"])
            ),
            extraction_method=receipt["extraction_method"],
        ),
    )
