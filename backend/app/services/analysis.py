"""Read-only, household-scoped snapshot. No SQL or identifiers from the model."""
from sqlalchemy import select

from fastapi import HTTPException
from pydantic import ValidationError

from app.core.privacy import redact_text
from app.db.models import Purchase, PurchaseDraft, PurchaseItem
from app.schemas.purchase import AdjustmentInput
from app.services.purchases import require_member

MAX_PURCHASES = 2000
MAX_ITEMS = 100000


def safe_text(value):
    return redact_text(value)[0] if value else None


def load_snapshot(db, household_id, user_id, start_date, end_date):
    if end_date < start_date or (end_date - start_date).days > 366:
        raise HTTPException(422, "Choose a date range of at most 367 days.")

    with db.begin():
        require_member(db, household_id, user_id)
        purchases = db.scalars(
            select(Purchase)
            .join(PurchaseDraft, (PurchaseDraft.id == Purchase.draft_id)
                  & (PurchaseDraft.household_id == Purchase.household_id))
            .where(Purchase.household_id == household_id,
                   Purchase.purchased_on >= start_date,
                   Purchase.purchased_on <= end_date,
                   PurchaseDraft.status == "confirmed")
            .order_by(Purchase.purchased_on.desc(), Purchase.id.desc())
            .limit(MAX_PURCHASES + 1)
        ).all()
        if len(purchases) > MAX_PURCHASES:
            raise HTTPException(422, "Too many purchases; choose a shorter period.")

        ids = [p.id for p in purchases]
        items = db.scalars(
            select(PurchaseItem).join(Purchase, Purchase.id == PurchaseItem.purchase_id)
            .where(Purchase.household_id == household_id,
                   PurchaseItem.purchase_id.in_(ids))
            .order_by(PurchaseItem.purchase_id, PurchaseItem.id)
            .limit(MAX_ITEMS + 1)
        ).all() if ids else []
        if len(items) > MAX_ITEMS:
            raise HTTPException(422, "Too many items; choose a shorter period.")

        grouped = {p.id: [] for p in purchases}
        for item in items:
            grouped[item.purchase_id].append({
                "id": str(item.id),
                "product_id": str(item.product_id) if item.product_id else None,
                "description": safe_text(item.original_text),
                "quantity": str(item.quantity),
                "quantity_unit": item.quantity_unit,
                "unit_price_cents": item.unit_price_cents,
                "line_total_cents": item.line_total_cents,
                "category": item.category,
            })

        records = []
        for p in purchases:
            if not isinstance(p.adjustments, list) or len(p.adjustments) > 30:
                raise HTTPException(500, "Purchase records could not be reconciled.")
            try:
                adjustments = [AdjustmentInput.model_validate(a) for a in p.adjustments]
            except (ValidationError, TypeError):
                raise HTTPException(500, "Purchase records could not be reconciled.") from None
            lines = grouped[p.id]
            if (p.currency != "EUR" or not lines or
                sum(i["line_total_cents"] for i in lines)
                + sum(a.amount_cents for a in adjustments) != p.total_cents):
                raise HTTPException(500, "Purchase records could not be reconciled.")
            records.append({
                "id": str(p.id), "merchant": safe_text(p.merchant),
                "purchased_on": p.purchased_on, "total_cents": p.total_cents,
                "items": lines,
                "adjustments": [{"category": a.category, "amount_cents": a.amount_cents,
                                 "kind": a.kind, "label": safe_text(a.label)}
                                for a in adjustments],
            })
    return records
