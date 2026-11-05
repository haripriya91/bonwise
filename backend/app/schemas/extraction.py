from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.purchase import (
    AdjustmentInput,
    Category,
    PurchaseContent,
)


class StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        hide_input_in_errors=True,
    )


class PrivacyInput(StrictModel):
    text: str = Field(
        min_length=1,
        max_length=16000,
        repr=False,
    )


class ExtractionInput(StrictModel):
    reviewed_text: str = Field(
        min_length=1,
        max_length=16000,
        repr=False,
    )
    privacy_reviewed: Literal[True]


class ExtractedItem(StrictModel):
    original_text: str = Field(min_length=1, max_length=300)
    quantity: str | None
    quantity_unit: Literal["pack", "each", "kg", "l"] | None

    unit_price_cents: int | None = Field(
        strict=True,
        ge=0,
        le=1_000_000_000,
    )

    line_total_cents: int | None = Field(
        strict=True,
        ge=0,
        le=1_000_000_000_000,
    )

    category: Category


class ExtractedPurchase(StrictModel):
    document_type: Literal[
        "receipt",
        "invoice",
        "purchase_note",
        "not_purchase",
    ]
    merchant: str | None = Field(max_length=200)
    purchased_on: str | None
    currency: str | None

    items: list[ExtractedItem] = Field(max_length=200)
    adjustments: list[AdjustmentInput] = Field(max_length=30)

    reported_total_cents: int | None = Field(
        strict=True,
        ge=0,
        le=1_000_000_000_000,
    )


class PrivacyPreview(StrictModel):
    reviewed_text: str
    findings: dict[str, int]
    requires_review: Literal[True]


class ExtractionOut(StrictModel):
    extracted: ExtractedPurchase
    missing_fields: list[str]
    warnings: list[str]
    candidate: PurchaseContent | None
    can_create_draft: bool

    run_id: str
    model: str
    latency_ms: int
    usage: dict[str, int]
    trace_sent: bool
    saved: Literal[False]