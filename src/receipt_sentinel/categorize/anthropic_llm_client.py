"""anthropic_llm_client

LlmClient adapter for the Anthropic Messages API. Pins an exact model id, max_tokens,
and a timeout; disables adaptive thinking; retries 429/5xx/timeouts with exponential backoff.

Status:
    Thinking is disabled explicitly — adaptive thinking is on by default on claude-sonnet-5
    and its tokens count against max_tokens, which truncated the JSON reply. Truncation is
    surfaced to the caller as LlmResponse.stop_reason == "max_tokens".
Issues:
    Does not pass `temperature` to the API. The installed anthropic SDK (1.2.0) no longer
    types `temperature` as a `messages.create()` parameter, and claude-sonnet-5 itself
    rejects sampling params (400) - see task-5-report.md for detail and the pinned-model
    determinism rationale.
Todo:
    None
"""

import asyncio
import time

import anthropic

from receipt_sentinel.categorize.llm_client import LlmRequest, LlmResponse, LlmTransientError

DEFAULT_MODEL_ID = "claude-sonnet-5"
RETRYABLE = (
    anthropic.RateLimitError,
    anthropic.InternalServerError,
    anthropic.APITimeoutError,
    anthropic.APIConnectionError,
)


class AnthropicLlmClient:
    def __init__(
        self,
        api_key: str,
        *,
        timeout_seconds: float = 30.0,
        max_retries: int = 3,
        backoff_seconds: float = 1.0,
        client: anthropic.AsyncAnthropic | None = None,
    ) -> None:
        # SDK-level retries disabled so our retry policy is the only one
        self._client = client or anthropic.AsyncAnthropic(
            api_key=api_key, timeout=timeout_seconds, max_retries=0
        )
        self._max_retries = max_retries
        self._backoff_seconds = backoff_seconds

    async def complete(self, request: LlmRequest) -> LlmResponse:
        last_error: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                return await self._call_once(request)
            except RETRYABLE as err:
                last_error = err
                if attempt < self._max_retries:
                    await asyncio.sleep(self._backoff_seconds * (2**attempt))
        raise LlmTransientError(f"gave up after {self._max_retries} retries") from last_error

    async def _call_once(self, request: LlmRequest) -> LlmResponse:
        started = time.perf_counter()
        message = await self._client.messages.create(
            model=request.model_id,
            max_tokens=request.max_tokens,
            system=request.system,
            messages=[{"role": "user", "content": request.user}],
            # claude-sonnet-5 runs adaptive thinking by default and thinking tokens count
            # against max_tokens — leaving it on truncates the JSON reply. This is a
            # single-shot strict-JSON extraction; it does not need reasoning tokens.
            thinking={"type": "disabled"},
        )
        latency_ms = int((time.perf_counter() - started) * 1000)
        text = "".join(
            str(getattr(block, "text", ""))
            for block in message.content
            if getattr(block, "type", None) == "text"
        )
        return LlmResponse(
            text=text,
            input_tokens=message.usage.input_tokens,
            output_tokens=message.usage.output_tokens,
            latency_ms=latency_ms,
            model_id=message.model,
            stop_reason=message.stop_reason,
        )
