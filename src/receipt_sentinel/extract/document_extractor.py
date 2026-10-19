"""document_extractor

PDF or image bytes → ExtractedDocument. PDF pages use their text layer; a page with almost
no native text (a scan) is rendered and OCR'd with Tesseract. Every file goes through the
attachment caps first, and one 30 s deadline covers the whole document.

Status:
    None
Issues:
    None
Todo:
    None
"""

import io
import time

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


def extract_document(data: bytes) -> ExtractedDocument:
    """Extract receipt text from PDF, PNG or JPEG bytes.

    Synchronous and CPU-bound: async callers run it in a worker thread.

    Raises:
        ExtractionRejected: The file breaks a cap, isn't a supported type, is encrypted or
            corrupt, or yields no text. str() is safe to show the user.
        OcrUnavailable: OCR was needed and Tesseract is not installed.
    """
    check_attachment_size(data)
    media_type = sniff_media_type(data)
    deadline = time.monotonic() + MAX_EXTRACT_SECONDS
    if media_type == "application/pdf":
        document = _extract_pdf(data, deadline)
    else:
        text, confidence = _ocr_image(_open_image(data), deadline)
        document = ExtractedDocument(text, 1, "ocr", confidence)
    if not document.text.strip():
        raise ExtractionRejected("no_text", "no readable text found in the file")
    return document


def _extract_pdf(data: bytes, deadline: float) -> ExtractedDocument:
    try:
        pdf = pymupdf.open(stream=data, filetype="pdf")
    except (pymupdf.FileDataError, RuntimeError, ValueError) as err:
        raise ExtractionRejected("unreadable", "the PDF is damaged or incomplete") from err
    if pdf.needs_pass:
        raise ExtractionRejected("encrypted", "the PDF is password-protected")
    if pdf.page_count > MAX_PAGES:
        raise ExtractionRejected("too_many_pages", f"the PDF has more than {MAX_PAGES} pages")
    page_texts: list[str] = []
    confidences: list[float] = []
    ocr_used = False
    for page in (pdf.load_page(number) for number in range(pdf.page_count)):
        native = str(page.get_text())
        if len("".join(native.split())) >= MIN_NATIVE_CHARS_PER_PAGE:
            page_texts.append(native)
            continue
        text, confidence = _ocr_image(_render_page(page), deadline)
        ocr_used = True
        page_texts.append(text)
        if confidence is not None:
            confidences.append(confidence)
    method: ExtractionMethod = "ocr" if ocr_used else "native"
    mean_confidence = sum(confidences) / len(confidences) if confidences else None
    return ExtractedDocument("\n".join(page_texts), pdf.page_count, method, mean_confidence)


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
    return ImageOps.exif_transpose(image)


def _ocr_image(image: Image.Image, deadline: float) -> tuple[str, float | None]:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ExtractionRejected("timeout", f"reading the file took over {MAX_EXTRACT_SECONDS:g} s")
    try:
        words = pytesseract.image_to_data(
            image, output_type=pytesseract.Output.DICT, timeout=remaining
        )
    except pytesseract.TesseractNotFoundError as err:
        raise OcrUnavailable() from err
    except RuntimeError as err:  # pytesseract's timeout
        raise ExtractionRejected(
            "timeout", f"reading the file took over {MAX_EXTRACT_SECONDS:g} s"
        ) from err
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
