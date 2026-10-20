# Container Image and 3-Minute Walkthrough Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A hiring manager with only Podman or Docker installed can run the eval demo, the
categorizer API and the monthly checklist demo, guided by a 3-minute walkthrough.

**Architecture:** One `Dockerfile`, in two stages. A uv builder runs `uv sync --frozen` into
`/app/.venv` as an editable install. That keeps `REPO_ROOT`
(`Path(__file__).parents[3]`) pointing at `/app`, where `config/` and the golden set
live. The runtime stage adds Tesseract and runs as a non-root user; its default command is
the API. Make targets wrap `podman`/`docker run` with the hardening flags and bind-mount
`runs/`, so reports land on the host. `scripts/container_smoke_test.sh` proves the image works
end to end, and CI runs it on every push. `docs/walkthrough.md` is a timed script that is
both a reader's tour and the narration for an optional screen recording.

**Tech Stack:** Podman (local default) / Docker (CI), `python:3.12-slim-bookworm`,
`ghcr.io/astral-sh/uv`, Debian `tesseract-ocr`, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-08-28-receipt-sentinel-design.md` (§4.8 "Dockerfile
added after CI is green"; §7 step 9 "Docker, 3-minute walkthrough").

## Global Constraints

- File is named `Dockerfile` (spec wording); Podman builds it unchanged.
- Base images pinned by digest: `python:3.12-slim-bookworm@sha256:<digest>` for both stages
  (same digest, since the venv's python symlink must resolve in the runtime stage) and
  `ghcr.io/astral-sh/uv:0.9.26@sha256:<digest>` (the version the lockfile was made with).
  Look up the digests with `podman pull` + `podman image inspect --format '{{.Digest}}'`.
- Non-root `USER 10001`; the image also works under an arbitrary `--user`/`--userns=keep-id`
  uid: everything world-readable, `HOME=/tmp`, run `/app/.venv/bin/*` directly, never `uv run`.
- No secrets in layers: no `ENV`/`ARG` carrying keys; keys arrive only as bare `-e NAME`
  pass-through at run time.
- Every documented `run` is `--read-only --tmpfs /tmp --cap-drop=all
  --security-opt=no-new-privileges`, and publishes only on `127.0.0.1`.
- Extras in the image: `api` only. Gmail is out: its OAuth flow opens a browser on the host
  and does not work headless. In the container the ingest and checklist demos use `--eml`.
- `ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH=/app/.venv/bin:$PATH`.
- Checks: `uv run ruff check . && uv run mypy && uv run pytest -q`.

## Review Focus

1. Someone runs `docker run -p 8000:8000 …` with their key set, and the keyed, unauthenticated
   API is open to their LAN. Expected: every documented command and make target publishes on
   `127.0.0.1:`, and the README warns. Pinned by `test_container_targets_publish_on_loopback_only`.
2. Host secrets or raw receipts get copied into an image someone pushes (`_raw_receipts/`,
   `credentials.json`, `token.json`, `.env`, `data/`, `runs/`). Expected: `.dockerignore`
   denies everything and allowlists back only what the image needs. Pinned by
   `test_dockerignore_denies_by_default` and `test_dockerignore_never_allowlists_secrets`.
3. `make container-demo` on a Linux host with rootless Podman: the report is never written
   (permission denied on the bind mount) or is owned by a subuid the user can't delete.
   Expected: the files appear in `runs/demo/` owned by the user. Pinned by smoke check 3,
   which runs the demo through the same flags and asserts the host file exists.
4. OCR or PyMuPDF writes temp files on a read-only root filesystem. Expected: the photo
   fixture still extracts. Pinned by smoke check 5 (an OCR upload under `--read-only`).
5. The key leaks through the make target (for example `-e ANTHROPIC_API_KEY=$(…)` shows it in
   `ps` and shell history). Expected: bare `-e ANTHROPIC_API_KEY` pass-through only. Pinned by
   `test_container_targets_pass_keys_by_name_only`.

---

### Task 1: Container image, make targets, smoke test in CI

**Files:**
- Create: `Dockerfile`
- Create: `.dockerignore`
- Create: `scripts/container_smoke_test.sh` (executable)
- Create: `tests/test_container_setup.py`
- Modify: `Makefile` (new targets `container-build`, `container-demo`, `container-serve`,
  `container-checklist-demo`; add them to `.PHONY`)
- Modify: `.github/workflows/tests.yml` (new job `container`)
- Modify: `src/receipt_sentinel/categorize/categorize_api.py` header only: "Binds to
  127.0.0.1 only" becomes "Binds to 127.0.0.1 (`make serve`); in the container it listens on
  0.0.0.0 inside the container's own network and is published on the host's 127.0.0.1 only".

**Interfaces:**
- Consumes: `build_app` factory (`receipt_sentinel.categorize.categorize_api:build_app`), the
  `sentinel` console script, `examples/demo_inbox/`, and
  `src/receipt_sentinel/evals/golden/fixtures/fixture_photo_adversarial_0001_override.jpg`.
- Produces: image tag `receipt-sentinel:local`. Make variable `CONTAINER ?= podman`.
  Targets: `container-build`, `container-demo`, `container-serve`, `container-checklist-demo`
  (Task 2's walkthrough uses exactly these names). `scripts/container_smoke_test.sh` reads
  `CONTAINER` from the environment (default `podman`).

- [ ] **Step 1: Write the failing tests** in `tests/test_container_setup.py`. They read the
  files as text, with no container runtime needed, so they run in the normal suite.

```python
REPO_ROOT = Path(__file__).resolve().parents[1]
SECRET_OR_PRIVATE = ("_raw_receipts", "credentials.json", "token.json", ".env", "data", "runs")

def _make_target(name: str) -> str:
    """Recipe lines of one Makefile target (the lines after `name:` up to the next blank)."""

def test_dockerignore_denies_by_default():
    lines = [l.strip() for l in (REPO_ROOT / ".dockerignore").read_text().splitlines()
             if l.strip() and not l.startswith("#")]
    assert lines[0] == "*"
    assert all(l.startswith("!") for l in lines[1:])

def test_dockerignore_never_allowlists_secrets():
    allowed = [l[1:].strip("/") for l in (REPO_ROOT / ".dockerignore").read_text().splitlines()
               if l.startswith("!")]
    assert not [a for a in allowed if a.split("/")[0] in SECRET_OR_PRIVATE]

def test_container_targets_publish_on_loopback_only():
    recipe = _make_target("container-serve")
    assert "-p 127.0.0.1:8000:8000" in recipe
    assert not re.search(r"-p\s+8000", recipe)

def test_container_targets_pass_keys_by_name_only():
    for target in ("container-serve", "container-demo", "container-checklist-demo"):
        assert not re.search(r"-e\s+\w+=", _make_target(target))
    assert re.search(r"-e ANTHROPIC_API_KEY(\s|$)", _make_target("container-serve"))

def test_dockerfile_runs_as_non_root_and_pins_bases():
    text = (REPO_ROOT / "Dockerfile").read_text()
    assert re.search(r"^USER 10001$", text, re.M)
    stages = set(re.findall(r"^FROM \S+ AS (\w+)", text, re.M))
    images = re.findall(r"^FROM (\S+)", text, re.M) + re.findall(r"COPY --from=(\S+)", text)
    external = [i for i in images if i not in stages]
    assert len(external) == 3 and all("@sha256:" in i for i in external)  # python x2, uv
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_container_setup.py -v`
Expected: 5 FAIL (FileNotFoundError on `.dockerignore`/`Dockerfile`, missing make targets).

- [ ] **Step 3: Write `.dockerignore`.** First line `*`, then `!` lines for exactly:
  `pyproject.toml`, `uv.lock`, `README.md` (hatchling reads it as the readme), `src/`,
  `config/`, `examples/`. Then write `Dockerfile`:
  - builder stage: `COPY --from=<uv image> /uv /bin/uv`; `WORKDIR /app`; copy
    `pyproject.toml uv.lock README.md` first and run
    `uv sync --frozen --extra api --no-install-project` (a cached dependency layer). Then copy
    `src config examples` and run `uv sync --frozen --extra api` (editable project install,
    never `--no-editable`, or `REPO_ROOT` moves into site-packages). Set
    `UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never`.
  - runtime stage: `apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng`,
    then `rm -rf /var/lib/apt/lists/*`; `COPY --from=builder /app /app`; the `ENV` from Global
    Constraints plus `HOME=/tmp`; `USER 10001`; `EXPOSE 8000`;
    `CMD ["uvicorn", "receipt_sentinel.categorize.categorize_api:build_app", "--factory",
    "--host", "0.0.0.0", "--port", "8000"]`.

- [ ] **Step 4: Add the make targets.** One shared variable:

```make
CONTAINER ?= podman
IMAGE = receipt-sentinel:local
# Rootless Podman maps the host user in with keep-id; Docker needs the uid spelled out.
# Either way, files written to the runs/ mount belong to you, not root.
CONTAINER_USER = $(if $(filter podman,$(CONTAINER)),--userns=keep-id,--user $$(id -u):$$(id -g))
CONTAINER_RUN = $(CONTAINER) run --rm --read-only --tmpfs /tmp --cap-drop=all \
	--security-opt=no-new-privileges $(CONTAINER_USER) -v "$(CURDIR)/runs:/app/runs:Z"
```

  - `container-build`: `$(CONTAINER) build -t $(IMAGE) .`
  - `container-demo`: `mkdir -p runs`, then `$(CONTAINER_RUN) $(IMAGE) sentinel evals run --fake
    --demo-noise --out runs/demo`, with the same exit-1 handling and message as `demo`.
  - `container-serve`: `$(CONTAINER_RUN) -it -p 127.0.0.1:8000:8000 -e ANTHROPIC_API_KEY $(IMAGE)`
  - `container-checklist-demo`: the `checklist-demo` command run through
    `$(CONTAINER_RUN) $(IMAGE) sentinel …`, with the same paths (relative to `/app`).

- [ ] **Step 5: Run the unit tests to verify they pass**

Run: `uv run pytest tests/test_container_setup.py -v`
Expected: 5 PASS.

- [ ] **Step 6: Write `scripts/container_smoke_test.sh`** (`set -euo pipefail`,
  `CONTAINER="${CONTAINER:-podman}"`, a `trap` that removes the detached container). It
  drives the make targets with `CONTAINER` exported, so it tests what a user runs. Each check
  prints `ok <n> <what>` or exits non-zero with the reason:
  1. `make container-build`.
  2. The image user isn't root: `$CONTAINER run --rm receipt-sentinel:local id -u` prints `10001`.
  3. `make container-demo` prints the "CRITICAL regression rendered as intended" line and
     `runs/demo/report.html` exists on the host and is owned by `$(id -u)`.
  4. `make container-checklist-demo` prints `2 receipt(s)` and a `review.html` exists under
     `runs/checklist-demo/`.
  5. Start the API detached (the container-serve flags, minus `-it`, plus `-d --name`, on
     host port `127.0.0.1:18000`). Poll `/docs` for up to 30 s. Then `curl -F
     file=@src/receipt_sentinel/evals/golden/fixtures/fixture_photo_adversarial_0001_override.jpg
     http://127.0.0.1:18000/categorize/file` returns JSON with `"injection_detected": true`
     and `"ocr"`. That covers Tesseract under `--read-only`, keyless.

- [ ] **Step 7: Run the smoke test locally**

Run: `scripts/container_smoke_test.sh`
Expected: `ok 1` … `ok 5`, exit 0. (On macOS, `podman machine` must be running.)

- [ ] **Step 8: Add the CI job.** In `tests.yml`, add a job `container` (`runs-on:
  ubuntu-latest`, `actions/checkout@v4`, `run: scripts/container_smoke_test.sh`,
  `env: CONTAINER: docker`). It is independent of `unit`; it needs no key and costs nothing.

- [ ] **Step 9: Run the full checks, then commit**

Run: `uv run ruff check . && uv run mypy && uv run pytest -q`
Expected: all pass (262 + 5).

```bash
git add Dockerfile .dockerignore scripts/container_smoke_test.sh tests/test_container_setup.py \
  Makefile .github/workflows/tests.yml src/receipt_sentinel/categorize/categorize_api.py
git commit -m "feat(container): hardened image, make targets, smoke test in CI"
```

### Task 2: 3-minute walkthrough and README

**Files:**
- Create: `docs/walkthrough.md`
- Create: `tests/test_walkthrough_doc.py`
- Modify: `README.md` ("Run it" gains a "With only Podman or Docker" block first; Roadmap
  step 9 is struck through, done)
- Modify: `CLAUDE.md` handoff (step 9 done; author action: optional recording)

**Interfaces:**
- Consumes: Task 1's make target names; `runs/demo/report.html`; the `checklist-demo` output
  (proposal id printed; items `r01`, Shell Gas Station, flagged injection, and `r02`).
- Produces: nothing code-facing.

- [ ] **Step 1: Write the failing test** `tests/test_walkthrough_doc.py`. It guards against
  doc rot, the way the walkthrough actually breaks:

```python
def test_walkthrough_make_targets_exist():
    targets = set(re.findall(r"^([\w-]+):", (REPO_ROOT / "Makefile").read_text(), re.M))
    used = set(re.findall(r"make ([\w-]+)", WALKTHROUGH.read_text()))
    assert used and used <= targets

def test_walkthrough_repo_paths_exist():
    # backticked paths that look like repo files (contain "/" and no "<" placeholder,
    # not under runs/, which the demos create)
    paths = [p for p in re.findall(r"`([\w./-]+/[\w./-]+)`", WALKTHROUGH.read_text())
             if not p.startswith(("runs/", "http"))]
    assert paths and all((REPO_ROOT / p).exists() for p in paths)

def test_walkthrough_fits_three_minutes():
    stamps = re.findall(r"^## (\d):(\d\d)", WALKTHROUGH.read_text(), re.M)
    assert stamps[0] == ("0", "00")
    assert int(stamps[-1][0]) * 60 + int(stamps[-1][1]) <= 170  # last section starts by 2:50
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_walkthrough_doc.py -v`
Expected: 3 FAIL (no `docs/walkthrough.md`).

- [ ] **Step 3: Write `docs/walkthrough.md`.** Each section is headed `## M:SS · <title>`. Each
  holds one command to type, what to look at (one or two lines), and a "say:" line of
  narration in plain words. Sections:
  - `0:00 · The problem`: an LLM feature silently gets worse when a prompt or model changes;
    this repo catches that in CI.
  - `0:20 · Catch a regression`: `make container-build` (do it before recording), then
    `make container-demo`. Open `runs/demo/report.html`: the CRITICAL banner, the flipped
    cases table, the trend chart. Point at `.github/workflows/evals.yml` as the gate that
    blocks the PR.
  - `1:10 · The feature under test`: `make container-serve`, `/docs`, Examples → prompt
    injection → `injection_detected: true`; upload
    `src/receipt_sentinel/evals/golden/fixtures/fixture_photo_adversarial_0001_override.jpg`
    to `/categorize/file` and note `ocr`.
  - `2:00 · A human confirms before anything is stored`: `make container-checklist-demo`,
    open the printed `review.html`, Enter / X keys, then `sentinel checklist confirm … --accept`
    run through the container (write the exact command, using the proposal id the demo
    prints at implementation time).
  - `2:50 · Where to look next`: three paths only: `src/receipt_sentinel/evals/`,
    `src/receipt_sentinel/prompts/`, `docs/superpowers/specs/2026-08-28-receipt-sentinel-design.md`.

- [ ] **Step 4: Run it to verify it passes**

Run: `uv run pytest tests/test_walkthrough_doc.py -v`
Expected: 3 PASS.

- [ ] **Step 5: Walk it for real.** Do a clean `rm -rf runs/demo runs/checklist-demo`, then
  run every command in the walkthrough in order, with a stopwatch, and open each page in a
  browser. Fix any step whose output differs from what the doc says. Expected: done in at
  most 3:00 with the image already built.

- [ ] **Step 6: Update the README and handoff.**
  - README "Run it": first block "With only Podman or Docker": `make container-build`,
    `make container-demo`, `make container-serve`; `CONTAINER=docker` to use Docker; link to
    `docs/walkthrough.md`.
  - Add one warning line: publish only on `127.0.0.1`, because the API has no auth and uses
    your key when one is set.
  - Gmail stays native-only (it needs a browser for OAuth).
  - Roadmap step 9 done.
  - In the CLAUDE.md handoff, step 9 is done, and the open author action is an optional
    3-minute screen recording following `docs/walkthrough.md`.

- [ ] **Step 7: Run the full checks, then commit**

Run: `uv run ruff check . && uv run mypy && uv run pytest -q`
Expected: all pass (262 + 8).

```bash
git add docs/walkthrough.md tests/test_walkthrough_doc.py README.md CLAUDE.md
git commit -m "docs: 3-minute walkthrough, container quick start"
```
