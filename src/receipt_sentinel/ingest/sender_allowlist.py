"""sender_allowlist

Deterministic sender check that runs before any attachment is opened (spec §4.1): exact
addresses or exact domains of the parsed From address, display names ignored, and, since
a From header is just text anyone can type, a DMARC pass for that domain recorded by our
own receiving server.

Status:
    None
Issues:
    None
Todo:
    None
"""

import re
from dataclasses import dataclass
from email import policy
from pathlib import Path

import yaml

from receipt_sentinel.ingest.inbound_message import MessageHeaders

DEFAULT_TRUSTED_AUTHSERV_ID = "mx.google.com"
_DMARC_RESULT = re.compile(r"\bdmarc=([a-z]+)", re.IGNORECASE)
_DMARC_HEADER_FROM = re.compile(r"\bheader\.from=([^\s;]+)", re.IGNORECASE)


@dataclass(frozen=True)
class SenderAllowlist:
    addresses: frozenset[str]
    domains: frozenset[str]
    # Only Authentication-Results headers stamped by this server are believed
    trusted_authserv_id: str


@dataclass(frozen=True)
class SenderDecision:
    allowed: bool
    sender: str | None
    reason: str


def load_sender_allowlist(path: Path) -> SenderAllowlist:
    """Read config/allowlist.yaml; a missing file is an empty allowlist (allow nobody)."""
    data = yaml.safe_load(path.read_text()) if path.exists() else None
    data = data if isinstance(data, dict) else {}
    return SenderAllowlist(
        addresses=frozenset(str(a).strip().lower() for a in data.get("addresses") or []),
        domains=frozenset(str(d).strip().lower() for d in data.get("domains") or []),
        trusted_authserv_id=str(data.get("trusted_authserv_id") or DEFAULT_TRUSTED_AUTHSERV_ID),
    )


def parse_single_sender(raw_from: str) -> str | None:
    """The one lowercased addr-spec in a From header; None for zero, several or junk."""
    header = policy.default.header_factory("From", raw_from)
    addresses = getattr(header, "addresses", ())
    if len(addresses) != 1:
        return None
    address = addresses[0]
    if not address.username or not address.domain:
        return None
    return f"{address.username}@{address.domain}".lower()


def decide_sender(headers: MessageHeaders, allowlist: SenderAllowlist) -> SenderDecision:
    """Allowed only when the From address (or its exact domain) is listed AND our receiving
    server recorded a DMARC pass for that domain."""
    sender = parse_single_sender(headers.raw_from)
    if sender is None:
        return SenderDecision(False, None, "malformed From")
    domain = sender.rsplit("@", 1)[1]
    if sender in allowlist.addresses:
        reason = "allowlisted address"
    elif domain in allowlist.domains:
        reason = "allowlisted domain"
    else:
        return SenderDecision(False, sender, "not on allowlist")
    if not _trusted_dmarc_pass(headers.authentication_results, domain, allowlist):
        return SenderDecision(False, sender, "no DMARC pass from trusted server")
    return SenderDecision(True, sender, reason)


def _trusted_dmarc_pass(
    authentication_results: tuple[str, ...], domain: str, allowlist: SenderAllowlist
) -> bool:
    # Receiving servers prepend their verdict, so the topmost header from our server is the
    # real one; anything below it could have been written by the sender
    for result in authentication_results:
        authserv_id = result.split(";", 1)[0].strip().split(" ", 1)[0].lower()
        if authserv_id != allowlist.trusted_authserv_id.lower():
            continue
        verdict = _DMARC_RESULT.search(result)
        header_from = _DMARC_HEADER_FROM.search(result)
        return (
            verdict is not None
            and verdict.group(1).lower() == "pass"
            and header_from is not None
            and header_from.group(1).lower() == domain
        )
    return False
