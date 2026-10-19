import io
import shutil

import pymupdf
import pytesseract
import pytest
from PIL import Image

from receipt_sentinel.evals.synthetic_fixture_renderer import (
    render_native_pdf,
    render_photo_jpeg,
    render_scanned_pdf,
)
from receipt_sentinel.extract import document_extractor
from receipt_sentinel.extract.document_extractor import extract_document
from receipt_sentinel.extract.extracted_document import (
    MAX_PAGES,
    ExtractionRejected,
    OcrUnavailable,
    RejectionKind,
)

needs_tesseract = pytest.mark.skipif(shutil.which("tesseract") is None, reason="needs tesseract")
RECEIPT = "SHELL GAS STATION\n08/05/2026\nUNLEADED 10.2 GAL  38.76\nTOTAL  38.76"


def _rejection_kind(data: bytes) -> RejectionKind:
    with pytest.raises(ExtractionRejected) as err:
        extract_document(data)
    return err.value.kind


def test_native_pdf_uses_text_layer() -> None:
    doc = extract_document(render_native_pdf(RECEIPT))
    assert doc.extraction_method == "native"
    assert doc.ocr_confidence is None
    assert doc.page_count == 1
    assert "38.76" in doc.text


@needs_tesseract
def test_scanned_pdf_falls_back_to_ocr() -> None:
    doc = extract_document(render_scanned_pdf(RECEIPT))
    assert doc.extraction_method == "ocr"
    assert "38.76" in doc.text
    assert doc.ocr_confidence is not None and 0 < doc.ocr_confidence <= 1


@needs_tesseract
def test_exif_rotated_photo_is_read_upright() -> None:
    data = render_photo_jpeg(RECEIPT, rotate_degrees=0, blur_radius=0, exif_orientation=6)
    doc = extract_document(data)
    assert doc.extraction_method == "ocr"
    assert "38.76" in doc.text


def test_encrypted_pdf_is_rejected() -> None:
    doc = pymupdf.open(stream=render_native_pdf(RECEIPT), filetype="pdf")
    locked = doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="x", owner_pw="y")
    assert _rejection_kind(locked) == "encrypted"


def test_truncated_pdf_is_unreadable() -> None:
    assert _rejection_kind(render_native_pdf(RECEIPT)[:200]) == "unreadable"


def test_twenty_one_pages_is_too_many() -> None:
    doc = pymupdf.open()
    for _ in range(MAX_PAGES + 1):
        doc.new_page().insert_text((40, 60), "TOTAL 1.00")
    assert _rejection_kind(doc.tobytes()) == "too_many_pages"


@needs_tesseract
def test_blank_scanned_page_has_no_text() -> None:
    assert _rejection_kind(render_scanned_pdf("   ")) == "no_text"


def test_oversized_image_rejected_before_decode() -> None:
    png = io.BytesIO()
    Image.new("1", (8000, 6000)).save(png, format="PNG")  # 48 MP, a few KB on disk
    assert _rejection_kind(png.getvalue()) == "too_large"


def test_deadline_exceeded_is_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(document_extractor, "MAX_EXTRACT_SECONDS", 0.0)
    assert _rejection_kind(render_scanned_pdf(RECEIPT)) == "timeout"


def test_missing_tesseract_raises_ocr_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    def not_installed(*args: object, **kwargs: object) -> None:
        raise pytesseract.TesseractNotFoundError()

    monkeypatch.setattr(pytesseract, "image_to_data", not_installed)
    with pytest.raises(OcrUnavailable, match="brew install tesseract"):
        extract_document(render_scanned_pdf(RECEIPT))
