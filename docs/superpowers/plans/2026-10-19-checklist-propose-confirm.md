# Checklist Propose/Confirm Implementation Plan (spec §7 step 8)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn a month of ingested receipts into a **proposed** budget checklist (per category, with flags), let the author review it item by item, and write to SQLite **only** when they confirm.

**Architecture:**
- **`sentinel checklist propose --month YYYY-MM`** runs the step 7 ingest, keeps receipts dated in that month, and flags each one for validation failures, low confidence and duplicates. It writes a proposal (JSON) plus a keyboard-driven review page (HTML) under `runs/checklists/<proposal_id>/`. Both are output files of an explicit command; nothing goes into the database.
- **`sentinel checklist confirm <proposal_id> --decisions FILE`** is the only path into the database. It runs in a single transaction.
- **`sentinel allowlist add`** also records an `allowlist_events` row.

**Tech Stack:** Python 3.12 stdlib `sqlite3` (no ORM) and `hashlib`, a static HTML page with inline JS and no dependencies, plus the existing `ingest/`, `validate/` and `categorize/` code.

**Spec:** `docs/superpowers/specs/2026-08-28-receipt-sentinel-design.md`: §4.5 Checklist, §4.4 (duplicate detection), §2 ("No autosave"; "No scheduled/cron checklist generation; `propose` is a manual command"), §5 LLM06.

## Global Constraints

- Everything in `CLAUDE.md` and the earlier plans' Global Constraints still holds: file headers, descriptive names, `mypy --strict`, ruff, no autosave, no PII or secrets in the repo, commit trailers.
- Spec §4.5, verbatim:
  - "All model-derived strings (vendor, line items, notes) are HTML-escaped in reports and the checklist; the terminal renderer strips control characters."
  - Monthly `ChecklistProposal`: "per category, list of receipts with totals, flagged items (validation failures, low confidence < 0.7, unknown senders)".
  - "Persisted only on `confirm_checklist(proposal_id, accepted_ids)` — an explicit CLI command (`sentinel checklist confirm`). SQLite tables: `receipts`, `checklists`, `allowlist_events`."
  - "`sentinel checklist propose --month 2026-08` renders the proposal to terminal + HTML."
- Spec §4.4: duplicate detection is a "vendor+total+date hash against SQLite". Failed records are "needs review", never silently dropped or auto-accepted.
- Spec §2 rules out a scheduled run, so there is **no cron**; the §4.5 Actions-cron option is not built. CI has no real Gmail anyway.
- The database defaults to `data/receipt_sentinel.db`, with `data/` added to `.gitignore`. It holds real receipts and never leaves the machine.
- Global CLAUDE.md "Human review and testing ergonomics" applies to the review page:
  - one item at a time, with a progress line
  - Enter accepts and auto-advances; reject, skip and back are one key each
  - keyboard hints shown on every button
  - the last item opens a save dialog with Save focused
  - a warning before leaving with unsaved decisions
  - each decision recorded as `confirmation: "user-reviewed"`
  - the page is verified in a browser before handover

## Decisions made in this plan (author can overrule)

- **The proposal is a file, not a DB row.** `propose` can run any number of times and `runs/` is gitignored scratch. Only `confirm` writes the database.
- **The review page is the confirm UI.** It downloads `decisions-<proposal_id>.json`, and `confirm --decisions` reads that file. `confirm --accept id,id` is the keyboard-free alternative, recorded as `confirmation: "cli-ids"`.
- **Duplicate key:** `sha256("<vendor lowercased, non-alphanumerics removed>|<total as a plain Decimal string>|<ISO date>")`, enforced by a `UNIQUE` column. A duplicate is flagged in the proposal. Accepting one fails `confirm`: nothing is written, and the message names the item. A duplicate is never silently skipped.
- **Totals are per (category, currency).** EUR and USD are never added together.
- **Month filter:** `propose` ingests messages from the 1st of the month onward, and keeps receipts whose **receipt date** falls in the month. A receipt dated Aug 31 and emailed Sep 2 still counts for August.
- **`proposal_id`:** `<YYYY-MM>-<first 8 hex chars of sha256 over the sorted item dedup keys>`. Re-proposing the same receipts gives the same id.

## Review Focus

1. **Confirming the same month twice, or re-proposing after a confirm:** the second proposal flags every item as `duplicate of confirmed receipt`, and confirming the same `proposal_id` again is refused, with nothing written. Test in Task 4.
2. **A hand-edited decisions file** (an id not in the proposal, a `proposal_id` that doesn't match, an id both accepted and rejected) is refused with a message, and nothing is written. Test in Task 4.
3. **Mixed currencies in one category:** totals are shown per currency, never summed together. Test in Task 2.
4. **A vendor of `</script><script>alert(1)</script>`, or line items holding HTML:** the review page shows them as text and runs no script, including inside the embedded JSON. Test in Task 3.
5. **A failure partway through `confirm`** (constraint error on the third of five rows): the transaction rolls back, so the database has no new `checklists` or `receipts` rows. Test in Task 4.

---

## File Structure

```
src/receipt_sentinel/
├── terminal_safe_text.py           render_terminal_safe(), render_one_line()  (moved from ingest_commands)
└── checklist/
    ├── __init__.py
    ├── receipt_store.py            SQLite schema, open_receipt_store(), dedup key, inserts/lookups
    ├── checklist_proposal.py       ChecklistItem/ChecklistProposal, build_checklist_proposal(), JSON save/load
    ├── checklist_review_page.py    render_review_page() → self-contained HTML
    └── checklist_commands.py       command_checklist_propose(), command_checklist_confirm(), confirm_checklist()
tests/checklist/…
```

---

### Task 1: Receipt store (SQLite) + allowlist events

**Files:**
- Create: `src/receipt_sentinel/checklist/__init__.py`, `receipt_store.py`
- Create: `src/receipt_sentinel/terminal_safe_text.py`. Move `render_terminal_safe` and `_field` (renamed `render_one_line`) here from `ingest/ingest_commands.py`, and update that module's imports.
- Modify: `ingest/ingest_commands.py` (`command_allowlist_add(..., store_path: Path)` records the event), `cli.py` (`--db` on `allowlist add`, default `REPO_ROOT / "data" / "receipt_sentinel.db"`), `.gitignore` (`data/`)
- Test: `tests/checklist/__init__.py`, `tests/checklist/test_receipt_store.py`, `tests/ingest/test_ingest_commands.py` (existing tests updated with `store_path`)

**Interfaces:**
- Produces:
  - `DEFAULT_STORE_PATH: Path`
  - `open_receipt_store(path: Path) -> sqlite3.Connection`: creates the parent dir and the tables if missing; `PRAGMA foreign_keys=ON`. Only `confirm` and `allowlist add` call it.
  - `open_receipt_store_readonly(path: Path) -> sqlite3.Connection | None`: None when the file doesn't exist; never creates anything (test: `test_readonly_open_never_creates_the_file`).
  - Tables:
    - `checklists(id INTEGER PRIMARY KEY, proposal_id TEXT UNIQUE NOT NULL, month TEXT NOT NULL, confirmed_at TEXT NOT NULL, confirmation TEXT NOT NULL, accepted_count INTEGER NOT NULL, rejected_count INTEGER NOT NULL)`
    - `receipts(id INTEGER PRIMARY KEY, checklist_id INTEGER NOT NULL REFERENCES checklists(id), dedup_key TEXT UNIQUE NOT NULL, vendor TEXT NOT NULL, total TEXT NOT NULL, currency TEXT NOT NULL, receipt_date TEXT NOT NULL, category TEXT NOT NULL, line_items_json TEXT NOT NULL, confidence REAL NOT NULL, model_id TEXT NOT NULL, prompt_version TEXT NOT NULL, source_message_id TEXT NOT NULL, source_filename TEXT NOT NULL, validation_passed INTEGER NOT NULL)`
    - `allowlist_events(id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, action TEXT NOT NULL, entry_kind TEXT NOT NULL, entry TEXT NOT NULL)`
  - `receipt_dedup_key(vendor: str, total: Decimal, receipt_date: date) -> str`
  - `confirmed_receipt_dates_by_key(conn: sqlite3.Connection, keys: Iterable[str]) -> dict[str, str]`: dedup key → the existing receipt's ISO date.
  - `checklist_exists(conn, proposal_id: str) -> bool`
  - `record_allowlist_event(conn, *, entry_kind: str, entry: str) -> None`: action `"add"`, UTC ISO timestamp.
  - `render_one_line(text: object) -> str`

- [ ] **Step 1: Write failing tests:**
  - `test_store_creates_schema_once` (opening twice is fine)
  - `test_dedup_key_ignores_vendor_case_and_punctuation` (`"Blue Bottle Coffee"` vs `"BLUE-BOTTLE coffee!"` same key; `Decimal("50.2")` vs `Decimal("50.20")` same key; a different date gives a different key)
  - `test_unique_dedup_key_is_enforced` (a second insert raises `sqlite3.IntegrityError`)
  - `test_allowlist_add_records_an_event` (via `command_allowlist_add`; one row with `entry_kind="addresses"`)
  - `test_rejected_allowlist_add_records_nothing`
- [ ] **Step 2: Run** `uv run pytest tests/checklist tests/ingest -q`. Expected: FAIL.
- [ ] **Step 3: Implement.** The total is normalised with `format(total.normalize(), "f")`, so `50.20` and `50.2` agree.
- [ ] **Step 4: Run** the full checks. Expected: PASS.
- [ ] **Step 5: Commit** `feat(checklist): SQLite receipt store, dedup key, allowlist events`

---

### Task 2: Checklist proposal

**Files:**
- Create: `src/receipt_sentinel/checklist/checklist_proposal.py`
- Test: `tests/checklist/test_checklist_proposal.py` (builds `IngestReport`s directly; no extraction or model)

**Interfaces:**
- Consumes: `IngestReport`, `IngestedReceipt`, `UnknownSender` (step 7); `receipt_dedup_key`, `confirmed_receipt_dates_by_key`.
- Produces:
  - `LOW_CONFIDENCE_THRESHOLD = 0.7`
  - `@dataclass(frozen=True) ChecklistItem(item_id: str, dedup_key: str, receipt: IngestedReceipt, flags: tuple[str, ...])`. `item_id` = `r01`, `r02`, … in receipt-date order.
  - `@dataclass(frozen=True) CategoryTotal(category: str, currency: str, total: Decimal, item_ids: tuple[str, ...])`
  - `@dataclass(frozen=True) ChecklistProposal(proposal_id: str, month: str, items: tuple[ChecklistItem, ...], category_totals: tuple[CategoryTotal, ...], unknown_senders: tuple[UnknownSender, ...], skipped: tuple[SkippedAttachment, ...])`
  - `build_checklist_proposal(report: IngestReport, *, month: str, conn: sqlite3.Connection) -> ChecklistProposal`
  - Flag texts (exact): `"needs review: <rule detail>"` per failed validation rule; `"low confidence 0.62"` (two decimals, when < 0.7); `"duplicate of confirmed receipt from <ISO date>"`; `"duplicate of <item_id> in this proposal"` (the later item gets it); `"injection attempt flagged"`.
  - `save_proposal(proposal: ChecklistProposal, path: Path) -> None` / `load_proposal(path: Path) -> ChecklistProposal`: a JSON round trip with Decimals as strings and dates as ISO.

- [ ] **Step 1: Write failing tests:**
  - `test_only_receipts_dated_in_the_month_are_kept`
  - `test_totals_are_per_category_and_currency` (Review Focus 3): two dining USD, one dining EUR → two `CategoryTotal` rows; the USD total is the exact Decimal sum.
  - `test_flags_validation_low_confidence_and_injection`
  - `test_duplicate_against_store_and_within_proposal`
  - `test_unknown_senders_and_skipped_are_carried`
  - `test_proposal_id_is_stable_for_the_same_receipts`
  - `test_proposal_json_round_trip`
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the full checks. Expected: PASS.
- [ ] **Step 5: Commit** `feat(checklist): monthly checklist proposal with flags and per-currency totals`

---

### Task 3: Review page

**Files:**
- Create: `src/receipt_sentinel/checklist/checklist_review_page.py`
- Test: `tests/checklist/test_checklist_review_page.py`

**Interfaces:**
- Consumes: `ChecklistProposal`.
- Produces: `render_review_page(proposal: ChecklistProposal) -> str`, a self-contained HTML page with no external requests. The proposal goes in as `<script type="application/json" id="proposal">` with `<`, `>` and `&` escaped as `<`, `>` and `&`, and the page script renders every value with `textContent`, never `innerHTML`.
- Behavior:
  - Shows one item at a time: vendor, total and currency, date, category, line items, flags highlighted, and source (sender, message, file).
  - Header: `"<Month> checklist · Item 3 of 12 · 2 accepted · 1 rejected"`.
  - Keys: **Enter** accepts and advances; **X** rejects and advances; **S** skips (undecided); **Backspace** or **←** goes back. Every button shows its key, and the main action sits above the fold. Category totals for accepted items update live in a side panel.
  - The unknown-senders and skipped-files lists are shown for information only, never as decisions.
  - When the last item is answered, a save dialog opens with **Save** focused (Enter saves). It downloads `decisions-<proposal_id>.json` = `{"proposal_id", "accepted_ids", "rejected_ids", "confirmation": "user-reviewed", "reviewed_at"}` and then shows the exact next command: `sentinel checklist confirm <proposal_id> --decisions ~/Downloads/decisions-<proposal_id>.json`.
  - A `beforeunload` warning fires while decisions are unsaved.
  - Nothing is stored in localStorage.

- [ ] **Step 1: Write failing tests:**
  - `test_page_is_self_contained` (no `http://` or `https://` src/href, no `<link`)
  - `test_hostile_vendor_cannot_break_out_of_the_json_block` (Review Focus 4): vendor `</script><script>alert(1)</script>` → the raw `</script><script>alert(1)` appears nowhere in the HTML, and the JSON block parses back to the original vendor.
  - `test_page_never_uses_innerhtml_for_data` (`"innerHTML"` not in the page)
  - `test_decisions_file_shape_is_documented_in_page` (the page contains the `confirmation: "user-reviewed"` key and the next-command text)
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Verify in a browser (Playwright):** open a page rendered from the demo inbox proposal. Press Enter, X, then Enter through the items. Check the progress text, the save dialog with Save focused, the downloaded file's contents, and the `beforeunload` prompt before saving. Take screenshots.
- [ ] **Step 5: Run** the full checks. Expected: PASS.
- [ ] **Step 6: Commit** `feat(checklist): keyboard review page that downloads a decisions file`

---

### Task 4: Propose and confirm commands

**Files:**
- Create: `src/receipt_sentinel/checklist/checklist_commands.py`
- Modify: `cli.py` (a `checklist propose|confirm` group), `Makefile` (`checklist-demo`: a propose run on the demo inbox, keyless, into a temp DB under `runs/`), `README.md` (a "Monthly checklist" section)
- Test: `tests/checklist/test_checklist_commands.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: Tasks 1–3; `command_ingest`'s pieces (`ingest_messages`, `build_client`, `load_sender_allowlist`, sources) from step 7.
- Produces:
  - `command_checklist_propose(source: MessageSource, *, month: str, allowlist_path: Path, store_path: Path, out_dir: Path, fake: bool, provider: str, max_messages: int) -> int`. Writes `out_dir/<proposal_id>/proposal.json` and `review.html`, prints a terminal summary through `render_one_line` (per-category totals, flagged count, unknown senders, the review page path, the confirm command), and returns 0. **It doesn't write the database:** it opens it read-only (`sqlite3.connect(f"file:{path}?mode=ro", uri=True)`) when the file exists, and otherwise checks duplicates within the proposal only, without creating the file. Task 1 adds `open_receipt_store_readonly(path: Path) -> sqlite3.Connection | None` for this.
  - `@dataclass(frozen=True) ChecklistDecisions(proposal_id: str, accepted_ids: tuple[str, ...], rejected_ids: tuple[str, ...], confirmation: Literal["user-reviewed", "cli-ids"])`
  - `load_decisions(path: Path) -> ChecklistDecisions` (validates the shape)
  - `confirm_checklist(conn: sqlite3.Connection, proposal: ChecklistProposal, decisions: ChecklistDecisions) -> int`: returns the new checklist id. It raises `ChecklistConfirmRefused(reason)` when:
    - the proposal id doesn't match;
    - an id is unknown, or both accepted and rejected;
    - the checklist is already confirmed;
    - an accepted item is flagged as a duplicate.

    It writes one `checklists` row and one `receipts` row per accepted item, in a single transaction (`with conn:`).
  - `command_checklist_confirm(proposal_id: str, *, out_dir: Path, store_path: Path, decisions_path: Path | None, accept: tuple[str, ...] | None) -> int`. Exactly one of `decisions_path` or `accept`. Returns 0 on write, 2 on refusal (stderr explains; nothing written).
  - CLI:
    - `sentinel checklist propose --month YYYY-MM (--eml DIR | --gmail) [--fake] [--provider] [--max-messages 50] [--allowlist] [--db] [--out runs/checklists]`
    - `sentinel checklist confirm PROPOSAL_ID (--decisions FILE | --accept r01,r02) [--db] [--out]`

- [ ] **Step 1: Write failing tests:**
  - `test_propose_writes_files_but_not_the_database` (demo inbox, `--fake`: `proposal.json` and `review.html` exist, the DB has 0 rows)
  - `test_confirm_writes_accepted_receipts_and_one_checklist`
  - `test_confirming_twice_is_refused_and_reproposal_flags_duplicates` (Review Focus 1)
  - `test_hand_edited_decisions_are_refused` (Review Focus 2): parametrize over unknown id, wrong `proposal_id`, an id in both lists, and malformed JSON. Each → exit 2, DB unchanged.
  - `test_accepting_a_flagged_duplicate_is_refused_with_its_id`
  - `test_failure_mid_confirm_rolls_back` (Review Focus 5): monkeypatch the receipt-row insert to raise `sqlite3.IntegrityError` on the third item → `checklists` and `receipts` counts are unchanged.
  - `test_cli_checklist_round_trip` (`propose` → `confirm --accept r01` → one receipt row)
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.** Then run `make checklist-demo` and read the output by eye.
- [ ] **Step 4: Run** the full checks. Expected: PASS.
- [ ] **Step 5: Commit** `feat(checklist): sentinel checklist propose/confirm, transactional confirm`

---

### Task 5: Docs and handoff

**Files:** `README.md` (architecture tree adds `checklist/`; feature path ends `→ proposal → you confirm → SQLite`; roadmap step 8 done), `CLAUDE.md` (handoff), `src/receipt_sentinel/validate/__init__.py` (remove the duplicate-detection TODO, which is now in the checklist)

- [ ] **Step 1:** Update the docs. Run the full checks and `make checklist-demo`.
- [ ] **Step 2: Commit** `docs: monthly checklist in README and handoff`
