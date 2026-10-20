from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse

import pytest

from receipt_sentinel import cli
from receipt_sentinel.hosted.demo_link_commands import (
    command_demo_link_issue,
    command_demo_link_list,
    command_demo_link_revoke,
)
from receipt_sentinel.hosted.demo_links import InMemoryLinkStore, issue_link, try_spend_call

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)


def test_issue_prints_a_link_whose_token_unlocks(capsys: pytest.CaptureFixture[str]) -> None:
    store = InMemoryLinkStore()
    assert command_demo_link_issue(store, "Acme - Jane", "https://demo.example.dev/", NOW) == 0
    url = urlparse(capsys.readouterr().out.strip().splitlines()[-1])
    assert (url.scheme, url.netloc, url.path) == ("https", "demo.example.dev", "/")
    token = parse_qs(url.query)["k"][0]
    assert try_spend_call(store, token, NOW).allowed


def test_issue_rejects_blank_label(capsys: pytest.CaptureFixture[str]) -> None:
    store = InMemoryLinkStore()
    assert command_demo_link_issue(store, "   ", "https://demo.example.dev", NOW) == 1
    assert store.list_all() == []
    assert "label" in capsys.readouterr().err


def test_list_never_prints_tokens_or_hashes(capsys: pytest.CaptureFixture[str]) -> None:
    store = InMemoryLinkStore()
    token, link = issue_link(store, "Acme - Jane", NOW)
    try_spend_call(store, token, NOW)
    assert command_demo_link_list(store, NOW) == 0
    out = capsys.readouterr().out
    assert "Acme - Jane" in out and "1/25" in out and "2026-11-03" in out and "active" in out
    assert token not in out and link.token_sha256 not in out


def test_revoke_by_label_and_by_id(capsys: pytest.CaptureFixture[str]) -> None:
    store = InMemoryLinkStore()
    _, first = issue_link(store, "Acme - Jane", NOW)
    _, second = issue_link(store, "Other", NOW)
    assert command_demo_link_revoke(store, "Acme - Jane") == 0
    assert command_demo_link_revoke(store, second.link_id) == 0
    assert all(link.revoked for link in store.list_all())


def test_revoke_unknown_exits_1(capsys: pytest.CaptureFixture[str]) -> None:
    assert command_demo_link_revoke(InMemoryLinkStore(), "nobody") == 1
    assert "nobody" in capsys.readouterr().err


def test_cli_names_missing_env_var(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("SENTINEL_TABLES_ENDPOINT", raising=False)
    assert cli.main(["demo-link", "list"]) == 2
    assert "SENTINEL_TABLES_ENDPOINT" in capsys.readouterr().err
    monkeypatch.setenv("SENTINEL_TABLES_ENDPOINT", "https://x.table.core.windows.net")
    monkeypatch.delenv("SENTINEL_PUBLIC_URL", raising=False)
    assert cli.main(["demo-link", "issue", "Acme"]) == 2
    assert "SENTINEL_PUBLIC_URL" in capsys.readouterr().err
