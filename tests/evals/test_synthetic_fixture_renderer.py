import io

import pymupdf
from PIL import Image

from receipt_sentinel.evals.synthetic_fixture_renderer import (
    render_native_pdf,
    render_photo_jpeg,
    render_scanned_pdf,
)
from receipt_sentinel.extract.attachment_guard import sniff_media_type

EXIF_ORIENTATION_TAG = 0x0112


def test_native_pdf_has_text_layer_and_scanned_pdf_has_none() -> None:
    text = "BLUE BOTTLE COFFEE\nTOTAL $ 50.23"
    native = pymupdf.open(stream=render_native_pdf(text), filetype="pdf")
    scanned = pymupdf.open(stream=render_scanned_pdf(text), filetype="pdf")
    assert "50.23" in native.load_page(0).get_text()
    assert scanned.load_page(0).get_text().strip() == ""


def test_photo_is_jpeg_with_requested_exif_orientation() -> None:
    data = render_photo_jpeg("TOTAL 9.99", rotate_degrees=2.0, blur_radius=0.6, exif_orientation=6)
    assert sniff_media_type(data) == "image/jpeg"
    assert Image.open(io.BytesIO(data)).getexif().get(EXIF_ORIENTATION_TAG) == 6
