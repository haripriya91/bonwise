"""Calendar isolation and reservation display, using the planner's offline fixture."""
import unittest
from datetime import timedelta
from uuid import uuid4
from fastapi import HTTPException
from sqlalchemy.orm import Session
import test_planner as fixture
from app.api.activity_calendar import calendar_events
from app.db.commerce import BudgetCommitment
from app.db.planner import WeeklyPlan
from app.db.models import PurchaseDraft, Purchase
from sqlalchemy import select


class CalendarTests(unittest.TestCase):
    setUp = fixture.PlannerTests.setUp
    tearDown = fixture.PlannerTests.tearDown
    purchase = fixture.PlannerTests.purchase

    def read(self, user=None, household=None):
        with Session(self.engine) as db:
            return calendar_events(db,household or self.household,user or self.user,
                                   self.day-timedelta(days=6),self.day)

    def test_only_confirmed_household_purchases(self):
        with Session(self.engine) as db, db.begin():
            self.purchase(db,self.day,[(self.milk,"Other household milk","1",100)],self.other_household)
            pid=self.purchase(db,self.day,[(self.milk,"Unconfirmed","1",100)])
            p=db.get(Purchase,pid);db.get(PurchaseDraft,p.draft_id).status="pending"
        result=self.read()
        self.assertEqual(len(result.events),1)  # Only the fixture's trip yesterday.
        self.assertEqual(result.events[0].kind,"purchase")
        self.assertFalse(result.truncated)

    def test_membership_required(self):
        with self.assertRaises(HTTPException) as caught:
            self.read(user=self.other_user)
        self.assertEqual(caught.exception.status_code,404)

    def test_plan_commitment_is_not_displayed_twice(self):
        from app.db.commerce import HouseholdBudget
        with Session(self.engine) as db, db.begin():
            budget=db.scalars(select(HouseholdBudget)).first()
            c=BudgetCommitment(household_id=self.household,budget_id=budget.id,
                client_request_id=uuid4(),creation_digest="0"*64,title="Shopping reserve",
                planned_on=self.day,amount_cents=500,status="open")
            db.add(c);db.flush()
            db.add(WeeklyPlan(household_id=self.household,client_request_id=uuid4(),
                week_start=self.day-timedelta(days=self.day.weekday()),planned_on=self.day,
                status="approved",phase="approved",content={},commitment_id=c.id))
            db.add(BudgetCommitment(household_id=self.household,budget_id=budget.id,
                client_request_id=uuid4(),creation_digest="1"*64,title="Insurance",
                planned_on=self.day,amount_cents=8000,status="open"))
        result=self.read()
        self.assertEqual(sum(e.kind=="plan" for e in result.events),1)
        self.assertEqual([e.title for e in result.events if e.kind=="commitment"],["Insurance"])

    def test_unbounded_range_rejected(self):
        with Session(self.engine) as db, self.assertRaises(HTTPException):
            calendar_events(db,self.household,self.user,self.day,self.day+timedelta(days=42))
