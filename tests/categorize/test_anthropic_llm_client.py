"""test_anthropic_llm_client

Tests for the Anthropic Messages API adapter: request shaping, response mapping,
and the retry policy for transient errors. The SDK client is mocked - no network.

Status:
    None
Issues:
    None
Todo:
    None
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import anthropic
import pytest

from receipt_sentinel.categorize.anthropic_llm_client import AnthropicLlmClient
from receipt_sentinel.categorize.llm_client import LlmRequest, LlmTransientError

REQ = LlmRequest(system="sys", user="usr", max_tokens=256, model_id="claude-sonnet-5")


def fake_message(text: str, stop_reason: str = "end_turn") -> SimpleNamespace:
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(input_tokens=10, output_tokens=5),
        model="claude-sonnet-5",
        stop_reason=stop_reason,
    )


def make_sdk(side_effect: list[object]) -> MagicMock:
    sdk = MagicMock()
    sdk.messages.create = AsyncMock(side_effect=side_effect)
    return sdk


async def test_complete_pins_model_and_returns_usage() -> None:
    # ponytail: claude-sonnet-5 rejects the `temperature` sampling param (400) and the
    # installed SDK no longer types it as a create() kwarg at all - see report for detail.
    sdk = make_sdk([fake_message('{"ok":1}')])
    client = AnthropicLlmClient(api_key="k", client=sdk)
    resp = await client.complete(REQ)
    kwargs = sdk.messages.create.call_args.kwargs
    assert kwargs["model"] == "claude-sonnet-5"
    assert kwargs["max_tokens"] == 256
    assert kwargs["system"] == "sys"
    assert kwargs["messages"] == [{"role": "user", "content": "usr"}]
    assert resp.text == '{"ok":1}'
    assert (resp.input_tokens, resp.output_tokens) == (10, 5)


async def test_retries_rate_limit_then_succeeds() -> None:
    err = anthropic.RateLimitError(message="slow", response=MagicMock(status_code=429), body=None)
    sdk = make_sdk([err, fake_message("x")])
    client = AnthropicLlmClient(api_key="k", client=sdk, backoff_seconds=0)
    resp = await client.complete(REQ)
    assert resp.text == "x"
    assert sdk.messages.create.await_count == 2


async def test_gives_up_after_max_retries() -> None:
    err = anthropic.InternalServerError(
        message="boom", response=MagicMock(status_code=500), body=None
    )
    sdk = make_sdk([err, err, err, err])
    client = AnthropicLlmClient(api_key="k", client=sdk, max_retries=3, backoff_seconds=0)
    with pytest.raises(LlmTransientError):
        await client.complete(REQ)
    assert sdk.messages.create.await_count == 4  # 1 initial + 3 retries


async def test_extracts_text_only_from_mixed_content_blocks() -> None:
    message = SimpleNamespace(
        content=[
            SimpleNamespace(type="tool_use", text=None),
            SimpleNamespace(type="text", text="hello "),
            SimpleNamespace(type="text", text="world"),
        ],
        usage=SimpleNamespace(input_tokens=1, output_tokens=1),
        model="claude-sonnet-5",
        stop_reason="end_turn",
    )
    sdk = make_sdk([message])
    client = AnthropicLlmClient(api_key="k", client=sdk)
    resp = await client.complete(REQ)
    assert resp.text == "hello world"


async def test_disables_adaptive_thinking() -> None:
    # Thinking tokens count against max_tokens on claude-sonnet-5 and would truncate
    # the JSON reply, so the adapter must always opt out.
    sdk = make_sdk([fake_message('{"ok":1}')])
    await AnthropicLlmClient(api_key="k", client=sdk).complete(REQ)
    assert sdk.messages.create.call_args.kwargs["thinking"] == {"type": "disabled"}


async def test_truncated_reply_is_surfaced_as_stop_reason() -> None:
    sdk = make_sdk([fake_message('{"vendor": "ACM', stop_reason="max_tokens")])
    resp = await AnthropicLlmClient(api_key="k", client=sdk).complete(REQ)
    assert resp.stop_reason == "max_tokens"
