import pytest

from receipt_sentinel.extract.attachment_guard import check_attachment_size, sniff_media_type
from receipt_sentinel.extract.extracted_document import MAX_ATTACHMENT_BYTES, ExtractionRejected


def test_sniffs_pdf_png_jpeg_by_magic_bytes_not_name() -> None:
    assert sniff_media_type(b"%PDF-1.7\n...") == "application/pdf"
    assert sniff_media_type(b"\x89PNG\r\n\x1a\n...") == "image/png"
    assert sniff_media_type(b"\xff\xd8\xff\xe0...") == "image/jpeg"


def test_heic_photo_is_rejected_with_export_hint() -> None:
    with pytest.raises(ExtractionRejected) as err:
        sniff_media_type(b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00")
    assert err.value.kind == "unsupported_type"
    assert "JPEG" in str(err.value)


def test_html_renamed_to_pdf_is_rejected() -> None:
    with pytest.raises(ExtractionRejected) as err:
        sniff_media_type(b"<html><script>")
    assert err.value.kind == "unsupported_type"


def test_eleven_megabytes_is_too_large() -> None:
    with pytest.raises(ExtractionRejected) as err:
        check_attachment_size(b"\0" * (MAX_ATTACHMENT_BYTES + 1))
    assert err.value.kind == "too_large"
    check_attachment_size(b"\0" * MAX_ATTACHMENT_BYTES)
