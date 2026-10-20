"""demo_links

Personal demo links: the author issues one per person, it unlocks the real model for
CALL_CAP calls over LINK_LIFETIME, and it can be revoked. Only the token's SHA-256 is
stored. Calls are counted with an ETag-conditional write, so two tabs can't both spend
the last call.

Status:
    None
Issues:
    None
Todo:
    None
"""

import hashlib
import secrets
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Literal, Protocol

LINK_LIFETIME = timedelta(days=14)
CALL_CAP = 25
_TOKEN_BYTES = 32

LinkState = Literal["active", "expired", "revoked", "used_up", "unknown"]


@dataclass(frozen=True)
class DemoLink:
    link_id: str
    label: str
    token_sha256: str
    created_at: datetime
    expires_at: datetime
    calls_used: int
    call_cap: int
    first_used_at: datetime | None
    revoked: bool
    etag: str


@dataclass(frozen=True)
class SpendDecision:
    allowed: bool
    state: LinkState
    calls_left: int
    first_use: bool
    link: DemoLink | None


class LinkStore(Protocol):
    def add(self, link: DemoLink) -> None: ...

    def get(self, token_sha256: str) -> DemoLink | None: ...

    def list_all(self) -> list[DemoLink]: ...

    def replace_if_unchanged(self, link: DemoLink, etag: str) -> bool:
        """Write `link` only if the stored row still has `etag`; False if it changed."""
        ...

    def revoke(self, link_id_or_label: str) -> bool:
        """Revoke the link with this id or exact label; False if there is none."""
        ...


class InMemoryLinkStore:
    """LinkStore for tests and local runs, with real ETag semantics."""

    def __init__(self) -> None:
        self._rows: dict[str, DemoLink] = {}

    def add(self, link: DemoLink) -> None:
        self._rows[link.token_sha256] = replace(link, etag=uuid.uuid4().hex)

    def get(self, token_sha256: str) -> DemoLink | None:
        return self._rows.get(token_sha256)

    def list_all(self) -> list[DemoLink]:
        return list(self._rows.values())

    def replace_if_unchanged(self, link: DemoLink, etag: str) -> bool:
        current = self._rows.get(link.token_sha256)
        if current is None or current.etag != etag:
            return False
        self._rows[link.token_sha256] = replace(link, etag=uuid.uuid4().hex)
        return True

    def revoke(self, link_id_or_label: str) -> bool:
        for link in self._rows.values():
            if link_id_or_label in (link.link_id, link.label):
                return self.replace_if_unchanged(replace(link, revoked=True), link.etag)
        return False


def hash_token(token: str) -> str:
    """SHA-256 hex of a link token. A 256-bit random token needs no slow hash."""
    return hashlib.sha256(token.encode()).hexdigest()


def issue_link(store: LinkStore, label: str, now: datetime) -> tuple[str, DemoLink]:
    """Create and store a new personal link.

    Returns:
        The raw token (shown once, to put in the email) and the stored row.
    """
    token = secrets.token_urlsafe(_TOKEN_BYTES)
    token_sha256 = hash_token(token)
    link = DemoLink(
        link_id=token_sha256[:12],
        label=label,
        token_sha256=token_sha256,
        created_at=now,
        expires_at=now + LINK_LIFETIME,
        calls_used=0,
        call_cap=CALL_CAP,
        first_used_at=None,
        revoked=False,
        etag="",
    )
    store.add(link)
    return token, link


def link_state(link: DemoLink | None, now: datetime) -> LinkState:
    """Why a link can or can't be used right now."""
    if link is None:
        return "unknown"
    if link.revoked:
        return "revoked"
    if now >= link.expires_at:
        return "expired"
    if link.calls_used >= link.call_cap:
        return "used_up"
    return "active"


def try_spend_call(store: LinkStore, token: str, now: datetime, attempts: int = 3) -> SpendDecision:
    """Use one of the link's calls, if it has one left.

    Re-reads and retries when another request wrote the row first; after `attempts`
    conflicts it refuses (fail closed).
    """
    token_sha256 = hash_token(token)
    link: DemoLink | None = None
    for _ in range(attempts):
        link = store.get(token_sha256)
        state = link_state(link, now)
        if link is None or state != "active":
            left = 0 if link is None else max(0, link.call_cap - link.calls_used)
            return SpendDecision(False, state, left, False, link)
        first_use = link.first_used_at is None
        spent = replace(
            link, calls_used=link.calls_used + 1, first_used_at=link.first_used_at or now
        )
        if store.replace_if_unchanged(spent, link.etag):
            return SpendDecision(
                True, "active", spent.call_cap - spent.calls_used, first_use, spent
            )
    return SpendDecision(False, "active", 0, False, link)
