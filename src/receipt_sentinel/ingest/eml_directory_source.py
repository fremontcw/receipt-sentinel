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
    """Every `*.eml` in a directory dated on or after `since`, in file-name order."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def list_messages(self, since: date) -> list[RawMessage]:
        messages: list[RawMessage] = []
        for path in sorted(self.directory.glob("*.eml")):
            raw = path.read_bytes()
            if parse_message_headers(raw).received_at.date() >= since:
                messages.append(RawMessage(message_id=path.stem, raw=raw, received_at=None))
        return messages
