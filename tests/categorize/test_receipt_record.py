"""test_receipt_record

Tests for ReceiptRecord schema and ModelOutput contract.

Status:
    None
Issues:
    None
Todo:
    None
"""

from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from receipt_sentinel.categorize.receipt_record import (
    RECEIPT_OUTPUT_JSON_SCHEMA,
    Category,
    ModelOutput,
    ReceiptRecord,
)

VALID = {
    "vendor": "Trader Joe's",
    "total": "42.17",
    "currency": "USD",
    "date": "2026-08-03",
    "category": "groceries",
    "line_items": [{"description": "Bananas", "amount": "1.99"}],
    "confidence": 0.93,
    "injection_detected": False,
}


def test_model_output_parses_valid_json() -> None:
    out = ModelOutput.model_validate(VALID)
    assert out.total == Decimal("42.17")
    assert out.date == date(2026, 8, 3)
    assert out.category is Category.GROCERIES


def test_model_output_rejects_unknown_category() -> None:
    with pytest.raises(ValidationError):
        ModelOutput.model_validate({**VALID, "category": "crypto"})


def test_model_output_rejects_model_id_from_model() -> None:
    with pytest.raises(ValidationError):
        ModelOutput.model_validate({**VALID, "model_id": "gpt-99"})


def test_model_output_rejects_confidence_out_of_range() -> None:
    with pytest.raises(ValidationError):
        ModelOutput.model_validate({**VALID, "confidence": 1.7})


def test_model_output_rejects_non_iso_currency() -> None:
    with pytest.raises(ValidationError):
        ModelOutput.model_validate({**VALID, "currency": "dollars"})


def test_receipt_record_from_model_output() -> None:
    out = ModelOutput.model_validate(VALID)
    rec = ReceiptRecord.from_model_output(out, model_id="claude-sonnet-5", prompt_version="1")
    assert rec.model_id == "claude-sonnet-5"
    assert rec.prompt_version == "1"
    assert rec.vendor == "Trader Joe's"


def test_json_schema_has_no_model_id() -> None:
    assert "model_id" not in RECEIPT_OUTPUT_JSON_SCHEMA["properties"]  # type: ignore[index]
    assert RECEIPT_OUTPUT_JSON_SCHEMA["additionalProperties"] is False
