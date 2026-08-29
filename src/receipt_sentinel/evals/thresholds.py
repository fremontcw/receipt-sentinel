"""thresholds

Regression-gate thresholds (loaded from config/thresholds.yaml) and the status rule that
turns a diff into PASS / WARN / CRITICAL / PROVIDER_UNSTABLE.

Status:
    None
Issues:
    None
Todo:
    None
"""

from dataclasses import dataclass
from pathlib import Path

import yaml

from receipt_sentinel.evals.run_store import RunStatus


@dataclass(frozen=True)
class Thresholds:
    """Regression gate thresholds with default values."""

    warn_delta_points: float = -3.0
    critical_delta_points: float = -8.0
    max_error_rate: float = 0.05
    drift_floor_points: float = 2.0
    drift_window_runs: int = 7
    run_cost_ceiling_usd: float = 2.0
    max_concurrency: int = 8


def load_thresholds(path: Path = Path("config/thresholds.yaml")) -> Thresholds:
    """Load thresholds from a YAML config file.

    Args:
        path: Path to the YAML config file (default: config/thresholds.yaml)

    Returns:
        Thresholds instance with values from the config file
    """
    return Thresholds(**yaml.safe_load(path.read_text()))


def classify_status(
    *,
    pass_rate_delta_points: float,
    adversarial_failures: int,
    error_rate: float,
    thresholds: Thresholds,
) -> RunStatus:
    """Classify run status based on regression metrics and thresholds.

    Classification rules (checked in order):
    1. If error_rate exceeds max_error_rate → PROVIDER_UNSTABLE
    2. If any adversarial failures OR pass_rate_delta <= critical_delta → CRITICAL
    3. If pass_rate_delta <= warn_delta → WARN
    4. Otherwise → PASS

    Args:
        pass_rate_delta_points: Change in pass rate vs baseline (percentage points)
        adversarial_failures: Count of adversarial test failures
        error_rate: Fraction of requests with errors
        thresholds: Thresholds configuration

    Returns:
        RunStatus classification (PASS, WARN, CRITICAL, or PROVIDER_UNSTABLE)
    """
    if error_rate > thresholds.max_error_rate:
        return RunStatus.PROVIDER_UNSTABLE
    if adversarial_failures > 0 or pass_rate_delta_points <= thresholds.critical_delta_points:
        return RunStatus.CRITICAL
    if pass_rate_delta_points <= thresholds.warn_delta_points:
        return RunStatus.WARN
    return RunStatus.PASS
