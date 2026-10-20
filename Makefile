.PHONY: install test lint demo compare-demo serve ingest-demo checklist-demo \
	container-build container-demo container-serve container-checklist-demo container-checklist-confirm

CONTAINER ?= podman
IMAGE = receipt-sentinel:local
# Rootless Podman maps the host user in with keep-id; Docker needs the uid spelled out.
# Either way, files written to the runs/ mount belong to you, not root.
CONTAINER_USER = $(if $(filter podman,$(CONTAINER)),--userns=keep-id,--user $$(id -u):$$(id -g))
CONTAINER_RUN = $(CONTAINER) run --rm --read-only --tmpfs /tmp --cap-drop=all \
	--security-opt=no-new-privileges $(CONTAINER_USER) -v "$(CURDIR)/runs:/app/runs:Z"

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

# Keyless: proposes August from the demo inbox into runs/ (a throwaway store, not data/)
checklist-demo:
	uv run sentinel checklist propose --month 2026-08 --eml examples/demo_inbox --fake --allowlist examples/demo_inbox/allowlist.yaml --db runs/checklist-demo/store.db --out runs/checklist-demo

container-build:
	$(CONTAINER) build -t $(IMAGE) .

container-demo:
	mkdir -p runs
	$(CONTAINER_RUN) $(IMAGE) sentinel evals run --fake --demo-noise --out runs/demo; rc=$$?; \
	if [ $$rc -eq 1 ]; then echo "demo: CRITICAL regression rendered as intended -> runs/demo/report.html"; else exit $$rc; fi

# 127.0.0.1 only: the API has no auth and uses your key when one is set (passed by name)
container-serve:
	$(CONTAINER_RUN) -it -p 127.0.0.1:8000:8000 -e ANTHROPIC_API_KEY $(IMAGE)

container-checklist-demo:
	mkdir -p runs
	$(CONTAINER_RUN) $(IMAGE) sentinel checklist propose --month 2026-08 --eml examples/demo_inbox --fake --allowlist examples/demo_inbox/allowlist.yaml --db runs/checklist-demo/store.db --out runs/checklist-demo

# make container-checklist-confirm PROPOSAL=<id the demo printed> ACCEPT=r02
container-checklist-confirm:
	$(CONTAINER_RUN) $(IMAGE) sentinel checklist confirm $(PROPOSAL) --accept $(ACCEPT) --db runs/checklist-demo/store.db --out runs/checklist-demo
