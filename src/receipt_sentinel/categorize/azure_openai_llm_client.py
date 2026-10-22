"""azure_openai_llm_client

LlmClient adapter for a GPT model deployed in Azure OpenAI, so `compare-models` can put a
non-Claude model through the same golden set. Signs in with Microsoft Entra ID (the caller's
Azure login or managed identity); there is no API key. Same prompt, parser and retry policy
as the Claude client, so a score difference is the model, not the plumbing.

Status:
    None
Issues:
    None
Todo:
    None
"""

import asyncio
import time

import httpx2
import openai

from receipt_sentinel.categorize.llm_client import LlmRequest, LlmResponse, LlmTransientError

# OpenTelemetry GenAI conventions' value for Azure OpenAI.
AZURE_OPENAI_PROVIDER_NAME = "azure.ai.openai"
ENDPOINT_ENV_VAR = "AZURE_OPENAI_ENDPOINT"
# Azure OpenAI addresses models by deployment name; name the deployment after the model so
# the model_pricing.yaml key matches.
DEPLOYMENT_ENV_VAR = "AZURE_OPENAI_DEPLOYMENT"
DEFAULT_DEPLOYMENT = "gpt-4.1"
API_VERSION = "2024-10-21"
ENTRA_SCOPE = "https://cognitiveservices.azure.com/.default"
RETRYABLE = (
    openai.RateLimitError,
    openai.InternalServerError,
    openai.APITimeoutError,
    openai.APIConnectionError,
)
# OpenAI's finish_reason for a reply cut off at the token cap; categorize_receipt expects
# the Anthropic spelling.
_TRUNCATED = {"length": "max_tokens"}
# Cap on a server-requested wait, so a bad header can't stall a run.
MAX_RETRY_AFTER_SECONDS = 60.0


class AzureOpenAiLlmClient:
    def __init__(
        self,
        client: openai.AsyncAzureOpenAI,
        *,
        max_retries: int = 3,
        backoff_seconds: float = 1.0,
    ) -> None:
        self._client = client
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
                    await asyncio.sleep(_retry_delay(err, self._backoff_seconds * (2**attempt)))
        raise LlmTransientError(f"gave up after {self._max_retries} retries") from last_error

    async def _call_once(self, request: LlmRequest) -> LlmResponse:
        started = time.perf_counter()
        try:
            # No temperature or response_format: Claude gets neither, so the comparison is fair.
            completion = await self._client.chat.completions.create(
                model=request.model_id,
                max_completion_tokens=request.max_tokens,
                messages=[
                    {"role": "system", "content": request.system},
                    {"role": "user", "content": request.user},
                ],
            )
        except openai.BadRequestError as err:
            # Azure's prompt filter answers 400 instead of a reply. Report it the way an
            # output-filtered reply reads (finish_reason "content_filter"); other 400s are
            # broken setup and still propagate.
            if err.code != "content_filter":
                raise
            return LlmResponse(
                text="",
                input_tokens=0,
                output_tokens=0,
                latency_ms=int((time.perf_counter() - started) * 1000),
                model_id=request.model_id,
                stop_reason="content_filter",
            )
        latency_ms = int((time.perf_counter() - started) * 1000)
        choice = completion.choices[0]
        usage = completion.usage
        return LlmResponse(
            text=choice.message.content or "",
            input_tokens=usage.prompt_tokens if usage else 0,
            output_tokens=usage.completion_tokens if usage else 0,
            latency_ms=latency_ms,
            model_id=completion.model,
            stop_reason=_TRUNCATED.get(choice.finish_reason, choice.finish_reason),
        )


def _retry_delay(err: Exception, backoff: float) -> float:
    # Azure's 429 says how long its rate window needs (often 10-60 s); the short
    # exponential backoff alone gave up before the window reset on a full golden run.
    if isinstance(err, openai.RateLimitError):
        try:
            return min(
                max(backoff, float(err.response.headers.get("retry-after", 0))),
                MAX_RETRY_AFTER_SECONDS,
            )
        except ValueError:
            pass
    return backoff


def build_azure_openai_llm_client(
    endpoint: str,
    *,
    timeout_seconds: float = 30.0,
    http_client: httpx2.AsyncClient | None = None,
    azure_ad_token_provider: openai.lib.azure.AsyncAzureADTokenProvider | None = None,
) -> AzureOpenAiLlmClient:
    """Build an LlmClient for an Azure OpenAI resource, signed in with Entra ID.

    Args:
        endpoint: The resource endpoint, e.g. https://<resource>.cognitiveservices.azure.com/.
        timeout_seconds: Per-request timeout.
        http_client: Test-only seam for a mocked transport.
        azure_ad_token_provider: Test-only seam; None uses DefaultAzureCredential (az login
            locally, managed identity in Azure).

    Returns:
        An AzureOpenAiLlmClient whose requests carry an Entra ID bearer token.
    """
    if azure_ad_token_provider is None:
        from azure.identity import DefaultAzureCredential, get_bearer_token_provider

        # ponytail: sync credential (the aio one needs aiohttp); it caches the token, so it
        # blocks the event loop about once an hour. Switch to azure.identity.aio if that shows.
        azure_ad_token_provider = get_bearer_token_provider(DefaultAzureCredential(), ENTRA_SCOPE)
    # SDK-level retries disabled so AzureOpenAiLlmClient's retry policy is the only one.
    sdk = openai.AsyncAzureOpenAI(
        azure_endpoint=endpoint,
        api_version=API_VERSION,
        azure_ad_token_provider=azure_ad_token_provider,
        timeout=timeout_seconds,
        max_retries=0,
        http_client=http_client,
    )
    return AzureOpenAiLlmClient(sdk)
