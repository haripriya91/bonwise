from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.purchase import Category


class ProductSuggestionInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    original_text: str = Field(min_length=1, max_length=300)
    merchant: str | None = Field(default=None, min_length=1, max_length=200)
    category: Category
    quantity_unit: Literal["pack", "each", "kg", "l"] = "pack"


class ProductCandidateOut(BaseModel):
    product_id: UUID
    name: str
    name_de: str | None
    brand: str | None
    category: Category
    pack_base_quantity: str | None
    base_unit: Literal["kg", "l", "each", "wash", "roll"] | None
    source: Literal["synthetic", "receipt_review"]
    status: Literal["supported", "check_details"]
    checks: list[str]
    match_origin: Literal["catalogue", "exact_memory", "correction_rag"] = "catalogue"


class ProductSuggestionOut(BaseModel):
    status: Literal["suggested", "review_required", "no_match"]
    message: str
    memory_status: Literal["reused", "conflict", "not_found", "not_checked", "retrieved", "unavailable", "disabled"] = "not_found"
    suggested_product_id: UUID | None = None
    candidates: list[ProductCandidateOut] = Field(default_factory=list)