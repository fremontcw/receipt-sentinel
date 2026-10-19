"""golden_fixture_text

Before a run, swaps each fixture case's receipt_text for what the extract layer reads from
its file, so the model is graded on real extraction output and the keyless label oracle
(which matches on receipt_text) still recognises every case.

Status:
    None
Issues:
    None
Todo:
    None
"""

from functools import lru_cache
from pathlib import Path

from receipt_sentinel.evals.golden_case import GOLDEN_DIR, GoldenCase
from receipt_sentinel.extract.document_extractor import extract_document
from receipt_sentinel.extract.extracted_document import ExtractedDocument, ExtractionRejected

# ponytail: extraction is a pure function of the bytes, and a process (the test suite, the
# demo server) runs the same 11 fixtures many times; OCR costs ~1 s each. Rejections are
# not cached, so a broken fixture is re-tried each run.
_extract_cached = lru_cache(maxsize=64)(extract_document)


def extract_fixture_cases(
    cases: list[GoldenCase], golden_dir: Path = GOLDEN_DIR
) -> tuple[list[GoldenCase], dict[str, str]]:
    """Run extraction on every fixture case.

    Args:
        cases: Golden cases, with or without fixtures.
        golden_dir: Directory fixture paths are relative to.

    Returns:
        The cases (fixture cases carrying extracted text, others unchanged) and a map of
        case id → reason for fixtures that could not be extracted. Those cases keep their
        source text; the runner scores them as `extract` failures without calling the model.

    Raises:
        OcrUnavailable: Tesseract is missing, so no OCR fixture can be judged at all.
    """
    extracted: list[GoldenCase] = []
    errors: dict[str, str] = {}
    for case in cases:
        if case.fixture is None:
            extracted.append(case)
            continue
        try:
            document: ExtractedDocument = _extract_cached((golden_dir / case.fixture).read_bytes())
        except (ExtractionRejected, FileNotFoundError) as err:
            errors[case.id] = str(err)
            extracted.append(case)
            continue
        extracted.append(case.model_copy(update={"receipt_text": document.text}))
    return extracted, errors
