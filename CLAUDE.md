# Receipt Sentinel — project notes

Portfolio project: LLM receipt extraction feature + regression-detection CI harness.

- Design spec (approved, Codex-reviewed): `docs/superpowers/specs/2026-08-28-receipt-sentinel-design.md`
- Eval harness (spec §7 steps 1–5) is built and merged: plan at `docs/superpowers/plans/2026-08-28-receipt-sentinel-eval-harness.md`. `make demo` runs keyless.
- Author to-do before the next plan: first real run (`uv run sentinel evals run --out runs/local` with `ANTHROPIC_API_KEY`), `sentinel evals promote-baseline <run_id> --out runs/local`, commit `baseline.json`; add redacted receipts per `scripts/redaction_checklist.md`; capture `docs/sample_report.png`; add repo secrets.
- Next plan: spec §7 steps 6–9 (extract layer, validate, ingest + allowlist, checklist, Docker) via `superpowers:writing-plans`.
- `_raw_receipts/` holds the author's unredacted receipts — gitignored. Redacted copies + labels go in `src/receipt_sentinel/evals/golden/`. Author reviews every redaction before commit.
- Public repo: no BRANY content, no real PII, no secrets.
