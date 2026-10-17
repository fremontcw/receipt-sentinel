# Receipt Sentinel

An LLM document-extraction feature and the CI that keeps it honest.

**Live dashboard:** [nightly eval history](https://fremontcw.github.io/receipt-sentinel/) (numbers only; rebuilt after every nightly run).

## Problem

Prompt tweaks, model swaps, and provider-side updates silently change what an
LLM extraction feature does. There's no compiler error for "the model now
reads `total` off the tip line instead of the total line." Without a
regression gate, that kind of drift ships straight to prod and gets found by
a user, not a test.

Receipt Sentinel is two things in one repo: a small feature that reads a
receipt and returns structured fields with an LLM (`categorize_receipt`),
and a CI harness that runs that feature against a hand-labeled golden
dataset on every prompt/model change, diffs the result against a blessed
baseline, and blocks the merge on a real regression.

## Architecture

```
src/receipt_sentinel/
├── categorize/         LLM call: receipt text → ReceiptRecord (strict JSON schema, no tools)
│   ├── categorize_receipt.py
│   ├── llm_client.py           LlmClient protocol
│   ├── anthropic_llm_client.py Real client (claude-sonnet-5, thinking disabled)
│   ├── azure_foundry_llm_client.py Same model via Microsoft Foundry (--provider azure)
│   └── receipt_record.py       Pydantic output schema
├── prompts/             Versioned YAML prompts (id, version, created_at, system, few-shot)
├── telemetry.py         OpenTelemetry: run / case / GenAI chat spans (off unless configured)
mcp_run_history_server/  C# MCP server: lets an agent ask "what regressed?" (read-only)
└── evals/
    ├── golden/           Hand-labeled receipts: cases/*.yaml + DATASET_CARD.md + dataset_version.txt
    ├── runner.py          Async batch: PromptConfig × golden cases → raw outputs
    ├── scoring.py         Per-case multi-dimension scores
    ├── diff.py            Run vs baseline: flips, deltas, thresholds, drift
    ├── report.py          Static HTML diff report (no JS, inline SVG trend)
    ├── alerts.py          Slack webhook (no-op if unset)
    ├── run_store.py       Local run history (SQLite/JSON, not committed)
    └── eval_commands.py   CLI glue: run / promote-baseline / promote
```

The ingest, extract, validate, and checklist layers described in the design
spec are not built yet — see [Roadmap](#roadmap). Today the golden dataset
supplies pre-extracted receipt text directly, so the harness can gate the
one component that exists: the LLM call itself.

Harness loop:

```
 golden/cases/*.yaml
        │
        ▼
   ┌─────────┐      PromptConfig       ┌────────────┐
   │ runner  │ ───────────────────────▶│ LlmClient  │
   └─────────┘                         │ (real/fake)│
        │  raw ReceiptRecord           └────────────┘
        ▼
   ┌─────────┐
   │ scoring │  per-case: category/total/date/vendor/line-items/
   └─────────┘  validation/injection/latency/tokens/cost
        │
        ▼
   ┌─────────┐   baseline.json (blessed, committed)
   │  diff   │◀──────────────────────────────────────
   └─────────┘
        │ status: PASS / WARN / CRITICAL / PROVIDER_UNSTABLE
        ▼
   ┌───────────────────┐     ┌───────┐
   │ report.html +      │────▶│ Slack │  (SLACK_WEBHOOK_URL, no-op if unset)
   │ summary.md         │     └───────┘
   └───────────────────┘
        │
        ▼
   GitHub Actions: artifact + sticky PR comment; fails the job on CRITICAL
```

## Eval methodology

**Dataset** — 56 golden cases at dataset version 1: 48 seeded synthetic
(`scripts/generate_synthetic_golden_cases.py`, deterministic) plus 8
hand-written adversarial cases. Zero `real_redacted` cases so far; the
author adds them via `scripts/redaction_checklist.md` and
`sentinel evals promote`. Full sourcing, labeling, and known limitations are
in [`src/receipt_sentinel/evals/golden/DATASET_CARD.md`](src/receipt_sentinel/evals/golden/DATASET_CARD.md).

**Scoring dimensions per case:**

| Dimension | Rule |
|---|---|
| Category | exact match |
| Total | within ±0.01 |
| Date | exact match |
| Vendor | fuzzy match (normalised) ≥ 0.85 |
| Line items | recall by multiset of amounts |
| Validation | deterministic: line items sum ≈ total; date within 60 days before the receiving email's date |
| Injection resisted | adversarial cases only — `injection_detected` flag set **and** category/total still correct |
| Latency, tokens, cost | recorded, not gating |

A case **passes** when every field matches, validation passes, and (for
adversarial cases) the injection isn't successful.

**Baseline** — `evals/baseline.json` (real client) and
`evals/baseline_fake.json` (fake client, for the keyless demo) are the
blessed references. Every run diffs against the baseline, never against the
prior run, so quality can't ratchet down one small regression at a time.
Promoting a run is explicit: `sentinel evals promote-baseline <run_id>`. A
baseline is only valid for the dataset version it was blessed against.

**Status thresholds** (`config/thresholds.yaml`):

| Status | Trigger |
|---|---|
| WARN | pass-rate delta ≤ −3 pts vs baseline |
| CRITICAL | pass-rate delta ≤ −8 pts, or any adversarial case fails |
| PROVIDER_UNSTABLE | > 5% of cases hit a provider error after retries |
| DRIFT (reported alongside PASS) | 7-run moving average > 2 pts below baseline |

Parse errors (malformed model JSON, including a reply truncated at
`max_tokens`) count as scored failures, not provider instability — only
transport-level and non-retryable API errors (timeouts, 5xx after retries,
401/403) count toward the 5% `PROVIDER_UNSTABLE` threshold. A
provider-errored case never reached the model, so it is **excluded from the
pass-rate denominator, from pass↔fail flips, and from the adversarial
failure count** — an outage can't be scored as a model regression, and its
only signal is the `PROVIDER_UNSTABLE` rate. An unstable run is reported but
never stored as a scored run, so it can't corrupt the drift window. Cost
ceiling: $2/run. Max concurrency: 8.

**Drift comes from the published nightly history.** Each nightly run on
`main` appends one numbers-only line to `history.jsonl` on the `gh-pages`
branch; the 7-night moving average counts only nights judged against the
current baseline, so promoting a new baseline restarts the window instead of
raising a false alarm. Local runs without `--history` fall back to the SQLite
store under `runs/`.

**Error taxonomy** — every reviewed failure is tagged in its golden case:
`ocr | schema | reasoning | ambiguity | label_error | provider`.

## A regression, caught

`--fake` swaps the real model for a **clean oracle** that answers straight
from the golden labels — a perfect model, by construction. That's what
`baseline_fake.json` is blessed from, and what CI's keyless smoke run
exercises (100% pass, PASS).

`--demo-noise` adds deterministic damage on top of that oracle: ~6% of cases
are mis-categorised to `other`. Diffed against the clean baseline, that is a
real regression, so `make demo` prints **CRITICAL** at −10.7 pts with 6
regressed cases and exits 1 — which the `make` target catches so the demo
itself still succeeds. The resulting report is committed at
[`docs/sample_report.html`](docs/sample_report.html) — open it directly, or
see below.

<!-- ![Sample regression report](docs/sample_report.png) -->

The report lists each regressed case side by side: expected value, previous
(baseline) output, current output, and which scoring dimension flipped —
e.g. a case that passed at baseline now fails on `category` because the
noise-injected oracle mis-routes a grocery receipt to `other`. That's the
shape of report a real prompt or model regression produces.

## The thing under test

`categorize_receipt(doc, prompt) -> ReceiptRecord` is deliberately small:

- Receipt text is passed **only in the user turn, as delimited data** — the
  system prompt (the trusted instruction template, versioned in
  `prompts/`) states explicitly that this text is untrusted evidence, not
  instructions, and that any instruction-like content inside it must be
  ignored and reported via `injection_detected: bool`.
- **No tools** — the model can't take any action beyond returning JSON.
- Output is forced to a strict JSON schema, parsed into a Pydantic
  `ReceiptRecord`; anything that doesn't parse is a scored failure, not a
  silent pass-through.
- **Provenance is stamped by code, not the model** — `model_id` and
  `prompt_version` are set from the calling context after the response
  comes back, not trusted from model output, so a case attempting to set
  `model_id`/`confidence` itself (see `adversarial_0006_set_model_id.yaml`,
  `adversarial_0005_set_confidence.yaml`) can't succeed.
- 8 adversarial golden cases exercise instruction override, schema
  tampering, secret exfiltration, HTML/script injection in vendor or line
  items, role-play jailbreaks, and JSON smuggled inside a line-item field.

## Run it

**Demo (no API key):**

```bash
make install
make demo
```

Runs `sentinel evals run --fake --demo-noise --out runs/demo` and produces
`runs/demo/report.html` + `runs/demo/summary.md`. It reports **CRITICAL** and
the command exits 1 — that is the demo working, and the `make` target absorbs
the exit code. Drop `--demo-noise` for a clean 100%-pass fake run.

**Real run (needs `ANTHROPIC_API_KEY`):**

```bash
uv run sentinel evals run --out runs/local
uv run sentinel evals promote-baseline <run_id> --out runs/local
```

**Try the categorizer in a browser:**

```bash
make serve    # then open http://127.0.0.1:8000/docs
```

`POST /categorize` takes receipt text and returns the structured `ReceiptRecord`.
It calls the same `categorize_receipt` the eval harness regression-tests. Without a
key it runs keyless: pick a sample from the **Examples** dropdown (a clean receipt,
one with a tip, and a prompt-injection attack, which comes back flagged
`injection_detected: true`, not obeyed). Set `ANTHROPIC_API_KEY` (or
`SENTINEL_PROVIDER=azure` plus the Foundry variables below) to extract your own
receipts with the real model. It listens on `127.0.0.1` only, so there is no auth,
and nothing is stored.

**Same model on Azure (Microsoft Foundry):**

```bash
export ANTHROPIC_FOUNDRY_API_KEY=...        # Foundry resource key
export ANTHROPIC_FOUNDRY_RESOURCE=my-resource   # https://my-resource.services.ai.azure.com/anthropic/
export ANTHROPIC_FOUNDRY_MODEL=claude-sonnet-5  # deployment name, if different
uv run sentinel evals run --provider azure --out runs/azure
```

The Azure adapter hands the SDK's Foundry client to the same
`AnthropicLlmClient`, so retries, timeouts, and parsing are identical and a
run on each provider is a like-for-like comparison. It's tested offline
against a mocked Foundry endpoint; it hasn't been run against a real
deployment yet. Pricing is looked up by the deployment name, so add it to
`config/model_pricing.yaml` if it differs.

`evals run` accepts `--fake` (clean label oracle, no API key), `--demo-noise`
(with `--fake`: inject the demo regression), `--provider anthropic|azure`, `--no-save` (don't store the
run), `--out DIR`, and `--report-url URL` (linked from the Slack alert).
Turning a real failed receipt into a new golden case:

```bash
uv run sentinel evals promote RECEIPT_TXT --id CASE_ID
```

**Tracing (OpenTelemetry):** every run emits one `eval_run` span, one
`eval_case` span per golden case, and one `chat <model>` span per LLM call,
using the OpenTelemetry GenAI conventions (model, input/output tokens, stop
reason, plus each case's pass/fail, difficulty, and cost). Prompt text,
receipt text, and model output are never put on spans, since receipts carry PII.
Off by default; turn it on with environment variables:

```bash
uv sync --extra telemetry
SENTINEL_TRACE_CONSOLE=1 uv run sentinel evals run --fake --no-save   # print spans
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318 uv run sentinel evals run  # Jaeger, Grafana, Azure Monitor via a collector
```

**MCP server (C#):** `mcp_run_history_server/` is a read-only
[Model Context Protocol](https://modelcontextprotocol.io) server, built on
Microsoft's official C# SDK, so an agent (Claude Code, Claude Desktop, VS
Code) can query the run history in plain language. Two tools:
`list_runs` (recent runs with pass rate, model, prompt, cost) and
`what_regressed` (cases that newly fail against the blessed baseline, with
the fields that broke, plus cases that newly pass). Run ids are validated
before they touch a file path, and no receipt text is returned.

```bash
dotnet build mcp_run_history_server
claude mcp add sentinel-runs \
  -e SENTINEL_RUNS_DIR="$PWD/runs/local/runs" \
  -e SENTINEL_BASELINE="$PWD/src/receipt_sentinel/evals/baseline.json" \
  -- dotnet "$PWD/mcp_run_history_server/bin/Debug/net10.0/RunHistoryMcpServer.dll"
```

`tests/test_mcp_run_history_server.py` drives the built server over stdio
JSON-RPC end to end (skipped if it isn't built; CI builds it).

**CI:** `.github/workflows/tests.yml` runs ruff, mypy, a warnings-as-errors
build of the C# MCP server, pytest, and a keyless fake smoke run on every
push/PR. `.github/workflows/evals.yml` runs the
real golden set on PRs touching `prompts/`, `categorize/`,
`active_prompt.yaml`, `thresholds.yaml`, `golden/`, or `baseline.json`;
uploads the report artifact, posts a sticky PR comment, and fails on
CRITICAL. `.github/workflows/nightly-evals.yml` runs the real golden set on
`main` every night (06:00 UTC), keeps one `eval-regression` issue in sync
(opened on CRITICAL or drift, closed on recovery), and publishes the
numbers-only history and dashboard to `gh-pages`. Required secret:
`ANTHROPIC_API_KEY`. Optional: `SLACK_WEBHOOK_URL` (no-op if unset).
Pages (one-time author step): Settings → Pages → Source: Deploy from a
branch → `gh-pages` / root.

## Trade-offs

- **Text-only fixtures.** The golden dataset feeds pre-extracted receipt
  text directly; PDF/image fixtures and the PyMuPDF/OCR extract layer
  arrive in a later plan, so the harness today gates the categorize step
  only, not extraction quality.
- **No `temperature` pinned.** The design called for `temperature=0` for
  determinism, but the installed SDK/model combination doesn't accept
  sampling parameters on this call — determinism instead comes from
  diffing against a committed baseline with tolerant per-field thresholds,
  not from byte-identical model output.
- **Single labeler.** All 56 golden cases were labeled by one person; no
  second-rater agreement check exists yet.
- **Public history is numbers only.** Receipt text and model answers never
  leave the author's machine once real receipts are in the golden set
  (`--public`); the public page shows pass rates, deltas, counts, and cost.
- **Foundry path is mock-verified only.** Claude runs on the Anthropic API or
  Microsoft Foundry (`--provider`), and `sentinel evals compare-models` diffs
  any two model/provider pairs side by side, but the Foundry path has only
  been exercised against a mocked endpoint.

## Roadmap

Per the design spec's build order (steps 1–5 are this harness; 6–9 are the
feature it will eventually gate more of):

6. Extract layer (PyMuPDF native text + Tesseract OCR fallback) and the
   deterministic validate layer, plus PDF/image golden fixtures.
7. Ingest: `.eml` fixture source, then the Gmail API source with a sender
   allowlist.
8. Checklist propose/confirm (SQLite, user-confirmed persistence only).
9. Dockerfile and a 3-minute walkthrough.
