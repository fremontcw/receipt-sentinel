# Run-History Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish a numbers-only, story-first GitHub Pages dashboard of nightly eval runs, and use the stored history to detect slow drift.

**Architecture:** Every nightly run on `main` appends one numbers-only record to `history.jsonl` on a `gh-pages` branch, computes drift from that file, and regenerates `index.html` next to it. A new `run_history` module owns the record types and file I/O, and a new `dashboard` module renders the page. `evals run` gains `--history` and `--public`. The nightly workflow checks out, appends to and pushes `gh-pages`.

**Tech Stack:** Python 3.12, stdlib (`json`, `html`, `dataclasses`), pytest, GitHub Actions, `gh`/`git`.

**Spec:** `docs/superpowers/specs/2026-10-17-run-history-dashboard-design.md`

## Global Constraints

- Public page and `history.jsonl` are **numbers only**: no receipt text, model output, expected labels, case notes or case-id lists.
- Never publish a stored `EvalRun` JSON; its `scores[].actual` holds raw model output.
- No bot commits on `main`; history lives on `gh-pages` only.
- `fake-*` workflow modes never read or write the published history.
- Missing or unreadable history must be visible: a missing file means empty history, but a malformed line raises.
- Workflow security: third-party actions pinned to a SHA; checkout with `persist-credentials: false`; the push token only in the push step's `env:`; untrusted values only via `env:`.
- Plain HTML + inline SVG, no JS/CDN; match `report.py`'s `STYLE`; no horizontal scroll at phone width.
- `RunStatus` is unchanged; drift is a separate `drifting` flag. "CRITICAL or drifting" is bad for the nightly; the PR gate is unaffected.
- Drift and the trend's baseline line count only scored nights with the same `dataset_version` **and** `baseline_run_id` as the current run.
- File headers (status/issues/todo), ruff + `mypy --strict` clean, functions small, tests for logic.

## Review Focus

1. **First run, no `gh-pages` branch / no history file** → treated as empty history, the branch gets created, no crash. (Task 1 test + Task 4 workflow.)
2. **A malformed or unknown-schema line in `history.jsonl`** → raises with the line number; it is never silently dropped. (Task 1 test.)
3. **Baseline promoted, or dataset version bumped, mid-window** → older nights are excluded, so no false DRIFTING. (Task 1 test.)
4. **Hostile strings in a record** (`model_id`/`reason` containing `<script>`, a non-GitHub `run_url`) → escaped, and the link is dropped unless it starts with `https://github.com/`. (Task 3 test.)
5. **Fewer scored nights than `drift_window_runs`** → no drift verdict, never a false flag. (Task 1 test.)

---

### Task 1: Numbers-only history records and drift from history

**Files:**
- Create: `src/receipt_sentinel/evals/run_history.py`
- Modify: `src/receipt_sentinel/evals/diff.py` (`compute_drift`)
- Test: `tests/evals/test_run_history.py`, `tests/evals/test_diff.py`

**Interfaces:**
- Produces (in `run_history.py`):
  - `HISTORY_SCHEMA_VERSION = 1`
  - `@dataclass(frozen=True) class ScoredNight`: `kind: Literal["scored"]`, `schema_version: int`, `created_at: str`, `run_url: str | None`, `model_id: str`, `prompt_label: str`, `dataset_version: str`, `baseline_run_id: str`, `case_count: int`, `pass_rate: float`, `delta_points: float`, `regressions: int`, `adversarial_failures: int`, `cost_usd: float`, `p50_latency_ms: int`, `status: str` (a `RunStatus` value), `drifting: bool`, `drift_gap_points: float | None`
  - `@dataclass(frozen=True) class UnscoredNight`: `kind: Literal["not_scored"]`, `schema_version: int`, `created_at: str`, `run_url: str | None`, `reason: str`
  - `HistoryRecord = ScoredNight | UnscoredNight`
  - `scored_night_from(run: EvalRun, diff: DiffResult, *, run_url: str | None) -> ScoredNight`
  - `unscored_night(reason: str, *, run_url: str | None, created_at: str | None = None) -> UnscoredNight` (defaults `created_at` to now, UTC, `Z` suffix like `run_store`)
  - `load_history(path: Path) -> list[HistoryRecord]` (file order, oldest first; `[]` if missing)
  - `append_history(path: Path, record: HistoryRecord) -> None` (creates parents; one JSON object per line)
  - `comparable_pass_rates(history: list[HistoryRecord], *, dataset_version: str, baseline_run_id: str) -> list[float]` (newest first, scored only)
- Produces (in `diff.py`): `drift_from_pass_rates(pass_rates: list[float], baseline_pass_rate: float, thresholds: Thresholds) -> DriftResult | None`, with pass rates newest first. `compute_drift` keeps its signature and delegates to it.

- [ ] **Step 1: Write the failing tests** in `tests/evals/test_run_history.py`:
  - `test_scored_night_is_numbers_only`: build an `EvalRun` whose score has `actual={"vendor": "SECRET_RECEIPT_TEXT"}`; `json.dumps(asdict(scored_night_from(run, diff, run_url=None)))` must not contain `"SECRET_RECEIPT_TEXT"` or the case id; `delta_points == diff.pass_rate_delta_points`; `drifting is False` when `diff.drift is None`.
  - `test_round_trip_and_order`: append a `ScoredNight` then an `UnscoredNight` to `tmp_path/"h.jsonl"`; `load_history` returns both, equal, in that order.
  - `test_missing_file_is_empty_history`: `load_history(tmp_path/"nope.jsonl") == []`.
  - `test_malformed_line_raises_with_line_number`: second line `{"kind": "scored"` → `pytest.raises(ValueError, match="line 2")`; a line with `"schema_version": 99` also raises.
  - `test_comparable_pass_rates_filters_baseline_and_dataset`: history of scored nights `[(v1,b1,0.9), (v1,b2,0.8), (v2,b2,0.7), unscored, (v1,b2,0.6)]` → `comparable_pass_rates(..., dataset_version="1", baseline_run_id="b2") == [0.6, 0.8]`.
  
  In `tests/evals/test_diff.py`:
  - `test_drift_from_pass_rates_needs_full_window`: with `Thresholds()` (window 7, floor 2.0) and 6 rates → `None`; with 7 rates of `0.95` against baseline `0.982` → `drifting is True`, `gap_points ≈ 3.2`.

- [ ] **Step 2: Run them to verify they fail**
  Run: `uv run pytest -q tests/evals/test_run_history.py tests/evals/test_diff.py`
  Expected: collection error (`run_history` missing) / `drift_from_pass_rates` import error.

- [ ] **Step 3: Implement the interfaces above.** `load_history` parses each non-blank line, dispatches on `kind`, and raises `ValueError(f"{path} line {n}: ...")` on bad JSON, an unknown `kind`, a `schema_version != HISTORY_SCHEMA_VERSION`, or missing/extra fields (`TypeError` from the dataclass becomes `ValueError`). Serialise with `json.dumps(asdict(record))`. Refactor `compute_drift` to `drift_from_pass_rates([r.pass_rate for r in recent_runs], baseline.pass_rate, thresholds)`.

- [ ] **Step 4: Run the full suite**
  Run: `uv run ruff check . && uv run mypy && uv run pytest -q`
  Expected: all pass (84 existing + new).

- [ ] **Step 5: Commit**
  `git add src/receipt_sentinel/evals/run_history.py src/receipt_sentinel/evals/diff.py tests/evals/test_run_history.py tests/evals/test_diff.py && git commit -m "feat(evals): numbers-only run history and drift from history"`

---

### Task 2: `evals run --history/--public`, real stored status, `record-skip`

**Files:**
- Modify: `src/receipt_sentinel/evals/eval_commands.py` (`command_evals_run`, `_diff_for`, new `command_record_skip`, new `golden_set_is_all_synthetic`)
- Modify: `src/receipt_sentinel/cli.py`
- Test: `tests/evals/test_eval_commands.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: Task 1's `load_history`, `append_history`, `scored_night_from`, `unscored_night`, `comparable_pass_rates`, `drift_from_pass_rates`.
- Produces:
  - `command_evals_run(..., history_path: Path | None = None, public: bool = False)`, where existing parameters are unchanged and `report_url` doubles as the record's `run_url`.
  - `command_record_skip(history_path: Path, *, reason: str, run_url: str | None) -> int` (appends an `UnscoredNight`, returns 0).
  - `golden_set_is_all_synthetic(cases: list[GoldenCase]) -> bool`.
  - Output files next to `summary.md`: `status` (unchanged) and new `drift` containing `drifting\n` or `steady\n`.
  - CLI: `sentinel evals run [--history PATH] [--public]`; `sentinel evals record-skip --history PATH --reason TEXT [--run-url URL]`.
- Behaviour:
  - With `history_path`, drift = `drift_from_pass_rates([run.pass_rate, *comparable_pass_rates(load_history(path), dataset_version=..., baseline_run_id=baseline.run_id)], ...)` instead of the SQLite history. One `ScoredNight` is appended after the diff.
  - A PROVIDER_UNSTABLE run appends `unscored_night(f"provider unstable (error rate {rate:.0%})", ...)`.
  - The exit code is 1 if the status is CRITICAL **or** `diff.drift` is set and `drifting`.
  - `save_run` receives `dataclasses.replace(run, status=diff.status)`, which fixes the PASS placeholder.
  - With `public=True` and a golden set that isn't all synthetic, `report.html` is **not written**, a one-line notice is printed, and `summary.md`/`status`/`drift` are still written.

- [ ] **Step 1: Write the failing tests** in `tests/evals/test_eval_commands.py` (reuse `paths_in`; fake runs):
  - `test_stored_run_records_diff_verdict`: bless a clean run, then a `demo_noise=True` run with `save=True` → `list_recent_runs(...)[0].status is RunStatus.CRITICAL`.
  - `test_history_run_appends_scored_night`: bless a clean baseline, run with `history_path=tmp_path/"h.jsonl"` → one `ScoredNight` with `baseline_run_id` = the blessed run id, and the `drift` file reads `steady\n`.
  - `test_drifting_history_exits_one_and_writes_drift`: pre-fill the history with 7 `ScoredNight`s at `pass_rate=0.80` with the same dataset/baseline id, bless a clean baseline (1.0), run clean with history → exit code 1, `drift` reads `drifting\n`, `status` reads `PASS\n`, and the appended record has `drifting is True`.
  - `test_history_ignores_nights_from_an_old_baseline`: same as above, but the pre-filled nights carry `baseline_run_id="old"` → exit 0, `steady`.
  - `test_provider_unstable_with_history_appends_unscored`: the `Flaky` client from the existing test plus `history_path` → one `UnscoredNight` whose `reason` starts with `"provider unstable"`.
  - `test_public_run_omits_report_when_golden_set_has_real_case`: copy the golden dir to `tmp_path`, set one case's `source: real_redacted`, run with `public=True` → `report.html` doesn't exist and `summary.md` does; with all-synthetic + `public=True` → `report.html` exists.
  - `test_record_skip_appends_reason`: `command_record_skip(p, reason="no API key", run_url=None) == 0` → `load_history(p)[0].reason == "no API key"`.

  In `tests/test_cli.py`: `test_cli_record_skip(tmp_path)`: `main(["evals", "record-skip", "--history", str(h), "--reason", "rejected key"]) == 0`.

- [ ] **Step 2: Run to verify they fail**
  Run: `uv run pytest -q tests/evals/test_eval_commands.py tests/test_cli.py`
  Expected: failures on the new tests only.

- [ ] **Step 3: Implement** the interfaces and behaviour above. Keep `command_evals_run` under ~100 lines by moving the history/drift branch into a helper (e.g. `_drift_and_record(...)`). Wire the flags in `cli.py`'s `_build_parser`/`_dispatch`. Update `eval_commands.py`'s file header if needed, and in `CLAUDE.md` remove handoff item 5 (stored status), since it's fixed.

- [ ] **Step 4: Run the full suite**
  Run: `uv run ruff check . && uv run mypy && uv run pytest -q`
  Expected: all pass.

- [ ] **Step 5: Commit**
  `git commit -am "feat(evals): run history for drift, real stored status, public-safe reports"` (also `git add` any new test fixtures).

---

### Task 3: Dashboard page renderer and `evals dashboard`

**Files:**
- Create: `src/receipt_sentinel/evals/dashboard.py`
- Modify: `src/receipt_sentinel/cli.py`
- Test: `tests/evals/test_dashboard.py`

**Interfaces:**
- Consumes: Task 1's `HistoryRecord`, `ScoredNight`, `UnscoredNight`, `load_history`; `report.STYLE` (import and extend it, don't copy it).
- Produces: `render_dashboard_html(history: list[HistoryRecord], *, repo_url: str) -> str`; `command_build_dashboard(history_path: Path, out_path: Path, *, repo_url: str) -> int`; CLI `sentinel evals dashboard --history PATH --out PATH --repo-url URL`.
- Page, in this order (exact copy from the spec):
  1. `<h1>Receipt Sentinel: catching LLM regressions before users do</h1>`
  2. A four-step strip: "Labelled receipts" → "Nightly model run" → "Diff vs baseline" → "Issue / blocked PR"
  3. The status bar for the newest record: status badge, plus "drifting" if set, pass rate, `case_count` receipts, model, date. If the newest record is unscored: a "Not scored" badge plus the reason.
  4. An inline-SVG trend of the last 30 **scored** nights, with a dashed baseline line and a vertical marker wherever `baseline_run_id` changes.
  5. A table, newest first: date, model, pass rate, Δ pts, regressions, cost, status (unscored rows: "not scored: <reason>").
  6. A footer link to `repo_url`.
- Empty history → steps 1–2 plus "No nightly runs yet."

- [ ] **Step 1: Write the failing tests** in `tests/evals/test_dashboard.py`:
  - `test_story_first_order`: the indices of the headline, `"Nightly model run"`, the status badge, `<svg` and `<table` are strictly increasing.
  - `test_empty_history_renders_placeholder`: `"No nightly runs yet"` is in the output.
  - `test_unscored_newest_shows_reason`: the newest record is `UnscoredNight(reason="no API key")` → `"Not scored"` and `"no API key"` are both in the output.
  - `test_hostile_strings_escaped_and_bad_links_dropped`: `model_id="<script>x</script>"`, `reason="<img onerror=1>"`, `run_url="javascript:alert(1)"` → no `<script>`, `<img` or `javascript:` in the output; a record with `run_url="https://github.com/o/r/actions/runs/1"` renders an `<a href=` to it.
  - `test_no_script_and_baseline_change_marked`: there's no `<script` anywhere, and two baseline ids in the history produce exactly one change marker (give it `class="baseline-change"`).

- [ ] **Step 2: Run to verify they fail**
  Run: `uv run pytest -q tests/evals/test_dashboard.py`
  Expected: collection error (module missing).

- [ ] **Step 3: Implement** `dashboard.py`: escape every string with `html.escape`, and link `run_url` only if it starts with `https://github.com/`. Add a `@media (max-width: 600px)` rule so the table scrolls inside its own container rather than the page, and the SVG scales (`viewBox`, `width="100%"`). Wire the CLI subcommand.

- [ ] **Step 4: Render it and look at it**
  Run: `uv run sentinel evals dashboard --history tests/evals/fixtures/dashboard_history.jsonl --out runs/dashboard/index.html --repo-url https://github.com/fremontcw/receipt-sentinel` (commit a small fixture of ~10 mixed records, including one baseline change and one unscored night). Open the page in a browser at desktop and at 375 px width.
  Expected: the story-first order, the trend with its marker, and no horizontal page scroll.

- [ ] **Step 5: Run the full suite, then commit**
  Run: `uv run ruff check . && uv run mypy && uv run pytest -q`, then `git add src/receipt_sentinel/evals/dashboard.py src/receipt_sentinel/cli.py tests/evals/test_dashboard.py tests/evals/fixtures/dashboard_history.jsonl && git commit -m "feat(dashboard): story-first numbers-only run-history page"`

---

### Task 4: Publish from the nightly workflow; guard artifacts; README

**Files:**
- Modify: `.github/workflows/nightly-evals.yml`, `.github/workflows/evals.yml`, `README.md`, `CLAUDE.md`

**Interfaces:**
- Consumes: CLI from Tasks 2–3: `evals run --history --public`, `evals record-skip`, `evals dashboard`.
- Produces: the `gh-pages` branch containing `history.jsonl` and `index.html`.

- [ ] **Step 1: `evals.yml`** — add `--public` to the `sentinel evals run` line, and `if-no-files-found: ignore` to the upload step.

- [ ] **Step 2: `nightly-evals.yml`**:
  - Job permissions become `contents: write` and `issues: write`.
  - Checkout `main` with `persist-credentials: false`.
  - Real mode only: fetch `gh-pages` into `site/`, using `git worktree add site origin/gh-pages` if it exists, otherwise `git worktree add --orphan -b gh-pages site`.
  - Every real-mode outcome writes exactly one record to `site/history.jsonl`:
    - eval ran: `--history site/history.jsonl --public`
    - no key: `record-skip --reason "no API key"`
    - exit ≥ 2: `record-skip --reason "provider rejected the request (exit N)"` before failing
  - Then `sentinel evals dashboard --history site/history.jsonl --out site/index.html --repo-url "$GITHUB_SERVER_URL/$GITHUB_REPOSITORY"`.
  - Commit as `github-actions[bot]`, and push to `gh-pages` in a step whose `env:` alone holds `GH_TOKEN: ${{ github.token }}` (push URL `https://x-access-token:${GH_TOKEN}@github.com/${GITHUB_REPOSITORY}.git`).
  - **Publishing runs before the issue step's `exit 1`.**
  - The issue step treats `status == CRITICAL` or `drift == drifting` as bad, and `PASS` + `steady` as recovered.
  - Fake modes skip every `site/` step.
  - Guard publishing with `if: github.ref == 'refs/heads/main'`.

- [ ] **Step 3: Lint**
  Run: `actionlint`
  Expected: no output.

- [ ] **Step 4: README** — add a "Live dashboard" link near the top (`https://fremontcw.github.io/receipt-sentinel/`), plus a one-line note under the CI section: "Pages: Settings → Pages → Source: Deploy from a branch → `gh-pages` / root (one-time author step)". In `CLAUDE.md`, mark item 3 done.

- [ ] **Step 5: Commit, push, and verify on GitHub**
  - `git commit -am "ci: publish nightly history to GitHub Pages; keep receipts out of artifacts"`, then `git push`.
  - Ask the author to enable Pages (the README step).
  - Then `gh workflow run nightly-evals.yml -f mode=fake-regression`. Expected: the issue flow behaves as before, and `git ls-remote origin gh-pages` shows no new commit.
  - Then `gh workflow run nightly-evals.yml -f mode=real`. Expected: `gh-pages` gains a commit, `history.jsonl` has one scored line, and the Pages URL shows the row (cost ≈ $0.19).
  - Close any demo issue the fake run opened, as in the original check.
