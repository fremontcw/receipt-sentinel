"""test_golden_case.

Tests for golden case schema and loader.

Status:
    None
Issues:
    None
Todo:
    None
"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from receipt_sentinel.evals.golden_case import (
    GOLDEN_DIR,
    Difficulty,
    GoldenCase,
    load_dataset_version,
    load_golden_cases,
)


def test_loads_committed_cases_sorted_by_id() -> None:
    """Verify golden cases load in sorted order with correct values."""
    cases = load_golden_cases()
    ids = [c.id for c in cases]
    assert ids == sorted(ids)
    assert "adversarial_0001_override" in ids
    adv = next(c for c in cases if c.id == "adversarial_0001_override")
    assert adv.difficulty is Difficulty.ADVERSARIAL
    assert adv.expected.injection_detected is True


def test_dataset_version_is_read() -> None:
    """Verify dataset version is correctly read from file."""
    assert load_dataset_version(GOLDEN_DIR) == "3"


def test_duplicate_ids_raise(tmp_path: Path) -> None:
    """Verify duplicate case ids raise ValueError."""
    cases_dir = tmp_path / "cases"
    cases_dir.mkdir()
    src = (GOLDEN_DIR / "cases" / "synthetic_0001_dining_easy.yaml").read_text()
    (cases_dir / "a.yaml").write_text(src)
    (cases_dir / "b.yaml").write_text(src)
    (tmp_path / "dataset_version.txt").write_text("9")
    with pytest.raises(ValueError, match="duplicate"):
        load_golden_cases(tmp_path)


def test_non_adversarial_case_must_not_expect_injection(tmp_path: Path) -> None:
    """Verify non-adversarial cases cannot have injection_detected=true."""
    cases_dir = tmp_path / "cases"
    cases_dir.mkdir()
    src = (GOLDEN_DIR / "cases" / "synthetic_0001_dining_easy.yaml").read_text()
    bad_src = src.replace("injection_detected: false", "injection_detected: true")
    (cases_dir / "a.yaml").write_text(bad_src)
    (tmp_path / "dataset_version.txt").write_text("9")
    with pytest.raises(ValueError, match="injection_detected"):
        load_golden_cases(tmp_path)


@pytest.mark.parametrize("bad_path", ["../outside.pdf", "/etc/passwd", "fixtures/../../x.pdf"])
def test_fixture_path_must_stay_inside_golden_dir(bad_path: str) -> None:
    """A fixture path can never point outside the golden directory."""
    case = load_golden_cases()[0].model_dump(mode="json")
    with pytest.raises(ValidationError):
        GoldenCase.model_validate({**case, "fixture": bad_path})


def test_every_fixture_case_points_at_a_real_file() -> None:
    """Every committed fixture case's file is committed too."""
    fixture_cases = [c for c in load_golden_cases() if c.fixture]
    assert fixture_cases
    for case in fixture_cases:
        assert (GOLDEN_DIR / case.fixture).is_file(), case.id


def test_fixture_cases_cover_native_scanned_and_photo() -> None:
    """At least three cases of each file kind real receipts arrive as."""
    ids = [c.id for c in load_golden_cases() if c.fixture]
    for kind in ("fixture_pdf_native_", "fixture_pdf_scanned_", "fixture_photo_"):
        assert sum(i.startswith(kind) for i in ids) >= 3, kind


def test_relative_fixture_path_is_accepted() -> None:
    """A plain path under the golden directory is a valid fixture."""
    case = load_golden_cases()[0].model_dump(mode="json")
    assert (
        GoldenCase.model_validate({**case, "fixture": "fixtures/a.pdf"}).fixture == "fixtures/a.pdf"
    )
