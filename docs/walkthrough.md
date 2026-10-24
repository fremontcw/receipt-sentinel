# Receipt Sentinel in 3 minutes

You need Podman or Docker, nothing else, and no API key. Add `CONTAINER=docker` to each
`make` command if you use Docker. Build the image once before you start the clock (about two
minutes the first time):

```bash
make container-build
```

Each section has one command, what to look at, and a **Say** line: the narration, if you
are recording this.

## 0:00 · The problem

**Say:** "A feature built on an LLM can get worse without anything crashing. Change one
line of a prompt or switch models, and some receipts quietly come back wrong. This repo
catches that in CI, before the change merges."

## 0:20 · Catch a regression

```bash
make container-demo
```

Open `runs/demo/report.html`. Look at the **CRITICAL** status in the header, the
**Scorecard** against the baseline, and **Regressed cases**: each one shows what was
expected, what the previous run said and what this run said. The demo injects noise on
purpose, so the command reports CRITICAL; that is it working.

**Say:** "This is 85 golden receipts run through the extractor and scored against the
baseline. On a pull request that touches a prompt, `.github/workflows/evals.yml` runs this
with the real model and fails the check on CRITICAL, so the regression never merges."

## 1:10 · The feature under test

```bash
make container-serve
```

Open http://127.0.0.1:8000/docs. In **POST /categorize**, choose **prompt_injection** from
the Examples list and press Execute. The receipt tells the model to ignore its
instructions. The answer is still a normal receipt, with `injection_detected: true`. Then,
in **POST /categorize/file**, upload
`src/receipt_sentinel/evals/golden/fixtures/fixture_photo_adversarial_0001_override.jpg`, a
phone photo of the same receipt. The response says `ocr`: Tesseract read the photo first.
Press Ctrl+C to stop the server.

**Say:** "This is the thing under test: receipt in, structured record out, PDF or photo.
The model has no tools, and its reply has to parse as this record, so an injected
instruction has little to grab. In the tests it gets flagged and resisted."

## 2:00 · A human confirms before anything is stored

```bash
make container-checklist-demo
```

It reads a demo inbox, opens attachments only from allowlisted senders, and proposes
August's checklist: 2 receipts, one flagged as an injection attempt. Open the
`review.html` path it prints. Press **X** to reject the flagged receipt and **Enter** to
accept the other; the save dialog then opens. Run natively, **Save** downloads a decisions
file for `sentinel checklist confirm --decisions`, the `Then:` line the demo prints. The
container can't see your Downloads folder, so here confirm the same choice with the make
target and the proposal id the demo printed:

```bash
make container-checklist-confirm PROPOSAL=2026-08-01ef3167 ACCEPT=r02
```

**Say:** "Nothing reaches the database until a person confirms. Propose only drafts files; confirm is the
one path into the database, in one transaction, and it refuses duplicates and stale
decisions."

## 2:50 · Where to look next

- `src/receipt_sentinel/evals/`: scoring, the baseline diff and the thresholds behind CRITICAL.
- `src/receipt_sentinel/prompts/`: the prompt the eval gate protects.
- `docs/superpowers/specs/2026-08-28-receipt-sentinel-design.md`: the design and its trade-offs.
