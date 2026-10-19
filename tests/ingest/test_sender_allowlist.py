import base64
from pathlib import Path

import pytest
from receipt_sentinel.ingest.sender_allowlist import (
    SenderAllowlist,
    decide_sender,
    load_sender_allowlist,
    parse_single_sender,
)

from receipt_sentinel.ingest.inbound_message import parse_message_headers
from tests.ingest.email_builders import build_eml, gmail_dmarc_pass

SHOP = SenderAllowlist(
    addresses=frozenset({"receipts@shop.com"}),
    domains=frozenset({"shop.com", "trusted.com", "x.com"}),
    trusted_authserv_id="mx.google.com",
)


def _decide(from_header: str, auth: list[str]) -> tuple[bool, str]:
    decision = decide_sender(parse_message_headers(build_eml(from_header, auth_results=auth)), SHOP)
    return decision.allowed, decision.reason


def test_exact_address_with_dmarc_pass_is_allowed() -> None:
    assert _decide("Shop <receipts@shop.com>", [gmail_dmarc_pass("shop.com")]) == (
        True,
        "allowlisted address",
    )


def test_listed_domain_with_dmarc_pass_is_allowed() -> None:
    assert _decide("orders@shop.com", [gmail_dmarc_pass("shop.com")]) == (
        True,
        "allowlisted domain",
    )


def test_display_name_spoof_is_not_allowed() -> None:
    allowed, _ = _decide('"bank@trusted.com" <evil@x.io>', [gmail_dmarc_pass("x.io")])
    assert not allowed


def test_subdomain_does_not_match_parent_domain() -> None:
    allowed, reason = _decide("a@mail.shop.com", [gmail_dmarc_pass("mail.shop.com")])
    assert (allowed, reason) == (False, "not on allowlist")


@pytest.mark.parametrize("sender", ["a@shop.com.evil.io", "a@sh0p.com", "a@xn--shp-sna.com"])
def test_lookalike_domain_does_not_match(sender: str) -> None:
    domain = sender.split("@")[1]
    assert _decide(sender, [gmail_dmarc_pass(domain)]) == (False, "not on allowlist")


@pytest.mark.parametrize("from_header", ["", "not an address", "a@x.com, b@x.com", "<>"])
def test_malformed_or_multiple_from_is_rejected(from_header: str) -> None:
    assert _decide(from_header, [gmail_dmarc_pass("x.com")]) == (False, "malformed From")


def test_forged_from_without_dmarc_is_not_allowed() -> None:
    assert _decide("receipts@shop.com", []) == (False, "no DMARC pass from trusted server")


def test_dmarc_pass_for_other_domain_is_not_enough() -> None:
    allowed, reason = _decide("receipts@shop.com", [gmail_dmarc_pass("evil.io")])
    assert (allowed, reason) == (False, "no DMARC pass from trusted server")


def test_dmarc_pass_from_untrusted_server_is_not_enough() -> None:
    allowed, _ = _decide("receipts@shop.com", ["evil.io; dmarc=pass header.from=shop.com"])
    assert not allowed


def test_only_the_topmost_trusted_auth_header_counts() -> None:
    real = "mx.google.com; dmarc=fail (p=REJECT) header.from=shop.com"
    forged = gmail_dmarc_pass("shop.com")  # added by the sender, so it sits below the real one
    assert _decide("receipts@shop.com", [real, forged]) == (
        False,
        "no DMARC pass from trusted server",
    )


def test_encoded_word_display_name_is_ignored() -> None:
    encoded = base64.b64encode(b"bank@trusted.com").decode()
    allowed, _ = _decide(f"=?utf-8?b?{encoded}?= <evil@x.io>", [gmail_dmarc_pass("x.io")])
    assert not allowed


def test_case_insensitive_match() -> None:
    assert _decide("Receipts@SHOP.com", [gmail_dmarc_pass("SHOP.com")])[0]


def test_parse_single_sender_returns_lowercased_address() -> None:
    assert parse_single_sender("Shop <Receipts@Shop.COM>") == "receipts@shop.com"
    assert parse_single_sender("a@x.com, b@y.com") is None


def test_missing_allowlist_file_is_empty(tmp_path: Path) -> None:
    allowlist = load_sender_allowlist(tmp_path / "absent.yaml")
    assert allowlist.addresses == frozenset() and allowlist.domains == frozenset()
    assert allowlist.trusted_authserv_id == "mx.google.com"


def test_allowlist_file_entries_are_lowercased(tmp_path: Path) -> None:
    path = tmp_path / "allowlist.yaml"
    path.write_text("addresses: [Receipts@Shop.com]\ndomains: [Shop.COM]\n")
    allowlist = load_sender_allowlist(path)
    assert allowlist.addresses == {"receipts@shop.com"} and allowlist.domains == {"shop.com"}
