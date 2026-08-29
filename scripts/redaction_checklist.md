# Redaction checklist (run per real receipt before commit)

1. Copy receipt text (from the PDF viewer, or later the extract layer) into a new
   `real_NNNN_<category>_<difficulty>.yaml` with `source: real_redacted`.
2. Remove/replace with `[REDACTED]`: your name, any card digits (incl. last 4), street
   address, phone, email, loyalty/member IDs, order/transaction numbers, barcodes/URLs.
3. Keep: vendor, date, line items, amounts, tax, tip, total, currency.
4. Label `expected` by hand. `confidence: 1.0` unless the printed text is genuinely ambiguous.
5. `grep -nE '[0-9]{4}[- ]?[0-9]{4}|@|\b[0-9]{3}[-. ][0-9]{3}[-. ][0-9]{4}\b' <file>` must return nothing.
6. `uv run pytest tests/evals/test_golden_case.py -q` passes.
7. Reviewed by the author personally. Commit.
