.PHONY: install test lint demo compare-demo serve ingest-demo

install:
	uv sync --extra dev

test:
	uv run pytest -q

lint:
	uv run ruff check . && uv run mypy

# Exit 1 (CRITICAL) is the demo working; exit 2 (e.g. Tesseract missing) is a real failure
demo:
	uv run sentinel evals run --fake --demo-noise --out runs/demo; rc=$$?; \
	if [ $$rc -eq 1 ]; then echo "demo: CRITICAL regression rendered as intended -> runs/demo/report.html"; else exit $$rc; fi

compare-demo:
	uv run sentinel evals compare-models --fake --demo-noise --out runs/compare-demo; rc=$$?; \
	if [ $$rc -eq 1 ]; then echo "compare-demo: worse candidate blocked (CRITICAL) as intended -> runs/compare-demo/report.html"; else exit $$rc; fi

serve:
	uv run --extra api uvicorn receipt_sentinel.categorize.categorize_api:build_app --factory --host 127.0.0.1 --port 8000

ingest-demo:
	uv run sentinel ingest --eml examples/demo_inbox --since 2026-08-01 --fake --allowlist examples/demo_inbox/allowlist.yaml
