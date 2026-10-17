# Run-history dashboard (GitHub Pages) — design

Date: 2026-10-17 · Status: draft, pending author approval

## Intent

A public page, linked from the README, that shows how the receipt model has done night by
night. It serves two readers with one layout:

- **A visitor** (hiring manager, reviewer) should understand in about 30 seconds that this
  project catches LLM regressions before users do, and see that it is really running.
- **The author** checks it in the morning: was last night OK, and is anything trending down?

It also closes two gaps the nightly workflow left open: nightly runs are thrown away, so
slow drift is never checked, and stored runs record a placeholder status.

## Goals

1. A static page at the repo's GitHub Pages URL, rebuilt after every nightly run on `main`.
2. Page order (author chose layout "B · story first" from mockups):
   1. Headline: "Receipt Sentinel: catching LLM regressions before users do".
   2. A four-step pipeline strip: labelled receipts → nightly model run → diff vs baseline →
      issue / blocked PR.
   3. Status bar for the latest night: PASS / WARN / CRITICAL (plus a "drifting" flag when
      set), pass rate, number of receipts, model, when it ran.
   4. Pass-rate trend over the last 30 nights, with the baseline as a dashed line.
   5. Night-by-night table: date, model, pass rate, delta vs baseline (pts), regressed-case
      count, cost, status. Each row links to that night's GitHub Actions run.
3. Nightly history persists across runs, so the nightly eval computes drift
   (`compute_drift`, window and floor from `config/thresholds.yaml`).
4. Stored runs record the real verdict (the diff status), not the PASS placeholder.
5. README links to the dashboard near the top.

## Non-goals

- PR eval runs do not appear. They test changes that may never merge. (Author agreed.)
- No per-receipt detail on the public page (see anti-goals).
- No JavaScript framework, no build tooling, no external CDN. Plain HTML + inline SVG,
  the same approach as `report.py`.
- No backfill of history before the first nightly run that ships with this.

## Anti-goals (failure even if it "works")

- **Receipt content on a public surface.** Author decision: the public page shows numbers
  only. No receipt text, no model outputs, no expected labels, no case notes. Once redacted
  real receipts join the golden set, a page that leaks them is a failure. Case ids may appear
  only as counts, not lists.
- Publishing a stored `EvalRun` JSON as-is. Its `scores[].actual` holds raw model output.
  What gets published must be a numbers-only record built for the purpose.
- Nightly bot commits on `main`. History lives elsewhere; `main` stays human-authored.
- Drift or history logic that silently does nothing (the failure mode the exit-2 fix just
  removed). A missing or unreadable history must be visible in the run log.

## Constraints

- Public repo: nothing secret, nothing personal, no real PII (project CLAUDE.md).
- Every write traces to an explicit action (author's coding conventions, Rule #5). Here the
  action is the scheduled/manually-dispatched workflow; nothing writes from a page.
- Workflow security as already applied in `nightly-evals.yml`: third-party actions pinned to
  a commit SHA, tokens scoped to the step that needs them, untrusted values only via `env:`.
  Writing to the history branch needs `contents: write`; grant it only to the step or job
  that pushes.
- `--fake` / demo runs (`mode: fake-*`) must never write to the published history.
- Python 3.12, ruff + mypy --strict clean, files under ~1,000 lines, functions small,
  file headers per the author's conventions. Tests for non-trivial logic.

## Decided (author)

- Layout B, story first.
- Numbers only on the public page; receipts stay private.
- Only nightly runs on `main` feed it.
- Guard the existing artifact leak (below) as part of this work.

## Decided (my calls; author can override)

- **History storage:** a `gh-pages` branch holds the published site and an append-only
  numbers-only history file (one record per nightly run). The nightly job checks it out,
  appends, rebuilds the page, and pushes. Reason: survives the 90-day artifact expiry, no
  database to host, no commits on `main`.
- **Drift opens the issue too.** The author's concern is the model getting worse with no
  code change; slow drift is the quiet version of that. A DRIFTING night opens/comments on
  the same `eval-regression` issue as CRITICAL. A night that is PASS and not drifting closes it.
- **Status fix:** `save_run` stores the diff verdict. Simplest shape is up to the builder
  (e.g. save after the diff with the diff status). The C# MCP server then can return status;
  updating it is optional follow-on, not part of this work.
- **Visual style:** match the existing `report.py` look (plain, system font), not the
  green-heavy mockup. Must render on a phone without horizontal scroll.
- **Drift is a flag, not a new status.** `RunStatus` keeps PASS / WARN / CRITICAL /
  PROVIDER_UNSTABLE; drift is stored as its own field (`drifting` plus the gap in points).
  The page shows it next to the status ("PASS · drifting"), and the nightly issue step treats
  "CRITICAL or drifting" as bad. A drifting night also turns the workflow red, like CRITICAL,
  so it is visible in Actions too. No change to `classify_status`, Slack emoji, or the PR gate.
  "Stored runs record the real verdict" means the diff status plus this drift field.
- **Drift restarts on a new baseline.** Each history record carries the baseline run id
  (and model id). The drift window and the trend's dashed line only use nights judged against
  the current baseline. Promoting a better baseline, or moving `main` to a new model, must
  not raise a false public issue the next morning. The trend marks where the baseline
  changed.
- **Nights that produced no score still appear.** PROVIDER_UNSTABLE, rejected-key (exit 2)
  and "no API key, skipped" nights get a row marked "not scored" with the reason and the run
  link, so gaps are never silent. They have no pass rate and are skipped by drift and by
  the trend line.

## Artifact guard

Today `evals.yml` and `nightly-evals.yml` upload the full `report.html` as an Actions
artifact. On a public repo any signed-in GitHub user can download it. It contains receipt
text and model output for every regressed case.

Rule: CI uploads the full report only while every golden case has `source: synthetic`.
If any case has another source (e.g. `real_redacted`), CI uploads nothing receipt-level;
`summary.md` (numbers only) still goes to the PR comment / job summary. The author views
detailed reports locally (`runs/report.html`). The check belongs in Python (the golden set
is already loaded there), not in shell.

## Left to the builder

- History record schema and file format (JSON Lines is fine), as long as it is numbers-only
  and versioned with `dataset_version`.
- Whether drift reads the published history file directly or a small adapter feeds
  `compute_drift` (it only needs pass rates; it currently takes `EvalRun`s).
- How a `dataset_version` change shows on the page (e.g. a marker on the trend, baseline line
  per version). History from an older dataset version must not count toward drift.
- Page generation module name and structure (reuse `report._trend_svg` where it fits).
- First-run behaviour: no `gh-pages` branch yet → create it.
- Enabling Pages (repo setting, source = `gh-pages` branch) is an author action; the builder
  documents the one step.

## Done when

- A manual nightly run (`mode: real`) on `main` appends to the history and the public URL
  shows the new row; a `fake-*` run leaves the history untouched.
- After `drift_window_runs` nights of history, drift is computed and a simulated drift opens
  the issue (verified the way the issue automation was: keyless fake modes or a test).
- A stored run's status matches its diff verdict.
- The artifact guard is covered by a test (synthetic-only → full report; any real case →
  summary only).
- The page contains no receipt text or model output (asserted by a test on the generator).
- README links the dashboard; CI green.
