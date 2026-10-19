"""extraction_worker

Child-process entry for extract_document: reads the file from stdin, extracts it, writes one
JSON reply to stdout. Run as `python -I -m receipt_sentinel.extract.extraction_worker <secs>`.
The parent kills it at the time limit, so a hostile file can't hold a server thread.

Status:
    None
Issues:
    None
Todo:
    None
"""

import json
import resource
import sys
from dataclasses import asdict

from receipt_sentinel.extract.document_extractor import extract_document_in_process
from receipt_sentinel.extract.extracted_document import ExtractionRejected, OcrUnavailable

WORKER_MEMORY_LIMIT_BYTES = 2 * 1024**3


def _limit_memory() -> None:
    # ponytail: RLIMIT_AS is enforced on Linux (CI, containers), ignored or refused on macOS;
    # the image-dimension caps are the portable defence, this is the backstop
    try:
        resource.setrlimit(
            resource.RLIMIT_AS, (WORKER_MEMORY_LIMIT_BYTES, WORKER_MEMORY_LIMIT_BYTES)
        )
    except (ValueError, OSError):
        return


def main() -> int:
    """Extract stdin's bytes and print {"document"|"rejected"|"ocr_unavailable": ...}."""
    _limit_memory()
    time_limit_seconds = float(sys.argv[1])
    data = sys.stdin.buffer.read()
    try:
        document = extract_document_in_process(data, time_limit_seconds=time_limit_seconds)
        payload: dict[str, object] = {"document": asdict(document)}
    except ExtractionRejected as err:
        payload = {"rejected": {"kind": err.kind, "reason": str(err)}}
    except OcrUnavailable:
        payload = {"ocr_unavailable": True}
    sys.stdout.write(json.dumps(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
