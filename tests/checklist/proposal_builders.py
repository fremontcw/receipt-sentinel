"""IngestReport pieces for checklist tests, without extraction or a model."""

from datetime import date
from decimal import Decimal

from receipt_sentinel.categorize.receipt_record import Category, LineItem, ReceiptRecord
from receipt_sentinel.ingest.ingest_pipeline import IngestedReceipt
from receipt_sentinel.validate.receipt_validation import validate_receipt


def ingested(
    vendor: str = "Blue Bottle Coffee",
    total: str = "12.50",
    *,
    day: date = date(2026, 8, 17),
    category: Category = Category.DINING,
    currency: str = "USD",
    confidence: float = 0.95,
    injection: bool = False,
    items_sum: str | None = None,
    message_id: str = "m1",
) -> IngestedReceipt:
    record = ReceiptRecord(
        vendor=vendor,
        total=Decimal(total),
        currency=currency,
        date=day,
        category=category,
        line_items=[LineItem(description="Item", amount=Decimal(items_sum or total))],
        confidence=confidence,
        injection_detected=injection,
        model_id="fake",
        prompt_version="1",
    )
    return IngestedReceipt(
        message_id=message_id,
        sender="receipts@shop.com",
        subject="Your receipt",
        filename="receipt.pdf",
        record=record,
        validation=validate_receipt(record, email_date=day),
        extraction_method="native",
    )
