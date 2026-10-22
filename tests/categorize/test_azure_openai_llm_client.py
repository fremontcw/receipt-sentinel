import json
from typing import Any

import httpx2
import pytest

from receipt_sentinel.categorize.azure_openai_llm_client import build_azure_openai_llm_client
from receipt_sentinel.categorize.llm_client import LlmRequest, LlmTransientError
from receipt_sentinel.evals.eval_commands import build_client

REQ = LlmRequest(system="sys", user="receipt", max_tokens=64, model_id="gpt-4.1")


def _completion(content: str, finish_reason: str = "stop") -> dict[str, Any]:
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "created": 0,
        "model": "gpt-4.1-2025-04-14",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": finish_reason,
            }
        ],
        "usage": {"prompt_tokens": 12, "completion_tokens": 5, "total_tokens": 17},
    }


async def _fake_token() -> str:
    return "entra-token"


def _client(handler: Any) -> Any:
    return build_azure_openai_llm_client(
        "https://demo-resource.cognitiveservices.azure.com/",
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
        azure_ad_token_provider=_fake_token,
    )


async def test_requests_use_the_deployment_and_an_entra_token_not_a_key() -> None:
    seen: list[httpx2.Request] = []

    def azure(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, json=_completion('{"ok": true}'))

    response = await _client(azure).complete(REQ)

    (request,) = seen
    assert request.url.host == "demo-resource.cognitiveservices.azure.com"
    assert request.url.path == "/openai/deployments/gpt-4.1/chat/completions"
    assert request.headers["authorization"] == "Bearer entra-token"
    assert "api-key" not in request.headers
    body = json.loads(request.content)
    assert body["messages"][0] == {"role": "system", "content": "sys"}
    assert body["max_completion_tokens"] == 64
    assert "temperature" not in body and "response_format" not in body
    assert (response.text, response.input_tokens, response.output_tokens) == ('{"ok": true}', 12, 5)


async def test_a_reply_cut_off_at_the_token_cap_reads_as_max_tokens() -> None:
    def azure(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json=_completion('{"vendor": "Kro', "length"))

    response = await _client(azure).complete(REQ)
    assert response.stop_reason == "max_tokens"


async def test_rate_limits_are_retried_then_reported_as_transient() -> None:
    calls = 0

    def azure(request: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        return httpx2.Response(429, json={"error": {"message": "slow down"}})

    client = _client(azure)
    client._backoff_seconds = 0
    with pytest.raises(LlmTransientError):
        await client.complete(REQ)
    assert calls == 4


def test_azure_openai_provider_without_endpoint_says_what_to_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AZURE_OPENAI_ENDPOINT", raising=False)
    with pytest.raises(SystemExit, match="AZURE_OPENAI_ENDPOINT"):
        build_client(fake=False, demo_noise=False, cases=[], provider="azure-openai")


def test_azure_openai_provider_defaults_to_the_gpt_4_1_deployment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "AZURE_OPENAI_ENDPOINT", "https://demo-resource.cognitiveservices.azure.com/"
    )
    monkeypatch.delenv("AZURE_OPENAI_DEPLOYMENT", raising=False)
    _client, model_id = build_client(
        fake=False, demo_noise=False, cases=[], provider="azure-openai"
    )
    assert model_id == "gpt-4.1"
