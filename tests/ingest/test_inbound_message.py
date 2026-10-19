import time
from datetime import UTC, datetime

from receipt_sentinel.ingest.inbound_message import parse_inbound_message, parse_message_headers
from tests.ingest.email_builders import build_eml, gmail_dmarc_pass


def test_header_parse_does_not_touch_the_body() -> None:
    raw = build_eml("a@shop.com", subject="Order 42", body="A" * 5_000_000)
    started = time.monotonic()
    headers = parse_message_headers(raw)
    assert time.monotonic() - started < 1.0
    assert headers.subject == "Order 42"
    assert headers.received_at == datetime(2026, 8, 17, 10, 0, tzinfo=UTC)


def test_authentication_results_kept_in_order() -> None:
    raw = build_eml(
        "a@shop.com", auth_results=[gmail_dmarc_pass("shop.com"), "evil.io; dmarc=pass"]
    )
    results = parse_message_headers(raw).authentication_results
    assert results[0].startswith("mx.google.com;")
    assert results[1].startswith("evil.io;")


def test_attachments_carry_bytes_and_declared_mime() -> None:
    raw = build_eml("a@shop.com", attachments=[("receipt.pdf", b"%PDF-1.7 data")])
    message = parse_inbound_message(raw, sender="a@shop.com")
    assert message.sender == "a@shop.com"
    assert len(message.attachments) == 1
    attachment = message.attachments[0]
    assert attachment.filename == "receipt.pdf"
    assert attachment.data == b"%PDF-1.7 data"
    assert attachment.declared_mime == "application/octet-stream"


def test_filename_path_is_stripped() -> None:
    raw = build_eml(
        "a@shop.com", attachments=[("../../etc/passwd", b"x"), ("..\\..\\win.ini", b"y")]
    )
    names = [a.filename for a in parse_inbound_message(raw, sender="a@shop.com").attachments]
    assert names == ["passwd", "win.ini"]


def test_body_text_is_not_an_attachment() -> None:
    raw = build_eml("a@shop.com")
    assert parse_inbound_message(raw, sender="a@shop.com").attachments == ()
