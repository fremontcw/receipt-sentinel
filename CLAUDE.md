# Receipt Sentinel — project notes

Portfolio project: LLM receipt extraction feature + regression-detection CI harness.
Public repo: https://github.com/fremontcw/project_01_model_regression_detection

- Design spec (approved, Codex-reviewed): `docs/superpowers/specs/2026-08-28-receipt-sentinel-design.md`
- Eval harness (spec §7 steps 1–5) plan: `docs/superpowers/plans/2026-08-28-receipt-sentinel-eval-harness.md`. `make demo` runs keyless.
- `_raw_receipts/` holds the author's unredacted receipts — gitignored. Redacted copies + labels go in `src/receipt_sentinel/evals/golden/`. Author reviews every redaction before commit.
- Public repo: no BRANY content, no real PII, no secrets. No personal or career notes in this file either.

## Handoff (2026-10-17)

Goal of the current work: make this repo show a full "AI-native automation engineer" skill set
(evals, GitHub automation, telemetry, MCP, C#/.NET, Azure AI, human-in-the-loop, dashboards).

**Done (CI green at push; 83 tests locally):**

- Eval harness (Aug): golden set, scoring, baseline diff, HTML report, Slack, GitHub Actions gate.
- OpenTelemetry (`src/receipt_sentinel/telemetry.py`): `eval_run` → `eval_case` → `chat <model>` spans,
  GenAI conventions; never receipt/prompt/output text on spans. Off unless `OTEL_EXPORTER_OTLP_ENDPOINT`
  or `SENTINEL_TRACE_CONSOLE=1`. SDK + exporter in the `telemetry` extra.
- C# MCP server (`mcp_run_history_server/`, official ModelContextProtocol 2.2.0 SDK, stdio): `list_runs`,
  `what_regressed`. Run ids regex-checked before path use. E2E test `tests/test_mcp_run_history_server.py`
  (skips unless built; CI builds with `-warnaserror`).
- `sentinel evals compare-models` (model migration gate, 2026-10-17): runs incumbent and
  candidate on the golden set, diffs candidate vs incumbent with the normal thresholds. Strict gate:
  cost/p50 are shown, never gate. Nothing is stored. `make compare-demo` runs keyless. A real model
  needs a `config/model_pricing.yaml` entry (look prices up via the `claude-api` skill).
- Azure provider (`categorize/azure_foundry_llm_client.py`): `--provider azure` = same Claude model via
  Microsoft Foundry (`AsyncAnthropicFoundry` handed to `AnthropicLlmClient`). Offline-tested with a mocked
  endpoint only; never run against a real deployment.

**Next, in suggested order:**

1. ~~`compare-models`~~ done (see above).
2. ~~Auto-issue on CRITICAL~~ done: `.github/workflows/nightly-evals.yml` runs evals on `main` nightly
   (skips without `ANTHROPIC_API_KEY`) and keeps one `eval-regression` issue in sync (open → comment →
   close on PASS). Manual `mode: fake-regression|fake-clean` tests it keyless under a separate
   `eval-regression-demo` label. Drift is not checked nightly yet: CI has no run history (solve with #3).
3. Publish run history as a GitHub Pages dashboard (reuse `report.py`).
4. Small REST endpoint for the categorize feature.
5. Known issue: runs are saved before the baseline diff, so a stored run's `status` is the PASS placeholder
   even when the diff was CRITICAL (`command_evals_run` saves `run`, not the diff verdict). The MCP server
   omits status for this reason. Fix by storing the diff status.
6. When the author has Azure: deploy Claude in Foundry, set `ANTHROPIC_FOUNDRY_API_KEY`/`_RESOURCE`/`_MODEL`,
   `uv run sentinel evals run --provider azure --out runs/azure`; then switch to Entra ID auth (TODO in the
   module header).
7. Still-open author actions from August (first real run + baseline done 2026-10-17: claude-sonnet-5,
   98.2%, $0.19/run; key is a Default-workspace key, in GitHub secrets + macOS keychain `anthropic-api-key`):
   ~20 redacted real receipts per `scripts/redaction_checklist.md`; `docs/sample_report.png`; optional
   `SLACK_WEBHOOK_URL` secret.
8. Spec §7 steps 6–9 (extract layer, validate, ingest + allowlist, checklist, Docker) via `superpowers:writing-plans`.

**Environment:**

- `uv sync --all-extras`; checks: `uv run ruff check . && uv run mypy && uv run pytest -q`.
- .NET 10 SDK is installed user-locally in `~/.dotnet` (on PATH via `~/.zshrc`; a non-interactive shell may
  need `export PATH=$HOME/.dotnet:$PATH`). Build: `dotnet build mcp_run_history_server`.
- Load the `claude-api` skill before touching `anthropic_llm_client.py`; don't take model params from memory.
