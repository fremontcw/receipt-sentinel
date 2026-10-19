"""extracted_document

What extraction returns, why it refuses a file, and the attachment caps (spec §4.1/§4.2).

Status:
    None
Issues:
    None
Todo:
    None
"""

from dataclasses import dataclass
from typing import Literal

MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
MAX_PAGES = 20
MAX_EXTRACT_SECONDS = 30.0
MAX_IMAGE_PIXELS = 40_000_000

MediaType = Literal["application/pdf", "image/png", "image/jpeg"]
ExtractionMethod = Literal["native", "ocr"]
RejectionKind = Literal[
    "unsupported_type",
    "too_large",
    "too_many_pages",
    "timeout",
    "encrypted",
    "unreadable",
    "no_text",
]


@dataclass(frozen=True)
class ExtractedDocument:
    text: str
    page_count: int
    extraction_method: ExtractionMethod
    # Mean Tesseract word confidence (0-1) over OCR'd words; None when nothing was OCR'd
    ocr_confidence: float | None


class ExtractionRejected(Exception):  # noqa: N818 - a refusal, not a crash
    """The file can't be extracted. str() is a human-readable reason safe to show users."""

    def __init__(self, kind: RejectionKind, reason: str) -> None:
        super().__init__(reason)
        self.kind = kind


class OcrUnavailable(RuntimeError):  # noqa: N818 - matches ExtractionRejected
    """The Tesseract binary is missing: an environment problem, not a bad file."""

    def __init__(self) -> None:
        super().__init__(
            "Tesseract OCR is not installed: brew install tesseract (macOS) "
            "or apt-get install tesseract-ocr (Linux)"
        )
