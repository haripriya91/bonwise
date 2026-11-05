from datetime import date
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


Category = Literal[
    "groceries",
    "household",
    "personal_care",
    "transport",
    "dining_out",
    "other",
]

MAX_TOTAL = 1_000_000_000_000


class InputModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )


class ItemInput(InputModel):
    product_id: UUID | None = None
    original_text: str = Field(min_length=1, max_length=300)
    quantity: Decimal = Field(
        default=Decimal("1"),
        gt=0,
        le=1_000_000,
        max_digits=12,
        decimal_places=3,
    )
    quantity_unit: Literal["pack", "each", "kg", "l"] = "pack"
    unit_price_cents: int = Field(
        strict=True,
        ge=0,
        le=1_000_000_000,
    )
    category: Category


class AdjustmentInput(InputModel):
    kind: Literal["discount", "deposit"]
    label: str = Field(min_length=1, max_length=120)
    amount_cents: int = Field(
        strict=True,
        ge=-MAX_TOTAL,
        le=MAX_TOTAL,
    )
    category: Category

    @model_validator(mode="after")
    def validate_sign(self):
        if self.kind == "discount" and self.amount_cents >= 0:
            raise ValueError("A discount must be negative cents.")

        if self.kind == "deposit" and self.amount_cents <= 0:
            raise ValueError("A deposit must be positive cents.")

        return self


class PurchaseContent(InputModel):
    merchant: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
    )
    purchased_on: date
    currency: Literal["EUR"] = "EUR"
    items: list[ItemInput] = Field(min_length=1, max_length=200)
    adjustments: list[AdjustmentInput] = Field(
        default_factory=list,
        max_length=30,
    )
    reported_total_cents: int | None = Field(
        default=None,
        strict=True,
        ge=0,
        le=MAX_TOTAL,
    )


class DraftCreate(InputModel):
    client_request_id: UUID
    source_type: Literal["text", "image"] = "text"
    content: PurchaseContent


class DraftUpdate(InputModel):
    revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    content: PurchaseContent


class ConfirmDraft(InputModel):
    revision: str = Field(pattern=r"^[a-f0-9]{64}$")


class DraftOut(BaseModel):
    id: UUID
    household_id: UUID
    status: Literal["pending", "confirmed", "rejected"]
    source_type: str
    client_request_id: UUID
    revision: str
    content: PurchaseContent
    calculated_total_cents: int
    warnings: list[str]
    purchase_id: UUID | None


class ItemOut(ItemInput):
    id: UUID
    line_total_cents: int


class PurchaseOut(BaseModel):
    id: UUID
    household_id: UUID
    draft_id: UUID
    merchant: str | None
    purchased_on: date
    currency: Literal["EUR"]
    total_cents: int
    adjustments: list[AdjustmentInput]
    items: list[ItemOut]


class PurchasePage(BaseModel):
    total: int
    limit: int
    offset: int
    purchases: list[PurchaseOut]