import shutil

import pytest
from fastapi.testclient import TestClient
from httpx import Response

from receipt_sentinel.categorize.categorize_api import (
    KEYLESS_UNKNOWN_RECEIPT,
    SAMPLE_CASES,
    build_app,
)
from receipt_sentinel.evals.golden_case import GOLDEN_DIR, GoldenCase, load_golden_cases
from receipt_sentinel.extract.extracted_document import MAX_ATTACHMENT_BYTES

needs_tesseract = pytest.mark.skipif(shutil.which("tesseract") is None, reason="needs tesseract")


@pytest.fixture
def keyless_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("SENTINEL_PROVIDER", raising=False)
    return TestClient(build_app())


def test_keyless_sample_injection_receipt_is_flagged_not_obeyed(keyless_client: TestClient) -> None:
    case = {c.id: c for c in load_golden_cases()}[SAMPLE_CASES["prompt_injection"]]
    response = keyless_client.post("/categorize", json={"receipt_text": case.receipt_text})
    assert response.status_code == 200
    body = response.json()
    assert body["injection_detected"] is True
    assert body["category"] == case.expected.category.value
    assert body["model_id"] == "fake"


def test_keyless_unknown_receipt_says_how_to_use_a_real_model(keyless_client: TestClient) -> None:
    response = keyless_client.post("/categorize", json={"receipt_text": "CORNER DELI\nTOTAL 9.99"})
    assert response.status_code == 422
    assert response.json()["detail"] == KEYLESS_UNKNOWN_RECEIPT


def test_empty_and_oversized_receipts_are_rejected(keyless_client: TestClient) -> None:
    assert keyless_client.post("/categorize", json={"receipt_text": ""}).status_code == 422
    huge = "x" * 20_001
    assert keyless_client.post("/categorize", json={"receipt_text": huge}).status_code == 422


def test_docs_offer_every_sample_receipt(keyless_client: TestClient) -> None:
    spec = keyless_client.get("/openapi.json").json()
    body = spec["paths"]["/categorize"]["post"]["requestBody"]["content"]["application/json"]
    assert set(body["examples"]) == set(SAMPLE_CASES)
    assert keyless_client.get("/health").json() == {"status": "ok", "model_id": "fake"}


def _fixture_case(case_id: str) -> GoldenCase:
    return {c.id: c for c in load_golden_cases()}[case_id]


def _upload(client: TestClient, data: bytes, name: str = "receipt.pdf") -> Response:
    return client.post("/categorize/file", files={"file": (name, data)})


def test_keyless_upload_of_native_pdf_fixture_extracts_and_categorizes(
    keyless_client: TestClient,
) -> None:
    case = _fixture_case("fixture_pdf_native_0001_dining_easy")
    assert case.fixture is not None
    response = _upload(keyless_client, (GOLDEN_DIR / case.fixture).read_bytes())
    assert response.status_code == 200
    body = response.json()
    assert body["extraction_method"] == "native" and body["page_count"] == 1
    assert body["record"]["total"] == str(case.expected.total)


@needs_tesseract
def test_keyless_upload_of_photo_fixture_uses_ocr(keyless_client: TestClient) -> None:
    case = _fixture_case("fixture_photo_adversarial_0001_override")
    assert case.fixture is not None
    response = _upload(keyless_client, (GOLDEN_DIR / case.fixture).read_bytes(), "photo.jpg")
    assert response.status_code == 200
    body = response.json()
    assert body["extraction_method"] == "ocr" and body["ocr_confidence"] > 0
    assert body["record"]["injection_detected"] is True


def test_html_named_pdf_is_unsupported_media_type(keyless_client: TestClient) -> None:
    response = _upload(keyless_client, b"<html><script>alert(1)</script>")
    assert response.status_code == 415
    assert "PDF" in response.json()["detail"]


def test_upload_over_ten_megabytes_is_too_large(keyless_client: TestClient) -> None:
    response = _upload(keyless_client, b"%PDF-" + b"\0" * MAX_ATTACHMENT_BYTES)
    assert response.status_code == 413
