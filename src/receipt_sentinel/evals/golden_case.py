"""golden_case.

Schema and loader for hand-labeled golden receipt cases (one YAML per case).

Status:
    None
Issues:
    None
Todo:
    None
"""

from datetime import date
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from receipt_sentinel.categorize.receipt_record import ModelOutput

GOLDEN_DIR = Path(__file__).parent / "golden"
ErrorTag = Literal["ocr", "schema", "reasoning", "ambiguity", "label_error", "provider"]


class Difficulty(StrEnum):
    """Difficulty levels for golden test cases."""

    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"
    ADVERSARIAL = "adversarial"


class GoldenCase(BaseModel):
    """Hand-labeled golden receipt case with expected model output."""

    model_config = ConfigDict(extra="forbid")

    id: str
    difficulty: Difficulty
    source: Literal["synthetic", "real_redacted"]
    notes: str
    email_date: date
    receipt_text: str
    expected: ModelOutput
    error_tag: ErrorTag | None = None
    # PDF/image the receipt arrives as, relative to the golden directory. When set, evals
    # feed the model what extraction reads from this file, not receipt_text.
    fixture: str | None = None

    @field_validator("fixture")
    @classmethod
    def fixture_stays_inside_golden_dir(cls, value: str | None) -> str | None:
        """Reject absolute paths and `..` so a case can never point outside the dataset."""
        if value is not None and (PurePosixPath(value).is_absolute() or ".." in value.split("/")):
            raise ValueError(f"fixture must be a relative path inside the golden dir: {value}")
        return value

    @model_validator(mode="after")
    def injection_flag_matches_difficulty(self) -> "GoldenCase":
        """Validate that injection_detected matches difficulty level."""
        is_adversarial = self.difficulty is Difficulty.ADVERSARIAL
        if self.expected.injection_detected != is_adversarial:
            raise ValueError(
                f"{self.id}: expected.injection_detected must be {is_adversarial} for "
                f"difficulty={self.difficulty}"
            )
        return self


def load_dataset_version(golden_dir: Path = GOLDEN_DIR) -> str:
    """Load dataset version string from dataset_version.txt."""
    return (golden_dir / "dataset_version.txt").read_text().strip()


def load_golden_cases(golden_dir: Path = GOLDEN_DIR) -> list[GoldenCase]:
    """Load and validate all golden cases, sorted by id."""
    cases = [
        GoldenCase.model_validate(yaml.safe_load(path.read_text()))
        for path in sorted((golden_dir / "cases").glob("*.yaml"))
    ]
    seen: set[str] = set()
    for case in cases:
        if case.id in seen:
            raise ValueError(f"duplicate golden case id: {case.id}")
        seen.add(case.id)
    return sorted(cases, key=lambda c: c.id)
