"""synthetic_fixture_renderer

Renders synthetic receipt text as the files real receipts arrive as: a PDF with a text
layer, a scanned (image-only) PDF, and a slightly tilted, blurred phone photo (JPEG).

Status:
    None
Issues:
    None
Todo:
    None
"""

import io

import pymupdf
from PIL import Image, ImageFilter

_PAGE_WIDTH_PT = 300
_MARGIN_PT = 24
_FONT_SIZE_PT = 11
_LINE_HEIGHT_PT = 15
_SCAN_DPI = 200
_PAPER = (250, 248, 240)
_EXIF_ORIENTATION_TAG = 0x0112
# How to store an upright image so that a viewer applying this EXIF orientation shows it upright
_STORE_TRANSPOSE_FOR_ORIENTATION = {
    3: Image.Transpose.ROTATE_180,
    6: Image.Transpose.ROTATE_90,
    8: Image.Transpose.ROTATE_270,
}


def render_native_pdf(text: str) -> bytes:
    """One-page receipt PDF with real, selectable text (Courier)."""
    lines = text.splitlines()
    height = 2 * _MARGIN_PT + _LINE_HEIGHT_PT * max(1, len(lines))
    doc = pymupdf.open()
    page = doc.new_page(width=_PAGE_WIDTH_PT, height=height)
    for index, line in enumerate(lines):
        baseline = _MARGIN_PT + _LINE_HEIGHT_PT * (index + 1)
        page.insert_text((_MARGIN_PT, baseline), line, fontname="cour", fontsize=_FONT_SIZE_PT)
    return bytes(doc.tobytes())


def _render_receipt_image(text: str) -> Image.Image:
    page = pymupdf.open(stream=render_native_pdf(text), filetype="pdf").load_page(0)
    pixmap = page.get_pixmap(dpi=_SCAN_DPI)
    return Image.open(io.BytesIO(pixmap.tobytes("png"))).convert("RGB")


def render_scanned_pdf(text: str) -> bytes:
    """One-page PDF holding only a picture of the receipt: no text layer, so OCR is needed."""
    image = _render_receipt_image(text)
    png = io.BytesIO()
    image.save(png, format="PNG")
    doc = pymupdf.open()
    scale = 72 / _SCAN_DPI
    page = doc.new_page(width=image.width * scale, height=image.height * scale)
    page.insert_image(page.rect, stream=png.getvalue())
    return bytes(doc.tobytes())


def render_photo_jpeg(
    text: str, *, rotate_degrees: float, blur_radius: float, exif_orientation: int | None
) -> bytes:
    """Phone-photo-like JPEG: tilted, softly blurred, optionally stored sideways + EXIF tag.

    Args:
        text: Receipt text to draw.
        rotate_degrees: Small hand-held tilt.
        blur_radius: Gaussian blur radius in pixels (focus softness).
        exif_orientation: 3, 6 or 8 stores the pixels rotated and tags them so a viewer that
            honours EXIF shows the receipt upright, as phones do; None stores them upright.
    """
    image = _render_receipt_image(text).rotate(
        rotate_degrees, expand=True, fillcolor=_PAPER, resample=Image.Resampling.BICUBIC
    )
    if blur_radius:
        image = image.filter(ImageFilter.GaussianBlur(blur_radius))
    exif = Image.Exif()
    if exif_orientation is not None:
        image = image.transpose(_STORE_TRANSPOSE_FOR_ORIENTATION[exif_orientation])
        exif[_EXIF_ORIENTATION_TAG] = exif_orientation
    out = io.BytesIO()
    image.save(out, format="JPEG", quality=85, exif=exif)
    return out.getvalue()
