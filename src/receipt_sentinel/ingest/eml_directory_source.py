"""eml_directory_source

A MessageSource over a directory of .eml files: the CI and demo stand-in for Gmail.

Status:
    None
Issues:
    None
Todo:
    None
"""

from datetime import date
from pathlib import Path

from receipt_sentinel.ingest.inbound_message import RawMessage, parse_message_headers


class EmlDirectorySource:
    """Every `*.eml` in a directory dated in [since, until), in file-name order."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def list_messages(self, since: date, until: date | None = None) -> list[RawMessage]:
        messages: list[RawMessage] = []
        for path in sorted(self.directory.glob("*.eml")):
            raw = path.read_bytes()
            day = parse_message_headers(raw).received_at.date()
            if day >= since and (until is None or day < until):
                messages.append(RawMessage(message_id=path.stem, raw=raw, received_at=None))
        return messages

    def full_message(self, message: RawMessage) -> bytes:
        return message.raw  # a local file is already whole
