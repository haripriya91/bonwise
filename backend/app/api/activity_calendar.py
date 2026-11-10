"""Read-only household calendar. No generation, reservations or model calls."""
from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import CurrentUser, get_current_user
from app.db.session import get_db
from app.db.models import Purchase, PurchaseDraft
from app.db.commerce import BudgetCommitment
from app.db.planner import WeeklyPlan
from app.services.purchases import require_member


class CalendarEvent(BaseModel):
    id: str
    date: date
    kind: str
    title: str
    amount_cents: int | None = None
    status: str | None = None


class CalendarOut(BaseModel):
    events: list[CalendarEvent]
    truncated: bool


router = APIRouter(prefix="/households/{household_id}", tags=["calendar"])


def calendar_events(db, household_id, user_id, start_date, end_date):
    if not 0 <= (end_date - start_date).days <= 41:
        raise HTTPException(422, "Choose a calendar range of at most 42 days.")
    events = []
    truncated = False
    with db.begin():
        require_member(db, household_id, user_id)
        purchases = db.scalars(select(Purchase).join(PurchaseDraft,
            (PurchaseDraft.id == Purchase.draft_id) & (PurchaseDraft.household_id == household_id))
            .where(Purchase.household_id == household_id, PurchaseDraft.status == "confirmed",
                   Purchase.purchased_on >= start_date, Purchase.purchased_on <= end_date)
            .order_by(Purchase.purchased_on, Purchase.id).limit(501)).all()
        plans = db.scalars(select(WeeklyPlan).where(WeeklyPlan.household_id == household_id,
            WeeklyPlan.planned_on >= start_date, WeeklyPlan.planned_on <= end_date,
            WeeklyPlan.status.in_(["draft", "approved"]))
            .order_by(WeeklyPlan.planned_on, WeeklyPlan.id).limit(501)).all()
        commitments = db.scalars(select(BudgetCommitment).where(
            BudgetCommitment.household_id == household_id, BudgetCommitment.status == "open",
            BudgetCommitment.planned_on >= start_date, BudgetCommitment.planned_on <= end_date)
            .order_by(BudgetCommitment.planned_on, BudgetCommitment.id).limit(501)).all()
        # A shopping reservation is already represented by its plan; do not show it twice.
        plan_commitments = {p.commitment_id for p in plans if p.commitment_id}
        for p in purchases[:500]:
            events.append(CalendarEvent(id=str(p.id), date=p.purchased_on, kind="purchase",
                title=p.merchant or "Purchase", amount_cents=p.total_cents, status="confirmed"))
        for p in plans[:500]:
            events.append(CalendarEvent(id=str(p.id), date=p.planned_on, kind="plan",
                title="Shopping plan", status=p.status))
        for c in commitments[:500]:
            if c.id not in plan_commitments:
                events.append(CalendarEvent(id=str(c.id), date=c.planned_on, kind="commitment",
                    title=c.title, amount_cents=c.amount_cents, status=c.status))
        truncated = any(len(rows) > 500 for rows in (purchases, plans, commitments))
    return CalendarOut(events=sorted(events, key=lambda e: (e.date, e.kind, e.id)), truncated=truncated)


@router.get("/activity-calendar", response_model=CalendarOut)
def activity(household_id: UUID, response: Response, start_date: date = Query(...),
             end_date: date = Query(...), user: CurrentUser = Depends(get_current_user),
             db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    return calendar_events(db, household_id, user.id, start_date, end_date)
