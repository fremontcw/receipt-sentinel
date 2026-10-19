"""Hand-built hostile PDFs for extractor tests (from the final review's fuzzing)."""

import zlib


def _pdf(objects: list[bytes]) -> bytes:
    out = b"%PDF-1.7\n"
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets)
    out += f"trailer<</Size {len(objects) + 1}/Root 1 0 R>>\nstartxref\n{xref}\n%%EOF".encode()
    return out


def _stream(data: bytes, extra: bytes = b"") -> bytes:
    return b"<<" + extra + b"/Length %d>>stream\n" % len(data) + data + b"\nendstream"


def vector_heavy_pdf(fill_operations: int, kids: int = 1) -> bytes:
    """No text, many full-page fills: tiny file, slow render. kids > 1 lists the same page
    object repeatedly, which MuPDF rejects as an invalid page tree."""
    ops = zlib.compress(b"0 0 m 612 792 l 0 792 l h f\n" * fill_operations, 9)
    return _pdf(
        [
            b"<</Type/Catalog/Pages 2 0 R>>",
            b"<</Type/Pages/Kids[%s]/Count %d>>" % (b" ".join([b"3 0 R"] * kids), kids),
            b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]/Contents 4 0 R>>",
            _stream(ops, b"/Filter/FlateDecode"),
        ]
    )


def embedded_image_bomb_pdf(side: int) -> bytes:
    """One side×side gray image, Flate-compressed to a few KB, drawn full page."""
    pixels = zlib.compress(b"\xff" * (side * side), 9)
    return _pdf(
        [
            b"<</Type/Catalog/Pages 2 0 R>>",
            b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
            b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]"
            b"/Resources<</XObject<</Im0 5 0 R>>>>/Contents 4 0 R>>",
            _stream(b"q 612 0 0 792 0 0 cm /Im0 Do Q"),
            _stream(
                pixels,
                b"/Type/XObject/Subtype/Image/Width %d/Height %d/ColorSpace/DeviceGray"
                b"/BitsPerComponent 8/Filter/FlateDecode" % (side, side),
            ),
        ]
    )
