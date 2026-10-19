from datetime import date
from pathlib import Path

from receipt_sentinel.ingest.eml_directory_source import EmlDirectorySource
from tests.ingest.email_builders import build_eml


def test_eml_directory_filters_by_since_and_sorts(tmp_path: Path) -> None:
    (tmp_path / "b.eml").write_bytes(build_eml("a@x.com", date="Tue, 18 Aug 2026 09:00:00 +0000"))
    (tmp_path / "a.eml").write_bytes(build_eml("a@x.com", date="Mon, 17 Aug 2026 09:00:00 +0000"))
    (tmp_path / "old.eml").write_bytes(build_eml("a@x.com", date="Wed, 01 Jul 2026 09:00:00 +0000"))
    (tmp_path / "notes.txt").write_text("not a message")
    messages = EmlDirectorySource(tmp_path).list_messages(since=date(2026, 8, 1))
    assert [m.message_id for m in messages] == ["a", "b"]
    assert messages[0].raw.startswith(b"From: a@x.com")


def test_missing_date_is_filtered_out_by_any_since(tmp_path: Path) -> None:
    (tmp_path / "undated.eml").write_bytes(build_eml("a@x.com", date=None))
    assert EmlDirectorySource(tmp_path).list_messages(since=date(1970, 1, 1)) == []


def test_eml_directory_honours_until(tmp_path: Path) -> None:
    (tmp_path / "a.eml").write_bytes(build_eml("a@x.com", date="Mon, 17 Aug 2026 09:00:00 +0000"))
    (tmp_path / "b.eml").write_bytes(build_eml("a@x.com", date="Thu, 01 Oct 2026 09:00:00 +0000"))
    found = EmlDirectorySource(tmp_path).list_messages(
        since=date(2026, 8, 1), until=date(2026, 10, 1)
    )
    assert [m.message_id for m in found] == ["a"]
