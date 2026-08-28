"""generate_synthetic_golden_cases

Writes seeded synthetic golden cases into the package's golden/cases directory.
Run: uv run python scripts/generate_synthetic_golden_cases.py --count 48 --seed 2026

Status:
    None
Issues:
    None
Todo:
    None
"""

import argparse

from receipt_sentinel.evals.golden_case import GOLDEN_DIR
from receipt_sentinel.evals.synthetic_receipt_generator import (
    generate_synthetic_cases,
    golden_case_to_yaml,
)


def main() -> int:
    """Generate and write synthetic golden cases per CLI args."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=48)
    parser.add_argument("--seed", type=int, default=2026)
    args = parser.parse_args()
    for case in generate_synthetic_cases(seed=args.seed, count=args.count):
        (GOLDEN_DIR / "cases" / f"{case.id}.yaml").write_text(golden_case_to_yaml(case))
    print(f"wrote {args.count} cases to {GOLDEN_DIR / 'cases'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
