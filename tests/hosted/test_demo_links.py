from dataclasses import asdict
from datetime import UTC, datetime, timedelta

from receipt_sentinel.hosted.demo_links import (
    CALL_CAP,
    LINK_LIFETIME,
    DemoLink,
    InMemoryLinkStore,
    hash_token,
    issue_link,
    link_state,
    try_spend_call,
)

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)


def test_issue_stores_only_the_hash() -> None:
    store = InMemoryLinkStore()
    token, link = issue_link(store, "Acme - Jane", NOW)
    assert link.token_sha256 == hash_token(token)
    assert all(token not in str(value) for value in asdict(link).values())
    assert store.get(hash_token(token)) is not None


def test_spend_counts_and_reports_calls_left() -> None:
    store = InMemoryLinkStore()
    token, _ = issue_link(store, "a", NOW)
    decision = try_spend_call(store, token, NOW)
    assert decision.allowed and decision.calls_left == CALL_CAP - 1


def test_first_spend_reports_first_use_once() -> None:
    store = InMemoryLinkStore()
    token, _ = issue_link(store, "a", NOW)
    assert try_spend_call(store, token, NOW).first_use
    assert not try_spend_call(store, token, NOW).first_use


def test_used_up_link_is_refused() -> None:
    store = InMemoryLinkStore()
    token, _ = issue_link(store, "a", NOW)
    for _ in range(CALL_CAP):
        assert try_spend_call(store, token, NOW).allowed
    decision = try_spend_call(store, token, NOW)
    assert not decision.allowed and decision.state == "used_up" and decision.calls_left == 0


def test_link_expires_at_exactly_fourteen_days() -> None:
    store = InMemoryLinkStore()
    token, _ = issue_link(store, "a", NOW)
    just_before = NOW + LINK_LIFETIME - timedelta(microseconds=1)
    assert try_spend_call(store, token, just_before).allowed
    expired = try_spend_call(store, token, NOW + LINK_LIFETIME)
    assert not expired.allowed and expired.state == "expired"


def test_revoked_link_is_refused() -> None:
    store = InMemoryLinkStore()
    token, link = issue_link(store, "Acme - Jane", NOW)
    assert store.revoke("Acme - Jane")
    decision = try_spend_call(store, token, NOW)
    assert not decision.allowed and decision.state == "revoked"
    assert store.revoke(link.link_id)  # by id works too


def test_unknown_token_is_refused() -> None:
    decision = try_spend_call(InMemoryLinkStore(), "not-a-token", NOW)
    assert not decision.allowed and decision.state == "unknown"
    assert link_state(None, NOW) == "unknown"


class StaleOnceStore(InMemoryLinkStore):
    """Hands every reader the same pre-spend row once, as two simultaneous requests would see."""

    def __init__(self) -> None:
        super().__init__()
        self.stale: DemoLink | None = None
        self.stale_reads = 0

    def get(self, token_sha256: str) -> DemoLink | None:
        if self.stale is not None and self.stale_reads < 2:
            self.stale_reads += 1
            return self.stale
        return super().get(token_sha256)


def test_last_call_race_lets_exactly_one_through() -> None:
    store = StaleOnceStore()
    token, _ = issue_link(store, "a", NOW)
    for _ in range(CALL_CAP - 1):
        try_spend_call(store, token, NOW)
    store.stale = store.get(hash_token(token))  # 24/25 used, seen by both requests
    first = try_spend_call(store, token, NOW)
    second = try_spend_call(store, token, NOW)
    assert [first.allowed, second.allowed].count(True) == 1
