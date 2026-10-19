"""categorize_api

Local REST endpoint for the categorizer: `make serve`, then try it at /docs, with receipt
text or an uploaded PDF/photo (through the extract layer). Calls the same
categorize_receipt the eval harness gates. Keyless (golden-label fake) unless a real
provider key is set. Binds to 127.0.0.1 only: no auth, nothing stored.

Status:
    None
Issues:
    None
Todo:
    None
"""

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Literal

import anthropic
from fastapi import Body, FastAPI, HTTPException, UploadFile
from fastapi.openapi.models import Example
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from receipt_sentinel.categorize.azure_foundry_llm_client import API_KEY_ENV_VAR
from receipt_sentinel.categorize.categorize_receipt import (
    MAX_RECEIPT_CHARS,
    ParseFailure,
    categorize_receipt,
)
from receipt_sentinel.categorize.llm_client import LlmClient, LlmTransientError
from receipt_sentinel.categorize.receipt_record import ReceiptRecord
from receipt_sentinel.evals.eval_commands import REPO_ROOT, build_client
from receipt_sentinel.evals.golden_case import GoldenCase, load_golden_cases
from receipt_sentinel.evals.golden_fixture_text import extract_fixture_cases
from receipt_sentinel.extract.document_extractor import extract_document
from receipt_sentinel.extract.extracted_document import (
    MAX_ATTACHMENT_BYTES,
    ExtractionRejected,
    OcrUnavailable,
    RejectionKind,
)
from receipt_sentinel.prompts.prompt_config import PromptConfig, load_active_prompt
from receipt_sentinel.telemetry import configure_tracing

logger = logging.getLogger(__name__)

PROVIDER_ENV_VAR = "SENTINEL_PROVIDER"
FIXTURES_DIR_HINT = "src/receipt_sentinel/evals/golden/fixtures/"
KEYLESS_UNKNOWN_RECEIPT = (
    "keyless demo mode only knows the golden-set receipts (pick one from the examples, or "
    f"upload a file from {FIXTURES_DIR_HINT}); set ANTHROPIC_API_KEY to extract your own"
)
_REJECTION_STATUS: dict[RejectionKind, int] = {"unsupported_type": 415, "too_large": 413}
# Shown in the /docs dropdown. The fake answers these from their golden labels.
SAMPLE_CASES = {
    "clean": "synthetic_0001_dining_easy",
    "tip": "synthetic_0003_dining_medium",
    "prompt_injection": "adversarial_0001_override",
}


class CategorizeRequest(BaseModel):
    receipt_text: str = Field(min_length=1, max_length=MAX_RECEIPT_CHARS)


class FileCategorizeResponse(BaseModel):
    record: ReceiptRecord
    extraction_method: Literal["native", "ocr"]
    page_count: int
    ocr_confidence: float | None


def _keyless_cases() -> list[GoldenCase]:
    # The label oracle matches on the text the model sees, so fixture cases need their
    # extracted text for an uploaded fixture to be recognised
    try:
        return extract_fixture_cases(load_golden_cases())[0]
    except OcrUnavailable as err:
        logger.warning("%s; uploaded fixtures will not be recognised", err)
        return load_golden_cases()


def _uses_real_model(provider: str) -> bool:
    key_env = API_KEY_ENV_VAR if provider == "azure" else "ANTHROPIC_API_KEY"
    return bool(os.environ.get(key_env))


async def _categorize_or_http_error(
    receipt_text: str, prompt: PromptConfig, client: LlmClient, model_id: str, *, fake: bool
) -> ReceiptRecord:
    """Categorize, turning every expected failure into an HTTP error a person can read."""
    try:
        result = await categorize_receipt(receipt_text, prompt, client, model_id=model_id)
    except ParseFailure as err:
        if fake:
            raise HTTPException(422, KEYLESS_UNKNOWN_RECEIPT) from err
        # Never raw_text: the whole model reply about a receipt can carry PII
        logger.error("categorize parse failure: %s", err)
        raise HTTPException(502, "the model's reply did not match the receipt schema") from err
    except LlmTransientError as err:
        logger.error("categorize provider error: %s", err)
        raise HTTPException(503, "the model provider is unavailable; try again") from err
    except anthropic.APIStatusError as err:
        # The API's message names the problem (key, workspace, model) and never the key
        logger.error("provider rejected request (HTTP %s): %s", err.status_code, err.message)
        raise HTTPException(502, "the model provider rejected the request") from err
    return result.record


def build_app() -> FastAPI:
    """App factory for `uvicorn --factory`: picks the model once, from the environment.

    Returns:
        The app. Real model when the provider's key is set (SENTINEL_PROVIDER=anthropic|azure,
        default anthropic); otherwise the keyless golden-label fake.
    """
    provider = os.environ.get(PROVIDER_ENV_VAR, "anthropic")
    fake = not _uses_real_model(provider)
    cases = _keyless_cases() if fake else load_golden_cases()
    client, model_id = build_client(fake=fake, demo_noise=False, cases=cases, provider=provider)
    prompt = load_active_prompt(REPO_ROOT / "config" / "active_prompt.yaml")
    by_id = {case.id: case for case in cases}
    examples = {
        name: Example(summary=name, value={"receipt_text": by_id[case_id].receipt_text})
        for name, case_id in SAMPLE_CASES.items()
    }
    # A real record for the docs; the auto-generated one renders Decimals as long zero strings
    sample_record = {
        **by_id[SAMPLE_CASES["clean"]].expected.model_dump(mode="json"),
        "model_id": model_id,
        "prompt_version": prompt.version,
    }

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        shutdown_tracing = configure_tracing()
        yield
        shutdown_tracing()

    app = FastAPI(
        title="Receipt Sentinel",
        description="Open **POST /categorize**, click *Try it out*, and pick a sample receipt "
        "from **Examples**, or upload a PDF/photo to **POST /categorize/file** (samples in "
        f"`{FIXTURES_DIR_HINT}`). Model: `{model_id}`.",
        lifespan=lifespan,
    )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "model_id": model_id}

    @app.post(
        "/categorize",
        responses={200: {"content": {"application/json": {"example": sample_record}}}},
    )
    async def categorize(
        body: Annotated[CategorizeRequest, Body(openapi_examples=examples)],
    ) -> ReceiptRecord:
        return await _categorize_or_http_error(
            body.receipt_text, prompt, client, model_id, fake=fake
        )

    file_example = {
        "record": sample_record,
        "extraction_method": "ocr",
        "page_count": 1,
        "ocr_confidence": 0.94,
    }

    @app.post(
        "/categorize/file",
        responses={200: {"content": {"application/json": {"example": file_example}}}},
    )
    async def categorize_file(file: UploadFile) -> FileCategorizeResponse:
        # Read one byte past the cap so an oversized upload is caught without reading it all
        data = await file.read(MAX_ATTACHMENT_BYTES + 1)
        try:
            document = await run_in_threadpool(extract_document, data)
        except ExtractionRejected as err:
            logger.info("upload rejected: %s", err.kind)
            raise HTTPException(_REJECTION_STATUS.get(err.kind, 422), str(err)) from err
        except OcrUnavailable as err:
            raise HTTPException(503, str(err)) from err
        record = await _categorize_or_http_error(document.text, prompt, client, model_id, fake=fake)
        return FileCategorizeResponse(
            record=record,
            extraction_method=document.extraction_method,
            page_count=document.page_count,
            ocr_confidence=document.ocr_confidence,
        )

    return app
