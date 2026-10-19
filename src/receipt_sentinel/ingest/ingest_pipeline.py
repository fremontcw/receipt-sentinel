"""ingest_pipeline

Messages in, IngestReport out: allowlist decision on headers alone, then (for allowed
senders only) attachments through extract → categorize → validate. Unknown senders are
reported as metadata and their messages are never parsed past the headers. Stores nothing.

Status:
    None
Issues:
    None
Todo:
    None
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import date

from receipt_sentinel.categorize.categorize_receipt import ParseFailure, categorize_receipt
from receipt_sentinel.categorize.llm_client import LlmClient, LlmTransientError
from receipt_sentinel.categorize.receipt_record import ReceiptRecord
from receipt_sentinel.extract.document_extractor import extract_document
from receipt_sentinel.extract.extracted_document import ExtractionRejected
from receipt_sentinel.ingest.inbound_message import (
    MessageHeaders,
    MessageSource,
    RawMessage,
    parse_inbound_message,
    parse_message_headers,
)
from receipt_sentinel.ingest.sender_allowlist import (
    SenderAllowlist,
    SenderDecision,
    decide_sender,
)
from receipt_sentinel.prompts.prompt_config import PromptConfig
from receipt_sentinel.validate.receipt_validation import ValidationResult, validate_receipt

logger = logging.getLogger(__name__)

MAX_SUBJECTS_PER_UNKNOWN_SENDER = 5
# A receipt email carries one or two files; more is a newsletter or abuse, and each costs OCR
# plus a model call
MAX_ATTACHMENTS_PER_MESSAGE = 5
OVER_ATTACHMENT_CAP = "over the per-message attachment cap"
MAX_RAW_FROM_CHARS = 200
OVER_MAX_MESSAGES = "over --max-messages"
NOT_OPENED = "(not opened)"


@dataclass(frozen=True)
class IngestedReceipt:
    message_id: str
    sender: str
    subject: str
    filename: str
    record: ReceiptRecord
    validation: ValidationResult
    extraction_method: str


@dataclass(frozen=True)
class SkippedAttachment:
    message_id: str
    sender: str
    filename: str
    reason: str


@dataclass(frozen=True)
class UnknownSender:
    address: str
    message_count: int
    subjects: tuple[str, ...]
    reason: str


@dataclass(frozen=True)
class IngestReport:
    receipts: tuple[IngestedReceipt, ...]
    skipped: tuple[SkippedAttachment, ...]
    unknown_senders: tuple[UnknownSender, ...]


@dataclass
class _UnknownTally:
    count: int = 0
    subjects: list[str] = field(default_factory=list)
    reason: str = ""


@dataclass(frozen=True)
class _Categorizer:
    prompt: PromptConfig
    client: LlmClient
    model_id: str


async def ingest_messages(
    source: MessageSource,
    allowlist: SenderAllowlist,
    *,
    since: date,
    prompt: PromptConfig,
    client: LlmClient,
    model_id: str,
    max_messages: int = 50,
) -> IngestReport:
    """Run every message from `since` through the allowlist and the receipt pipeline.

    Args:
        source: Where messages come from (.eml directory, Gmail).
        allowlist: Trusted senders; everyone else is reported, never opened.
        since: Earliest message date to fetch.
        prompt: Prompt version for categorization.
        client: Model client (real or the keyless fake).
        model_id: Model to call.
        max_messages: Cap on allowed messages processed per run (model spend bound).

    Raises:
        OcrUnavailable: An attachment needed OCR and Tesseract is missing.
    """
    categorizer = _Categorizer(prompt, client, model_id)
    receipts: list[IngestedReceipt] = []
    skipped: list[SkippedAttachment] = []
    unknown: dict[str, _UnknownTally] = {}
    processed = 0
    for raw in source.list_messages(since):
        headers = parse_message_headers(raw.raw)
        decision = decide_sender(headers, allowlist)
        if not decision.allowed:
            _tally_unknown(unknown, headers, decision)
            continue
        sender = decision.sender or ""
        if processed >= max_messages:
            skipped.append(SkippedAttachment(raw.message_id, sender, NOT_OPENED, OVER_MAX_MESSAGES))
            continue
        processed += 1
        full = source.full_message(raw)
        await _ingest_allowed(raw, full, headers, sender, categorizer, receipts, skipped)
    return IngestReport(tuple(receipts), tuple(skipped), _unknown_senders(unknown))


async def _ingest_allowed(
    raw: RawMessage,
    full: bytes,
    headers: MessageHeaders,
    sender: str,
    categorizer: _Categorizer,
    receipts: list[IngestedReceipt],
    skipped: list[SkippedAttachment],
) -> None:
    message = parse_inbound_message(full, sender=sender)
    email_date = (raw.received_at or headers.received_at).date()
    for extra in message.attachments[MAX_ATTACHMENTS_PER_MESSAGE:]:
        skipped.append(
            SkippedAttachment(raw.message_id, sender, extra.filename, OVER_ATTACHMENT_CAP)
        )
    for attachment in message.attachments[:MAX_ATTACHMENTS_PER_MESSAGE]:
        try:
            document = await asyncio.to_thread(extract_document, attachment.data)
            result = await categorize_receipt(
                document.text, categorizer.prompt, categorizer.client, model_id=categorizer.model_id
            )
        except ExtractionRejected as err:
            reason = str(err)
        except ParseFailure:
            reason = "model reply did not match the receipt schema"
        except LlmTransientError:
            reason = "model provider unavailable; try again"
        else:
            receipts.append(
                IngestedReceipt(
                    message_id=raw.message_id,
                    sender=sender,
                    subject=message.subject,
                    filename=attachment.filename,
                    record=result.record,
                    validation=validate_receipt(result.record, email_date=email_date),
                    extraction_method=document.extraction_method,
                )
            )
            continue
        skipped.append(SkippedAttachment(raw.message_id, sender, attachment.filename, reason))


def _tally_unknown(
    unknown: dict[str, _UnknownTally], headers: MessageHeaders, decision: SenderDecision
) -> None:
    # Count only: the address and subject are the untrusted sender's text, kept out of logs
    logger.info("dropped message from unknown sender (%s)", decision.reason)
    address = decision.sender or headers.raw_from[:MAX_RAW_FROM_CHARS]
    tally = unknown.setdefault(address, _UnknownTally(reason=decision.reason))
    tally.count += 1
    if len(tally.subjects) < MAX_SUBJECTS_PER_UNKNOWN_SENDER:
        tally.subjects.append(headers.subject)


def _unknown_senders(unknown: dict[str, _UnknownTally]) -> tuple[UnknownSender, ...]:
    return tuple(
        UnknownSender(address, tally.count, tuple(tally.subjects), tally.reason)
        for address, tally in unknown.items()
    )
