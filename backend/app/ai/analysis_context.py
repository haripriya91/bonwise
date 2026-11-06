"""Small, validated browser-memory context. No database/global session store."""
from fastapi import HTTPException

from app.core.privacy import redact_text
from app.schemas.analysis import (ContextPeriod, ConversationContext,
                                  PendingAnalysisContext)


def checked_context(context):
    """Validate PII-sensitive filter strings on EVERY incoming/outgoing turn."""
    if context is None:
        return None
    candidates = [context.focus_product, context.focus_merchant]
    for plan in (context.last_plan, context.pending):
        if plan is not None:
            candidates.extend([plan.product_query, plan.merchant_query])
    for text in candidates:
        if text and (redact_text(text)[1] or "[REMOVED]" in text):
            raise HTTPException(422, "Remove personal details from the question and retry.")
    if len(context.model_dump_json().encode("utf-8")) > 6000:
        raise HTTPException(422, "Conversation context is too large. Start a new conversation.")
    return context


def safe_focus(values):
    values = {v for v in values if v}
    if len(values) != 1:
        return None
    text = next(iter(values))
    if len(text) > 120 or redact_text(text)[1] or "[REMOVED]" in text:
        return None
    return text


def next_context(previous, plan, body, payload):
    """Remember resolved intent or a pending clarification, not totals/history."""
    period = ContextPeriod(start_date=body.start_date, end_date=body.end_date)
    status = payload["status"]
    if status in {"answered", "no_data"}:
        context = ConversationContext(last_plan=plan, selected_period=period)
        refs = payload.get("evidence", [])
        if plan.operation == "highest_category":
            categories = {r.category for r in refs if r.category}
            if len(categories) == 1:
                context.focus_category = next(iter(categories))
        if plan.operation in {"items", "quantity"}:
            context.focus_product = safe_focus([r.item_description for r in refs])
        if plan.operation == "merchants":
            context.focus_merchant = safe_focus([r.merchant for r in refs])
        return checked_context(context)
    if (status == "clarification_required" and plan.operation == "clarify"
            and plan.clarification_reason in {"ranking", "period"}):
        context = previous.model_copy(deep=True) if previous else ConversationContext()
        context.pending = PendingAnalysisContext(
            target=plan.clarification_target,
            reason=plan.clarification_reason, category=plan.category,
            product_query=plan.product_query, merchant_query=plan.merchant_query,
            quantity_unit=plan.quantity_unit,
            start_date=plan.start_date or body.start_date,
            end_date=plan.end_date or body.end_date,
            comparison_start_date=plan.comparison_start_date,
            comparison_end_date=plan.comparison_end_date, top_n=plan.top_n)
        context.selected_period = period
        # A pending ranking choice deliberately has no assumed metric field.
        return checked_context(context)
    # Unsupported/test messages and invalid interpretations do not erase context.
    return checked_context(previous)