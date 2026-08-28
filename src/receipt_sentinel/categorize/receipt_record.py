"""receipt_record

Pydantic contract for what the categorizer returns. ModelOutput is the strict shape the
model is allowed to produce; ReceiptRecord adds the provenance fields we stamp ourselves.

Status:
    None
Issues:
    None
Todo:
    None
"""

from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field


class Category(StrEnum):
    GROCERIES = "groceries"
    DINING = "dining"
    TRANSPORT = "transport"
    UTILITIES = "utilities"
    SUBSCRIPTIONS = "subscriptions"
    HEALTH = "health"
    HOME = "home"
    OTHER = "other"


class LineItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str = Field(max_length=200)
    amount: Decimal


class ModelOutput(BaseModel):
    """Exactly the JSON the model may return. Extra keys are rejected so the model can
    never set provenance fields."""

    model_config = ConfigDict(extra="forbid")

    vendor: str = Field(max_length=200)
    total: Decimal
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    date: date
    category: Category
    line_items: list[LineItem] = Field(max_length=100)
    confidence: float = Field(ge=0.0, le=1.0)
    injection_detected: bool


class ReceiptRecord(ModelOutput):
    model_id: str
    prompt_version: str

    @classmethod
    def from_model_output(cls, output: ModelOutput, *, model_id: str, prompt_version: str) -> Self:
        return cls(**output.model_dump(), model_id=model_id, prompt_version=prompt_version)


RECEIPT_OUTPUT_JSON_SCHEMA: dict[str, object] = ModelOutput.model_json_schema()
