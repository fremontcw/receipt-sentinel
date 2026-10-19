# Extract + Validate Layers Implementation Plan (spec §7 step 6)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Receipts arrive as PDFs and photos, not pre-extracted text: add the extract layer (PyMuPDF native text, Tesseract OCR fallback, attachment caps), put PDF/photo fixtures in the golden set so CI gates extraction too, finish the deterministic validate layer, and let the local `/docs` demo accept an uploaded receipt file.

**Architecture:** New `extract/` package turns bytes into an `ExtractedDocument` (or a typed `ExtractionRejected`). Golden cases gain an optional `fixture` path; before a run, fixture cases have their `receipt_text` replaced by what extraction actually produced, so the runner, the keyless label oracle and scoring stay unchanged, and a fixture that fails to extract is scored as a failed case (`error_kind="extract"`). New `validate/` package replaces the ad-hoc subset in `scoring.py`. `categorize_api.py` gets `POST /categorize/file`.

**Tech Stack:** Python 3.12, PyMuPDF (`pymupdf`), `pytesseract` + system Tesseract 5, Pillow, FastAPI + `python-multipart`, pytest, ruff, mypy --strict.

**Spec:** `docs/superpowers/specs/2026-08-28-receipt-sentinel-design.md` (§4.1 attachment hardening, §4.2 Extract, §4.4 Validate, §4.7 golden fixtures)

## Global Constraints

- Everything in `CLAUDE.md` (project + global) and the August plan's Global Constraints still holds: file headers, descriptive names, `mypy --strict`, ruff `E,F,I,N,S,C901,PLR0915`, no autosave, no PII in the public repo, commit trailers.
- Caps (spec §4.1, verbatim): **size 10 MB, page count 20, OCR/render time 30 s per document**. MIME type verified by **magic bytes, not header/extension**. Only PDF and images are processed.
- Extract output (spec §4.2): `ExtractedDocument`: `text`, `page_count`, `extraction_method` (`native|ocr`), `ocr_confidence`.
- Validate (spec §4.4): `sum(line_items) ≈ total` within tolerance; date within `[email_date − 60d, email_date]`; currency is ISO-4217; failures are reported per rule, never silently dropped.
- Receipt text and model output never go on spans or into logs (existing telemetry rule).
- `pymupdf` is AGPL-3.0. The repo is public, so that's fine, including when it's hosted later. Say so in the README trade-offs.
- System Tesseract is required wherever extraction runs: local `brew install tesseract`, CI `sudo apt-get install -y tesseract-ocr`.

## Decisions made in this plan (author can overrule)

- **Fixtures are rendered with PyMuPDF + Pillow, not reportlab** (spec §8 default). PyMuPDF is already a dependency, and this saves adding another.
- **Native-vs-OCR threshold:** a PDF page with fewer than **20 non-whitespace characters** of native text is OCR'd at **300 DPI**. If any page was OCR'd, `extraction_method` is `"ocr"`.
- **`ocr_confidence`** = mean Tesseract word confidence (0–1) over OCR'd words, or `None` for fully native documents.
- **Image pixel cap:** 40 megapixels, checked before decoding.
- **Duplicate detection (spec §4.4) is deferred to step 8.** It needs the `receipts` store, which the checklist step creates.
- **Category-in-enum is not a separate rule.** Pydantic already rejects anything outside the enum.
- **The text endpoint and the eval harness don't run validation in the API response.** The validate layer feeds scoring now and the checklist in step 8.
- **The dataset version goes from 1 to 2.** The fake baseline is re-blessed in Task 4. The real baseline needs one paid run (about $0.20) in Task 7, with the author's OK.

## Review Focus

1. **iPhone photo upload (HEIC)** → clean 415 "HEIC isn't supported; export the photo as JPEG", not a 500. Test in Task 1.
2. **Phone photo with an EXIF rotation tag** → apply `ImageOps.exif_transpose` before OCR, so the total is still read. Test in Task 2.
3. **Password-protected or corrupt PDF** → `ExtractionRejected` (`encrypted` / `unreadable`) → 422 with a readable reason. Test in Task 2.
4. **Huge image (decompression bomb) or giant page** → rejected as `too_large` before full decode or render. Test in Task 2.
5. **Tesseract not installed** → a single readable error naming the install command, from the CLI and the API, never a traceback. Test in Task 2 (raise) and Task 4 (CLI message).

---

## File Structure

```
src/receipt_sentinel/
├── extract/
│   ├── __init__.py
│   ├── extracted_document.py      ExtractedDocument, ExtractionRejected, OcrUnavailable, caps
│   ├── attachment_guard.py        sniff_media_type(), check_attachment_size()
│   └── document_extractor.py      extract_document(): native PDF, OCR fallback, images
├── validate/
│   ├── __init__.py
│   ├── receipt_validation.py      ValidationRuleResult, ValidationResult, validate_receipt()
│   └── iso4217_currency_codes.py  ISO_4217_CURRENCY_CODES frozenset
├── evals/
│   ├── synthetic_fixture_renderer.py   text → native PDF / scanned PDF / JPEG photo bytes
│   ├── golden_fixture_text.py          extract_fixture_cases()
│   ├── golden_case.py                  + fixture field            (modify)
│   ├── scoring.py                      + "extract" ErrorKind, uses validate_receipt (modify)
│   ├── runner.py                       + extract_errors param     (modify)
│   ├── eval_commands.py                extract fixtures before runs (modify)
│   └── golden/fixtures/*.pdf|*.jpg     committed rendered fixtures
├── categorize/categorize_api.py        + POST /categorize/file    (modify)
scripts/render_golden_fixtures.py       writes fixture files + fixture case YAMLs
tests/extract/, tests/validate/, tests/evals/test_golden_fixture_text.py
```

---

### Task 1: Extract contracts, attachment guard, fixture renderer

**Files:**
- Modify: `pyproject.toml` (base deps `pymupdf`, `pytesseract`, `pillow`; `python-multipart` to `api` and `dev`; mypy override `ignore_missing_imports` for `pytesseract` and, only if needed, `fitz`/`pymupdf`)
- Create: `src/receipt_sentinel/extract/__init__.py`, `extracted_document.py`, `attachment_guard.py`
- Create: `src/receipt_sentinel/evals/synthetic_fixture_renderer.py`
- Test: `tests/extract/test_attachment_guard.py`, `tests/evals/test_synthetic_fixture_renderer.py`

**Interfaces:**
- Produces (`extracted_document.py`):
  - `MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024`, `MAX_PAGES = 20`, `MAX_EXTRACT_SECONDS = 30.0`, `MAX_IMAGE_PIXELS = 40_000_000`
  - `MediaType = Literal["application/pdf", "image/png", "image/jpeg"]`
  - `@dataclass(frozen=True) ExtractedDocument(text: str, page_count: int, extraction_method: Literal["native","ocr"], ocr_confidence: float | None)`
  - `RejectionKind = Literal["unsupported_type","too_large","too_many_pages","timeout","encrypted","unreadable","no_text"]`
  - `class ExtractionRejected(Exception)` with `kind: RejectionKind` and a human-readable `str()`
  - `class OcrUnavailable(RuntimeError)`, raised when the Tesseract binary is missing. Message: `"Tesseract OCR is not installed: brew install tesseract (macOS) or apt-get install tesseract-ocr (Linux)"`
- Produces (`attachment_guard.py`): `sniff_media_type(data: bytes) -> MediaType` (raises `unsupported_type`), `check_attachment_size(data: bytes) -> None` (raises `too_large`)
- Produces (`synthetic_fixture_renderer.py`): `render_native_pdf(text: str) -> bytes`, `render_scanned_pdf(text: str) -> bytes` (image-only page, no text layer), `render_photo_jpeg(text: str, *, rotate_degrees: float, blur_radius: float, exif_orientation: int | None) -> bytes`

- [ ] **Step 1: Add deps** (`uv add pymupdf pytesseract pillow`, `uv add --optional api python-multipart`, `uv add --optional dev python-multipart`) and `uv sync --all-extras`.
- [ ] **Step 2: Write failing tests**

```python
# tests/extract/test_attachment_guard.py
def test_sniffs_pdf_png_jpeg_by_magic_bytes_not_name():
    assert sniff_media_type(b"%PDF-1.7\n...") == "application/pdf"
    assert sniff_media_type(b"\x89PNG\r\n\x1a\n...") == "image/png"
    assert sniff_media_type(b"\xff\xd8\xff\xe0...") == "image/jpeg"

def test_heic_photo_is_rejected_with_export_hint():          # Review Focus 1
    with pytest.raises(ExtractionRejected) as err:
        sniff_media_type(b"\x00\x00\x00\x18ftypheic....")
    assert err.value.kind == "unsupported_type" and "JPEG" in str(err.value)

def test_html_renamed_to_pdf_is_rejected():
    with pytest.raises(ExtractionRejected) as err:
        sniff_media_type(b"<html><script>")
    assert err.value.kind == "unsupported_type"

def test_eleven_megabytes_is_too_large():
    with pytest.raises(ExtractionRejected) as err:
        check_attachment_size(b"\0" * (MAX_ATTACHMENT_BYTES + 1))
    assert err.value.kind == "too_large"

# tests/evals/test_synthetic_fixture_renderer.py
def test_native_pdf_has_text_layer_and_scanned_pdf_has_none():
    text = "BLUE BOTTLE COFFEE\nTOTAL $ 50.23"
    assert "50.23" in pymupdf.open(stream=render_native_pdf(text)).load_page(0).get_text()
    assert pymupdf.open(stream=render_scanned_pdf(text)).load_page(0).get_text().strip() == ""

def test_photo_is_jpeg_with_requested_exif_orientation():
    data = render_photo_jpeg("TOTAL 9.99", rotate_degrees=2.0, blur_radius=0.6, exif_orientation=6)
    assert sniff_media_type(data) == "image/jpeg"
    assert Image.open(io.BytesIO(data)).getexif().get(0x0112) == 6
```

- [ ] **Step 3: Run** `uv run pytest tests/extract tests/evals/test_synthetic_fixture_renderer.py -q`. Expected: FAIL (imports missing).
- [ ] **Step 4: Implement.** HEIC/HEIF is recognized by `ftyp` at offset 4 followed by `heic|heix|mif1|heif`, and gets its own message. For `exif_orientation=6`, the renderer stores the image rotated 90° counter-clockwise and tags orientation 6, so a viewer that honors EXIF shows it upright. Use a monospace font at ~28 px, black on off-white, and seed nothing: output depends only on the arguments.
- [ ] **Step 5: Run** the same tests, then `uv run ruff check . && uv run mypy`. Expected: PASS.
- [ ] **Step 6: Commit** `feat(extract): document contracts, magic-byte guard, fixture renderer`

---

### Task 2: `extract_document`: native PDF, OCR fallback, images, caps

**Files:**
- Create: `src/receipt_sentinel/extract/document_extractor.py`
- Test: `tests/extract/test_document_extractor.py` (OCR tests carry `@pytest.mark.skipif(shutil.which("tesseract") is None, reason="needs tesseract")`; CI installs it, so they always run there)

**Interfaces:**
- Consumes: everything Task 1 produces.
- Produces: `extract_document(data: bytes) -> ExtractedDocument`. Synchronous; callers in async code use a thread. Raises `ExtractionRejected` or `OcrUnavailable`.

- [ ] **Step 1: Write failing tests** (render inputs with the Task 1 renderer)

```python
def test_native_pdf_uses_text_layer():
    doc = extract_document(render_native_pdf("SHELL\nTOTAL 38.76"))
    assert doc.extraction_method == "native" and doc.ocr_confidence is None and "38.76" in doc.text

def test_scanned_pdf_falls_back_to_ocr():
    doc = extract_document(render_scanned_pdf("SHELL\nTOTAL 38.76"))
    assert doc.extraction_method == "ocr" and "38.76" in doc.text and 0 < doc.ocr_confidence <= 1

def test_exif_rotated_photo_is_read_upright():                   # Review Focus 2
    data = render_photo_jpeg("TOTAL 38.76", rotate_degrees=0, blur_radius=0, exif_orientation=6)
    assert "38.76" in extract_document(data).text

def test_encrypted_pdf_is_rejected(): ...   # build with pymupdf save(encryption=PDF_ENCRYPT_AES_256, user_pw="x"); kind == "encrypted"   (Review Focus 3)
def test_truncated_pdf_is_unreadable(): ... # render_native_pdf(...)[:200]; kind == "unreadable"                                       (Review Focus 3)
def test_twenty_one_pages_is_too_many(): ... # kind == "too_many_pages"
def test_blank_scanned_page_has_no_text(): ... # render_scanned_pdf("   "); kind == "no_text"
def test_oversized_image_rejected_before_decode(): ... # 8000x6000 PNG header-only or tiny-compressed; kind == "too_large"  (Review Focus 4)
def test_deadline_exceeded_is_timeout(monkeypatch): ... # patch time.monotonic to jump past MAX_EXTRACT_SECONDS; kind == "timeout"
def test_missing_tesseract_raises_ocr_unavailable(monkeypatch): ... # patch pytesseract.image_to_data to raise TesseractNotFoundError; OcrUnavailable, message names "brew install tesseract"   (Review Focus 5)
```

- [ ] **Step 2: Run** `uv run pytest tests/extract/test_document_extractor.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement `extract_document`.** Order: `check_attachment_size` → `sniff_media_type` → PDF or image branch, with one `deadline = time.monotonic() + MAX_EXTRACT_SECONDS` shared across pages.
  - PDF: open from bytes; open failure → `unreadable`; `needs_pass` → `encrypted`; more than `MAX_PAGES` → `too_many_pages`. Per page: native `get_text()`; if under 20 non-whitespace chars, render at 300 DPI (scale DPI down if the page would exceed `MAX_IMAGE_PIXELS`) and OCR it.
  - Image: `Image.open` (lazy), check `width*height > MAX_IMAGE_PIXELS` → `too_large` before `load()`, also catch `Image.DecompressionBombError` → `too_large`, then `ImageOps.exif_transpose`, then OCR.
  - OCR: `pytesseract.image_to_data(img, output_type=Output.DICT, timeout=remaining)`. Join words by line. Confidence is the mean of `conf >= 0` values divided by 100. Tesseract's `RuntimeError` on timeout → `timeout`. `TesseractNotFoundError` → `OcrUnavailable`.
  - Empty final text → `no_text`.
  - Keep each function under the complexity limit by splitting `_extract_pdf`, `_extract_image` and `_ocr_image`.
- [ ] **Step 4: Run** the tests, `ruff`, and `mypy`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(extract): native PDF text with OCR fallback and attachment caps`

---

### Task 3: PDF/photo golden fixtures

**Files:**
- Modify: `src/receipt_sentinel/evals/golden_case.py` (add `fixture: str | None = None`, a path relative to `GOLDEN_DIR`. Validator: no `..` or absolute paths, and the file must exist)
- Create: `scripts/render_golden_fixtures.py`, `src/receipt_sentinel/evals/golden/fixtures/` (committed binaries), 11 new case YAMLs
- Modify: `golden/dataset_version.txt` (`2`), `golden/DATASET_CARD.md` (fixture section; drop the "text-only fixtures" limitation)
- Test: `tests/evals/test_golden_case.py` (add cases)

**Interfaces:**
- Consumes: Task 1 renderer, `GoldenCase`, `golden_case_to_yaml`.
- Produces: `GoldenCase.fixture: str | None`. Each fixture case's `receipt_text` is the source text it was rendered from, which serves as documentation and as the fallback for text-only tools.

- [ ] **Step 1: Write failing tests:** `test_fixture_path_must_stay_inside_golden_dir` (`"../x.pdf"` and `"/etc/passwd"` raise `ValidationError`), `test_every_fixture_case_points_at_a_real_file` (all loaded cases with `fixture` → `(GOLDEN_DIR / fixture).is_file()`), and `test_fixture_cases_cover_native_scanned_and_photo` (at least 3 cases each for `.pdf` native, scanned, and `.jpg`, told apart by id prefix).
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement the field and validator; write `scripts/render_golden_fixtures.py`.** It reads the source cases, renders each fixture, and writes `fixtures/<new_id>.<ext>` plus `cases/<new_id>.yaml`, copying `expected`, `email_date` and `receipt_text` from the source. Mapping (ids are `fixture_<kind>_<source suffix>`):
  - `pdf_native`: `synthetic_0001`, `synthetic_0003` (tip), `synthetic_0046` (EUR), `adversarial_0001` (keeps `adversarial`)
  - `pdf_scanned`: `synthetic_0010`, `synthetic_0017`, `synthetic_0022`. Difficulty `hard`, notes `"scanned PDF, OCR"`
  - `photo`: `synthetic_0002`, `synthetic_0033`, `synthetic_0042` with `rotate_degrees=2.5, blur_radius=0.6, exif_orientation=6`, plus `adversarial_0001` photo (keeps `adversarial`). Difficulty `hard`, except the adversarial one.
- [ ] **Step 4: Run** `uv run python scripts/render_golden_fixtures.py`, check that each fixture file is under 200 KB, and look at two of the JPEGs by eye.
- [ ] **Step 5: Run** the tests. Expected: PASS.
- [ ] **Step 6: Commit** `feat(evals): PDF and photo fixtures in the golden set (dataset v2)`

---

### Task 4: Evals run through extraction

**Files:**
- Create: `src/receipt_sentinel/evals/golden_fixture_text.py`
- Modify: `scoring.py` (`ErrorKind = Literal["parse","provider","extract"]`), `runner.py`, `eval_commands.py` (both `command_evals_run` and `command_compare_models`), `cli.py` (`OcrUnavailable` → stderr message, exit 2)
- Modify: `.github/workflows/tests.yml`, `evals.yml`, `nightly-evals.yml` (`sudo apt-get install -y tesseract-ocr` before evals/tests)
- Modify: `src/receipt_sentinel/evals/baseline_fake.json` (re-blessed)
- Test: `tests/evals/test_golden_fixture_text.py`, `tests/evals/test_runner.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: `extract_document`, `ExtractionRejected`, `OcrUnavailable`, `GoldenCase.fixture`.
- Produces:
  - `extract_fixture_cases(cases: list[GoldenCase], golden_dir: Path = GOLDEN_DIR) -> tuple[list[GoldenCase], dict[str, str]]`: returns cases where each fixture case's `receipt_text` is replaced by the extracted text (`model_copy(update=...)`), and a map `case_id → rejection reason` for fixtures that failed to extract. Lets `OcrUnavailable` propagate.
  - `run_evals(..., extract_errors: Mapping[str, str] | None = None)`: cases in the map get `errored_score(case, "extract", None, pricing)` without a model call.
- Note: `_label_oracle` is untouched. It matches `case.receipt_text`, which after extraction is exactly what the model sees, so keyless runs stay 100% on fixtures whatever the OCR quality.

- [ ] **Step 1: Write failing tests**

```python
def test_fixture_case_text_is_what_extraction_produced(): ...  # native-PDF fixture case → receipt_text == extract_document(file).extracted text
def test_failed_fixture_is_reported_not_raised(tmp_path): ...  # golden dir copy with one fixture truncated → errors == {id: "<reason>"}, case still returned
async def test_extract_error_scores_as_failure_without_model_call(): ...
    # run_evals with extract_errors={"c1": "unreadable"} → score.error_kind == "extract", not passed, fake.calls == []
def test_cli_reports_missing_tesseract_without_traceback(monkeypatch, capsys): ...
    # patch extract_fixture_cases to raise OcrUnavailable → main(["evals","run","--fake",...]) == 2, "brew install tesseract" in stderr, "Traceback" not in stderr
```

- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.** In `eval_commands`, one private `_load_cases_for_run(golden_dir) -> tuple[list[GoldenCase], dict[str, str]]` used by both commands, before `build_client` (so the oracle sees the extracted texts). Extraction runs synchronously before the async run; with 11 fixtures that takes a few seconds, which is acceptable.
- [ ] **Step 4: Run** `uv run sentinel evals run --fake --no-save --out runs/check`. Expected: 100% pass. Then re-bless the fake baseline: `uv run sentinel evals run --fake --out runs/fake-v2` and `uv run sentinel evals promote-baseline <run_id> --fake --out runs/fake-v2`. Then `make demo` still shows CRITICAL.
- [ ] **Step 5: Run** the full checks (`ruff`, `mypy`, `pytest -q`). Expected: PASS.
- [ ] **Step 6: Commit** `feat(evals): golden runs go through extraction; CI installs tesseract`

---

### Task 5: Deterministic validate layer

**Files:**
- Create: `src/receipt_sentinel/validate/__init__.py`, `receipt_validation.py`, `iso4217_currency_codes.py`
- Modify: `src/receipt_sentinel/evals/scoring.py` (replace the `deterministic_validation_passes` body with `validate_receipt(record, email_date=case.email_date).passed`; move `TOTAL_TOLERANCE` and `MAX_RECEIPT_AGE` into `receipt_validation.py` and import them back)
- Test: `tests/validate/test_receipt_validation.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) ValidationRuleResult(rule: Literal["line_items_sum_to_total","date_in_window","currency_is_iso4217"], passed: bool, detail: str)`
  - `@dataclass(frozen=True) ValidationResult(rules: tuple[ValidationRuleResult, ...])` with property `passed: bool` (all rules passed)
  - `validate_receipt(record: ModelOutput, *, email_date: date) -> ValidationResult` (takes `ModelOutput`, so the checklist can reuse it on any record)
  - `ISO_4217_CURRENCY_CODES: frozenset[str]`: active ISO 4217 alphabetic codes, as a data constant

- [ ] **Step 1: Write failing tests:** each rule passes on a golden-shaped record, and each fails with a `detail` naming the numbers (e.g. `"items sum 12.00 ≠ total 12.50"`); the date 61 days before `email_date` fails and 60 days passes; the date after `email_date` fails; `"XYZ"` fails and `"EUR"` passes; no line items skips the sum rule (passes); a refund (negative total, negative items) passes; `ValidationResult.passed` is False if any single rule fails.
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the full checks plus `uv run sentinel evals run --fake --no-save --out runs/check`. Expected: PASS, still 100%.
- [ ] **Step 5: Commit** `feat(validate): per-rule deterministic validation; scoring uses it`

---

### Task 6: Upload a receipt file in the local demo

**Files:**
- Modify: `src/receipt_sentinel/categorize/categorize_api.py`, `README.md` ("Try the categorizer" section plus the trade-offs bullet for AGPL and OCR variance), `Makefile` (none expected)
- Test: `tests/categorize/test_categorize_api.py`

**Interfaces:**
- Consumes: `extract_document`, `ExtractionRejected`, `OcrUnavailable`, `extract_fixture_cases`, `MAX_ATTACHMENT_BYTES`.
- Produces: `POST /categorize/file` (multipart field `file`) → `FileCategorizeResponse(BaseModel)`: `record: ReceiptRecord`, `extraction_method: Literal["native","ocr"]`, `page_count: int`, `ocr_confidence: float | None`.
- Status mapping: `unsupported_type` → 415, `too_large` → 413, other rejections → 422 (detail = the rejection's message), `OcrUnavailable` → 503 with its message, plus the existing parse/provider mapping. Keyless mode with an unknown receipt → 422 `KEYLESS_UNKNOWN_RECEIPT`, with the wording adjusted to mention the fixture folder.

- [ ] **Step 1: Write failing tests:** uploading `golden/fixtures/fixture_pdf_native_0001*.pdf` keyless → 200, `extraction_method == "native"`, `record.total == expected.total`; uploading the scanned fixture → `"ocr"` (skipif no tesseract); uploading `b"<html>"` named `x.pdf` → 415; uploading 10 MB + 1 byte → 413 (read at most `MAX_ATTACHMENT_BYTES + 1` bytes from the upload, never the whole stream).
- [ ] **Step 2: Run.** Expected: FAIL.
- [ ] **Step 3: Implement.** Run extraction with `starlette.concurrency.run_in_threadpool`. In keyless mode, `build_app` calls `extract_fixture_cases` at startup and passes the result to `build_client`, so uploaded fixtures match the oracle. Update the page description to say "or upload a file from `src/receipt_sentinel/evals/golden/fixtures/`". Log rejections by kind only, never content.
- [ ] **Step 4: Verify in a browser:** `make serve`, then in `/docs` upload the photo fixture, see `extraction_method: "ocr"` and the right total, and take a screenshot.
- [ ] **Step 5: Run** the full checks. Expected: PASS.
- [ ] **Step 6: Commit** `feat(api): upload a receipt PDF or photo in the local demo`

---

### Task 7: Real baseline for dataset v2 (author approval needed)

**Files:**
- Modify: `src/receipt_sentinel/evals/baseline.json`, `CLAUDE.md` (handoff: step 6 done), `README.md` roadmap

- [ ] **Step 1: Ask the author** before spending, about $0.20 at the last run's rate (`claude-sonnet-5`, key from the macOS keychain `anthropic-api-key`).
- [ ] **Step 2: Run** `ANTHROPIC_API_KEY=$(security find-generic-password -s anthropic-api-key -w) uv run sentinel evals run --out runs/v2`. Report the pass rate per difficulty, and list any fixture case that fails with its likely cause (OCR vs reasoning).
- [ ] **Step 3: Promote** with `uv run sentinel evals promote-baseline <run_id> --out runs/v2`, only after the author has seen Step 2's results.
- [ ] **Step 4: Commit** `chore(evals): bless real baseline for dataset v2`, open the PR, and confirm CI is green.
