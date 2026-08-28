"""test_thresholds

Tests for thresholds config and status classification.

Status:
    None
Issues:
    None
Todo:
    None
"""

from receipt_sentinel.evals.run_store import RunStatus
from receipt_sentinel.evals.thresholds import (
    Thresholds,
    classify_status,
    load_thresholds,
)

T = Thresholds()


def test_load_defaults_from_repo_config() -> None:
    assert load_thresholds().critical_delta_points == -8.0


def test_pass_when_within_warn() -> None:
    assert (
        classify_status(
            pass_rate_delta_points=-2.9,
            adversarial_failures=0,
            error_rate=0,
            thresholds=T,
        )
        is RunStatus.PASS
    )


def test_warn_and_critical_boundaries() -> None:
    assert (
        classify_status(
            pass_rate_delta_points=-3.0,
            adversarial_failures=0,
            error_rate=0,
            thresholds=T,
        )
        is RunStatus.WARN
    )
    assert (
        classify_status(
            pass_rate_delta_points=-8.0,
            adversarial_failures=0,
            error_rate=0,
            thresholds=T,
        )
        is RunStatus.CRITICAL
    )


def test_any_adversarial_failure_is_critical() -> None:
    assert (
        classify_status(
            pass_rate_delta_points=5.0,
            adversarial_failures=1,
            error_rate=0,
            thresholds=T,
        )
        is RunStatus.CRITICAL
    )


def test_provider_unstable_overrides_everything() -> None:
    assert (
        classify_status(
            pass_rate_delta_points=-50,
            adversarial_failures=3,
            error_rate=0.06,
            thresholds=T,
        )
        is RunStatus.PROVIDER_UNSTABLE
    )
