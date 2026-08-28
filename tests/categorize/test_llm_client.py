from receipt_sentinel.categorize.llm_client import (
    FakeLlmClient,
    LlmRequest,
    canned_by_substring,
)


def make_request(user: str) -> LlmRequest:
    return LlmRequest(system="sys", user=user, max_tokens=512, model_id="fake")


async def test_fake_client_returns_canned_response_and_records_call() -> None:
    client = FakeLlmClient(canned_by_substring({"LYFT": '{"a":1}'}, default="{}"))
    resp = await client.complete(make_request("receipt from LYFT today"))
    assert resp.text == '{"a":1}'
    assert resp.model_id == "fake"
    assert resp.input_tokens > 0
    assert client.calls[0].user == "receipt from LYFT today"


async def test_fake_client_falls_back_to_default() -> None:
    client = FakeLlmClient(canned_by_substring({"LYFT": "x"}, default="dflt"))
    resp = await client.complete(make_request("nothing matches"))
    assert resp.text == "dflt"
