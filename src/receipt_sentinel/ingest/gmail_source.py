"""gmail_source

A read-only MessageSource over the author's Gmail (scope gmail.readonly, nothing else).
Messages come back as raw RFC 822 bytes so the same header-first parser and allowlist
run on them. Needs the `gmail` extra; never runs in CI.

Status:
    None
Issues:
    None
Todo:
    None
"""

import base64
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Protocol

from receipt_sentinel.ingest.inbound_message import RawMessage

GMAIL_SCOPES = ("https://www.googleapis.com/auth/gmail.readonly",)
TOKEN_FILE_MODE = 0o600
HTTP_UNAUTHORIZED = 401
METADATA_HEADERS = ["From", "Subject", "Date", "Authentication-Results"]


class GmailSetupError(RuntimeError):
    """Gmail can't be reached as configured; str() says what to do."""


class GmailAuthExpired(GmailSetupError):  # noqa: N818 - matches ExtractionRejected style
    def __init__(self) -> None:
        super().__init__("Gmail sign-in expired: run sentinel ingest --gmail --reauth")


class _Request(Protocol):
    def execute(self) -> dict[str, object]: ...


class _MessagesResource(Protocol):
    def list(self, **kwargs: object) -> _Request: ...
    def get(self, **kwargs: object) -> _Request: ...


class _UsersResource(Protocol):
    def messages(self) -> _MessagesResource: ...


class GmailService(Protocol):
    """The slice of googleapiclient's Gmail v1 resource this source uses."""

    def users(self) -> _UsersResource: ...


class GmailSource:
    """Messages with attachments since a date. Listing fetches headers only; a message's body
    and attachments are downloaded only when the allowlist asks for them (full_message)."""

    def __init__(self, service: GmailService, *, page_limit: int = 5) -> None:
        self.service = service
        self.page_limit = page_limit

    def list_messages(self, since: date) -> list[RawMessage]:
        messages = self.service.users().messages()
        # Sent and draft mail carry no inbound Authentication-Results, so would only be noise
        query = f"has:attachment after:{since:%Y/%m/%d} -in:sent -in:drafts"
        ids: list[str] = []
        page_token: str | None = None
        for _ in range(self.page_limit):
            page = _execute(messages.list(userId="me", q=query, pageToken=page_token))
            ids += [str(item["id"]) for item in _dicts(page.get("messages"))]
            page_token = str(page["nextPageToken"]) if page.get("nextPageToken") else None
            if page_token is None:
                break
        return [self._fetch_headers(messages, message_id) for message_id in ids]

    def full_message(self, message: RawMessage) -> bytes:
        request = (
            self.service.users().messages().get(userId="me", id=message.message_id, format="raw")
        )
        encoded = str(_execute(request)["raw"])
        return base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))

    @staticmethod
    def _fetch_headers(messages: _MessagesResource, message_id: str) -> RawMessage:
        reply = _execute(
            messages.get(
                userId="me", id=message_id, format="metadata", metadataHeaders=METADATA_HEADERS
            )
        )
        payload = reply.get("payload")
        headers = _dicts(payload.get("headers")) if isinstance(payload, dict) else []
        # Rebuilt as a header block in Gmail's order (the order the allowlist's DMARC check
        # relies on); folding or stray CR/LF in a value can't start a new header
        block = "".join(
            f"{_one_line(h.get('name'))}: {_one_line(h.get('value'))}\r\n" for h in headers
        )
        received_ms = int(str(reply.get("internalDate", "0")))
        received_at = datetime.fromtimestamp(received_ms / 1000, tz=UTC) if received_ms else None
        return RawMessage(message_id, (block + "\r\n").encode(), received_at)


def _execute(request: _Request) -> dict[str, object]:
    try:
        return request.execute()
    except Exception as err:
        if _is_auth_failure(err):
            raise GmailAuthExpired() from err
        raise


def _is_auth_failure(err: Exception) -> bool:
    # Revoked mid-run: google-auth refreshes on a 401 and fails with RefreshError
    from google.auth.exceptions import RefreshError
    from googleapiclient.errors import HttpError

    return isinstance(err, RefreshError) or (
        isinstance(err, HttpError) and getattr(err.resp, "status", None) == HTTP_UNAUTHORIZED
    )


def _one_line(value: object) -> str:
    return " ".join(str(value or "").split())


def build_gmail_service(credentials_path: Path, token_path: Path, *, reauth: bool) -> GmailService:
    """Signed-in Gmail v1 service: cached token, refreshed when expired, browser flow otherwise.

    The token is written only to `token_path` (owner read/write only).

    Raises:
        GmailAuthExpired: The saved token can't be refreshed (expired or revoked).
        GmailSetupError: The `gmail` extra or the OAuth client file is missing.
    """
    try:
        from google.auth.exceptions import RefreshError
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError as err:
        raise GmailSetupError("Gmail support is not installed: uv sync --extra gmail") from err
    credentials = None
    if token_path.exists() and not reauth:
        try:
            credentials = Credentials.from_authorized_user_file(str(token_path), list(GMAIL_SCOPES))
        except ValueError as err:  # also JSONDecodeError: corrupt, or wrong fields
            raise GmailSetupError(
                f"{token_path} is unreadable: run sentinel ingest --gmail --reauth"
            ) from err
    if credentials is not None and credentials.expired and credentials.refresh_token:
        try:
            credentials.refresh(Request())
        except RefreshError as err:
            raise GmailAuthExpired() from err
    if credentials is None or not credentials.valid:
        if not credentials_path.exists():
            raise GmailSetupError(
                f"no OAuth client at {credentials_path}: create a Desktop OAuth client in "
                "Google Cloud and save its JSON there (see README)"
            )
        flow = InstalledAppFlow.from_client_secrets_file(str(credentials_path), list(GMAIL_SCOPES))
        credentials = flow.run_local_server(port=0)
    token_path.write_text(credentials.to_json())
    token_path.chmod(TOKEN_FILE_MODE)
    service: GmailService = build("gmail", "v1", credentials=credentials, cache_discovery=False)
    return service


def _dicts(value: object) -> list[dict[str, object]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []
