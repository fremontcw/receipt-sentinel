from datetime import date, timedelta
from decimal import Decimal

from receipt_sentinel.categorize.receipt_record import Category, LineItem, ModelOutput
from receipt_sentinel.validate.receipt_validation import validate_receipt

EMAIL_DATE = date(2026, 8, 20)


def _record(**overrides: object) -> ModelOutput:
    fields: dict[str, object] = {
        "vendor": "Blue Bottle Coffee",
        "total": Decimal("12.50"),
        "currency": "USD",
        "date": date(2026, 8, 19),
        "category": Category.DINING,
        "line_items": [
            LineItem(description="Latte", amount=Decimal("6.25")),
            LineItem(description="Scone", amount=Decimal("6.25")),
        ],
        "confidence": 0.9,
        "injection_detected": False,
    }
    return ModelOutput.model_validate({**fields, **overrides})


def _rule(record: ModelOutput, name: str) -> tuple[bool, str]:
    result = validate_receipt(record, email_date=EMAIL_DATE)
    rule = next(r for r in result.rules if r.rule == name)
    return rule.passed, rule.detail


def test_clean_receipt_passes_every_rule() -> None:
    result = validate_receipt(_record(), email_date=EMAIL_DATE)
    assert result.passed
    assert {r.rule for r in result.rules} == {
        "line_items_sum_to_total",
        "date_in_window",
        "currency_is_iso4217",
    }


def test_items_not_summing_to_total_fails_and_names_both_numbers() -> None:
    passed, detail = _rule(_record(total=Decimal("13.50")), "line_items_sum_to_total")
    assert not passed
    assert "12.50" in detail and "13.50" in detail


def test_one_cent_rounding_is_tolerated() -> None:
    assert _rule(_record(total=Decimal("12.51")), "line_items_sum_to_total")[0]


def test_no_line_items_skips_the_sum_rule() -> None:
    assert _rule(_record(line_items=[]), "line_items_sum_to_total")[0]


def test_refund_with_negative_amounts_passes() -> None:
    refund = _record(
        total=Decimal("-6.25"), line_items=[LineItem(description="Refund", amount=Decimal("-6.25"))]
    )
    assert validate_receipt(refund, email_date=EMAIL_DATE).passed


def test_date_window_is_sixty_days_inclusive() -> None:
    assert _rule(_record(date=EMAIL_DATE - timedelta(days=60)), "date_in_window")[0]
    assert not _rule(_record(date=EMAIL_DATE - timedelta(days=61)), "date_in_window")[0]
    assert not _rule(_record(date=EMAIL_DATE + timedelta(days=1)), "date_in_window")[0]


def test_currency_must_be_a_real_iso_code() -> None:
    assert _rule(_record(currency="EUR"), "currency_is_iso4217")[0]
    passed, detail = _rule(_record(currency="XYZ"), "currency_is_iso4217")
    assert not passed and "XYZ" in detail


def test_any_failing_rule_fails_the_result() -> None:
    assert not validate_receipt(_record(currency="XYZ"), email_date=EMAIL_DATE).passed
