"""inbound_message

The message shapes every source shares, and two parsers over raw RFC 822 bytes: a
headers-only one (enough to decide whether the sender is trusted) and a full one that
opens attachments, used only after the sender is allowed.

Status:
    None
Issues:
    None
Todo:
    None
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime
from email import policy
from email.headerregistry import DateHeader
from email.message import EmailMessage, Message
from email.parser import BytesHeaderParser, BytesParser
from pathlib import PurePosixPath
from typing import Protocol

# A missing or unreadable Date sorts before everything and fails every `since` filter
UNKNOWN_DATE = datetime.min.replace(tzinfo=UTC)


@dataclass(frozen=True)
class InboundAttachment:
    filename: str
    declared_mime: str  # what the email claims; extraction trusts magic bytes instead
    data: bytes


@dataclass(frozen=True)
class MessageHeaders:
    raw_from: str  # as sent (encoded words intact), all From headers joined
    subject: str
    received_at: datetime
    authentication_results: tuple[str, ...]  # top to bottom, as the message stores them


@dataclass(frozen=True)
class InboundMessage:
    sender: str
    subject: str
    received_at: datetime
    attachments: tuple[InboundAttachment, ...]


@dataclass(frozen=True)
class RawMessage:
    message_id: str
    raw: bytes
    received_at: datetime | None  # set when the source knows better than the Date header


class MessageSource(Protocol):
    def list_messages(self, since: date) -> list[RawMessage]: ...


def parse_message_headers(raw: bytes) -> MessageHeaders:
    """Read only the header block: no body or attachment is parsed or decoded."""
    message = BytesHeaderParser(policy=policy.default).parsebytes(raw)
    raw_items = list(message.raw_items())  # an iterator: read it once
    return MessageHeaders(
        raw_from=", ".join(str(value) for name, value in raw_items if name.lower() == "from"),
        subject=str(message.get("Subject", "")),
        received_at=_date_header(message),
        authentication_results=tuple(
            " ".join(str(value).split())
            for name, value in raw_items
            if name.lower() == "authentication-results"
        ),
    )


def parse_inbound_message(raw: bytes, sender: str) -> InboundMessage:
    """Full parse of an already-allowed message.

    Args:
        raw: The RFC 822 bytes.
        sender: The address the allowlist accepted (never re-derived from display names).
    """
    message = BytesParser(policy=policy.default).parsebytes(raw)
    attachments = tuple(
        _attachment(part)
        for part in message.walk()
        if not part.is_multipart()
        and (part.get_filename() or part.get_content_maintype() != "text")
    )
    return InboundMessage(
        sender=sender,
        subject=str(message.get("Subject", "")),
        received_at=_date_header(message),
        attachments=attachments,
    )


def _attachment(part: Message) -> InboundAttachment:
    # Basename only: a filename is attacker-chosen text, never a path
    name = PurePosixPath((part.get_filename() or "").replace("\\", "/")).name
    payload = part.get_payload(decode=True)
    return InboundAttachment(
        filename=name or "attachment",
        declared_mime=part.get_content_type(),
        data=payload if isinstance(payload, bytes) else b"",
    )


def _date_header(message: EmailMessage | Message) -> datetime:
    header = message.get("Date")
    if not isinstance(header, DateHeader) or header.datetime is None:
        return UNKNOWN_DATE
    when = header.datetime
    return when if when.tzinfo else when.replace(tzinfo=UTC)
