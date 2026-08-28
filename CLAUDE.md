# Receipt Sentinel — project notes

Portfolio project: LLM receipt extraction feature + regression-detection CI harness.

- Design spec (approved, Codex-reviewed): `docs/superpowers/specs/2026-08-28-receipt-sentinel-design.md`
- Next step: invoke `superpowers:writing-plans` on the spec to produce the implementation plan, then build in the spec's §7 order (evals first).
- `_raw_receipts/` holds the author's unredacted receipts — gitignored. Redacted copies + labels go in `src/receipt_sentinel/evals/golden/`. Author reviews every redaction before commit.
- Public repo: no BRANY content, no real PII, no secrets.
