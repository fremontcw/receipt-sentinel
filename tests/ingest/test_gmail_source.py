import base64
import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from google.auth.exceptions import RefreshError
from google.oauth2.credentials import Credentials

from receipt_sentinel.cli import main
from receipt_sentinel.ingest.gmail_source import GMAIL_SCOPES, GmailAuthExpired, GmailSource


class _Call:
    def __init__(self, result: dict[str, object]) -> None:
        self.result = result

    def execute(self) -> dict[str, object]:
        return self.result


class FakeMessages:
    """Gmail's users().messages() resource over in-memory pages and raw messages."""

    def __init__(self, pages: list[list[str]], raws: dict[str, bytes]) -> None:
        self.pages, self.raws = pages, raws
        self.list_calls: list[dict[str, object]] = []
        self.get_calls: list[tuple[str, str]] = []

    def list(self, **kwargs: object) -> _Call:
        self.list_calls.append(kwargs)
        index = int(str(kwargs.get("pageToken") or 0))
        page: dict[str, object] = {"messages": [{"id": i} for i in self.pages[index]]}
        if index + 1 < len(self.pages):
            page["nextPageToken"] = str(index + 1)
        return _Call(page)

    def get(self, **kwargs: object) -> _Call:
        assert kwargs["userId"] == "me"
        message_id = str(kwargs["id"])
        self.get_calls.append((message_id, str(kwargs["format"])))
        reply: dict[str, object] = {"id": message_id, "internalDate": "1787050800000"}
        header_block, _, _ = self.raws[message_id].partition(b"\r\n\r\n")
        if kwargs["format"] == "metadata":
            wanted = {str(h).lower() for h in kwargs["metadataHeaders"]}  # type: ignore[attr-defined]
            headers = [line.split(": ", 1) for line in header_block.decode().split("\r\n")]
            reply["payload"] = {
                "headers": [{"name": n, "value": v} for n, v in headers if n.lower() in wanted]
            }
        else:
            assert kwargs["format"] == "raw"
            reply["raw"] = base64.urlsafe_b64encode(self.raws[message_id]).decode().rstrip("=")
        return _Call(reply)


class FakeService:
    def __init__(self, messages: FakeMessages) -> None:
        self._messages = messages

    def users(self) -> "FakeService":
        return self

    def messages(self) -> FakeMessages:
        return self._messages


def test_lists_headers_first_and_fetches_raw_only_on_demand() -> None:
    full = b"From: x@y.com\r\nSubject: Hi\r\n\r\nhello"
    messages = FakeMessages([["a"]], {"a": full})
    source = GmailSource(FakeService(messages))
    (listed,) = source.list_messages(since=date(2026, 8, 1))
    assert listed.message_id == "a"
    assert listed.raw.startswith(b"From: x@y.com") and b"hello" not in listed.raw
    assert messages.get_calls == [("a", "metadata")]
    assert source.full_message(listed) == full
    assert messages.list_calls[0]["q"] == "has:attachment after:2026/08/01 -in:sent -in:drafts"
    assert messages.list_calls[0]["userId"] == "me"


def test_follows_page_tokens_up_to_limit() -> None:
    pages = [["a"], ["b"], ["c"]]
    messages = FakeMessages(pages, {k: b"From: x@y.com\r\n\r\n" for k in "abc"})
    found = GmailSource(FakeService(messages), page_limit=2).list_messages(since=date(2026, 8, 1))
    assert [m.message_id for m in found] == ["a", "b"]


def test_received_at_comes_from_internal_date() -> None:
    messages = FakeMessages([["a"]], {"a": b"From: x@y.com\r\n\r\n"})
    (raw,) = GmailSource(FakeService(messages)).list_messages(since=date(2026, 8, 1))
    assert raw.received_at == datetime.fromtimestamp(1787050800, tz=UTC)


def test_scope_is_readonly_only() -> None:
    assert GMAIL_SCOPES == ("https://www.googleapis.com/auth/gmail.readonly",)


def test_expired_token_is_one_readable_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    token = tmp_path / "token.json"
    token.write_text(
        json.dumps(
            {
                "token": "old",
                "refresh_token": "revoked",
                "client_id": "id",
                "client_secret": "secret",
                "expiry": "2020-01-01T00:00:00Z",
                "scopes": list(GMAIL_SCOPES),
            }
        )
    )

    def revoked(self: Credentials, request: object) -> None:
        raise RefreshError("invalid_grant: Token has been expired or revoked.")

    monkeypatch.setattr(Credentials, "refresh", revoked)
    argv = ["ingest", "--gmail", "--gmail-token", str(token)]
    assert main([*argv, "--gmail-credentials", str(tmp_path / "credentials.json")]) == 2
    err = capsys.readouterr().err
    assert "--reauth" in err and "Traceback" not in err


class _RevokedCall:
    def execute(self) -> dict[str, object]:
        raise RefreshError("invalid_grant: Token has been expired or revoked.")


def test_revocation_mid_run_is_auth_expired() -> None:
    messages = FakeMessages([["a"]], {"a": b"From: x@y.com\r\n\r\n"})
    messages.list = lambda **kwargs: _RevokedCall()  # type: ignore[method-assign]
    with pytest.raises(GmailAuthExpired):
        GmailSource(FakeService(messages)).list_messages(since=date(2026, 8, 1))


async def test_unknown_sender_message_body_is_never_downloaded() -> None:
    from receipt_sentinel.ingest.ingest_pipeline import ingest_messages
    from receipt_sentinel.ingest.sender_allowlist import SenderAllowlist

    raw = b"From: spam@unknown.example\r\nSubject: Invoice\r\n\r\nbody"
    messages = FakeMessages([["a"]], {"a": raw})
    nobody = SenderAllowlist(frozenset(), frozenset(), "mx.google.com")
    report = await ingest_messages(
        GmailSource(FakeService(messages)),
        nobody,
        since=date(2026, 8, 1),
        prompt=None,
        client=None,
        model_id="fake",  # type: ignore[arg-type]
    )
    assert report.unknown_senders[0].address == "spam@unknown.example"
    assert ("a", "raw") not in messages.get_calls


def test_corrupt_token_file_is_one_readable_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    token = tmp_path / "token.json"
    token.write_text("not json")
    argv = ["ingest", "--gmail", "--gmail-token", str(token)]
    assert main([*argv, "--gmail-credentials", str(tmp_path / "credentials.json")]) == 2
    err = capsys.readouterr().err
    assert "--reauth" in err and "Traceback" not in err


def test_until_adds_a_before_clause() -> None:
    messages = FakeMessages([["a"]], {"a": b"From: x@y.com\r\n\r\n"})
    GmailSource(FakeService(messages)).list_messages(
        since=date(2026, 8, 1), until=date(2026, 10, 1)
    )
    assert "before:2026/10/01" in str(messages.list_calls[0]["q"])
