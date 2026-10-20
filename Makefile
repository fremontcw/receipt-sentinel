.PHONY: install test lint demo compare-demo serve ingest-demo checklist-demo \
	container-build container-demo container-serve container-checklist-demo container-checklist-confirm

CONTAINER ?= podman
IMAGE = receipt-sentinel:local
PORT ?= 8000
# The smoke test passes "-d --name …" to run the API exactly as users do, detached
SERVE_FLAGS ?= -it
# Run as your own uid so files written to the runs/ mount are yours. Rootless Podman also
# needs keep-id to map that uid to you on the host (--user alone would land on a subuid).
CONTAINER_USER = $(if $(filter podman,$(CONTAINER)),--userns=keep-id --user $$(id -u):$$(id -g),--user $$(id -u):$$(id -g))
CONTAINER_BASE = $(CONTAINER) run --rm --read-only --tmpfs /tmp --cap-drop=all \
	--security-opt=no-new-privileges $(CONTAINER_USER)
CONTAINER_RUN = $(CONTAINER_BASE) -v "$(CURDIR)/runs:/app/runs:Z"

install:
	uv sync --extra dev

test:
	uv run pytest -q

lint:
	uv run ruff check . && uv run mypy

# Exit 1 (CRITICAL) is the demo working; exit 2 (e.g. Tesseract missing) is a real failure
demo:
	rm -rf runs/demo
	uv run sentinel evals run --fake --demo-noise --out runs/demo; rc=$$?; \
	if [ $$rc -eq 1 ] && [ -f runs/demo/report.html ]; then echo "demo: CRITICAL regression rendered as intended -> runs/demo/report.html"; else exit $$rc; fi

compare-demo:
	rm -rf runs/demo
	uv run sentinel evals compare-models --fake --demo-noise --out runs/compare-demo; rc=$$?; \
	if [ $$rc -eq 1 ]; then echo "compare-demo: worse candidate blocked (CRITICAL) as intended -> runs/compare-demo/report.html"; else exit $$rc; fi

serve:
	uv run --extra api uvicorn receipt_sentinel.categorize.categorize_api:build_app --factory --host 127.0.0.1 --port 8000

ingest-demo:
	rm -rf runs/demo
	uv run sentinel ingest --eml examples/demo_inbox --since 2026-08-01 --fake --allowlist examples/demo_inbox/allowlist.yaml

# Keyless: proposes August from the demo inbox into runs/ (a throwaway store, not data/)
checklist-demo:
	rm -rf runs/demo
	uv run sentinel checklist propose --month 2026-08 --eml examples/demo_inbox --fake --allowlist examples/demo_inbox/allowlist.yaml --db runs/checklist-demo/store.db --out runs/checklist-demo

container-build:
	$(CONTAINER) build --build-arg GIT_SHA=$$(git rev-parse --short HEAD 2>/dev/null || echo unknown) -t $(IMAGE) .

container-demo:
	rm -rf runs/demo && mkdir -p runs
	$(CONTAINER_RUN) $(IMAGE) sentinel evals run --fake --demo-noise --out runs/demo; rc=$$?; \
	if [ $$rc -eq 1 ] && [ -f runs/demo/report.html ]; then echo "demo: CRITICAL regression rendered as intended -> runs/demo/report.html"; else exit $$rc; fi

# 127.0.0.1 only: the API has no auth and uses your key when one is set (passed by name)
container-serve:
	$(CONTAINER_BASE) $(SERVE_FLAGS) -p 127.0.0.1:$(PORT):8000 -e ANTHROPIC_API_KEY $(IMAGE)

# Starts from an empty store, so the proposal id matches docs/walkthrough.md on every take
container-checklist-demo:
	rm -rf runs/checklist-demo && mkdir -p runs
	$(CONTAINER_RUN) $(IMAGE) sentinel checklist propose --month 2026-08 --eml examples/demo_inbox --fake --allowlist examples/demo_inbox/allowlist.yaml --db runs/checklist-demo/store.db --out runs/checklist-demo

# make container-checklist-confirm PROPOSAL=<id the demo printed> ACCEPT=r02
container-checklist-confirm:
	@test -n "$(PROPOSAL)" -a -n "$(ACCEPT)" || { echo "usage: make container-checklist-confirm PROPOSAL=<id the demo printed> ACCEPT=r02" >&2; exit 2; }
	mkdir -p runs
	$(CONTAINER_RUN) $(IMAGE) sentinel checklist confirm $(PROPOSAL) --accept $(ACCEPT) --db runs/checklist-demo/store.db --out runs/checklist-demo
