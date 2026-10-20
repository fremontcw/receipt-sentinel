from datetime import UTC, datetime, timedelta

from receipt_sentinel.hosted.free_mode_limits import FreeModeLimiter

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)


def test_thirty_first_ip_hit_in_window_is_limited() -> None:
    limiter = FreeModeLimiter()
    assert all(limiter.check("1.2.3.4", NOW) == "ok" for _ in range(30))
    assert limiter.check("1.2.3.4", NOW) == "ip_limited"
    assert limiter.check("5.6.7.8", NOW) == "ok"


def test_ip_window_slides() -> None:
    limiter = FreeModeLimiter()
    for _ in range(30):
        limiter.check("1.2.3.4", NOW)
    assert limiter.check("1.2.3.4", NOW + timedelta(minutes=10)) == "ok"


def test_day_cap_counts_all_ips() -> None:
    limiter = FreeModeLimiter()
    for n in range(300):
        assert limiter.check(f"10.0.{n // 250}.{n % 250}", NOW) == "ok"
    assert limiter.check("9.9.9.9", NOW) == "day_limited"


def test_day_cap_resets_at_utc_midnight() -> None:
    limiter = FreeModeLimiter(per_day=1)
    assert limiter.check("a", datetime(2026, 10, 20, 23, 59, tzinfo=UTC)) == "ok"
    assert limiter.check("b", datetime(2026, 10, 20, 23, 59, 30, tzinfo=UTC)) == "day_limited"
    assert limiter.check("c", datetime(2026, 10, 21, 0, 0, tzinfo=UTC)) == "ok"
