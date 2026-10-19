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


def _related_receipt_with_inline_logo_and_forward() -> bytes:
    from email.message import EmailMessage

    message = EmailMessage()
    message["From"] = "a@shop.com"
    message["Subject"] = "Receipt"
    message.set_content("text")
    message.add_alternative("<html><img src='cid:logo'></html>", subtype="html")
    html = message.get_payload()[1]
    html.add_related(b"\x89PNG\r\n\x1a\nlogo", maintype="image", subtype="png", cid="<logo>")
    forwarded = EmailMessage()
    forwarded["From"] = "other@unknown.example"
    forwarded.add_attachment(
        b"%PDF-1.7 inner", maintype="application", subtype="pdf", filename="inner.pdf"
    )
    message.add_attachment(forwarded)
    message.add_attachment(
        b"%PDF-1.7 outer", maintype="application", subtype="pdf", filename="receipt.pdf"
    )
    return message.as_bytes()


def test_inline_images_and_forwarded_message_parts_are_not_receipts() -> None:
    names = [
        a.filename
        for a in parse_inbound_message(
            _related_receipt_with_inline_logo_and_forward(), "a@shop.com"
        ).attachments
    ]
    assert "receipt.pdf" in names
    assert "inner.pdf" not in names  # never walk into a forwarded message
    assert all(
        a.declared_mime != "image/png"
        for a in parse_inbound_message(
            _related_receipt_with_inline_logo_and_forward(), "a@shop.com"
        ).attachments
    )
