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

from dataclasses import dataclass
from email import policy
from email.headerregistry import Address
from pathlib import Path

import yaml

from receipt_sentinel.ingest.inbound_message import MessageHeaders

DEFAULT_TRUSTED_AUTHSERV_ID = "mx.google.com"


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
    addresses: tuple[Address, ...] = getattr(header, "addresses", ())
    if len(addresses) != 1:
        return None
    address = addresses[0]
    if not address.username or not address.domain:
        return None
    return f"{address.username}@{address.domain}".lower()


def decide_sender(headers: MessageHeaders, allowlist: SenderAllowlist) -> SenderDecision:
    """Allowed only when the From address (or its exact domain) is listed AND the topmost
    Authentication-Results header, from our receiving server, has exactly one dmarc clause:
    a pass for that domain."""
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
    # Our receiving server prepends its verdict, so it must be the topmost header; anything
    # below it, even one claiming our server's name, could have been written by the sender
    if not authentication_results:
        return False
    clauses = _structural_text(authentication_results[0]).split(";")
    authserv_id = (clauses[0].split() or [""])[0].lower()
    if authserv_id != allowlist.trusted_authserv_id.lower():
        return False
    return _dmarc_clause_passes(clauses[1:], domain)


def _dmarc_clause_passes(clauses: list[str], domain: str) -> bool:
    dmarc_clauses = [
        tokens
        for tokens in (c.split() for c in clauses)
        if tokens and tokens[0].lower().startswith("dmarc=")
    ]
    # More than one means sender text (e.g. an unquoted smtp.mailfrom) forged a clause
    if len(dmarc_clauses) != 1:
        return False
    tokens = dmarc_clauses[0]
    properties = dict(token.split("=", 1) for token in tokens[1:] if "=" in token)
    header_from = properties.get("header.from", "").lower()
    return tokens[0][len("dmarc=") :].lower() == "pass" and header_from == domain


def _structural_text(header: str) -> str:
    """The header with comments removed and quoted strings blanked to "" (RFC 8601 / 5322).

    Comments and quoted strings echo sender-chosen text (the envelope address in an SPF
    comment, a quoted local part); blanking them keeps their `;` and `dmarc=` from being
    read as structure.
    """
    out: list[str] = []
    depth, quoted, escaped = 0, False, False
    for char in header:
        if escaped:
            escaped = False
        elif char == "\\" and (quoted or depth):
            escaped = True
        elif quoted:
            if char == '"':
                quoted = False
                out.append('"')
        elif char == "(":
            depth += 1
        elif char == ")" and depth:
            depth -= 1
        elif depth:
            continue
        elif char == '"':
            quoted = True
            out.append('"')
        else:
            out.append(char)
    return "".join(out)
