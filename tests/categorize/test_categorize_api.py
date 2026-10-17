import pytest
from fastapi.testclient import TestClient

from receipt_sentinel.categorize.categorize_api import (
    KEYLESS_UNKNOWN_RECEIPT,
    SAMPLE_CASES,
    build_app,
)
from receipt_sentinel.evals.golden_case import load_golden_cases


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
