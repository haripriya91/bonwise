from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.purchase import Category, SpendingSummary


class AnalysisInput(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True,
                              str_strip_whitespace=True)
    question: str = Field(min_length=3, max_length=2000, repr=False)
    start_date: date
    end_date: date

    @model_validator(mode="after")
    def valid_dates(self):
        if self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date.")
        return self


class AnalysisPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    operation: Literal["summary", "highest_category", "items", "merchants",
                       "records", "compare", "clarify", "unsupported"]
    language: Literal["en", "de"]
    start_date: date | None
    end_date: date | None
    category: Category | None
    product_query: str | None = Field(max_length=120)
    merchant_query: str | None = Field(max_length=120)
    metric: Literal["spending", "purchase_frequency"]
    top_n: int = Field(ge=1, le=10)
    comparison_start_date: date | None
    comparison_end_date: date | None
    clarification_reason: Literal["ranking", "period", "scope", "none"]


class EvidenceReference(BaseModel):
    evidence_id: str
    kind: Literal["summary", "purchase"]
    start_date: date
    end_date: date
    category: Category | None = None
    total_cents: int
    purchase_count: int | None = None
    purchase_id: UUID | None = None
    merchant: str | None = None
    purchased_on: date | None = None
    item_description: str | None = None
    item_id: UUID | None = None


class ToolActivity(BaseModel):
    name: str
    latency_ms: int


class AnalysisOut(BaseModel):
    status: Literal["answered", "clarification_required", "unsupported", "no_data"]
    answer: str
    evidence: list[EvidenceReference]
    summaries: list[SpendingSummary]
    evidence_truncated: bool
    tool_activity: list[ToolActivity]
    run_id: UUID
    model: str
    model_calls: int
    model_latency_ms: int
    latency_ms: int
    usage: dict[str, int]
    trace_sent: bool
    calculation_note: str | None = None
