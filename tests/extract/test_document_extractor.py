import io
import shutil
import time
from collections.abc import Callable

import pymupdf
import pytesseract
import pytest
from PIL import Image

from receipt_sentinel.evals.synthetic_fixture_renderer import (
    render_native_pdf,
    render_photo_jpeg,
    render_scanned_pdf,
)
from receipt_sentinel.extract.document_extractor import (
    estimate_skew_degrees,
    extract_document,
    extract_document_in_process,
)
from receipt_sentinel.extract.extracted_document import (
    MAX_PAGES,
    ExtractionRejected,
    OcrUnavailable,
    RejectionKind,
)
from tests.extract.malformed_pdf_builders import embedded_image_bomb_pdf, vector_heavy_pdf

needs_tesseract = pytest.mark.skipif(shutil.which("tesseract") is None, reason="needs tesseract")


def _tilted_photo(text: str) -> bytes:
    return render_photo_jpeg(text, rotate_degrees=2.5, blur_radius=0.6, exif_orientation=6)


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


def test_deadline_exceeded_before_ocr_is_timeout() -> None:
    with pytest.raises(ExtractionRejected) as err:
        extract_document_in_process(render_scanned_pdf(RECEIPT), time_limit_seconds=0.0)
    assert err.value.kind == "timeout"


def test_missing_tesseract_raises_ocr_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    def not_installed(*args: object, **kwargs: object) -> None:
        raise pytesseract.TesseractNotFoundError()

    monkeypatch.setattr(pytesseract, "image_to_data", not_installed)
    with pytest.raises(OcrUnavailable, match="brew install tesseract"):
        extract_document_in_process(render_scanned_pdf(RECEIPT))


def test_corrupt_page_tree_is_unreadable_not_a_crash() -> None:
    assert _rejection_kind(vector_heavy_pdf(10, kids=5)) == "unreadable"


@needs_tesseract
def test_cmyk_jpeg_is_read() -> None:
    upright = render_photo_jpeg(RECEIPT, rotate_degrees=0, blur_radius=0, exif_orientation=None)
    cmyk = io.BytesIO()
    Image.open(io.BytesIO(upright)).convert("CMYK").save(cmyk, format="JPEG")
    assert "38.76" in extract_document(cmyk.getvalue()).text


def test_slow_to_render_pdf_is_cut_off_at_the_time_limit() -> None:
    started = time.monotonic()
    with pytest.raises(ExtractionRejected) as err:
        extract_document(vector_heavy_pdf(20_000), time_limit_seconds=1.0)
    assert err.value.kind == "timeout"
    assert time.monotonic() - started < 10


def test_pdf_embedded_image_bomb_is_rejected_before_render() -> None:
    assert _rejection_kind(embedded_image_bomb_pdf(7_000)) == "too_large"  # 49 MP > 40 MP cap


COLUMNS_RECEIPT = (
    "WALGREENS\n08/14/2026\nIbuprofen               $   47.41\n"
    "Cleaning                $    54.8\nVitamin D               $   11.45\n"
    "TOTAL                   $  113.66\n"
)


@needs_tesseract
@pytest.mark.parametrize("render", [render_scanned_pdf, _tilted_photo])
def test_ocr_keeps_each_amount_on_its_label_line(render: Callable[[str], bytes]) -> None:
    # Found by the first real v2 eval: default page segmentation read labels and amounts
    # as separate columns, so the model could not tell which number was the total
    lines = extract_document(render(COLUMNS_RECEIPT)).text.splitlines()
    total_line = next(line for line in lines if "TOTAL" in line)
    assert "113.66" in total_line
    # Tesseract versions spell words differently (CI's 5.3 read "Ibuprofen" otherwise);
    # what matters is that the amount shares a line with its item
    assert any("47.41" in line and "profen" in line for line in lines)


@pytest.mark.parametrize("tilt", [-4.0, -2.5, 0.0, 1.5, 3.0])
def test_skew_estimate_recovers_the_tilt(tilt: float) -> None:
    upright = render_photo_jpeg(
        COLUMNS_RECEIPT, rotate_degrees=0, blur_radius=0, exif_orientation=None
    )
    tilted = Image.open(io.BytesIO(upright)).rotate(tilt, expand=True, fillcolor="white")
    assert estimate_skew_degrees(tilted) == pytest.approx(tilt, abs=0.5)
