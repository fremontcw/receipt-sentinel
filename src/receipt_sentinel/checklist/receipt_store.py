"""receipt_store

The local SQLite store: confirmed checklists, their receipts, and allowlist changes. Only
explicit user commands write here (`checklist confirm`, `allowlist add`); `propose` opens it
read-only, and never creates it.

Status:
    None
Issues:
    None
Todo:
    None
"""

import hashlib
import re
import sqlite3
import unicodedata
from collections.abc import Iterable
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from receipt_sentinel.evals.eval_commands import REPO_ROOT

DEFAULT_STORE_PATH = REPO_ROOT / "data" / "receipt_sentinel.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS checklists (
    id INTEGER PRIMARY KEY,
    proposal_id TEXT UNIQUE NOT NULL,
    month TEXT NOT NULL,
    confirmed_at TEXT NOT NULL,
    confirmation TEXT NOT NULL,
    accepted_count INTEGER NOT NULL,
    rejected_count INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS receipts (
    id INTEGER PRIMARY KEY,
    checklist_id INTEGER NOT NULL REFERENCES checklists(id),
    dedup_key TEXT UNIQUE NOT NULL,
    vendor TEXT NOT NULL,
    total TEXT NOT NULL,
    currency TEXT NOT NULL,
    receipt_date TEXT NOT NULL,
    category TEXT NOT NULL,
    line_items_json TEXT NOT NULL,
    confidence REAL NOT NULL,
    model_id TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    source_message_id TEXT NOT NULL,
    source_filename TEXT NOT NULL,
    validation_passed INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS allowlist_events (
    id INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL,
    action TEXT NOT NULL,
    entry_kind TEXT NOT NULL,
    entry TEXT NOT NULL
);
"""


def open_receipt_store(path: Path) -> sqlite3.Connection:
    """Open (creating the file and tables if needed) for a command that is about to write."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(_SCHEMA)
    return conn


def open_receipt_store_readonly(path: Path) -> sqlite3.Connection | None:
    """Open an existing store read-only; None when there is none yet. Never creates files."""
    if not path.is_file():
        return None
    # as_uri() percent-escapes '#' and '?', which would otherwise end the path and drop mode=ro
    return sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)


def receipt_dedup_key(vendor: str, total: Decimal, receipt_date: date) -> str:
    """Same vendor, amount and day = same receipt (spec §4.4), whatever the formatting."""
    # NFKC + casefold so width and case variants agree; drop only punctuation and spacing,
    # so non-Latin vendors keep their letters (an ASCII-only filter collapsed them to "")
    folded = unicodedata.normalize("NFKC", vendor).casefold()
    vendor_key = re.sub(r"[\W_]", "", folded) or folded
    total_key = format(total.normalize(), "f")
    return hashlib.sha256(
        f"{vendor_key}|{total_key}|{receipt_date.isoformat()}".encode()
    ).hexdigest()


def confirmed_receipt_dates_by_key(conn: sqlite3.Connection, keys: Iterable[str]) -> dict[str, str]:
    """Dedup key → ISO date of the already-confirmed receipt, for keys already stored."""
    wanted = list(keys)
    if not wanted:
        return {}
    placeholders = ",".join("?" * len(wanted))
    rows = conn.execute(
        f"SELECT dedup_key, receipt_date FROM receipts WHERE dedup_key IN ({placeholders})",  # noqa: S608 - placeholders only
        wanted,
    )
    return {str(key): str(day) for key, day in rows}


def checklist_exists(conn: sqlite3.Connection, proposal_id: str) -> bool:
    row = conn.execute("SELECT 1 FROM checklists WHERE proposal_id = ?", (proposal_id,)).fetchone()
    return row is not None


def record_allowlist_event(conn: sqlite3.Connection, *, entry_kind: str, entry: str) -> None:
    conn.execute(
        "INSERT INTO allowlist_events (created_at, action, entry_kind, entry) VALUES (?, ?, ?, ?)",
        (_utc_now(), "add", entry_kind, entry),
    )


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
