# Ingest + Sender Allowlist Implementation Plan (spec §7 step 7)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Receipts arrive by email. This plan reads messages from a directory of `.eml` files (CI and demo) or from Gmail (read-only). A deterministic sender allowlist runs before any attachment is opened. Allowed attachments go through the existing extract → categorize → validate path into a report, and nothing is stored.

**Architecture:** New `ingest/` package. Both sources hand back raw RFC 822 bytes, so one parser serves both. The parser reads **headers only** first; the allowlist decides on the parsed `From` (plus a DMARC check, see Decisions); only allowed messages are fully parsed and have their attachments opened. `ingest_messages` runs the pipeline and returns an `IngestReport`. `sentinel ingest` prints that report and writes nothing. `sentinel allowlist add` is the only command that writes, and it writes only `config/allowlist.yaml`.

**Tech Stack:** Python 3.12 stdlib `email` (policy `email.policy.default`), PyYAML, existing `extract/`, `categorize/` and `validate/`. For Gmail, `google-api-python-client` + `google-auth-oauthlib` in a new optional `gmail` extra.

**Spec:** `docs/superpowers/specs/2026-08-28-receipt-sentinel-design.md`: §4.1 Ingest, §2 Non-goals ("No real Gmail in CI"; one OAuth Gmail account), §5 "Ingest trust".

## Global Constraints

- Everything in `CLAUDE.md` and the earlier plans' Global Constraints still holds: file headers, descriptive names, `mypy --strict`, ruff rules, no autosave, no PII or secrets in the repo, commit trailers.
- Spec §4.1, verbatim:
  - `MessageSource` protocol: `list_messages(since) -> list[InboundMessage]`.
  - `InboundMessage`: sender, subject, received_at, attachments (bytes + mime).
  - Gmail scope `gmail.readonly` only.
  - Allowlist in `config/allowlist.yaml`: exact addresses and domains.
  - Non-allowlisted messages are logged and dropped **before any attachment is opened**. Unknown senders are surfaced as **metadata only** (address, message count, subjects).
  - Exact address preferred. Domain entries match the **parsed `From` address's domain only** (display names ignored).
  - Tests cover display-name spoofing (`"bank@trusted.com" <evil@x.io>`), sub-domain and look-alike domains, and malformed `From` headers.
  - Attachment caps: 10 MB, 20 pages, 30 s. MIME by magic bytes. Failures are logged and shown as "skipped".
- Spec §2: no real Gmail in CI. `token.json` and `credentials.json` stay gitignored (they already are).
- LLM10: `sentinel ingest` processes at most `--max-messages` (default **50**) allowed messages per run.
- Untrusted strings (sender, subject, filenames, model output) have control characters stripped before they're printed to the terminal (spec §4.5).

## Decisions made in this plan (author can overrule)

- **The From header alone is never trusted.** Anyone can type `From: receipts@amazon.com`. An allowlisted sender also needs the **receiving server's** `Authentication-Results` header to say `dmarc=pass` with `header.from=<that domain>`. That's the first such header whose authserv-id matches `trusted_authserv_id` in `allowlist.yaml` (default `mx.google.com`). Later, sender-added copies are ignored. A message that fails this check counts as an unknown sender.
- **A domain entry matches only that exact domain.** `amazon.com` does not admit `mail.amazon.com`; list it separately if needed. Matching is case-insensitive ASCII. A non-ASCII or punycode domain matches only if it is listed byte-for-byte.
- **The `From` must be exactly one valid address.** Zero, several, or unparseable means the message is not allowed.
- **Both sources yield raw bytes.** `GmailSource` fetches `format=raw`, so parsing, allowlist and DMARC logic exist once.
- **`received_at`:** the `Date` header for `.eml` files, Gmail `internalDate` for Gmail. Its date is the `email_date` for validation.
- **`sentinel ingest --fake`** uses the keyless golden-label oracle (as `evals run --fake` does). It recognises only golden fixture attachments, so the committed demo inbox attaches those.
- **The `allowlist_events` table (spec §4.5) waits for step 8,** with the rest of the SQLite store. `allowlist add` edits the YAML only.

## Review Focus

1. **Forged `From` on an allowlisted address** (no DMARC pass, or a DMARC pass for a different domain, or a fake `Authentication-Results` added *below* the real one) → treated as an unknown sender; no attachment opened. Test in Task 2.
2. **A message from an unknown sender with a hostile attachment** (zip bomb, HTML renamed `.pdf`) → never fully parsed; the report shows address, count and subjects only. Test in Task 3 (assert the extractor is never called).
3. **A subject or filename with terminal escape codes** (`\x1b]8;;evil\x07`) → printed with control characters removed. Test in Task 4.
4. **An encoded-word `From`** (`=?utf-8?b?...?=` display name, or an RFC 2047 encoded address) → the display name is ignored; the decoded address is matched exactly. Test in Task 2.
5. **A Gmail token that's expired or revoked** → one readable error telling the user to re-run `sentinel ingest --gmail --reauth`, with no traceback and no write outside `token.json`. Test in Task 5.

---

## File Structure

```
config/allowlist.yaml                         addresses, domains, trusted_authserv_id
src/receipt_sentinel/ingest/
├── __init__.py
├── inbound_message.py      InboundMessage, InboundAttachment, MessageSource protocol,
│                           parse_message_headers(), parse_inbound_message()
├── eml_directory_source.py EmlDirectorySource
├── sender_allowlist.py     SenderAllowlist, load/save, is_trusted_sender()
├── ingest_pipeline.py      ingest_messages() → IngestReport
├── ingest_commands.py      command_ingest(), command_allowlist_add(), terminal rendering
└── gmail_source.py         GmailSource (optional `gmail` extra)
src/receipt_sentinel/cli.py                   + `ingest`, `allowlist add` subcommands
examples/demo_inbox/*.eml                     4 committed demo messages (synthetic)
scripts/build_demo_inbox.py                   writes them from golden fixtures
tests/ingest/…
```

---

### Task 1: Inbound message model, header-first parsing, `.eml` source

**Files:**
- Create: `src/receipt_sentinel/ingest/__init__.py`, `inbound_message.py`, `eml_directory_source.py`
- Test: `tests/ingest/__init__.py`, `tests/ingest/test_inbound_message.py`, `tests/ingest/test_eml_directory_source.py`, `tests/ingest/email_builders.py` (test helper: `build_eml(from_header, subject, date, attachments: list[tuple[str, bytes]], auth_results: list[str]) -> bytes` using `EmailMessage`)

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) InboundAttachment(filename: str, declared_mime: str, data: bytes)`
  - `@dataclass(frozen=True) MessageHeaders(raw_from: str, subject: str, received_at: datetime, authentication_results: tuple[str, ...])`. Headers in top-to-bottom order.
  - `@dataclass(frozen=True) InboundMessage(sender: str, subject: str, received_at: datetime, attachments: tuple[InboundAttachment, ...])` (spec §4.1 fields)
  - `@dataclass(frozen=True) RawMessage(message_id: str, raw: bytes, received_at: datetime | None)`. `received_at` is set by sources that know it (Gmail `internalDate`); otherwise None.
  - `class MessageSource(Protocol): def list_messages(self, since: date) -> list[RawMessage]: ...`
  - `parse_message_headers(raw: bytes) -> MessageHeaders`: uses `BytesHeaderParser(policy=default)`, so **no body is parsed**. A missing or unparseable `Date` gives `datetime.min` (UTC).
  - `parse_inbound_message(raw: bytes, sender: str) -> InboundMessage`: full parse. Attachments are every MIME part with a filename or a non-text content type. The filename is the basename only, and is `"attachment"` when missing.
  - `EmlDirectorySource(directory: Path)`: `*.eml`, sorted by name. Messages whose `Date` is before `since` are skipped. `message_id` = file stem.

- [ ] **Step 1: Write failing tests:**
  - `test_header_parse_does_not_touch_the_body`: a message whose body is 5 MB of base64 garbage parses headers fine; assert `parse_message_headers` returns in well under 1 s and gives the right subject.
  - `test_authentication_results_kept_in_order`
  - `test_attachments_carry_bytes_and_declared_mime`
  - `test_filename_path_is_stripped` (`../../etc/passwd` → `passwd`)
  - `test_eml_directory_filters_by_since_and_sorts`
  - `test_missing_date_sorts_first_and_is_filtered_out_by_any_since`
- [ ] **Step 2: Run** `uv run pytest tests/ingest -q`. Expected: FAIL (imports).
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the tests, ruff and mypy. Expected: PASS.
- [ ] **Step 5: Commit** `feat(ingest): inbound message model, header-first parsing, .eml source`

---

### Task 2: Sender allowlist with DMARC check

**Files:**
- Create: `src/receipt_sentinel/ingest/sender_allowlist.py`, `config/allowlist.yaml` (empty lists, `trusted_authserv_id: mx.google.com`, comment explaining each key)
- Test: `tests/ingest/test_sender_allowlist.py`

**Interfaces:**
- Consumes: `MessageHeaders`.
- Produces:
  - `@dataclass(frozen=True) SenderAllowlist(addresses: frozenset[str], domains: frozenset[str], trusted_authserv_id: str)`
  - `load_sender_allowlist(path: Path) -> SenderAllowlist` (a missing file gives an empty allowlist; lowercases entries)
  - `parse_single_sender(raw_from: str) -> str | None`: exactly one addr-spec, lowercased; else None.
  - `@dataclass(frozen=True) SenderDecision(allowed: bool, sender: str | None, reason: str)`
  - `decide_sender(headers: MessageHeaders, allowlist: SenderAllowlist) -> SenderDecision`. Reasons: `"allowlisted address"`, `"allowlisted domain"`, `"not on allowlist"`, `"no DMARC pass from trusted server"`, `"malformed From"`.

- [ ] **Step 1: Write failing tests** (one per row, via `build_eml` + `parse_message_headers`):

| test | From / auth | allowlist | expect |
|---|---|---|---|
| `test_exact_address_with_dmarc_pass_is_allowed` | `Shop <receipts@shop.com>`, `mx.google.com; dmarc=pass header.from=shop.com` | address `receipts@shop.com` | allowed, "allowlisted address" |
| `test_display_name_spoof_is_not_allowed` | `"bank@trusted.com" <evil@x.io>` | domain `trusted.com` | not allowed |
| `test_subdomain_does_not_match_parent_domain` | `a@mail.shop.com` + pass for `mail.shop.com` | domain `shop.com` | not allowed |
| `test_lookalike_domain_does_not_match` | `a@shop.com.evil.io`, `a@sh0p.com`, `a@xn--shp-...` | domain `shop.com` | not allowed |
| `test_malformed_or_multiple_from_is_rejected` | `""`, `not an address`, `a@x.com, b@y.com` | domain `x.com` | not allowed, "malformed From" |
| `test_forged_from_without_dmarc_is_not_allowed` | allowlisted address, no auth header | — | "no DMARC pass from trusted server" |
| `test_dmarc_pass_for_other_domain_is_not_enough` | pass `header.from=evil.io` | — | not allowed |
| `test_only_the_topmost_trusted_auth_header_counts` | real header `dmarc=fail` above an added `mx.google.com; dmarc=pass` | — | not allowed |
| `test_encoded_word_display_name_is_ignored` (Review Focus 4) | `=?utf-8?b?<b64 of "bank@trusted.com">?= <evil@x.io>` | domain `trusted.com` | not allowed |
| `test_case_insensitive_match` | `Receipts@SHOP.com` | address `receipts@shop.com` | allowed |

- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.** Use `email.headerregistry.Address` via the default policy's `From` header (`.addresses`). Parse `Authentication-Results` per RFC 8601 enough to read the authserv-id (text before the first `;`) and the `dmarc=<result>` / `header.from=<domain>` pair; no full grammar. "Topmost trusted" = the first header in order whose authserv-id equals `trusted_authserv_id`.
- [ ] **Step 4: Run** the tests, ruff and mypy. Expected: PASS.
- [ ] **Step 5: Commit** `feat(ingest): sender allowlist with exact domains and a DMARC check`

---

### Task 3: Ingest pipeline

**Files:**
- Create: `src/receipt_sentinel/ingest/ingest_pipeline.py`
- Test: `tests/ingest/test_ingest_pipeline.py`

**Interfaces:**
- Consumes: Tasks 1–2; `extract_document`, `ExtractionRejected`, `OcrUnavailable` (lets it propagate); `categorize_receipt`, `ParseFailure`; `validate_receipt`, `ValidationResult`.
- Produces:
  - `@dataclass(frozen=True) IngestedReceipt(message_id: str, sender: str, subject: str, filename: str, record: ReceiptRecord, validation: ValidationResult, extraction_method: str)`
  - `@dataclass(frozen=True) SkippedAttachment(message_id: str, sender: str, filename: str, reason: str)`. The reason is the `ExtractionRejected` message, `"model reply did not match the receipt schema"`, or `"over --max-messages"`.
  - `@dataclass(frozen=True) UnknownSender(address: str, message_count: int, subjects: tuple[str, ...], reason: str)`. Up to 5 subjects; the address is the parsed sender, or the raw `From` (truncated to 200 chars) when malformed.
  - `@dataclass(frozen=True) IngestReport(receipts: tuple[IngestedReceipt, ...], skipped: tuple[SkippedAttachment, ...], unknown_senders: tuple[UnknownSender, ...])`
  - `async ingest_messages(source: MessageSource, allowlist: SenderAllowlist, *, since: date, prompt: PromptConfig, client: LlmClient, model_id: str, max_messages: int = 50) -> IngestReport`. Extraction is called with `asyncio.to_thread`. `email_date` for validation = `received_at.date()`.

- [ ] **Step 1: Write failing tests** (in-memory `MessageSource` fake; `FakeLlmClient` with golden-label answers; attachments are golden fixture files):
  - `test_allowed_message_attachment_becomes_validated_receipt`
  - `test_unknown_sender_attachments_are_never_parsed_or_extracted` (Review Focus 2): monkeypatch `ingest_pipeline.extract_document` and `ingest_pipeline.parse_inbound_message` to raise. Ingest a message from an unknown sender carrying an HTML-as-PDF attachment, and assert the report lists the sender with count and subject.
  - `test_unknown_senders_are_grouped_with_counts_and_capped_subjects` (7 messages from one sender → count 7, 5 subjects)
  - `test_rejected_attachment_is_skipped_with_reason_not_raised`
  - `test_messages_beyond_max_are_skipped_not_processed` (3 allowed, max 2 → 2 receipts + skipped reason `"over --max-messages"`)
  - `test_message_without_attachments_produces_nothing`
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.** Headers first → `decide_sender` → unknown: aggregate metadata and `logger.info("dropped message from unknown sender")`, with no address or subject in the log line. Allowed: full parse → per attachment extract → categorize → validate.
- [ ] **Step 4: Run** the tests, ruff and mypy. Expected: PASS.
- [ ] **Step 5: Commit** `feat(ingest): allowlist-first ingest pipeline into an IngestReport`

---

### Task 4: `sentinel ingest` / `sentinel allowlist add` + demo inbox

**Files:**
- Create: `src/receipt_sentinel/ingest/ingest_commands.py`, `scripts/build_demo_inbox.py`, `examples/demo_inbox/*.eml` (committed)
- Modify: `src/receipt_sentinel/cli.py`, `Makefile` (`ingest-demo`), `README.md` (an "Ingest" section)
- Test: `tests/ingest/test_ingest_commands.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: Task 3; `build_client` and `extract_fixture_cases` (keyless oracle) from evals.
- Produces:
  - `command_ingest(source: MessageSource, *, allowlist_path: Path, since: date, fake: bool, provider: str, max_messages: int) -> int`. Prints the report; returns 0. Writes nothing.
  - `command_allowlist_add(allowlist_path: Path, *, address: str | None, domain: str | None) -> int`. Validates (an address must pass `parse_single_sender`; a domain must be a lowercase ASCII hostname with a dot, else exit 2 with a readable message), dedupes, writes sorted YAML, keeps `trusted_authserv_id`, prints what changed.
  - `render_terminal_safe(text: str) -> str`: strips C0/C1 control characters except `\n`.
  - CLI: `sentinel ingest (--eml DIR | --gmail) [--since YYYY-MM-DD, default 30 days ago] [--fake] [--provider anthropic|azure] [--max-messages 50] [--allowlist config/allowlist.yaml]`, `sentinel allowlist add (ADDRESS | --domain DOMAIN)`.
- Demo inbox (`scripts/build_demo_inbox.py`, deterministic), with a dated `Date` header and a `mx.google.com` `Authentication-Results` where noted:
  1. `01_allowlisted_pdf.eml`: `receipts@demo-coffee.example`, dmarc=pass, attaches `fixture_pdf_native_0001_dining_easy.pdf`.
  2. `02_allowlisted_photo.eml`: same sender, attaches `fixture_photo_adversarial_0001_override.jpg`.
  3. `03_unknown_sender.eml`: `billing@unknown-shop.example`, attaches a PDF that must never be opened.
  4. `04_spoofed_display_name.eml`: `"receipts@demo-coffee.example" <attacker@evil.example>`, no DMARC pass.

  `config/allowlist.yaml` is not changed by the demo. `make ingest-demo` passes `--allowlist examples/demo_inbox/allowlist.yaml`, which lists `receipts@demo-coffee.example`.

- [ ] **Step 1: Write failing tests:**
  - `test_ingest_demo_inbox_fake_reports_two_receipts_two_unknown`: two receipts, one with `injection_detected` true; two unknown senders, including the spoofed one; exit 0.
  - `test_terminal_output_strips_escape_codes` (Review Focus 3): a subject containing `\x1b]8;;http://evil\x07click\x1b]8;;\x07`; the printed output has no `\x1b` or `\x07`.
  - `test_allowlist_add_address_then_domain_writes_sorted_deduped_yaml`
  - `test_allowlist_add_rejects_display_name_or_bad_domain` (`"Shop <a@b.com>"`, `shop`, `-rf`) → exit 2, file unchanged
  - `test_ingest_writes_nothing`: snapshot `tmp_path` and the repo `config/` mtimes before and after; they're unchanged.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.** Then run `uv run python scripts/build_demo_inbox.py` and `make ingest-demo`, and read the output by eye.
- [ ] **Step 4: Run** the full checks. Expected: PASS.
- [ ] **Step 5: Commit** `feat(ingest): sentinel ingest + allowlist add, keyless demo inbox`

---

### Task 5: Gmail source (read-only)

**Files:**
- Create: `src/receipt_sentinel/ingest/gmail_source.py`
- Modify: `pyproject.toml` (`gmail` extra: `google-api-python-client`, `google-auth-oauthlib`; also in `dev` so tests and mypy see it; mypy `ignore_missing_imports` for `googleapiclient.*`, `google_auth_oauthlib.*`, `google.oauth2.*` if they lack types), `cli.py` (`--gmail`, `--reauth`), `README.md` (one-time setup: create an OAuth "Desktop app" client in Google Cloud, save `credentials.json` at the repo root, which is gitignored; the first run opens a browser)
- Test: `tests/ingest/test_gmail_source.py`

**Interfaces:**
- Consumes: `RawMessage`, `MessageSource`.
- Produces:
  - `GMAIL_SCOPES = ("https://www.googleapis.com/auth/gmail.readonly",)`
  - `GmailSource(service: GmailService, *, page_limit: int = 5)`, where `GmailService` is a small `Protocol` covering the `users().messages().list/get(...).execute()` chain. Query `has:attachment after:<since as YYYY/MM/DD>`. Fetch each message `format="raw"`, base64url-decode it, and set `received_at` from `internalDate` (ms, UTC). Follow `nextPageToken` up to `page_limit` pages.
  - `build_gmail_service(credentials_path: Path, token_path: Path, *, reauth: bool) -> GmailService`. Installed-app OAuth flow with exactly `GMAIL_SCOPES`. The refreshed token is written only to `token_path`. A `RefreshError` raises `GmailAuthExpired` with the message `"Gmail sign-in expired: run sentinel ingest --gmail --reauth"`.
  - `class GmailAuthExpired(RuntimeError)`. `cli.main` maps it to stderr plus exit 2.

- [ ] **Step 1: Write failing tests** (a fake `GmailService`, no network):
  - `test_lists_with_attachment_query_and_decodes_raw`
  - `test_follows_page_tokens_up_to_limit`
  - `test_received_at_comes_from_internal_date`
  - `test_scope_is_readonly_only` (`GMAIL_SCOPES` is exactly the readonly scope)
  - `test_expired_token_is_one_readable_error` (Review Focus 5): patch the credentials refresh to raise `google.auth.exceptions.RefreshError`; `main(["ingest", "--gmail"])` returns 2, stderr contains `--reauth`, and there's no traceback.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the full checks. Expected: PASS.
- [ ] **Step 5: Commit** `feat(ingest): read-only Gmail source (gmail.readonly, raw format)`
- [ ] **Step 6 (author, optional, not CI):** a first real run, `uv run --extra gmail sentinel ingest --gmail --since <date> --max-messages 5`, against the author's own inbox. Real receipts never leave the terminal.

---

### Task 6: Docs and handoff

**Files:** `README.md` (architecture data flow now starts at Gmail/.eml; roadmap step 7 done), `CLAUDE.md` (handoff), `src/receipt_sentinel/ingest/__init__.py` header

- [ ] **Step 1:** Update the docs. Run the full checks and `make ingest-demo`.
- [ ] **Step 2: Commit** `docs: ingest + allowlist in README and handoff`
