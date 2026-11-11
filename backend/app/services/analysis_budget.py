"""A budget must cover the requested interval; never fall back to today's budget."""
from fastapi import HTTPException
from sqlalchemy import select
from app.db.commerce import HouseholdBudget
from app.services.budgets import snapshot
from app.services.purchases import require_member


def for_period(db, household_id, user_id, start_date, end_date):
    if not 0 <= (end_date - start_date).days <= 366:
        raise HTTPException(422, "Choose a date range of at most 367 days.")
    with db.begin():
        require_member(db, household_id, user_id)
        rows = db.scalars(select(HouseholdBudget).where(
            HouseholdBudget.household_id == household_id,
            HouseholdBudget.start_date <= end_date,
            HouseholdBudget.end_date >= start_date).order_by(HouseholdBudget.start_date)).all()
        covering = [b for b in rows if b.start_date <= start_date and b.end_date >= end_date]
        if len(covering) == 1:
            return dict(status="covered", budget=snapshot(db, covering[0]))
        return dict(status="partial" if rows else "not_set", budget=None)
