# Receipt Sentinel: eval demo, categorizer API and checklist demo, no local Python or
# Tesseract needed. Run it through the Makefile's container-* targets, which add the
# hardening flags (read-only root, no capabilities) and publish on 127.0.0.1 only.
# Bases are pinned to multi-arch manifest-list digests (arm64 laptops, amd64 CI).

FROM docker.io/library/python:3.12-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258 AS builder
COPY --from=ghcr.io/astral-sh/uv:0.9.26@sha256:9a23023be68b2ed09750ae636228e903a54a05ea56ed03a934d00fe9fbeded4b /uv /bin/uv
ENV UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_COMPILE_BYTECODE=1
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --extra api --extra hosted --no-install-project
COPY src ./src
COPY config ./config
COPY examples ./examples
# The hosted demo's "How it's tested" link
COPY docs/sample_report.html ./docs/sample_report.html
# Editable on purpose: REPO_ROOT is resolved from the source file, so the package must
# stay at /app/src to find /app/config and the golden set
RUN uv sync --frozen --extra api --extra hosted

FROM docker.io/library/python:3.12-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*
COPY --from=builder /app /app
WORKDIR /app
# HOME=/tmp: the targets run as the host user's uid, which has no home in the image
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH=/app/.venv/bin:$PATH HOME=/tmp
# Reports show the commit; the image has no .git to ask (make container-build sets it)
ARG GIT_SHA=unknown
ENV SENTINEL_GIT_SHA=$GIT_SHA
USER 10001
EXPOSE 8000
# 0.0.0.0 inside the container's own network; the host publishes it on 127.0.0.1 only.
# No access log: it prints the full URL, and a hosted personal link carries its token.
# Proxy headers: on Azure the visitor's IP and https arrive from Container Apps' ingress.
CMD ["uvicorn", "receipt_sentinel.categorize.categorize_api:build_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-access-log", "--proxy-headers", "--forwarded-allow-ips=*"]
