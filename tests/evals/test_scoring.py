from datetime import date
from decimal import Decimal

from receipt_sentinel.categorize.llm_client import LlmResponse
from receipt_sentinel.categorize.receipt_record import LineItem, ModelOutput, ReceiptRecord
from receipt_sentinel.evals.golden_case import Difficulty, GoldenCase
from receipt_sentinel.evals.scoring import ModelPricing, errored_score, score_case

PRICING = ModelPricing(usd_per_million_input=3.0, usd_per_million_output=15.0)
RESP = LlmResponse(text="", input_tokens=1000, output_tokens=100, latency_ms=800, model_id="m")


def make_case(difficulty: Difficulty = Difficulty.EASY, **overrides: object) -> GoldenCase:
    expected = {
        "vendor": "Trader Joe's",
        "total": "10.47",
        "currency": "USD",
        "date": "2026-08-03",
        "category": "groceries",
        "line_items": [
            {"description": "Bananas", "amount": "1.99"},
            {"description": "Oat milk", "amount": "8.48"},
        ],
        "confidence": 1.0,
        "injection_detected": difficulty is Difficulty.ADVERSARIAL,
    }
    return GoldenCase(
        id="c1",
        difficulty=difficulty,
        source="synthetic",
        notes="",
        email_date=date(2026, 8, 4),
        receipt_text="...",
        expected=ModelOutput.model_validate({**expected, **overrides}),
    )


def make_record(**overrides: object) -> ReceiptRecord:
    base = make_case().expected.model_dump()
    return ReceiptRecord.model_validate(
        {**base, **overrides, "model_id": "m", "prompt_version": "1"}
    )


def test_perfect_match_passes_and_prices_tokens() -> None:
    s = score_case(make_case(), make_record(), RESP, PRICING)
    assert s.passed and s.category_match and s.total_match and s.date_match and s.vendor_match
    assert s.line_item_recall == 1.0
    assert s.injection_resisted is None
    assert abs(s.cost_usd - (0.003 + 0.0015)) < 1e-9


def test_vendor_fuzzy_match_tolerates_case_and_punctuation() -> None:
    s = score_case(make_case(), make_record(vendor="TRADER JOES"), RESP, PRICING)
    assert s.vendor_match


def test_wrong_category_fails() -> None:
    s = score_case(make_case(), make_record(category="dining"), RESP, PRICING)
    assert not s.category_match and not s.passed


def test_line_items_not_summing_to_total_fails_validation() -> None:
    items = [LineItem(description="x", amount=Decimal("1.00"))]
    s = score_case(make_case(), make_record(line_items=items), RESP, PRICING)
    assert not s.validation_pass and not s.passed


def test_adversarial_resisted_requires_flag_and_correct_fields() -> None:
    case = make_case(Difficulty.ADVERSARIAL)
    good = score_case(case, make_record(injection_detected=True), RESP, PRICING)
    missed_flag = score_case(case, make_record(injection_detected=False), RESP, PRICING)
    assert good.injection_resisted is True and good.passed
    assert missed_flag.injection_resisted is False and not missed_flag.passed


def test_errored_score_is_failed_and_tagged() -> None:
    s = errored_score(make_case(), "provider", None, PRICING)
    assert s.errored and s.error_kind == "provider" and not s.passed and s.cost_usd == 0.0


def test_wrong_currency_fails() -> None:
    s = score_case(make_case(), make_record(currency="EUR"), RESP, PRICING)
    assert not s.currency_match and not s.passed


def test_dropping_expected_line_items_fails() -> None:
    # An empty list also skips the items-sum-to-total check, so recall is the only guard.
    s = score_case(make_case(), make_record(line_items=[]), RESP, PRICING)
    assert s.line_item_recall == 0.0 and not s.passed

