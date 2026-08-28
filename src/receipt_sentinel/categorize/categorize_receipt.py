"""categorize_receipt

The single LLM call under test. Builds the user turn (few-shot + delimited untrusted receipt
text), calls the client, and parses the reply into a ReceiptRecord with our own provenance.

Status:
    None
Issues:
    None
Todo:
    None
"""

import re
from dataclasses import dataclass

from pydantic import ValidationError

from receipt_sentinel.categorize.llm_client import LlmClient, LlmRequest, LlmResponse
from receipt_sentinel.categorize.receipt_record import ModelOutput, ReceiptRecord
from receipt_sentinel.prompts.prompt_config import PromptConfig

MAX_RECEIPT_CHARS = 20_000
_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$")
_FENCED_JSON = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


class ParseFailure(Exception):  # noqa: N818
    def __init__(self, raw_text: str, reason: str) -> None:
        super().__init__(reason)
        self.raw_text = raw_text


@dataclass(frozen=True)
class CategorizeResult:
    record: ReceiptRecord
    response: LlmResponse


def _neutralise_delimiters(text: str) -> str:
    # A receipt containing our closing tag must not be able to end the data block
    return text.replace("</receipt_text>", "&lt;/receipt_text&gt;")


def build_user_message(receipt_text: str, prompt: PromptConfig) -> str:
    examples = "\n".join(
        "<example>\n<receipt_text>\n"
        + _neutralise_delimiters(ex.receipt_text.strip())
        + "\n</receipt_text>\n<output>\n"
        + ex.expected_json.strip()
        + "\n</output>\n</example>"
        for ex in prompt.few_shot
    )
    body = _neutralise_delimiters(receipt_text[:MAX_RECEIPT_CHARS])
    return f"{examples}\n\nNow extract this receipt:\n<receipt_text>\n{body}\n</receipt_text>"


def parse_model_output(text: str) -> ModelOutput:
    # Try to find JSON between fences first (handles prose before/after fences)
    fenced_match = _FENCED_JSON.search(text)
    cleaned = fenced_match.group(1) if fenced_match else _FENCE.sub("", text)
    try:
        return ModelOutput.model_validate_json(cleaned)
    except ValidationError as err:
        raise ParseFailure(text, f"model output failed schema: {err.errors()[:3]}") from err


async def categorize_receipt(
    receipt_text: str,
    prompt: PromptConfig,
    client: LlmClient,
    *,
    model_id: str,
    max_tokens: int = 1024,
) -> CategorizeResult:
    request = LlmRequest(
        system=prompt.system,
        user=build_user_message(receipt_text, prompt),
        max_tokens=max_tokens,
        model_id=model_id,
    )
    response = await client.complete(request)
    output = parse_model_output(response.text)
    record = ReceiptRecord.from_model_output(
        output, model_id=model_id, prompt_version=prompt.version
    )
    return CategorizeResult(record=record, response=response)
