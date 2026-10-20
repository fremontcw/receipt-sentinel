from datetime import UTC, datetime, timedelta, timezone

from receipt_sentinel.hosted.demo_spend import (
    MONTHLY_CAP_USD,
    InMemorySpendStore,
    is_paused,
    month_key,
)

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)


def test_spend_accumulates_and_pauses_at_cap() -> None:
    store = InMemorySpendStore()
    store.add_usd(month_key(NOW), 7.99)
    assert not is_paused(store, NOW)
    assert store.add_usd(month_key(NOW), 0.01) == MONTHLY_CAP_USD
    assert is_paused(store, NOW)


def test_spend_month_rolls_over_at_utc_midnight() -> None:
    assert month_key(datetime(2026, 10, 31, 23, 59, 59, tzinfo=UTC)) == "2026-10"
    assert month_key(datetime(2026, 11, 1, 0, 0, 0, tzinfo=UTC)) == "2026-11"
    # 20:00 on the 31st in UTC-5 is already November in UTC
    eastern = timezone(timedelta(hours=-5))
    assert month_key(datetime(2026, 10, 31, 20, 0, tzinfo=eastern)) == "2026-11"


def test_cap_alert_sent_once_per_month() -> None:
    store = InMemorySpendStore()
    assert store.mark_cap_alert_sent("2026-10")
    assert not store.mark_cap_alert_sent("2026-10")
    assert store.mark_cap_alert_sent("2026-11")
