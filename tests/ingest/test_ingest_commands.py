import shutil
from datetime import date
from pathlib import Path

import pytest

from receipt_sentinel.evals.eval_commands import REPO_ROOT
from receipt_sentinel.ingest.eml_directory_source import EmlDirectorySource
from receipt_sentinel.ingest.ingest_commands import (
    command_allowlist_add,
    command_ingest,
    render_terminal_safe,
)
from receipt_sentinel.ingest.sender_allowlist import load_sender_allowlist
from tests.ingest.email_builders import build_eml

DEMO_INBOX = REPO_ROOT / "examples" / "demo_inbox"


def _snapshot(*roots: Path) -> dict[Path, float]:
    return {p: p.stat().st_mtime for root in roots for p in root.rglob("*") if p.is_file()}


def test_ingest_demo_inbox_fake_reports_two_receipts_two_unknown(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    inbox = tmp_path / "inbox"
    shutil.copytree(DEMO_INBOX, inbox)
    before = _snapshot(inbox, REPO_ROOT / "config")
    code = command_ingest(
        EmlDirectorySource(inbox),
        allowlist_path=inbox / "allowlist.yaml",
        since=date(2026, 8, 1),
        fake=True,
        provider="anthropic",
        max_messages=50,
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "2 receipt(s)" in out
    assert "injection attempt flagged" in out
    assert "billing@unknown-shop.example" in out and "attacker@evil.example" in out
    assert _snapshot(inbox, REPO_ROOT / "config") == before  # ingest writes nothing


def test_terminal_output_strips_escape_codes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    evil = "Invoice \x1b]8;;http://evil\x07click\x1b]8;;\x07 ‮dcba"
    (tmp_path / "m.eml").write_bytes(build_eml("x@unknown.example", subject=evil))
    command_ingest(
        EmlDirectorySource(tmp_path),
        allowlist_path=tmp_path / "none.yaml",
        since=date(2026, 8, 1),
        fake=True,
        provider="anthropic",
        max_messages=50,
    )
    out = capsys.readouterr().out
    assert "\x1b" not in out and "\x07" not in out and "‮" not in out
    assert "Invoice" in out


def test_render_terminal_safe_keeps_newlines_and_text() -> None:
    assert render_terminal_safe("a\nb\x00c\x9bd") == "a\nbcd"


def test_allowlist_add_address_then_domain_writes_sorted_deduped_yaml(tmp_path: Path) -> None:
    path = tmp_path / "allowlist.yaml"
    assert command_allowlist_add(path, address="Zed@Shop.com", domain=None) == 0
    assert command_allowlist_add(path, address="a@shop.com", domain=None) == 0
    assert command_allowlist_add(path, address="zed@shop.com", domain=None) == 0
    assert command_allowlist_add(path, address=None, domain="Store.Example") == 0
    allowlist = load_sender_allowlist(path)
    assert sorted(allowlist.addresses) == ["a@shop.com", "zed@shop.com"]
    assert allowlist.domains == {"store.example"}
    assert allowlist.trusted_authserv_id == "mx.google.com"
    text = path.read_text()
    assert text.index("a@shop.com") < text.index("zed@shop.com")


@pytest.mark.parametrize(
    ("address", "domain"),
    [("Shop <a@b.com>", None), ("a@x.com, b@y.com", None), (None, "shop"), (None, "-rf")],
)
def test_allowlist_add_rejects_display_name_or_bad_domain(
    tmp_path: Path, address: str | None, domain: str | None
) -> None:
    path = tmp_path / "allowlist.yaml"
    path.write_text("addresses: []\ndomains: []\n")
    before = path.read_text()
    assert command_allowlist_add(path, address=address, domain=domain) == 2
    assert path.read_text() == before
