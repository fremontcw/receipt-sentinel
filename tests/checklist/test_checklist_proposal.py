from datetime import date
from decimal import Decimal
from pathlib import Path

from receipt_sentinel.categorize.receipt_record import Category
from receipt_sentinel.checklist.checklist_proposal import (
    build_checklist_proposal,
    load_proposal,
    save_proposal,
)
from receipt_sentinel.checklist.receipt_store import open_receipt_store, receipt_dedup_key
from receipt_sentinel.ingest.ingest_pipeline import IngestReport, SkippedAttachment, UnknownSender
from tests.checklist.proposal_builders import ingested

MONTH = "2026-08"


def _report(*receipts: object, **extra: object) -> IngestReport:
    return IngestReport(
        receipts=tuple(receipts),  # type: ignore[arg-type]
        skipped=tuple(extra.get("skipped", ())),  # type: ignore[call-overload]
        unknown_senders=tuple(extra.get("unknown", ())),  # type: ignore[call-overload]
    )


def test_only_receipts_dated_in_the_month_are_kept() -> None:
    report = _report(
        ingested(day=date(2026, 7, 31)),
        ingested(day=date(2026, 8, 1)),
        ingested(day=date(2026, 9, 1)),
    )
    proposal = build_checklist_proposal(report, month=MONTH, conn=None)
    assert [item.receipt.record.date for item in proposal.items] == [date(2026, 8, 1)]
    assert proposal.items[0].item_id == "r01"


def test_totals_are_per_category_and_currency() -> None:
    report = _report(
        ingested("A", "10.10"),
        ingested("B", "2.25", day=date(2026, 8, 18)),
        ingested("C", "7.00", currency="EUR"),
    )
    totals = {
        (t.category, t.currency): t.total
        for t in build_checklist_proposal(report, month=MONTH, conn=None).category_totals
    }
    assert totals == {("dining", "USD"): Decimal("12.35"), ("dining", "EUR"): Decimal("7.00")}


def test_flags_validation_low_confidence_and_injection() -> None:
    report = _report(ingested("Shop", "12.50", items_sum="10.00", confidence=0.62, injection=True))
    (item,) = build_checklist_proposal(report, month=MONTH, conn=None).items
    assert any(flag.startswith("needs review: items sum 10.00") for flag in item.flags)
    assert "low confidence 0.62" in item.flags
    assert "injection attempt flagged" in item.flags


def test_clean_receipt_has_no_flags() -> None:
    (item,) = build_checklist_proposal(_report(ingested()), month=MONTH, conn=None).items
    assert item.flags == ()


def test_duplicate_against_store_and_within_proposal(tmp_path: Path) -> None:
    conn = open_receipt_store(tmp_path / "store.db")
    with conn:
        conn.execute(
            "INSERT INTO checklists (proposal_id, month, confirmed_at, confirmation,"
            " accepted_count, rejected_count) VALUES ('old', '2026-07', 'x', 'cli-ids', 1, 0)"
        )
        conn.execute(
            "INSERT INTO receipts (checklist_id, dedup_key, vendor, total, currency, receipt_date,"
            " category, line_items_json, confidence, model_id, prompt_version,"
            " source_message_id, source_filename, validation_passed)"
            " VALUES (1, ?, 'Old', '5', 'USD', '2026-08-02', 'other', '[]', 1, 'f', '1', 'm',"
            " 'f.pdf', 1)",
            (receipt_dedup_key("Old Shop", Decimal("5"), date(2026, 8, 2)),),
        )
    report = _report(
        ingested("Old Shop", "5.00", day=date(2026, 8, 2)),
        ingested("Twice", "3.00", day=date(2026, 8, 3), message_id="m1"),
        ingested("TWICE!", "3", day=date(2026, 8, 3), message_id="m2"),
    )
    items = build_checklist_proposal(report, month=MONTH, conn=conn).items
    assert "duplicate of confirmed receipt from 2026-08-02" in items[0].flags
    assert items[1].flags == ()
    assert "duplicate of r02 in this proposal" in items[2].flags


def test_unknown_senders_and_skipped_are_carried() -> None:
    unknown = UnknownSender("x@y.com", 2, ("Hi",), "not on allowlist")
    skipped = SkippedAttachment("m9", "a@b.com", "bad.pdf", "only PDF, PNG and JPEG")
    proposal = build_checklist_proposal(
        _report(ingested(), unknown=[unknown], skipped=[skipped]), month=MONTH, conn=None
    )
    assert proposal.unknown_senders == (unknown,) and proposal.skipped == (skipped,)


def test_proposal_id_is_stable_for_the_same_receipts() -> None:
    first = build_checklist_proposal(_report(ingested("A"), ingested("B")), month=MONTH, conn=None)
    again = build_checklist_proposal(_report(ingested("B"), ingested("A")), month=MONTH, conn=None)
    other = build_checklist_proposal(_report(ingested("C")), month=MONTH, conn=None)
    assert first.proposal_id == again.proposal_id != other.proposal_id
    assert first.proposal_id.startswith("2026-08-")


def test_proposal_json_round_trip(tmp_path: Path) -> None:
    unknown = UnknownSender("x@y.com", 2, ("Hi",), "not on allowlist")
    report = _report(
        ingested("A", "10.10", items_sum="9.00", category=Category.HOME), unknown=[unknown]
    )
    proposal = build_checklist_proposal(report, month=MONTH, conn=None)
    path = tmp_path / "proposal.json"
    save_proposal(proposal, path)
    assert load_proposal(path) == proposal
