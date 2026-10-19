"""document_extractor

PDF or image bytes → ExtractedDocument. PDF pages use their text layer; a page with almost
no native text (a scan) is rendered and OCR'd with Tesseract. Every file goes through the
attachment caps first, and runs in a worker process killed at the 30 s limit.

Status:
    None
Issues:
    None
Todo:
    None
"""

import io
import json
import logging
import subprocess
import sys
import time
from typing import Any

import pymupdf
import pytesseract
from PIL import Image, ImageOps

from receipt_sentinel.extract.attachment_guard import check_attachment_size, sniff_media_type
from receipt_sentinel.extract.extracted_document import (
    MAX_EXTRACT_SECONDS,
    MAX_IMAGE_PIXELS,
    MAX_PAGES,
    ExtractedDocument,
    ExtractionMethod,
    ExtractionRejected,
    OcrUnavailable,
)

# Below this many non-whitespace characters a PDF page is treated as a scan
MIN_NATIVE_CHARS_PER_PAGE = 20
OCR_DPI = 300
# Interpreter start-up + imports in the worker, on top of the document's own time limit
WORKER_STARTUP_GRACE_SECONDS = 5.0
WORKER_MODULE = "receipt_sentinel.extract.extraction_worker"
# Modes Tesseract (via a PNG hand-off) can take; anything else (CMYK, P, I;16) is converted
_OCR_IMAGE_MODES = frozenset({"1", "L", "LA", "RGB", "RGBA"})

logger = logging.getLogger(__name__)


def extract_document(
    data: bytes, *, time_limit_seconds: float = MAX_EXTRACT_SECONDS
) -> ExtractedDocument:
    """Extract receipt text from PDF, PNG or JPEG bytes, in a worker process that is killed
    at the time limit.

    MuPDF rendering and image decoding can't be interrupted from Python, and a 2 KB PDF can
    take minutes to render, so only a separate process bounds the work. Synchronous: async
    callers run it in a worker thread.

    Raises:
        ExtractionRejected: The file breaks a cap, isn't a supported type, is encrypted or
            corrupt, takes too long, or yields no text. str() is safe to show the user.
        OcrUnavailable: OCR was needed and Tesseract is not installed.
    """
    # Cheap checks first: junk never costs a process start
    check_attachment_size(data)
    sniff_media_type(data)
    try:
        done = subprocess.run(  # noqa: S603 - fixed argv; the file goes in on stdin
            [sys.executable, "-I", "-m", WORKER_MODULE, str(time_limit_seconds)],
            input=data,
            capture_output=True,
            timeout=time_limit_seconds + WORKER_STARTUP_GRACE_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as err:
        raise ExtractionRejected("timeout", _timeout_reason(time_limit_seconds)) from err
    if done.returncode != 0:
        # Killed for memory or crashed inside MuPDF/Pillow: the file is the likely cause
        logger.error("extraction worker exited with code %s", done.returncode)
        raise ExtractionRejected("unreadable", "the file could not be read")
    return decode_worker_result(json.loads(done.stdout))


def decode_worker_result(payload: dict[str, Any]) -> ExtractedDocument:
    """Turn the worker's JSON reply back into a document or the exception it stood for."""
    if "rejected" in payload:
        raise ExtractionRejected(payload["rejected"]["kind"], payload["rejected"]["reason"])
    if payload.get("ocr_unavailable"):
        raise OcrUnavailable()
    return ExtractedDocument(**payload["document"])


def extract_document_in_process(
    data: bytes, *, time_limit_seconds: float = MAX_EXTRACT_SECONDS
) -> ExtractedDocument:
    """The extraction itself, unbounded by any process limit: the worker calls this.

    The time limit is only checked between steps (before each OCR call), so call
    extract_document for untrusted files.
    """
    check_attachment_size(data)
    media_type = sniff_media_type(data)
    deadline = time.monotonic() + time_limit_seconds
    if media_type == "application/pdf":
        document = _extract_pdf(data, deadline)
    else:
        text, confidence = _ocr_image(_open_image(data), deadline)
        document = ExtractedDocument(text, 1, "ocr", confidence)
    if not document.text.strip():
        raise ExtractionRejected("no_text", "no readable text found in the file")
    return document


def _timeout_reason(time_limit_seconds: float) -> str:
    return f"reading the file took over {time_limit_seconds:g} s"


def _open_pdf_pages(data: bytes) -> list[pymupdf.Page]:
    try:
        pdf = pymupdf.open(stream=data, filetype="pdf")
    except (pymupdf.FileDataError, RuntimeError, ValueError) as err:
        raise ExtractionRejected("unreadable", "the PDF is damaged or incomplete") from err
    if pdf.needs_pass:
        raise ExtractionRejected("encrypted", "the PDF is password-protected")
    try:
        if pdf.page_count > MAX_PAGES:
            raise ExtractionRejected("too_many_pages", f"the PDF has more than {MAX_PAGES} pages")
        pages = [pdf.load_page(number) for number in range(pdf.page_count)]
        for page in pages:
            _reject_oversized_images(page)
    except (RuntimeError, ValueError) as err:  # MuPDF raises these for a broken page tree
        raise ExtractionRejected("unreadable", "the PDF is damaged or incomplete") from err
    return pages


def _reject_oversized_images(page: pymupdf.Page) -> None:
    # The render's pixel cap doesn't bound the images MuPDF decodes to draw it: a few KB
    # of Flate data can declare a 45000×45000 image
    for image in page.get_images(full=True):
        width, height = int(image[2]), int(image[3])
        if width * height > MAX_IMAGE_PIXELS:
            raise ExtractionRejected("too_large", "the PDF contains an image too large to read")


def _extract_pdf(data: bytes, deadline: float) -> ExtractedDocument:
    pages = _open_pdf_pages(data)
    page_texts: list[str] = []
    confidences: list[float] = []
    ocr_used = False
    for page in pages:
        native, rendered = _native_text_or_render(page)
        if rendered is None:
            page_texts.append(native)
            continue
        # OCR stays outside the MuPDF error mapping: OcrUnavailable is a RuntimeError too
        text, confidence = _ocr_image(rendered, deadline)
        ocr_used = True
        page_texts.append(text)
        if confidence is not None:
            confidences.append(confidence)
    method: ExtractionMethod = "ocr" if ocr_used else "native"
    mean_confidence = sum(confidences) / len(confidences) if confidences else None
    return ExtractedDocument("\n".join(page_texts), len(pages), method, mean_confidence)


def _native_text_or_render(page: pymupdf.Page) -> tuple[str, Image.Image | None]:
    """The page's text layer, or (when it has almost none) a render to OCR."""
    try:
        native = str(page.get_text())
        if len("".join(native.split())) >= MIN_NATIVE_CHARS_PER_PAGE:
            return native, None
        return native, _render_page(page)
    except (RuntimeError, ValueError) as err:
        raise ExtractionRejected("unreadable", "the PDF is damaged or incomplete") from err


def _render_page(page: pymupdf.Page) -> Image.Image:
    # Lower the DPI for huge pages so the render stays under the pixel cap
    width_in, height_in = page.rect.width / 72, page.rect.height / 72
    dpi = min(OCR_DPI, int((MAX_IMAGE_PIXELS / max(width_in * height_in, 1e-6)) ** 0.5))
    if dpi < 1:
        raise ExtractionRejected("too_large", "a PDF page is too large to read")
    pixmap = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
    return Image.open(io.BytesIO(pixmap.tobytes("png")))


def _open_image(data: bytes) -> Image.Image:
    try:
        image = Image.open(io.BytesIO(data))  # lazy: reads the header only
        if image.width * image.height > MAX_IMAGE_PIXELS:
            raise ExtractionRejected("too_large", "the image is too large to read")
        image.load()
    except Image.DecompressionBombError as err:
        raise ExtractionRejected("too_large", "the image is too large to read") from err
    except OSError as err:
        raise ExtractionRejected("unreadable", "the image is damaged or incomplete") from err
    # Phones store photos sideways plus an orientation tag; OCR needs them upright
    upright = ImageOps.exif_transpose(image)
    return upright if upright.mode in _OCR_IMAGE_MODES else upright.convert("RGB")


def _ocr_image(image: Image.Image, deadline: float) -> tuple[str, float | None]:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ExtractionRejected("timeout", "reading the file took too long")
    try:
        words = pytesseract.image_to_data(
            image, output_type=pytesseract.Output.DICT, timeout=remaining
        )
    except pytesseract.TesseractNotFoundError as err:
        raise OcrUnavailable() from err
    except RuntimeError as err:  # pytesseract's timeout
        raise ExtractionRejected("timeout", "reading the file took too long") from err
    return _join_ocr_words(words)


def _join_ocr_words(words: dict[str, list[object]]) -> tuple[str, float | None]:
    lines: dict[tuple[int, int, int], list[str]] = {}
    confidences: list[float] = []
    for index, word in enumerate(words["text"]):
        confidence = float(str(words["conf"][index]))
        if not str(word).strip() or confidence < 0:
            continue
        key = (
            int(str(words["block_num"][index])),
            int(str(words["par_num"][index])),
            int(str(words["line_num"][index])),
        )
        lines.setdefault(key, []).append(str(word))
        confidences.append(confidence / 100)
    text = "\n".join(" ".join(line) for line in lines.values())
    return text, (sum(confidences) / len(confidences) if confidences else None)
