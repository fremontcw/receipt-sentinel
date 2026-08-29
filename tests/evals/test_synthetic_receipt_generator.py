"""test_synthetic_receipt_generator.

Tests for the seeded synthetic golden case generator.

Status:
    None
Issues:
    None
Todo:
    None
"""

from decimal import Decimal

from receipt_sentinel.evals.golden_case import Difficulty, GoldenCase
from receipt_sentinel.evals.synthetic_receipt_generator import (
    generate_synthetic_cases,
    golden_case_to_yaml,
)


def test_generation_is_deterministic() -> None:
    """Verify the same seed produces identical cases across runs."""
    a = generate_synthetic_cases(seed=7, count=20)
    b = generate_synthetic_cases(seed=7, count=20)
    assert [c.model_dump() for c in a] == [c.model_dump() for c in b]


def test_totals_equal_sum_of_line_items() -> None:
    """Verify every generated case's total matches the sum of its line items."""
    for case in generate_synthetic_cases(seed=1, count=40):
        assert case.expected.total == sum(
            (li.amount for li in case.expected.line_items), Decimal("0")
        ), case.id


def test_covers_every_difficulty_except_adversarial_and_has_a_refund() -> None:
    """Verify the generator produces easy/medium/hard cases, a refund, and a EUR case."""
    cases = generate_synthetic_cases(seed=3, count=40)
    difficulties = {c.difficulty for c in cases}
    assert difficulties == {Difficulty.EASY, Difficulty.MEDIUM, Difficulty.HARD}
    assert any(c.expected.total < 0 for c in cases)
    assert any(c.expected.currency == "EUR" for c in cases)


def test_yaml_round_trips() -> None:
    """Verify a generated case survives a YAML dump/load round trip unchanged."""
    import yaml

    case = generate_synthetic_cases(seed=5, count=1)[0]
    assert GoldenCase.model_validate(yaml.safe_load(golden_case_to_yaml(case))) == case
