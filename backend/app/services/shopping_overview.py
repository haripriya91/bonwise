"""Home uses authoritative ledger/reservation figures and the same offer service."""

from datetime import date
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from fastapi import HTTPException
from app.db.commerce import HouseholdBudget
from app.db.models import Purchase, PurchaseDraft
from app.services import budgets, offers


def overview(db, household_id, user_id):
    with db.begin():
        budgets.read_household(db, household_id, user_id)
        today = offers.clock(db, household_id)
        budget = db.scalar(
            select(HouseholdBudget).where(
                HouseholdBudget.household_id == household_id,
                HouseholdBudget.start_date <= today,
                HouseholdBudget.end_date >= today,
            )
        )
        if budget:
            state = budgets.snapshot(db, budget)
            start, end = budget.start_date, budget.end_date
            spent, count = state["spent_cents"], state["purchase_count"]
        else:
            state = None
            start = date(today.year, today.month, 1)
            end = (
                date(today.year + 1, 1, 1)
                if today.month == 12
                else date(today.year, today.month + 1, 1)
            )
            from datetime import timedelta

            end -= timedelta(days=1)
            spent, count = db.execute(
                select(
                    func.coalesce(func.sum(Purchase.total_cents), 0),
                    func.count(Purchase.id),
                )
                .join(
                    PurchaseDraft,
                    (PurchaseDraft.id == Purchase.draft_id)
                    & (PurchaseDraft.household_id == Purchase.household_id),
                )
                .where(
                    Purchase.household_id == household_id,
                    PurchaseDraft.status == "confirmed",
                    Purchase.purchased_on >= start,
                    Purchase.purchased_on <= min(today, end),
                )
            ).one()
        suggested = None
        status = "ready"
        try:
            # A missing optional offers schema must not abort the ledger transaction.
            with db.begin_nested():
                suggested = offers.recommendations_locked(
                    db, household_id, today, limit=3
                )
        except HTTPException as exc:
            if exc.status_code != 503:
                raise
            status = "unavailable"
        except (SQLAlchemyError, ValueError, TypeError, KeyError):
            status = "unavailable"
        return dict(
            as_of=today,
            spending_start=start,
            spending_end=end,
            confirmed_spent_cents=int(spent),
            purchase_count=int(count),
            budget=state,
            offers=suggested,
            offers_status=status,
        )