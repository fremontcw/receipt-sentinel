.PHONY: install test lint demo compare-demo

install:
	uv sync --extra dev

test:
	uv run pytest -q

lint:
	uv run ruff check . && uv run mypy

demo:
	uv run sentinel evals run --fake --demo-noise --out runs/demo || echo "demo: CRITICAL regression rendered as intended -> runs/demo/report.html"

compare-demo:
	uv run sentinel evals compare-models --fake --demo-noise --out runs/compare-demo || echo "compare-demo: worse candidate blocked (CRITICAL) as intended -> runs/compare-demo/report.html"
