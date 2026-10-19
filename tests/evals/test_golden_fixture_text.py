import shutil
from pathlib import Path

import pytest

from receipt_sentinel.evals.golden_case import GOLDEN_DIR, load_golden_cases
from receipt_sentinel.evals.golden_fixture_text import extract_fixture_cases
from receipt_sentinel.extract.document_extractor import extract_document

pytestmark = pytest.mark.skipif(shutil.which("tesseract") is None, reason="needs tesseract")
NATIVE_ID = "fixture_pdf_native_0001_dining_easy"


def test_fixture_case_text_is_what_extraction_produced() -> None:
    cases = load_golden_cases()
    extracted, errors = extract_fixture_cases(cases)
    by_id = {c.id: c for c in extracted}
    native = by_id[NATIVE_ID]
    assert native.fixture is not None
    assert native.receipt_text == extract_document((GOLDEN_DIR / native.fixture).read_bytes()).text
    assert errors == {}
    plain = next(c for c in cases if not c.fixture)
    assert by_id[plain.id].receipt_text == plain.receipt_text


def test_failed_fixture_is_reported_not_raised(tmp_path: Path) -> None:
    golden = tmp_path / "golden"
    shutil.copytree(GOLDEN_DIR, golden)
    damaged = golden / "fixtures" / f"{NATIVE_ID}.pdf"
    damaged.write_bytes(damaged.read_bytes()[:200])
    cases = load_golden_cases(golden)
    extracted, errors = extract_fixture_cases(cases, golden)
    assert set(errors) == {NATIVE_ID}
    assert "damaged" in errors[NATIVE_ID]
    assert len(extracted) == len(cases)
