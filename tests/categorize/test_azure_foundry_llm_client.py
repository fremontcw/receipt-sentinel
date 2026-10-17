import json

import httpx2
import pytest

from receipt_sentinel.categorize.azure_foundry_llm_client import build_azure_foundry_llm_client
from receipt_sentinel.categorize.llm_client import LlmRequest
from receipt_sentinel.evals.eval_commands import build_client

REQ = LlmRequest(system="sys", user="receipt", max_tokens=64, model_id="claude-sonnet-5")
MESSAGE_REPLY = {
    "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-sonnet-5",
    "content": [{"type": "text", "text": '{"ok": true}'}], "stop_reason": "end_turn",
    "stop_sequence": None, "usage": {"input_tokens": 12, "output_tokens": 5},
}


async def test_requests_go_to_the_foundry_endpoint_with_the_foundry_key() -> None:
    seen: list[httpx2.Request] = []

    def foundry(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, json=MESSAGE_REPLY)

    client = build_azure_foundry_llm_client(
        resource="demo-resource", api_key="test-key",
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(foundry)),
    )
    response = await client.complete(REQ)

    (request,) = seen
    assert request.url.host == "demo-resource.services.ai.azure.com"
    assert request.url.path == "/anthropic/v1/messages"
    assert request.headers["api-key"] == "test-key"
    assert json.loads(request.content)["model"] == "claude-sonnet-5"
    assert (response.text, response.input_tokens, response.output_tokens) == ('{"ok": true}', 12, 5)


def test_azure_provider_without_credentials_says_what_to_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("ANTHROPIC_FOUNDRY_API_KEY", "ANTHROPIC_FOUNDRY_RESOURCE",
                 "ANTHROPIC_FOUNDRY_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(SystemExit, match="ANTHROPIC_FOUNDRY_API_KEY"):
        build_client(fake=False, demo_noise=False, cases=[], provider="azure")


def test_azure_provider_uses_the_deployment_name_as_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_FOUNDRY_API_KEY", "k")
    monkeypatch.setenv("ANTHROPIC_FOUNDRY_RESOURCE", "demo-resource")
    monkeypatch.setenv("ANTHROPIC_FOUNDRY_MODEL", "my-sonnet-deployment")
    _client, model_id = build_client(fake=False, demo_noise=False, cases=[], provider="azure")
    assert model_id == "my-sonnet-deployment"
