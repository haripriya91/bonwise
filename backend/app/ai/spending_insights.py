"""One bounded AI selection call; no raw receipts, arbitrary figures or writes."""
import json
from datetime import datetime, timezone
from functools import lru_cache

from fastapi import HTTPException
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langsmith import tracing_context

from app.ai.analysis_tracing import AnalysisTrace
from app.core.config import get_settings
from app.schemas.insights import InsightSelection, InsightsOut
from app.services.insights import NOTICE, apply_selection, load_facts

PROMPT_VERSION = "spending-insights-v2-actionable-patterns"
PROMPT = """Select the most useful conditional suggestions for the supplied verified spending facts.
Return one to three selections, each with a distinct fact_id and an allowed suggestion_id
from that fact. Include budget_pressure when supplied, then prioritize decisions
supported by repeated purchase records, comparable unit-price increases and review
of clothing candidates before repeating a weekly shop. Do not repeat chart rankings
or ordinary budget balances. Show fewer than three when fewer findings are useful.
Prefer checking home for repeated item purchases when appropriate;
compare is useful only if a person plans to buy again, not a promise of savings.
Never infer waste, health, family circumstances, affordability or causes of price changes.
Highest spending is not proof of overspending. Budget figures have their own date scope.
Do not calculate figures, supply prose, add references or request tools. You cannot change records.
"""


@lru_cache
def insights_model():
    settings = get_settings()
    return ChatOpenAI(model=settings.llm_model, api_key=settings.openai_api_key.get_secret_value(),
        timeout=25, max_retries=0, max_tokens=600, store=False).with_structured_output(
            InsightSelection, method="function_calling", include_raw=True)


def generate(db, household_id, user_id, body, background_tasks):
    facts = load_facts(db, household_id, user_id, body)
    summary = facts["summary"]
    base = dict(start_date=body.start_date, end_date=body.end_date,
                purchase_count=summary.purchase_count, total_cents=summary.total_cents,
                top_items=facts["top_items"], generated_at=datetime.now(timezone.utc),
                revision=facts["revision"], prompt_version=PROMPT_VERSION, notice=facts.get("notice", NOTICE))
    if not summary.purchase_count:
        return InsightsOut(**base, status="no_data", cards=[], model_calls=0)
    if not facts["candidates"]:
        return InsightsOut(**base, status="ready", cards=[], model_calls=0)

    settings = get_settings()
    trace = AnalysisTrace(PROMPT_VERSION, settings.llm_model, root_name="spending_insights")
    cards, status = facts["cards"], "fallback"
    try:
        with trace.span("insights_select_suggestions") as span:
            # Explicit manual tracing keeps aggregates and content out of LangSmith.
            with tracing_context(enabled=False):
                output = insights_model().invoke([SystemMessage(content=PROMPT), HumanMessage(content=json.dumps({
                    "selected_dates": [str(body.start_date), str(body.end_date)],
                    "purchase_count": summary.purchase_count, "total_cents": summary.total_cents,
                    "facts": facts["candidates"], "coverage": "partial receipt records only"}, default=str))])
            usage = getattr(output.get("raw"), "usage_metadata", None)
            if isinstance(usage, dict):
                trace.set_usage(span, usage)
            if output.get("parsing_error") or output.get("parsed") is None:
                raise ValueError("Invalid structured selection.")
            selection = InsightSelection.model_validate(output["parsed"])
            cards = apply_selection(facts, selection)
            status = "ready"
    except Exception:
        # Never return provider error text or unvalidated suggestions.
        cards, status = facts["cards"], "fallback"
    # Relevant writes while the model ran invalidate the result, including budgets/reservations.
    try:
        current = load_facts(db, household_id, user_id, body)
        if current["revision"] != facts["revision"]:
            raise HTTPException(409, "Purchases or budget changed. Generate insights again for the latest records.")
    except Exception as exc:
        trace.finish(exc)
        trace.send()
        raise
    trace.outcome = status
    trace.finish()
    background_tasks.add_task(trace.send)
    base["generated_at"] = datetime.now(timezone.utc)
    return InsightsOut(**base, status=status, cards=cards, model_calls=1)
