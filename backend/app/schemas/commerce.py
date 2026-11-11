from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_CENTS = 1_000_000_000_000


class Input(BaseModel):
    model_config = ConfigDict(
        extra="forbid", str_strip_whitespace=True, hide_input_in_errors=True
    )


class BudgetCreate(Input):
    client_request_id: UUID
    start_date: date
    end_date: date
    amount_cents: int = Field(strict=True, ge=0, le=MAX_CENTS)

    @model_validator(mode="after")
    def dates(self):
        if not 0 <= (self.end_date - self.start_date).days <= 366:
            raise ValueError("Use an inclusive budget period of at most 367 days.")
        return self


class BudgetUpdate(Input):
    expected_version: int = Field(strict=True, ge=1)
    amount_cents: int = Field(strict=True, ge=0, le=MAX_CENTS)


class CommitmentCreate(Input):
    client_request_id: UUID
    expected_version: int = Field(strict=True, ge=1)
    title: str = Field(min_length=1, max_length=120)
    planned_on: date
    amount_cents: int = Field(strict=True, gt=0, le=MAX_CENTS)


class VersionInput(Input):
    expected_version: int = Field(strict=True, ge=1)


class CommitmentSettle(VersionInput):
    purchase_id: UUID


class CommitmentOut(BaseModel):
    kind: Literal["commitment", "shopping_plan"]
    id: UUID
    title: str
    planned_on: date
    amount_cents: int
    status: Literal["open", "settled", "cancelled"]
    purchase_id: UUID | None


class BudgetOut(BaseModel):
    id: UUID
    start_date: date
    end_date: date
    currency: Literal["EUR"] = "EUR"
    amount_cents: int
    spent_cents: int
    reserved_cents: int
    available_cents: int
    purchase_count: int
    version: int
    commitments: list[CommitmentOut]


class BudgetPage(BaseModel):
    budgets: list[BudgetOut]


class CompareInput(Input):
    product_ids: list[UUID] = Field(min_length=1, max_length=20)
    include_alternatives: bool = False


class OfferCard(BaseModel):
    offer_id: str
    batch_id: str
    product_id: UUID
    product_name: str
    brand: str | None
    chain: str
    store_scope: Literal["national"] = "national"
    country_code: Literal["DE"] = "DE"
    relation: Literal["same_product", "compatible_alternative"]
    valid_from: date
    valid_until: date
    availability: Literal["active", "upcoming_this_week"]
    pack_base_quantity: str
    comparison_unit: Literal["kg", "l", "each", "wash", "roll"]
    unit_price_eur: str
    advertised_price_cents: int
    required_packs: int
    deposit_total_cents: int | None
    minimum_checkout_cents: int | None
    membership_required: bool
    price_access: Literal["standard", "membership"]
    is_best_unit_price: bool = False
    source: Literal["demo"] = "demo"


class ProductOffers(BaseModel):
    product_id: UUID
    product_name: str
    status: Literal["offers_found", "no_compatible_offer"]
    message: str
    offers: list[OfferCard]
    results_truncated: bool = False


class WeekContext(BaseModel):
    country_code: Literal["DE"] = "DE"
    coverage_note: str = (
        "National offer listings; retailer conditions and stock limitations still apply. No user location is collected for this feature."
    )
    as_of: date
    week_start: date
    week_end: date
    source: Literal["demo"] = "demo"


class CompareOut(WeekContext):
    currency: Literal["EUR"] = "EUR"
    results: list[ProductOffers]


class ProductOption(BaseModel):
    id: UUID
    name: str
    brand: str | None
    pack_base_quantity: str | None = None
    base_unit: str | None = None
    purchase_count: int = 0
    last_purchased_on: date | None = None
    suggestion_reason: str | None = None
    current_offer_count: int = 0


class OfferOptions(WeekContext):
    products: list[ProductOption]
    suggested_product_ids: list[UUID]
    history_start: date
    history_end: date
    history_linked_product_count: int
    explanation: str


class RecommendedProduct(BaseModel):
    product: ProductOption
    comparison: ProductOffers


class Recommendations(WeekContext):
    history_start: date
    history_end: date
    history_linked_product_count: int
    explanation: str
    recommendations: list[RecommendedProduct]


class ShoppingOverview(BaseModel):
    as_of: date
    spending_start: date
    spending_end: date
    confirmed_spent_cents: int
    purchase_count: int
    budget: BudgetOut | None = None
    offers: Recommendations | None = None
    offers_status: Literal["ready", "unavailable"]