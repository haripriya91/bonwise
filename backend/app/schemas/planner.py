from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Unit = Literal["pack", "each", "kg", "l"]
MAX_CENTS = 1_000_000_000


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, hide_input_in_errors=True)


def clean_quantity(value):
    if value is None:
        return None
    try:
        number = Decimal(value.replace(",", "."))
        if not number.is_finite() or not 0 < number <= 1000 or number.as_tuple().exponent < -3:
            raise ValueError()
        return format(number.normalize(), "f")
    except (InvalidOperation, ValueError):
        raise ValueError("Use a quantity above zero, at most 1000, with up to three decimal places.") from None


class ItemEdit(Input):
    id: UUID = Field(default_factory=uuid4)
    product_id: UUID | None = None
    name: str = Field(min_length=1, max_length=160)
    quantity: str | None = Field(default=None, max_length=12)
    unit: Unit | None = None
    excluded: bool = False
    done: bool = False
    manual_unit_price_cents: int | None = Field(default=None, strict=True, ge=0, le=MAX_CENTS)
    offer_id: str | None = Field(default=None, max_length=160)
    offer_batch_id: str | None = Field(default=None, max_length=100)

    _quantity = field_validator("quantity")(clean_quantity)

    @model_validator(mode="after")
    def pairs(self):
        if (self.quantity is None) != (self.unit is None):
            raise ValueError("Supply both quantity and unit, or leave both unspecified.")
        if self.unit in {"pack", "each"} and Decimal(self.quantity) % 1:
            raise ValueError("Packs and individual items need whole quantities.")
        if (self.offer_id is None) != (self.offer_batch_id is None):
            raise ValueError("An offer requires both identifiers.")
        return self


class PreparePlan(Input):
    client_request_id: UUID
    planned_on: date
    force_new: bool = False


class Version(Input):
    expected_version: int = Field(strict=True, ge=1)


class EditPlan(Version):
    planned_on: date
    items: list[ItemEdit] = Field(max_length=40)
    target_cents: int | None = Field(default=None, strict=True, gt=0, le=MAX_CENTS)
    buffer_cents: int = Field(default=0, strict=True, ge=0, le=MAX_CENTS)

    @model_validator(mode="after")
    def unique_items(self):
        if len({item.id for item in self.items}) != len(self.items):
            raise ValueError("Item identifiers must be unique.")
        return self


class ApprovePlan(Version):
    client_request_id: UUID
    expected_budget_version: int = Field(strict=True, ge=1)
    reservation_cents: int = Field(strict=True, gt=0, le=MAX_CENTS)
    quantities_reviewed: Literal[True]
    acknowledge_unknown_prices: bool = False


class ChecklistEdit(Version):
    item_id: UUID
    done: bool


class SettlePlan(Version):
    purchase_id: UUID


class TextEdit(Version):
    text: str = Field(min_length=2, max_length=1000, repr=False)
    privacy_reviewed: Literal[True]


class TextAction(Input):
    action: Literal["add", "change", "remove", "already_have"]
    item_id: str | None
    name: str | None = Field(max_length=160)
    quantity: str | None = Field(max_length=12)
    unit: Unit | None
    quote: str = Field(min_length=1, max_length=300)


class TextActions(Input):
    actions: list[TextAction] = Field(max_length=10)
    clarification_required: bool


class Selection(Input):
    candidate_id: str
    quantity: str | None


class Proposal(Input):
    selections: list[Selection] = Field(max_length=20)


class PlanOut(BaseModel):
    id: UUID
    household_id: UUID
    week_start: date
    planned_on: date
    status: Literal["draft", "approved", "cancelled", "settled"]
    phase: str
    version: int
    content: dict
    budget: dict | None
    reservation_cents: int
    commitment_id: UUID | None
    is_revision: bool


class PlanPage(BaseModel):
    plans: list[PlanOut]


class TextPreviewOut(BaseModel):
    token: UUID
    base_version: int
    changes: list[str]
    clarification: str | None
    proposed_items: list[ItemEdit]


class AcceptText(Version):
    token: UUID
