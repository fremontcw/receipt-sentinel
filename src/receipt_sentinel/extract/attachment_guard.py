"""attachment_guard

Cheap checks before any parser touches a file: size cap and media type from magic bytes,
never from the file name or the declared MIME type.

Status:
    None
Issues:
    None
Todo:
    None
"""

from receipt_sentinel.extract.extracted_document import (
    MAX_ATTACHMENT_BYTES,
    ExtractionRejected,
    MediaType,
)

_HEIF_BRANDS = (b"heic", b"heix", b"heif", b"mif1", b"msf1", b"hevc")


def check_attachment_size(data: bytes) -> None:
    """Raise `too_large` when the file is over the 10 MB cap."""
    if len(data) > MAX_ATTACHMENT_BYTES:
        raise ExtractionRejected(
            "too_large", f"file is over the {MAX_ATTACHMENT_BYTES // (1024 * 1024)} MB limit"
        )


def sniff_media_type(data: bytes) -> MediaType:
    """Identify a PDF, PNG or JPEG by its leading bytes; reject everything else."""
    if data.startswith(b"%PDF-"):
        return "application/pdf"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[4:8] == b"ftyp" and data[8:12] in _HEIF_BRANDS:
        raise ExtractionRejected(
            "unsupported_type", "HEIC photos aren't supported; export the photo as JPEG"
        )
    raise ExtractionRejected("unsupported_type", "only PDF, PNG and JPEG receipts are supported")
