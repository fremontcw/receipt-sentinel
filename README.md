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
├── ingest/             .eml directory or Gmail (read-only) → sender allowlist + DMARC → attachments
├── extract/            PDF/photo → text (PyMuPDF, Tesseract OCR, deskew; killable worker, caps)
├── validate/           Deterministic rules: items sum to total, date window, ISO 4217 currency
├── checklist/          Monthly proposal + keyboard review page; confirm → SQLite (dedup, one transaction)
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

Feature path: `email → allowlist (headers only) → attachment → extract → categorize
→ validate → monthly proposal → you review and confirm → SQLite`. Nothing reaches the
database without that confirm. The golden set feeds both text and file fixtures, so the harness
gates extraction as well as the LLM call.

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

**Dataset** — 85 golden cases at dataset version 3: 48 seeded synthetic
(`scripts/generate_synthetic_golden_cases.py`, deterministic), 8
hand-written adversarial cases, 11 file fixtures (text PDFs, scanned
PDFs, tilted phone photos; `scripts/render_golden_fixtures.py`) that go
through the extract layer, so the gate covers extraction too, and 18 of the
author's own email receipts, redacted per `scripts/redaction_checklist.md` and
approved one by one on a keyboard review page
(`scripts/build_redaction_review.py`). Real-model baseline
(`claude-sonnet-5`, 2026-10-24, currency and every line item scored): 94.1%,
every adversarial case passing, $0.29 per run. Two of the five misses are
real receipts: the model writes "DashMart (DoorDash)" where the label says
"DashMart", and files Roblox under subscriptions — the kind of ambiguity
synthetic data never shows. Full sourcing, labeling, and known limitations are
in [`src/receipt_sentinel/evals/golden/DATASET_CARD.md`](src/receipt_sentinel/evals/golden/DATASET_CARD.md).

**Scoring dimensions per case:**

| Dimension | Rule |
|---|---|
| Category | exact match |
| Total | within ±0.01 |
| Date | exact match |
| Vendor | fuzzy match (normalised) ≥ 0.85 |
| Currency | exact match |
| Line items | every labelled item found, matched by amount |
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
never stored as a scored run, so it can't corrupt the drift window, and it
exits 3 so a PR check fails rather than passing with no verdict. Cost
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

`--demo-noise` adds deterministic damage on top of that oracle: 10% of cases
are mis-categorised to `other`. Diffed against the clean baseline, that is a
real regression, so `make demo` prints **CRITICAL** at −10.6 pts with 9
regressed cases and exits 1 — which the `make` target catches so the demo
itself still succeeds. The resulting report is committed at
[`docs/sample_report.html`](docs/sample_report.html) — open it directly, or
see below.

![Sample regression report: CRITICAL, pass rate 100% → 89%](docs/sample_report.png)

![Regressed cases: expected, baseline and current output side by side](docs/sample_report_regressions.png)

The report lists each regressed case side by side: expected value, previous
(baseline) output, current output, and which scoring dimension flipped —
e.g. a case that passed at baseline now fails on `category` because the
noise-injected oracle mis-routes a grocery receipt to `other`. That's the
shape of report a real prompt or model regression produces.

## Claude vs GPT-4.1, measured

`sentinel evals compare-models` runs two models on the same 85 golden cases with
the same prompt and diffs them with the normal thresholds. Real run, 2026-10-22,
Claude on the Anthropic API vs GPT-4.1 on Azure OpenAI (Entra ID, no key):

| | claude-sonnet-5 | gpt-4.1 |
|---|---|---|
| pass rate | 92.9% | **96.5%** (+3.5 pts) |
| regressions vs Claude | — | 0 (3 improvements) |
| adversarial failures | 0 | 0 |
| cost per run | $0.287 | $0.186 (list price) |
| p50 latency | 1480 ms | 1078 ms |

Verdict: PASS, GPT-4.1 is a safe swap on this set. Two things only a real run
surfaced:

- **Azure's jailbreak filter blocked every request.** The prompt's few-shot
  example of an injection attack (`SYSTEM: ignore all prior rules…`) trips
  Prompt Shields, so all 85 calls came back HTTP 400. The deployment uses a
  copy of Azure's default policy with Jailbreak set to *annotate, don't block*;
  every other filter is unchanged ([`infra/azure_openai.bicep`](infra/azure_openai.bicep)). Acceptable here because the model has no tools,
  its reply must parse as a `ReceiptRecord` or the case fails, and the
  injection cases in the golden set still score whether it resisted. A
  receipt a filter does block now fails that one case instead of the run.
- **Rate limits need the server's `retry-after`.** At 50K TPM, 10 of 85 calls
  hit 429 and the 7-second backoff gave up; the client now waits as long as
  Azure asks (capped at 60 s).

The prompt was written and tuned against Claude, so this is not a general
ranking, just the evidence a migration decision on this workload needs.

## The thing under test

`categorize_receipt(doc, prompt) -> ReceiptRecord` is deliberately small:

- Receipt text is passed **only in the user turn, as delimited data** — the
  system prompt (the trusted instruction template, versioned in
  `prompts/`) states explicitly that this text is untrusted evidence, not
  instructions, and that any instruction-like content inside it must be
  ignored and reported via `injection_detected: bool`.
- **No tools** — the model can't take any action beyond returning JSON.
- The prompt asks for a strict JSON shape, and the reply is parsed into a
  Pydantic `ReceiptRecord`; anything that doesn't parse is a scored failure,
  not a silent pass-through. Parsing checks shape, not truth: a well-formed
  but wrong answer is caught by scoring against the label, not here.
- **Provenance is stamped by code, not the model** — `model_id` and
  `prompt_version` are set from the calling context after the response
  comes back, so a receipt that tries to set `model_id`
  (`adversarial_0006_set_model_id.yaml`) can't. `confidence` is the model's
  own estimate and is not trusted for anything; a receipt that tries to
  inflate it (`adversarial_0005_set_confidence.yaml`) fails only if the
  injection also changes a scored field.
- 8 adversarial golden cases exercise instruction override, schema
  tampering, secret exfiltration, HTML/script injection in vendor or line
  items, role-play jailbreaks, and JSON smuggled inside a line-item field.

## Run it

**With only Podman or Docker** (no Python, no Tesseract, no API key):

```bash
make container-build          # add CONTAINER=docker to each command to use Docker
make container-demo           # eval demo -> runs/demo/report.html
make container-serve          # API at http://127.0.0.1:8000/docs
make container-checklist-demo # monthly checklist proposal + review page
```

[`docs/walkthrough.md`](docs/walkthrough.md) is a 3-minute guided tour of all three. The
targets run the image with a read-only root and no capabilities, as your own user, and
publish the API on `127.0.0.1` only. Keep it that way: the API has no auth and uses your
key when `ANTHROPIC_API_KEY` is set. Gmail ingest is native-only, since its sign-in
opens a browser. Tested with rootless Podman and rootful Docker; with rootless Docker or
`sudo podman`, files written to `runs/` may not belong to you.

**Demo (no API key):**

Needs Tesseract for the scanned and photo fixtures: `brew install tesseract` (macOS) or
`apt-get install tesseract-ocr` (Linux).

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

`POST /categorize/file` takes the receipt as a file instead: a PDF, PNG or JPEG photo
(10 MB / 20 pages max). The response says how the text was read, from the PDF's text
layer (`native`) or by OCR (`ocr`, with Tesseract's confidence). Keyless, upload one of
the golden fixtures in `src/receipt_sentinel/evals/golden/fixtures/`; the phone photo of
the prompt-injection receipt is the good one. OCR needs Tesseract:
`brew install tesseract` (macOS) or `apt-get install tesseract-ocr` (Linux).

**Ingest receipt emails (allowlisted senders only):**

```bash
make ingest-demo     # keyless: 4 demo emails in examples/demo_inbox/
uv run sentinel ingest --eml path/to/emails --since 2026-08-01   # real model
uv run sentinel allowlist add receipts@shop.com   # or: --domain shop.com
```

The demo reads two receipts from an allowlisted sender: a PDF, and a phone photo
whose prompt-injection text comes back flagged. It also lists two senders that were
never opened: an unknown shop, and an attacker whose display name impersonates the
allowlisted address. A sender is trusted only if its exact address or exact domain is
in `config/allowlist.yaml` *and* the receiving server (Gmail) recorded a DMARC pass for
that domain. That check is on the server's own header, because a `From` line is just text
anyone can type. Everything else is reported as metadata (address, count, subjects),
and its attachments are never opened. Ingest prints a report and stores nothing.

**Ingest from your Gmail (read-only, optional):**

1. In Google Cloud, enable the Gmail API, create an OAuth client of type *Desktop app*,
   and save its JSON as `credentials.json` at the repo root. It's gitignored.
2. `uv sync --extra gmail`
3. `uv run sentinel ingest --gmail --since 2026-08-01 --max-messages 5`

The first run opens a browser to sign in. The scope is `gmail.readonly` only, and the
token is saved to `token.json` (gitignored, owner-only). If it expires, add `--reauth`.
Gmail never runs in CI; tests use a fake Gmail service.

**Monthly checklist (propose → you review → confirm):**

```bash
make checklist-demo   # keyless: proposes August 2026 from the demo inbox
uv run sentinel checklist propose --month 2026-08 --gmail          # or --eml DIR
open runs/checklists/<proposal_id>/review.html                      # Enter accept · X reject · S skip · ⌫ back
uv run sentinel checklist confirm <proposal_id> --decisions ~/Downloads/decisions-<proposal_id>.json
```

`propose` ingests the month and writes a proposal: receipts with flags (needs review, low
confidence < 0.7, duplicate, injection attempt), totals per category *and currency*, and
the unknown senders, plus a keyboard review page, as files under `runs/`; the database is not touched. The review page
downloads your decisions, and `confirm` is the only thing that writes to the local SQLite
store (`data/receipt_sentinel.db`, gitignored), in one transaction. A second confirm of
the same proposal, an id that isn't in the proposal, or an accepted duplicate is refused
with nothing written. `--accept r01,r03` works without the page. For the demo, add
`--db runs/checklist-demo/store.db --out runs/checklist-demo` to `confirm`.

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

### Hosted demo (Azure)

The same image runs as a public demo page on Azure Container Apps
(`SENTINEL_HOSTED=1`; try it locally with `make container-serve-hosted`, then
http://127.0.0.1:8000). Anyone gets the free mode: sample, random or the author's
redacted real receipts (stamped "REDACTED COPY"), answered from the answer key, no model call. A personal link
(`sentinel demo-link issue "<label>"`) unlocks Claude for 25 calls over 14 days
and emails the author on its first use. The hosted app takes no uploads or free
text, only a sample name or a seed, so no stranger's receipt reaches it, and
every generated receipt is stamped `SAMPLE - NOT A REAL RECEIPT`.

Spend is capped in layers: the app's own monthly cap ($8, which binds first),
an Anthropic workspace limit ($20, the hard backstop), free-mode limits (30
per visitor per 10 minutes, 300 a day), one replica that scales to zero, and a
$15 Azure budget alert. Infrastructure
is `infra/main.bicep` (managed identity for Table Storage, Key Vault and email;
no keys or connection strings); `.github/workflows/deploy-hosted.yml` pushes the
image to GHCR on every green `main` and, once Azure is set up, deploys it with
OIDC. One-time setup: `infra/hosted_demo_setup.md`.

## Trade-offs

- **Rendered, not photographed, fixtures.** 11 golden cases are PDFs,
  scanned PDFs and tilted, blurred phone photos, so CI gates extraction as
  well as categorization. They are rendered from text, so glare, creases and
  faded thermal paper are not covered until real redacted receipts land.
- **OCR output varies by Tesseract version.** Each run feeds the model what
  that machine's Tesseract reads, so a real baseline is tied to the CI
  runner's Tesseract; a version bump there can move OCR-case scores.
- **PyMuPDF is AGPL-3.0.** Fine for a public repo, including when hosted
  (the source is already public); a closed-source fork would need to swap it
  for `pypdf` + `pypdfium2`.
- **No `temperature` pinned.** The design called for `temperature=0` for
  determinism, but the installed SDK/model combination doesn't accept
  sampling parameters on this call. Output is not deterministic; the gate
  absorbs run-to-run variance with tolerant per-field thresholds against a
  committed baseline instead.
- **Single labeler.** All 85 golden cases were labeled by one person; no
  second-rater agreement check exists yet.
- **Public history is numbers only.** With `--public`, nightly CI publishes
  no receipt text or model answers, only pass rates, deltas, counts, and
  cost. (Receipt text does go to the model provider on every real run, and
  the redacted receipts themselves are in this repo.)
- **Claude-on-Foundry is mock-verified only.** Claude runs on the Anthropic
  API or Microsoft Foundry (`--provider azure`), but Foundry Claude quota for
  this subscription was denied, so that path has only run against a mocked
  endpoint. GPT-4.1 on Azure OpenAI (`--provider azure-openai`) has run for
  real (see above).

## Roadmap

Per the design spec's build order (steps 1–5 are this harness; 6–9 are the
feature it will eventually gate more of):

6. ~~Extract layer, validate layer, PDF/image golden fixtures~~ done.
7. ~~Ingest: `.eml` source, Gmail (read-only) source, sender allowlist~~ done.
8. ~~Checklist propose/confirm (SQLite, user-confirmed persistence only)~~ done.
9. ~~Dockerfile and a 3-minute walkthrough~~ done.

Beyond the spec: ~~a hosted Azure demo with personal links~~ built (design:
`docs/superpowers/specs/2026-10-20-hosted-azure-demo-design.md`); going live
waits on the one-time setup in `infra/hosted_demo_setup.md`.
