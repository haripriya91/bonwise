"""Verified facts and a deliberately small model selection contract."""
from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class InsightRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    start_date: date
    end_date: date

    @model_validator(mode="after")
    def bounded_dates(self):
        if not 0 <= (self.end_date - self.start_date).days <= 366:
            raise ValueError("Choose a date range of at most 367 days.")
        return self


class SuggestionSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    fact_id: Literal["category", "items", "budget", "repeat_purchases", "price_change", "planning_selection", "budget_pressure", "missing_target"]
    suggestion_id: Literal["review", "check_home", "compare", "set_target", "review_budget", "plan_check"]


class InsightSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    selections: list[SuggestionSelection] = Field(min_length=1, max_length=3)


class InsightEvidence(BaseModel):
    purchase_id: UUID
    purchased_on: date
    total_cents: int


class InsightCard(BaseModel):
    id: str
    title: str
    detail: str
    suggestion: str | None = None
    action: Literal["review", "offers", "budget", "planner"]
    evidence: list[InsightEvidence] = Field(default_factory=list)
    evidence_truncated: bool = False


class RankedInsightItem(BaseModel):
    label: str
    total_cents: int
    purchase_count: int


class InsightsOut(BaseModel):
    status: Literal["ready", "fallback", "no_data"]
    start_date: date
    end_date: date
    purchase_count: int
    total_cents: int
    top_items: list[RankedInsightItem]
    cards: list[InsightCard]
    generated_at: datetime
    revision: str
    prompt_version: str
    model_calls: int
    notice: str
