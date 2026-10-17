"""azure_foundry_llm_client

Azure provider: the same Claude model served through Microsoft Foundry. Reuses
AnthropicLlmClient's retry, timeout and parsing by handing it the SDK's Foundry client, so the
two providers differ only in endpoint and credentials.

Status:
    Tested offline against a mocked Foundry endpoint; not yet run against a real deployment.
Issues:
    None
Todo:
    - [ ] Entra ID auth (azure_ad_token_provider via azure-identity) instead of an API key,
          once a real Azure subscription exists.
"""

import anthropic
import httpx2

from receipt_sentinel.categorize.anthropic_llm_client import AnthropicLlmClient

# OpenTelemetry's GenAI conventions have no value yet for Claude hosted on Foundry.
FOUNDRY_PROVIDER_NAME = "microsoft.foundry"
API_KEY_ENV_VAR = "ANTHROPIC_FOUNDRY_API_KEY"
RESOURCE_ENV_VAR = "ANTHROPIC_FOUNDRY_RESOURCE"
BASE_URL_ENV_VAR = "ANTHROPIC_FOUNDRY_BASE_URL"
# Foundry addresses models by deployment name, which may differ from the model id.
MODEL_ENV_VAR = "ANTHROPIC_FOUNDRY_MODEL"


def build_azure_foundry_llm_client(
    *,
    resource: str | None = None,
    api_key: str | None = None,
    timeout_seconds: float = 30.0,
    http_client: httpx2.AsyncClient | None = None,
) -> AnthropicLlmClient:
    """Build an LlmClient that calls Claude through a Microsoft Foundry resource.

    Args:
        resource: Foundry resource name (the `<resource>` in
            https://<resource>.services.ai.azure.com/anthropic/). None reads
            ANTHROPIC_FOUNDRY_RESOURCE, or ANTHROPIC_FOUNDRY_BASE_URL for a full URL.
        api_key: Foundry API key. None reads ANTHROPIC_FOUNDRY_API_KEY.
        timeout_seconds: Per-request timeout.
        http_client: Test-only seam for a mocked transport.

    Returns:
        An AnthropicLlmClient whose requests go to the Foundry endpoint.
    """
    # SDK-level retries disabled so AnthropicLlmClient's retry policy is the only one.
    foundry_sdk = anthropic.AsyncAnthropicFoundry(
        resource=resource,
        api_key=api_key,
        timeout=timeout_seconds,
        max_retries=0,
        http_client=http_client,
    )
    return AnthropicLlmClient(client=foundry_sdk)
