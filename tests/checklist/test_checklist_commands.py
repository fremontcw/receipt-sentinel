import json
import sqlite3
from datetime import date
from pathlib import Path

import pytest

from receipt_sentinel.checklist import checklist_commands
from receipt_sentinel.checklist.checklist_commands import (
    ChecklistConfirmRefused,
    ChecklistDecisions,
    command_checklist_confirm,
    command_checklist_propose,
    confirm_checklist,
)
from receipt_sentinel.checklist.checklist_proposal import (
    build_checklist_proposal,
    load_proposal,
    save_proposal,
)
from receipt_sentinel.checklist.receipt_store import open_receipt_store
from receipt_sentinel.evals.eval_commands import REPO_ROOT
from receipt_sentinel.ingest.eml_directory_source import EmlDirectorySource
from receipt_sentinel.ingest.ingest_pipeline import IngestReport
from tests.checklist.proposal_builders import ingested

DEMO_INBOX = REPO_ROOT / "examples" / "demo_inbox"


def _propose(tmp_path: Path) -> str:
    out = tmp_path / "checklists"
    before = set(out.iterdir()) if out.exists() else set()
    code = command_checklist_propose(
        EmlDirectorySource(DEMO_INBOX),
        month="2026-08",
        allowlist_path=DEMO_INBOX / "allowlist.yaml",
        store_path=tmp_path / "store.db",
        out_dir=out,
        fake=True,
        provider="anthropic",
        max_messages=50,
    )
    assert code == 0
    (proposal_dir,) = set(out.iterdir()) - before  # the folder this run wrote
    return proposal_dir.name


def _confirm(tmp_path: Path, proposal_id: str, **kwargs: object) -> int:
    return command_checklist_confirm(
        proposal_id,
        out_dir=tmp_path / "checklists",
        store_path=tmp_path / "store.db",
        decisions_path=kwargs.get("decisions_path"),  # type: ignore[arg-type]
        accept=kwargs.get("accept"),  # type: ignore[arg-type]
    )


def _counts(store: Path) -> tuple[int, int]:
    conn = open_receipt_store(store)
    checklists = conn.execute("SELECT COUNT(*) FROM checklists").fetchone()[0]
    receipts = conn.execute("SELECT COUNT(*) FROM receipts").fetchone()[0]
    return int(checklists), int(receipts)


def test_propose_writes_files_but_not_the_database(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    proposal_id = _propose(tmp_path)
    proposal_dir = tmp_path / "checklists" / proposal_id
    assert (proposal_dir / "proposal.json").is_file() and (proposal_dir / "review.html").is_file()
    assert not (tmp_path / "store.db").exists()
    out = capsys.readouterr().out
    assert "sentinel checklist confirm" in out and "review.html" in out


def test_confirm_writes_accepted_receipts_and_one_checklist(tmp_path: Path) -> None:
    proposal_id = _propose(tmp_path)
    assert _confirm(tmp_path, proposal_id, accept=("r01",)) == 0
    assert _counts(tmp_path / "store.db") == (1, 1)
    row = (
        open_receipt_store(tmp_path / "store.db")
        .execute("SELECT confirmation, accepted_count, rejected_count FROM checklists")
        .fetchone()
    )
    assert row == ("cli-ids", 1, 1)


def test_confirming_twice_is_refused_and_reproposal_flags_duplicates(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    proposal_id = _propose(tmp_path)
    assert _confirm(tmp_path, proposal_id, accept=("r01", "r02")) == 0
    assert _confirm(tmp_path, proposal_id, accept=("r01",)) == 2
    assert "already confirmed" in capsys.readouterr().err
    assert _counts(tmp_path / "store.db") == (1, 2)
    again = _propose(tmp_path)  # now flagged against the store, so a new proposal id
    assert again != proposal_id
    flags = [
        f
        for item in load_proposal(tmp_path / "checklists" / again / "proposal.json").items
        for f in item.flags
    ]
    assert sum(f.startswith("duplicate of confirmed receipt") for f in flags) == 2


@pytest.mark.parametrize(
    "decisions",
    [
        {"accepted_ids": ["r99"], "rejected_ids": []},
        {"accepted_ids": ["r01"], "rejected_ids": [], "proposal_id": "2026-08-deadbeef"},
        {"accepted_ids": ["r01"], "rejected_ids": ["r01"]},
        "not json at all",
    ],
)
def test_hand_edited_decisions_are_refused(tmp_path: Path, decisions: object) -> None:
    proposal_id = _propose(tmp_path)
    path = tmp_path / "decisions.json"
    if isinstance(decisions, dict):
        body = {"proposal_id": proposal_id, "confirmation": "user-reviewed", **decisions}
        path.write_text(json.dumps(body))
    else:
        path.write_text(str(decisions))
    assert _confirm(tmp_path, proposal_id, decisions_path=path) == 2
    assert not (tmp_path / "store.db").exists() or _counts(tmp_path / "store.db") == (0, 0)


def test_review_page_decisions_file_is_accepted(tmp_path: Path) -> None:
    proposal_id = _propose(tmp_path)
    path = tmp_path / "decisions.json"
    path.write_text(
        json.dumps(
            {
                "proposal_id": proposal_id,
                "accepted_ids": ["r02"],
                "rejected_ids": ["r01"],
                "confirmation": "user-reviewed",
                "reviewed_at": "2026-10-19T18:53:49.578Z",
            }
        )
    )
    assert _confirm(tmp_path, proposal_id, decisions_path=path) == 0
    assert _counts(tmp_path / "store.db") == (1, 1)


def _duplicate_proposal() -> object:
    report = IngestReport(
        receipts=(
            ingested("Twice", "3.00", message_id="m1"),
            ingested("TWICE", "3", message_id="m2"),
        ),
        skipped=(),
        unknown_senders=(),
    )
    return build_checklist_proposal(report, month="2026-08", conn=None)


def test_accepting_a_flagged_duplicate_is_refused_with_its_id(tmp_path: Path) -> None:
    proposal = _duplicate_proposal()
    conn = open_receipt_store(tmp_path / "store.db")
    decisions = ChecklistDecisions(proposal.proposal_id, ("r01", "r02"), (), "cli-ids")  # type: ignore[attr-defined]
    with pytest.raises(ChecklistConfirmRefused, match="r02"):
        confirm_checklist(conn, proposal, decisions)  # type: ignore[arg-type]
    assert _counts(tmp_path / "store.db") == (0, 0)


def test_failure_mid_confirm_rolls_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    receipts = tuple(ingested(f"Shop {i}", f"{i}.00", day=date(2026, 8, i)) for i in range(1, 6))
    proposal = build_checklist_proposal(
        IngestReport(receipts=receipts, skipped=(), unknown_senders=()), month="2026-08", conn=None
    )
    real_insert = checklist_commands._insert_receipt
    calls = {"n": 0}

    def fail_on_third(*args: object, **kwargs: object) -> None:
        calls["n"] += 1
        if calls["n"] == 3:
            raise sqlite3.IntegrityError("disk said no")
        real_insert(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(checklist_commands, "_insert_receipt", fail_on_third)
    conn = open_receipt_store(tmp_path / "store.db")
    ids = tuple(item.item_id for item in proposal.items)
    with pytest.raises(ChecklistConfirmRefused):
        confirm_checklist(
            conn, proposal, ChecklistDecisions(proposal.proposal_id, ids, (), "cli-ids")
        )
    assert _counts(tmp_path / "store.db") == (0, 0)


def test_missing_proposal_is_refused(tmp_path: Path) -> None:
    assert _confirm(tmp_path, "2026-08-00000000", accept=("r01",)) == 2


def test_saved_proposal_round_trips_through_confirm(tmp_path: Path) -> None:
    proposal = _duplicate_proposal()
    save_proposal(proposal, tmp_path / "checklists" / proposal.proposal_id / "proposal.json")  # type: ignore[attr-defined]
    assert _confirm(tmp_path, proposal.proposal_id, accept=("r01",)) == 0  # type: ignore[attr-defined]


def test_confirm_rechecks_the_store_and_names_the_duplicate(tmp_path: Path) -> None:
    first = build_checklist_proposal(
        IngestReport(receipts=(ingested("A", "1.00"),), skipped=(), unknown_senders=()),
        month="2026-08",
        conn=None,
    )
    second = build_checklist_proposal(
        IngestReport(
            receipts=(ingested("A", "1.00"), ingested("B", "2.00")), skipped=(), unknown_senders=()
        ),
        month="2026-08",
        conn=None,
    )
    conn = open_receipt_store(tmp_path / "store.db")
    confirm_checklist(conn, first, ChecklistDecisions(first.proposal_id, ("r01",), (), "cli-ids"))
    with pytest.raises(ChecklistConfirmRefused, match="r01"):
        confirm_checklist(
            conn, second, ChecklistDecisions(second.proposal_id, ("r01", "r02"), (), "cli-ids")
        )
    assert _counts(tmp_path / "store.db") == (1, 1)


def test_propose_reports_skipped_and_over_cap(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "checklists"
    command_checklist_propose(
        EmlDirectorySource(DEMO_INBOX),
        month="2026-08",
        allowlist_path=DEMO_INBOX / "allowlist.yaml",
        store_path=tmp_path / "store.db",
        out_dir=out,
        fake=True,
        provider="anthropic",
        max_messages=1,
    )
    text = capsys.readouterr().out
    assert "Skipped files: 1" in text and "over --max-messages" in text
