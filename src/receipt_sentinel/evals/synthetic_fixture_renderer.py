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

_MIN_PAGE_WIDTH_PT = 300
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
    """One-page receipt PDF with real, selectable text, wide enough for the longest line."""
    # Embedded Courier (not the base-14 one): the base-14 encoding has no € sign
    font = pymupdf.Font("cour")
    lines = text.splitlines() or [""]
    width = 2 * _MARGIN_PT + max(font.text_length(line, fontsize=_FONT_SIZE_PT) for line in lines)
    height = 2 * _MARGIN_PT + _LINE_HEIGHT_PT * len(lines)
    doc = pymupdf.open()
    page = doc.new_page(width=max(_MIN_PAGE_WIDTH_PT, width), height=height)
    writer = pymupdf.TextWriter(page.rect)
    for index, line in enumerate(lines):
        baseline = _MARGIN_PT + _LINE_HEIGHT_PT * (index + 1)
        writer.append((_MARGIN_PT, baseline), line, font=font, fontsize=_FONT_SIZE_PT)
    writer.write_text(page)
    doc.subset_fonts()
    return _deterministic_pdf_bytes(doc)


def _render_receipt_image(text: str) -> Image.Image:
    page = pymupdf.open(stream=render_native_pdf(text), filetype="pdf").load_page(0)
    pixmap = page.get_pixmap(dpi=_SCAN_DPI)
    return Image.open(io.BytesIO(pixmap.tobytes("png"))).convert("RGB")


def render_receipt_png(text: str) -> bytes:
    """A flat picture of the receipt, for showing it on a web page."""
    png = io.BytesIO()
    _render_receipt_image(text).convert("L").save(png, format="PNG", optimize=True)
    return png.getvalue()


def render_scanned_pdf(text: str) -> bytes:
    """One-page PDF holding only a picture of the receipt: no text layer, so OCR is needed."""
    # Scanners save grayscale JPEG; an RGB PNG of the same page is ~1 MB
    image = _render_receipt_image(text).convert("L")
    scan = io.BytesIO()
    image.save(scan, format="JPEG", quality=80)
    doc = pymupdf.open()
    scale = 72 / _SCAN_DPI
    page = doc.new_page(width=image.width * scale, height=image.height * scale)
    page.insert_image(page.rect, stream=scan.getvalue())
    return _deterministic_pdf_bytes(doc)


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


def _deterministic_pdf_bytes(doc: pymupdf.Document) -> bytes:
    # No timestamps, no random document id: re-rendering a fixture must not change its bytes
    doc.set_metadata({})
    return bytes(doc.tobytes(no_new_id=True, garbage=3, deflate=True))
