.PHONY: install test lint demo

install:
	uv sync --extra dev

test:
	uv run pytest -q

lint:
	uv run ruff check . && uv run mypy

demo:
	uv run sentinel evals run --fake --out runs/demo
