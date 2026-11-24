"""Read-only household calendar. No generation, reservations or model calls."""
from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel
from sqlalchemy import BigInteger, exists, false, literal, null, select, union_all
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
        # Only project fields displayed by the calendar. In particular, do not
        # transfer plan content/checkpoints or receipt adjustments into this view.
        purchases = (select(Purchase.id, Purchase.purchased_on, Purchase.merchant, Purchase.total_cents)
            .join(PurchaseDraft, (PurchaseDraft.id == Purchase.draft_id)
                  & (PurchaseDraft.household_id == household_id))
            .where(Purchase.household_id == household_id, PurchaseDraft.status == "confirmed",
                   Purchase.purchased_on >= start_date, Purchase.purchased_on <= end_date)
            .order_by(Purchase.purchased_on, Purchase.id).limit(501).cte("calendar_purchases"))
        plans = (select(WeeklyPlan.id, WeeklyPlan.planned_on, WeeklyPlan.status, WeeklyPlan.commitment_id)
            .where(WeeklyPlan.household_id == household_id,
                   WeeklyPlan.planned_on >= start_date, WeeklyPlan.planned_on <= end_date,
                   WeeklyPlan.status.in_(["draft", "approved"]))
            .order_by(WeeklyPlan.planned_on, WeeklyPlan.id).limit(501).cte("calendar_plans"))
        commitments = (select(BudgetCommitment.id, BudgetCommitment.planned_on,
                              BudgetCommitment.title, BudgetCommitment.amount_cents, BudgetCommitment.status)
            .where(BudgetCommitment.household_id == household_id, BudgetCommitment.status == "open",
                   BudgetCommitment.planned_on >= start_date, BudgetCommitment.planned_on <= end_date)
            .order_by(BudgetCommitment.planned_on, BudgetCommitment.id).limit(501).cte("calendar_commitments"))
        timeline = union_all(
            select(purchases.c.id, purchases.c.purchased_on.label("event_date"),
                   literal("purchase").label("kind"), purchases.c.merchant.label("title"),
                   purchases.c.total_cents.label("amount_cents"), literal("confirmed").label("status"),
                   false().label("represented_by_plan")),
            select(plans.c.id, plans.c.planned_on, literal("plan"), literal("Shopping plan"),
                   null().cast(BigInteger), plans.c.status, false()),
            select(commitments.c.id, commitments.c.planned_on, literal("commitment"),
                   commitments.c.title, commitments.c.amount_cents, commitments.c.status,
                   exists(select(1).select_from(plans).where(plans.c.commitment_id == commitments.c.id))),
        ).subquery()
        counts = {"purchase": 0, "plan": 0, "commitment": 0}
        rows = db.execute(select(timeline).order_by(timeline.c.event_date, timeline.c.kind, timeline.c.id)).mappings()
        for row in rows:
            kind = row["kind"]
            counts[kind] += 1
            if counts[kind] > 500:
                truncated = True
                continue
            # Count the original commitment slice before hiding linked reserves,
            # preserving the previous per-type limit and truncation semantics.
            if row["represented_by_plan"]:
                continue
            events.append(CalendarEvent(id=str(row["id"]), date=row["event_date"], kind=kind,
                title=(row["title"] or "Purchase") if kind == "purchase" else row["title"],
                amount_cents=row["amount_cents"], status=row["status"]))

    return CalendarOut(events=sorted(events, key=lambda e: (e.date, e.kind, e.id)), truncated=truncated)


@router.get("/activity-calendar", response_model=CalendarOut)
def activity(household_id: UUID, response: Response, start_date: date = Query(...),
             end_date: date = Query(...), user: CurrentUser = Depends(get_current_user),
             db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    return calendar_events(db, household_id, user.id, start_date, end_date)
