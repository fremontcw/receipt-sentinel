import sqlite3
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from receipt_sentinel.checklist.receipt_store import (
    open_receipt_store,
    open_receipt_store_readonly,
    receipt_dedup_key,
)
from receipt_sentinel.ingest.ingest_commands import command_allowlist_add


def _insert_receipt(conn: sqlite3.Connection, checklist_id: int, dedup_key: str) -> None:
    conn.execute(
        "INSERT INTO receipts (checklist_id, dedup_key, vendor, total, currency, receipt_date,"
        " category, line_items_json, confidence, model_id, prompt_version, source_message_id,"
        " source_filename, validation_passed) VALUES (?, ?, 'V', '1', 'USD', '2026-08-01',"
        " 'dining', '[]', 1.0, 'fake', '1', 'm', 'f.pdf', 1)",
        (checklist_id, dedup_key),
    )


def _insert_checklist(conn: sqlite3.Connection, proposal_id: str = "2026-08-abc") -> int:
    cursor = conn.execute(
        "INSERT INTO checklists (proposal_id, month, confirmed_at, confirmation, accepted_count,"
        " rejected_count) VALUES (?, '2026-08', '2026-10-19T00:00:00Z', 'cli-ids', 1, 0)",
        (proposal_id,),
    )
    return int(cursor.lastrowid or 0)


def test_store_creates_schema_once(tmp_path: Path) -> None:
    path = tmp_path / "data" / "store.db"
    open_receipt_store(path).close()
    conn = open_receipt_store(path)
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"receipts", "checklists", "allowlist_events"} <= tables


def test_readonly_open_never_creates_the_file(tmp_path: Path) -> None:
    path = tmp_path / "data" / "store.db"
    assert open_receipt_store_readonly(path) is None
    assert not path.exists() and not path.parent.exists()


def test_readonly_open_cannot_write(tmp_path: Path) -> None:
    path = tmp_path / "store.db"
    open_receipt_store(path).close()
    conn = open_receipt_store_readonly(path)
    assert conn is not None
    with pytest.raises(sqlite3.OperationalError):
        _insert_checklist(conn)


def test_dedup_key_ignores_vendor_case_and_punctuation() -> None:
    day = date(2026, 8, 17)
    key = receipt_dedup_key("Blue Bottle Coffee", Decimal("50.20"), day)
    assert receipt_dedup_key("BLUE-BOTTLE coffee!", Decimal("50.2"), day) == key
    assert receipt_dedup_key("Blue Bottle Coffee", Decimal("50.20"), date(2026, 8, 18)) != key
    assert receipt_dedup_key("Blue Bottle Coffee", Decimal("50.21"), day) != key


def test_unique_dedup_key_is_enforced(tmp_path: Path) -> None:
    conn = open_receipt_store(tmp_path / "store.db")
    checklist_id = _insert_checklist(conn)
    _insert_receipt(conn, checklist_id, "k1")
    with pytest.raises(sqlite3.IntegrityError):
        _insert_receipt(conn, checklist_id, "k1")


def test_allowlist_add_records_an_event(tmp_path: Path) -> None:
    store = tmp_path / "store.db"
    code = command_allowlist_add(
        tmp_path / "allowlist.yaml", address="a@shop.com", domain=None, store_path=store
    )
    assert code == 0
    rows = (
        open_receipt_store(store)
        .execute("SELECT action, entry_kind, entry FROM allowlist_events")
        .fetchall()
    )
    assert rows == [("add", "addresses", "a@shop.com")]


def test_rejected_allowlist_add_records_nothing(tmp_path: Path) -> None:
    store = tmp_path / "store.db"
    code = command_allowlist_add(
        tmp_path / "allowlist.yaml", address="Shop <a@b.com>", domain=None, store_path=store
    )
    assert code == 2
    assert not store.exists()


def test_dedup_key_keeps_non_latin_vendors_apart() -> None:
    day = date(2026, 8, 17)
    assert receipt_dedup_key("スターバックス", Decimal("5"), day) != receipt_dedup_key(
        "ローソン", Decimal("5"), day
    )
    assert receipt_dedup_key("Café Luna", Decimal("5"), day) != receipt_dedup_key(
        "Caf Luna", Decimal("5"), day
    )
    assert receipt_dedup_key("!!!", Decimal("5"), day) != receipt_dedup_key(
        "???", Decimal("5"), day
    )
    assert receipt_dedup_key("ＣＡＦÉ", Decimal("5"), day) == receipt_dedup_key(
        "café", Decimal("5"), day
    )


def test_readonly_open_with_uri_characters_in_path_never_creates(tmp_path: Path) -> None:
    path = tmp_path / "we#ird?x" / "store.db"
    assert open_receipt_store_readonly(path) is None
    real = tmp_path / "odd#dir" / "store.db"
    open_receipt_store(real).close()
    conn = open_receipt_store_readonly(real)
    assert conn is not None
    with pytest.raises(sqlite3.OperationalError):
        _insert_checklist(conn)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["odd#dir"]


def test_dedup_key_treats_negative_zero_as_zero() -> None:
    day = date(2026, 8, 3)
    assert receipt_dedup_key("Cafe", Decimal("-0.00"), day) == receipt_dedup_key(
        "Cafe", Decimal("0"), day
    )
