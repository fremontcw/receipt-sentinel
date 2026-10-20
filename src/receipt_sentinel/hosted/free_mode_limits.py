"""free_mode_limits

Free mode costs Azure CPU (rendering and OCR), so it has two limits: per visitor IP in a
sliding window, and per UTC day across everyone (spec §6, §8).

Status:
    None
Issues:
    None
Todo:
    None
"""

from collections import deque
from datetime import UTC, datetime, timedelta
from typing import Literal

LimitResult = Literal["ok", "ip_limited", "day_limited"]


class FreeModeLimiter:
    """In-memory limiter.

    ponytail: in memory is correct because the app runs one replica (maxReplicas 1).
    The per-IP limit trusts X-Forwarded-For, which a client can vary; the per-day cap is
    the backstop. Move to Table Storage if the app ever scales out.
    """

    def __init__(
        self, per_ip: int = 30, window: timedelta = timedelta(minutes=10), per_day: int = 300
    ) -> None:
        self._per_ip = per_ip
        self._window = window
        self._per_day = per_day
        self._hits: dict[str, deque[datetime]] = {}
        self._day = ""
        self._day_count = 0

    def check(self, ip: str, now: datetime) -> LimitResult:
        """Record one attempt from `ip` and say whether it may go ahead."""
        day = now.astimezone(UTC).strftime("%Y-%m-%d")
        if day != self._day:
            self._day, self._day_count = day, 0
            self._hits.clear()  # ponytail: also bounds the dict's memory to one day of IPs
        if self._day_count >= self._per_day:
            return "day_limited"
        hits = self._hits.setdefault(ip, deque())
        while hits and hits[0] <= now - self._window:
            hits.popleft()
        if len(hits) >= self._per_ip:
            return "ip_limited"
        hits.append(now)
        self._day_count += 1
        return "ok"
