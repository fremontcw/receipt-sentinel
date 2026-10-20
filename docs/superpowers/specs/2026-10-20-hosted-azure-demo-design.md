# Hosted Azure demo: design

Status: draft for author review, 2026-10-20. Builds on the main design spec
(`2026-08-28-receipt-sentinel-design.md`) and the local REST endpoint (`make serve`).

## 1. Intent

The author puts one link on their resume. A hiring manager who clicks it should see,
within a few seconds and without installing anything, what this repo does: an LLM reads a
receipt, pulls out structured fields, flags a prompt injection instead of obeying it, and
a CI harness catches it when that gets worse.

The author also emails selected people a **personal link**. It unlocks the real model
(Claude) for that person, for a limited number of calls, and tells the author when they
used it. For a job seeker, that email is a useful signal in itself.

The project's profile is "AI-native automation engineer" in a Microsoft shop, so the
hosting is Azure and the deployment is the kind of thing that profile does at work:
infrastructure as code, keyless CI deploys, managed identity, budgets.

## 2. Goals, non-goals, anti-goals

**Goals**

- A public URL that works for anyone, costs nothing per visitor, and is the free demo.
- Personal links that switch on the real model: about 25 calls, 14-day expiry, revocable.
- A random-receipt button: every click makes a receipt nobody has seen before, so a live
  correct answer can't be memorised.
- An email to the author the first time each personal link uses the real model, and when
  the monthly cap trips.
- Total spend under **$30/month**, enforced, not hoped for.
- Deployed by GitHub Actions from `main`, no long-lived cloud credentials anywhere.

**Non-goals**

- Uploads of any kind in the hosted version. No PDFs, no photos, no pasted receipt text.
  Uploads stay in local `make serve`, where the data never leaves the user's machine.
- Hosting the eval harness, ingest, Gmail, or the checklist store. The page links to the
  committed sample report and the repo instead.
- Accounts, logins, or a sign-up flow.
- High availability. One replica, scale to zero; a cold start of a few seconds is fine.

**Anti-goals (would make it a failure even if it works)**

- A stranger's real personal data passes through the service.
- A public button that produces convincing receipts for real businesses (a fake-receipt
  maker for expense fraud). Every generated receipt carries a visible
  **SAMPLE · NOT A REAL RECEIPT** stamp and no logos. Real business names as plain text
  are fine with the stamp (author agreed 2026-10-20), so the hosted generator reuses the
  existing `VENDORS` lists; changing them would change the golden set.
- A forwarded or leaked link runs up a bill. The worst case for one link is its call cap.
- The page looks like a developer tool (the Swagger `/docs` page) to a non-engineer.
- Any long-lived secret in GitHub, the image, or source.

## 3. What the visitor sees

One page, layout **A** (chosen by the author from mockups):

- **Top bar:** "Receipt Sentinel" and the mode. Free demo: "Free demo · answers come from
  the answer key". Personal link: "Real model: 23 of 25 calls left · expires 3 Nov".
  Cap reached / expired / revoked / monthly pause: a plain sentence saying which, and that
  the free demo still works.
- **Left:** a **🎲 Random receipt (G)** button and the samples: clean, tip, prompt injection,
  tilted photo. Below them, the receipt image.
- **Right:** an **Extract (Enter)** button and the result: vendor, total, date, category,
  each with ✓ or ✗ against the answer key; an "injection detected: flagged, not followed"
  line when it applies; model name, latency, and whether it was read natively or by OCR.
- **Footer:** "How it's tested" → the sample regression report (served from the image's
  copy of `docs/sample_report.html`), the GitHub repo, and `docs/walkthrough.md`.

**Free mode with the random button (my call):** the receipt is generated and shown, and the
right side shows the answer key labelled as such, with a line "With a personal link, Claude
reads this receipt live." No locked door, and nothing pretends to be a model.

Keyboard: G = random receipt, Enter = extract. Buttons show their key.

## 4. Personal links

**Issuing (author, on their laptop):**

- `sentinel demo-link issue "Acme – Jane, eng manager"` prints
  `https://<host>/?k=<token>`, ready to paste into an email.
- `sentinel demo-link list` shows label, calls used, first used, expiry, revoked.
- `sentinel demo-link revoke <label-or-id>`.
- The CLI talks to Azure Table Storage with the author's own Azure login
  (`DefaultAzureCredential`); no storage key on the laptop either.

**Token:** 32 random bytes from `secrets`, URL-safe base64. Stored only as its SHA-256
(a high-entropy token doesn't need a slow hash). Row: label, hash, created, expires
(created + 14 days), calls_used, call_cap (25), first_used_at, revoked.

**Arriving with `?k=`:** the server checks the token, sets a cookie holding it
(`HttpOnly; Secure; SameSite=Lax; Max-Age` = time to expiry), and redirects to `/` so the
token leaves the address bar and history. A bad, expired or revoked token gets the free
demo with a one-line notice, never an error page.

**Counting:** a real-model call increments `calls_used` with an ETag-conditional update
(optimistic concurrency), so two tabs can't both spend the last call. The check and the
increment happen **before** the model call; a failed model call does not refund (my call:
simpler, and a refund path is a way to get free calls).

## 5. What can be sent to the model

The hosted real-model endpoint takes **no free text**. A request names one of:

- `{"sample": "<case id>"}`: one of the fixed sample cases, or
- `{"random": <seed>}`: the server regenerates the receipt from the seed with
  `generate_synthetic_cases` (plus the renderer for the photo variant) and owns the answer key.

So nobody can paste their own receipt (or anything else) into Claude through the site, and
the ✓/✗ comparison always has ground truth. The local `make serve` keeps its upload and
free-text endpoints; the hosted app is built with them switched off (one setting,
`SENTINEL_HOSTED=1`, read once at startup).

The random generator gains the adversarial and photo variants the samples already show, so
a random click can land on a prompt injection or a tilted photo.

## 6. Money

| Limit | Amount | Enforced by |
|---|---|---|
| Anthropic | $10/month | A dedicated demo API key in its own workspace with a workspace spend limit set in the Anthropic console. Hard stop, outside our code. |
| App's monthly cap | $8/month | The app adds each call's cost (tokens × `config/model_pricing.yaml`) to a per-month row in Table Storage. At $8 it switches everyone to free mode with a "real model paused until <1st>" notice, and emails the author once. |
| Per link | 25 calls | Section 4. About $0.08 per link at current prices. |
| Azure, extract work | 300 free-mode extracts/day, all visitors | App-level global counter (in memory; one replica makes it correct). Past it, free mode shows samples' saved answers without rendering or OCR. Bounds active CPU to minutes a day. |
| Azure | $15 budget alert | Azure budget on the resource group emailing the author at 50% and 100%. Alert only: Azure budgets don't stop resources. |

Worst case is about $25/month, under the author's $30. The one thing not hard-stopped is
an idle-but-warm replica: anything hitting the site every few minutes all month keeps it
from scaling to zero. At idle rates that's roughly $10/month for 0.5 vCPU / 1 GiB, inside
the $15 alert. Builder: confirm against current Container Apps pricing and size down if
it isn't.

**Model:** the hosted app uses the direct Anthropic API by default, because its workspace
spend limit is a hard stop. The existing `--provider azure` (Claude through Microsoft
Foundry) stays a one-setting switch, `SENTINEL_PROVIDER=azure`, if the author later wants
the model bill inside Azure too; Foundry spend would then be capped only by the app's own
monthly cap and the Azure alert.

## 7. Azure shape

- **Azure Container Apps**, consumption plan, `minReplicas 0`, `maxReplicas 1`, the smallest
  CPU/memory that runs OCR within the 30 s extraction limit. One replica bounds both cost and
  abuse. The monthly free grant should cover expected traffic.
- **Image:** the existing Dockerfile (add `!docs/sample_report.html` to the `.dockerignore`
  allowlist for the footer link), pushed to **GHCR** as a public package (no registry
  credentials needed to pull). Tagged with the git SHA; the deploy pins the SHA, never `latest`.
- **Address:** a custom domain the author buys (e.g. `demo.<name>.dev`), so the resume
  link and emailed links survive the environment being recreated; the default
  `*.azurecontainerapps.io` address changes when it is. Container Apps' free managed
  certificate covers it. The domain is a deploy parameter (not in source), and
  `demo-link issue` reads the public base URL from config (`SENTINEL_PUBLIC_URL`), never
  from a hard-coded host. Until the domain exists, the default address works unchanged.
- **Table Storage** (one storage account): tables `demolinks` and `demospend`. The app reaches
  it with its **managed identity** and a data-plane role scoped to that account; no
  connection string.
- **Secrets:** the Anthropic demo key lives in **Key Vault**; the container app references it
  through its managed identity. Never an env value in the template.
- **Email:** **Azure Communication Services Email** with the free Azure-managed sender domain,
  sent with the managed identity. The author's address is a deploy parameter, not in source.
  Sent on: first real-model use per link (label, time, nothing else), monthly cap reached.
  An email failure is logged and never blocks the visitor.
- **Infrastructure as code:** Bicep under `infra/`. One `main.bicep` for the resource group's
  contents. The author runs it once by hand; later changes go through the same file.
- **Deploy:** a GitHub Actions workflow on push to `main`, after the existing checks pass:
  build, push to GHCR, then `az containerapp update --image ghcr.io/...:<sha>`. It logs in to
  Azure with **OIDC** (a federated credential whose subject is pinned to
  `repo:fremontcw/receipt-sentinel:ref:refs/heads/main`), with a role limited to updating
  that one container app. No Azure secret in GitHub.
- **Container hardening:** the same posture as local where Container Apps allows it:
  non-root user, no added capabilities. Where a local flag has no Container Apps equivalent
  (e.g. read-only root), the builder notes it in the module header rather than dropping it
  silently.

## 8. Abuse and failure

- **Free mode** costs Azure CPU only (rendering and OCR). A simple per-IP rate limit in the
  app (my call: 30 extract calls per IP per 10 minutes, in memory; one replica makes that
  correct) plus `maxReplicas 1`.
- **Real mode** is bounded by the per-link cap, the monthly cap, and Anthropic's workspace limit.
- **Anthropic down or limit hit:** the visitor sees "the real model is unavailable right now"
  and the call isn't retried in a loop; the free demo still works.
- **Table Storage down:** personal links fail closed to free mode. Nothing grants real-model
  access without a successful read and a successful conditional increment.
- **Logs:** no tokens, no cookie values, no receipt text in logs. Link label and an id only.
  Generated receipts aren't personal data, but the rule stays the same as the rest of the repo.

## 9. Testing

- Unit tests for the token store (hashing, expiry, revoke, cap, concurrent last-call race
  with a fake ETag store), the monthly cap, and the `?k=` → cookie → redirect flow.
- API tests showing the hosted build refuses free text and uploads, and that free mode never
  calls the model.
- The existing container smoke test grows a hosted-mode check (`SENTINEL_HOSTED=1`, no key:
  random receipt + answer key works, `/categorize/file` is gone).
- `bicep build` (lint) in CI. No CI job touches real Azure except the deploy workflow.
- Page verified in a browser before handing over (keyboard, both modes, each notice).

## 10. What waits on the author

1. An Azure subscription, then one manual `az deployment group create` with `infra/main.bicep`.
2. In Entra: the app registration + federated credential for GitHub (the Bicep or a short
   script prints the exact commands).
3. In Anthropic's console: a demo workspace, its spend limit ($10), and a key for it, stored
   into Key Vault.
4. Buy the domain and add the two DNS records (a CNAME and the TXT verification record);
   the deploy prints both.
5. Email sender verification is automatic with the Azure-managed domain; the author confirms
   the address that receives alerts.

## 11. Left to the builder

- Exact Container Apps CPU/memory, as long as a photo receipt extracts inside the 30 s limit.
- Page styling, within layout A, readable on a phone.
- How the random generator's adversarial and photo variants are added, as long as the same
  seed always gives the same receipt and answer key.
- File and module names, following the repo's existing layout (`categorize/`, `evals/`).

## Calls I made (author to check)

- Free mode's random button shows the answer key instead of being locked.
- A failed model call still uses up one of the link's calls.
- Direct Anthropic API by default, Foundry as a switch.
- Per-IP limit of 30 extracts per 10 minutes in free mode, plus a global 300 a day.
- Email only on first real-model use per link and on the monthly cap, not every call.
