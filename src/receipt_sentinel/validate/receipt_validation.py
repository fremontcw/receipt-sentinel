"""receipt_validation

Per-rule deterministic validation of an extracted receipt: line items add up to the total,
the receipt date is plausible for when it was emailed, and the currency is a real ISO code.

Status:
    None
Issues:
    None
Todo:
    None
"""

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Literal

from receipt_sentinel.categorize.receipt_record import ModelOutput
from receipt_sentinel.validate.iso4217_currency_codes import ISO_4217_CURRENCY_CODES

TOTAL_TOLERANCE = Decimal("0.01")
MAX_RECEIPT_AGE = timedelta(days=60)

RuleName = Literal["line_items_sum_to_total", "date_in_window", "currency_is_iso4217"]


@dataclass(frozen=True)
class ValidationRuleResult:
    rule: RuleName
    passed: bool
    detail: str


@dataclass(frozen=True)
class ValidationResult:
    rules: tuple[ValidationRuleResult, ...]

    @property
    def passed(self) -> bool:
        return all(rule.passed for rule in self.rules)


def validate_receipt(record: ModelOutput, *, email_date: date) -> ValidationResult:
    """Run every deterministic rule on one receipt.

    Args:
        record: The model's structured output (or a ReceiptRecord).
        email_date: When the receipt arrived; it must be dated within 60 days before.

    Returns:
        One result per rule, each with a human-readable detail.
    """
    return ValidationResult(
        (
            _line_items_sum_to_total(record),
            _date_in_window(record, email_date),
            _currency_is_iso4217(record),
        )
    )


def _line_items_sum_to_total(record: ModelOutput) -> ValidationRuleResult:
    if not record.line_items:
        return ValidationRuleResult("line_items_sum_to_total", True, "no line items to check")
    items_sum = sum((item.amount for item in record.line_items), Decimal("0"))
    passed = abs(items_sum - record.total) <= TOTAL_TOLERANCE
    relation = "=" if passed else "≠"
    detail = f"items sum {items_sum} {relation} total {record.total}"
    return ValidationRuleResult("line_items_sum_to_total", passed, detail)


def _date_in_window(record: ModelOutput, email_date: date) -> ValidationRuleResult:
    earliest = email_date - MAX_RECEIPT_AGE
    passed = earliest <= record.date <= email_date
    detail = (
        f"receipt date {record.date} {'within' if passed else 'outside'} {earliest}…{email_date}"
    )
    return ValidationRuleResult("date_in_window", passed, detail)


def _currency_is_iso4217(record: ModelOutput) -> ValidationRuleResult:
    passed = record.currency in ISO_4217_CURRENCY_CODES
    detail = f"{record.currency} is {'an' if passed else 'not an'} ISO 4217 currency code"
    return ValidationRuleResult("currency_is_iso4217", passed, detail)
