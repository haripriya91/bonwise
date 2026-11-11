from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.purchase import Category, SpendingSummary
from app.schemas.commerce import BudgetOut


class AnalysisPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    operation: Literal["summary", "highest_category", "items", "merchants",
                       "records", "compare", "quantity", "budget", "clarify", "unsupported"]
    detail: Literal["merchant", "date"] | None = None
    language: Literal["en", "de"]
    start_date: date | None
    end_date: date | None
    category: Category | None
    product_query: str | None = Field(max_length=120)
    merchant_query: str | None = Field(max_length=120)
    quantity_unit: Literal["pack", "each", "kg", "l"] | None = None
    metric: Literal["spending", "purchase_frequency"]
    sort_direction: Literal["highest", "lowest"] = "highest"
    top_n: int = Field(ge=1, le=10)
    comparison_start_date: date | None
    comparison_end_date: date | None
    clarification_reason: Literal["ranking", "period", "scope", "none"]
    clarification_target: Literal["summary", "highest_category", "items", "merchants",
                                  "records", "compare", "quantity", "budget"] | None = None


class ContextPeriod(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    start_date: date
    end_date: date

    @model_validator(mode="after")
    def valid_dates(self):
        if self.end_date < self.start_date:
            raise ValueError("Invalid context period.")
        return self


class PendingAnalysisContext(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    target: Literal["summary", "highest_category", "items", "merchants",
                    "records", "compare", "quantity", "budget"] | None = None
    reason: Literal["ranking", "period"]
    category: Category | None = None
    product_query: str | None = Field(default=None, max_length=120)
    merchant_query: str | None = Field(default=None, max_length=120)
    quantity_unit: Literal["pack", "each", "kg", "l"] | None = None
    start_date: date | None = None
    end_date: date | None = None
    comparison_start_date: date | None = None
    comparison_end_date: date | None = None
    top_n: int = Field(default=1, ge=1, le=10)


class ConversationContext(BaseModel):
    """Untrusted client-supplied filters, never authorization or evidence."""
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    version: Literal[1] = 1
    last_plan: AnalysisPlan | None = None
    pending: PendingAnalysisContext | None = None
    selected_period: ContextPeriod | None = None
    focus_category: Category | None = None
    focus_product: str | None = Field(default=None, max_length=120)
    focus_merchant: str | None = Field(default=None, max_length=120)


class AnalysisInput(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True,
                              str_strip_whitespace=True)
    question: str = Field(min_length=3, max_length=2000, repr=False)
    start_date: date
    end_date: date
    conversation_context: ConversationContext | None = Field(default=None, repr=False)

    @model_validator(mode="after")
    def valid_dates(self):
        if self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date.")
        if self.conversation_context is not None:
            # A fixed small context replaces unbounded raw chat history.
            if len(self.conversation_context.model_dump_json().encode("utf-8")) > 6000:
                raise ValueError("Conversation context is too large.")
        return self


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
    quantity: str | None = None
    quantity_unit: Literal["pack", "each", "kg", "l"] | None = None


class QuantityTotal(BaseModel):
    quantity: str = Field(pattern=r"^(0|[1-9]\d*)(\.\d{1,3})?$")
    quantity_unit: Literal["pack", "each", "kg", "l"]


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
    conversation_context: ConversationContext | None = Field(default=None, repr=False)
    analysis_period: ContextPeriod | None = None
    quantity_totals: list[QuantityTotal] = Field(default_factory=list)

class AnalysisBudgetContext(BaseModel):
    status: Literal["covered", "not_set", "partial"]
    budget: BudgetOut | None
