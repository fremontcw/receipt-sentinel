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

from receipt_sentinel.evals.golden_case import (
    GOLDEN_DIR,
    Difficulty,
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
    assert load_dataset_version(GOLDEN_DIR) == "1"


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
