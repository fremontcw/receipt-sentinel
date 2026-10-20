#!/usr/bin/env bash
# Builds the image and runs what a user runs: both demos through the make targets, then the
# API with an OCR upload of the prompt-injection photo, then the hosted demo page, all keyless
# and on a read-only root.
# CONTAINER=docker in CI; podman locally (on macOS, `podman machine` must be running).
set -euo pipefail

export CONTAINER="${CONTAINER:-podman}"
IMAGE=receipt-sentinel:local
API_NAME=receipt-sentinel-smoke
HOSTED_NAME=receipt-sentinel-smoke-hosted
PORT=18000
HOSTED_PORT=18001
PHOTO=src/receipt_sentinel/evals/golden/fixtures/fixture_photo_adversarial_0001_override.jpg

cd "$(dirname "$0")/.."
trap '$CONTAINER rm -f "$API_NAME" "$HOSTED_NAME" >/dev/null 2>&1 || true' EXIT
fail() { echo "FAIL $*" >&2; exit 1; }
owner_uid() { stat -c %u "$1" 2>/dev/null || stat -f %u "$1"; }

make container-build >/dev/null
echo "ok 1 image builds"

[ "$($CONTAINER run --rm "$IMAGE" id -u)" = 10001 ] || fail "2 image user is root or unexpected"
echo "ok 2 image runs as uid 10001"

rm -rf runs/demo runs/checklist-demo
output=$(make container-demo)
grep -q "CRITICAL regression rendered as intended" <<<"$output" || fail "3 demo: $output"
[ -f runs/demo/report.html ] || fail "3 no runs/demo/report.html on the host"
[ "$(owner_uid runs/demo/report.html)" = "$(id -u)" ] || fail "3 report not owned by $(id -u)"
grep -q "$(git rev-parse --short HEAD)" runs/demo/report.html || fail "3 report lacks the git sha"
echo "ok 3 eval demo writes a report you own"

output=$(make container-checklist-demo)
grep -q "2 receipt(s)" <<<"$output" || fail "4 checklist demo: $output"
compgen -G "runs/checklist-demo/*/review.html" >/dev/null || fail "4 no review.html"
echo "ok 4 checklist demo writes a review page"

# The real container-serve target (your uid, its flags), detached on a spare port
make container-serve SERVE_FLAGS="-d --name $API_NAME" PORT="$PORT" >/dev/null
for _ in $(seq 30); do
  curl -fs "http://127.0.0.1:$PORT/docs" >/dev/null && break
  sleep 1
done
response=$(curl -fsS -F "file=@$PHOTO" "http://127.0.0.1:$PORT/categorize/file") \
  || fail "5 upload failed: $($CONTAINER logs "$API_NAME" 2>&1 | tail -5)"
grep -q '"injection_detected":true' <<<"$response" || fail "5 injection not flagged: $response"
grep -q '"ocr"' <<<"$response" || fail "5 not read by OCR: $response"
echo "ok 5 API flags the injection photo, read by OCR on a read-only root"

# The hosted demo: free mode answers from the answer key, and there is no upload route
env -u ANTHROPIC_API_KEY make container-serve-hosted \
  SERVE_FLAGS="-d --name $HOSTED_NAME" PORT="$HOSTED_PORT" >/dev/null
for _ in $(seq 30); do
  curl -fs "http://127.0.0.1:$HOSTED_PORT/api/status" >/dev/null && break
  sleep 1
done
response=$(curl -fsS -H "Content-Type: application/json" -d '{"sample":"tilted_photo"}' \
  "http://127.0.0.1:$HOSTED_PORT/api/extract") \
  || fail "6 hosted extract failed: $($CONTAINER logs "$HOSTED_NAME" 2>&1 | tail -5)"
grep -q '"source":"answer_key"' <<<"$response" || fail "6 not free mode: $response"
grep -q '"ocr"' <<<"$response" || fail "6 photo not read by OCR: $response"
status=$(curl -s -o /dev/null -w "%{http_code}" -F "file=@$PHOTO" \
  "http://127.0.0.1:$HOSTED_PORT/categorize/file")
[ "$status" = 404 ] || fail "6 hosted mode accepted an upload (HTTP $status)"
echo "ok 6 hosted demo serves free mode and refuses uploads"
