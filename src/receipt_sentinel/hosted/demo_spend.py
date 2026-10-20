"""demo_spend

The app's own monthly cap on real-model spend (spec §6): each call's cost is added to a
per-month total, and at MONTHLY_CAP_USD everyone drops to free mode until the next UTC
month. Anthropic's workspace limit sits above this as the hard stop.

Status:
    None
Issues:
    None
Todo:
    None
"""

from datetime import UTC, datetime
from typing import Protocol

MONTHLY_CAP_USD = 8.0


class SpendStore(Protocol):
    def spent_usd(self, month: str) -> float: ...

    def add_usd(self, month: str, amount: float) -> float:
        """Add to the month's total and return the new total."""
        ...

    def mark_cap_alert_sent(self, month: str) -> bool:
        """True only for the first caller in a month, so the cap email goes out once."""
        ...


class InMemorySpendStore:
    """SpendStore for tests and local runs."""

    def __init__(self) -> None:
        self._spent: dict[str, float] = {}
        self._alerted: set[str] = set()

    def spent_usd(self, month: str) -> float:
        return self._spent.get(month, 0.0)

    def add_usd(self, month: str, amount: float) -> float:
        self._spent[month] = round(self.spent_usd(month) + amount, 6)
        return self._spent[month]

    def mark_cap_alert_sent(self, month: str) -> bool:
        if month in self._alerted:
            return False
        self._alerted.add(month)
        return True


def month_key(now: datetime) -> str:
    """The UTC calendar month, e.g. "2026-10"."""
    return now.astimezone(UTC).strftime("%Y-%m")


def is_paused(store: SpendStore, now: datetime, cap_usd: float = MONTHLY_CAP_USD) -> bool:
    """True once this month's real-model spend has reached the cap."""
    return store.spent_usd(month_key(now)) >= cap_usd
