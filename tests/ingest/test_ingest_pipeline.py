from datetime import date

import pytest

from receipt_sentinel.evals.eval_commands import REPO_ROOT, build_client
from receipt_sentinel.evals.golden_case import GOLDEN_DIR, load_golden_cases
from receipt_sentinel.evals.golden_fixture_text import extract_fixture_cases
from receipt_sentinel.ingest import ingest_pipeline
from receipt_sentinel.ingest.inbound_message import RawMessage
from receipt_sentinel.ingest.ingest_pipeline import IngestReport, ingest_messages
from receipt_sentinel.ingest.sender_allowlist import SenderAllowlist
from receipt_sentinel.prompts.prompt_config import load_active_prompt
from tests.ingest.email_builders import build_eml, gmail_dmarc_pass

ALLOWLIST = SenderAllowlist(
    addresses=frozenset({"receipts@shop.com"}),
    domains=frozenset(),
    trusted_authserv_id="mx.google.com",
)
PDF_CASE = "fixture_pdf_native_0001_dining_easy"


class ListSource:
    def __init__(self, raws: list[bytes]) -> None:
        self.messages = [RawMessage(f"m{i}", raw, None) for i, raw in enumerate(raws)]

    def list_messages(self, since: date) -> list[RawMessage]:
        return self.messages

    def full_message(self, message: RawMessage) -> bytes:
        return message.raw


def _fixture_bytes(case_id: str) -> bytes:
    case = {c.id: c for c in load_golden_cases()}[case_id]
    assert case.fixture is not None
    return (GOLDEN_DIR / case.fixture).read_bytes()


def _allowed_message(attachments: list[tuple[str, bytes]]) -> bytes:
    return build_eml(
        "Shop <receipts@shop.com>",
        date="Tue, 18 Aug 2026 10:00:00 +0000",
        attachments=attachments,
        auth_results=[gmail_dmarc_pass("shop.com")],
    )


async def _ingest(raws: list[bytes], max_messages: int = 50) -> IngestReport:
    cases, _ = extract_fixture_cases(load_golden_cases())
    client, model_id = build_client(fake=True, demo_noise=False, cases=cases)
    return await ingest_messages(
        ListSource(raws),
        ALLOWLIST,
        since=date(2026, 8, 1),
        prompt=load_active_prompt(REPO_ROOT / "config" / "active_prompt.yaml"),
        client=client,
        model_id=model_id,
        max_messages=max_messages,
    )


async def test_allowed_message_attachment_becomes_validated_receipt() -> None:
    expected = {c.id: c for c in load_golden_cases()}[PDF_CASE].expected
    report = await _ingest([_allowed_message([("lunch.pdf", _fixture_bytes(PDF_CASE))])])
    assert len(report.receipts) == 1 and report.skipped == () and report.unknown_senders == ()
    receipt = report.receipts[0]
    assert receipt.sender == "receipts@shop.com" and receipt.filename == "lunch.pdf"
    assert receipt.record.total == expected.total
    assert receipt.extraction_method == "native"
    assert receipt.validation.passed


async def test_unknown_sender_attachments_are_never_parsed_or_extracted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def must_not_run(*args: object, **kwargs: object) -> None:
        raise AssertionError("an unknown sender's message was opened")

    monkeypatch.setattr(ingest_pipeline, "extract_document", must_not_run)
    monkeypatch.setattr(ingest_pipeline, "parse_inbound_message", must_not_run)
    hostile = build_eml(
        "billing@unknown.example",
        subject="Invoice",
        attachments=[("invoice.pdf", b"<html><script>alert(1)</script>")],
    )
    report = await _ingest([hostile])
    assert report.receipts == () and report.skipped == ()
    (unknown,) = report.unknown_senders
    assert (unknown.address, unknown.message_count, unknown.subjects) == (
        "billing@unknown.example",
        1,
        ("Invoice",),
    )
    assert unknown.reason == "not on allowlist"


async def test_unknown_senders_are_grouped_with_counts_and_capped_subjects() -> None:
    raws = [build_eml("spam@x.example", subject=f"Offer {i}") for i in range(7)]
    (unknown,) = (await _ingest(raws)).unknown_senders
    assert unknown.message_count == 7
    assert unknown.subjects == tuple(f"Offer {i}" for i in range(5))


async def test_rejected_attachment_is_skipped_with_reason_not_raised() -> None:
    report = await _ingest([_allowed_message([("fake.pdf", b"<html>not a pdf</html>")])])
    (skipped,) = report.skipped
    assert skipped.filename == "fake.pdf" and "PDF" in skipped.reason
    assert report.receipts == ()


async def test_messages_beyond_max_are_skipped_not_processed() -> None:
    raw = _allowed_message([("lunch.pdf", _fixture_bytes(PDF_CASE))])
    report = await _ingest([raw, raw, raw], max_messages=2)
    assert len(report.receipts) == 2
    assert [s.reason for s in report.skipped] == ["over --max-messages"]


async def test_message_without_attachments_produces_nothing() -> None:
    report = await _ingest([_allowed_message([])])
    assert report == IngestReport(receipts=(), skipped=(), unknown_senders=())


async def test_attachments_beyond_the_per_message_cap_are_skipped() -> None:
    pdf = _fixture_bytes(PDF_CASE)
    report = await _ingest([_allowed_message([(f"r{i}.pdf", pdf) for i in range(7)])])
    assert len(report.receipts) == ingest_pipeline.MAX_ATTACHMENTS_PER_MESSAGE
    assert [s.reason for s in report.skipped] == ["over the per-message attachment cap"] * 2
