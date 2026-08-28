"""llm_client

Provider-agnostic LLM interface plus the in-memory fake used by unit tests and `make demo`.

Status:
    None
Issues:
    None
Todo:
    None
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class LlmRequest:
    system: str
    user: str
    max_tokens: int
    model_id: str


@dataclass(frozen=True)
class LlmResponse:
    text: str
    input_tokens: int
    output_tokens: int
    latency_ms: int
    model_id: str
    # Provider's stop reason, when it exposes one. "max_tokens" means the reply was cut
    # off mid-JSON; categorize_receipt turns that into a ParseFailure. Carried as data so
    # the client layer never has to import the parser (circular).
    stop_reason: str | None = None


class LlmTransientError(Exception):
    """Provider failed after retries (timeouts, 429, 5xx). Counted as a provider error."""


class LlmClient(Protocol):
    async def complete(self, request: LlmRequest) -> LlmResponse: ...


def canned_by_substring(mapping: dict[str, str], default: str) -> Callable[[LlmRequest], str]:
    """Responder that returns the first value whose key appears in the user message."""

    def respond(request: LlmRequest) -> str:
        for needle, text in mapping.items():
            if needle in request.user:
                return text
        return default

    return respond


@dataclass
class FakeLlmClient:
    responder: Callable[[LlmRequest], str]
    calls: list[LlmRequest] = field(default_factory=list)

    async def complete(self, request: LlmRequest) -> LlmResponse:
        self.calls.append(request)
        text = self.responder(request)
        # ponytail: 4 chars ≈ 1 token; good enough for fake cost accounting
        return LlmResponse(
            text=text,
            input_tokens=max(1, (len(request.system) + len(request.user)) // 4),
            output_tokens=max(1, len(text) // 4),
            latency_ms=1,
            model_id=request.model_id,
        )
