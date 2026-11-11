from typing import Literal
from uuid import UUID
from pydantic import Field
from app.schemas.planner import Input, ItemEdit


class HouseholdCreate(Input):
    client_request_id: UUID
    name: str = Field(min_length=1, max_length=120)


class SettingsEdit(Input):
    expected_version: int = Field(strict=True, ge=0)
    name: str = Field(min_length=1, max_length=120)
    shopping_day: int | None = Field(default=None, strict=True, ge=0, le=6)
    setup_completed: bool = False


class ProfileEdit(Input):
    expected_version: int = Field(strict=True, ge=0)
    display_name: str = Field(max_length=80)


class NeedsPreview(Input):
    expected_version: int = Field(strict=True, ge=1)
    occasion: Literal["seasonal", "guests", "replacement", "other"]
    text: str = Field(min_length=3, max_length=400, repr=False)
    privacy_reviewed: Literal[True]
    extra_budget_cents: int | None = Field(default=None, strict=True, gt=0, le=1_000_000_000)


class NeedChoice(Input):
    product_code: str | None = Field(max_length=12)
    name: str = Field(min_length=1, max_length=120)
    reason: str = Field(min_length=1, max_length=180)
    quantity: str = Field(max_length=12)
    unit: Literal["pack", "each", "kg", "l"]
    purpose: Literal["main", "side", "drink", "extra", "household"]


class NeedProposal(Input):
    title: str = Field(min_length=1, max_length=100)
    explanation: str = Field(min_length=1, max_length=400)
    suggestions: list[NeedChoice] = Field(max_length=6)


class SavedNeed(Input):
    id: UUID
    item: ItemEdit


class SaveNeeds(Input):
    needs: list[SavedNeed] = Field(min_length=1, max_length=6)
