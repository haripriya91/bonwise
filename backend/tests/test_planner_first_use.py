"""First-time households must not accumulate automatically generated empty plans."""
import unittest
from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

import test_planner as fixture
from app.schemas.planner import PreparePlan, Version
from app.services import planner as service
from app.workflows import weekly_planner as workflow


class FirstUseTests(unittest.TestCase):
    setUp = fixture.PlannerTests.setUp
    tearDown = fixture.PlannerTests.tearDown
    purchase = fixture.PlannerTests.purchase
    read = fixture.PlannerTests.read

    def clear(self):
        with Session(self.engine) as db, db.begin():
            db.execute(delete(fixture.PurchaseItem)); db.execute(delete(fixture.Purchase)); db.execute(delete(fixture.PurchaseDraft))

    def body(self, mode="suggested", request_id=None):
        return PreparePlan(client_request_id=request_id or uuid4(), planned_on=self.day, mode=mode)

    def create(self, body=None):
        with Session(self.engine) as db:
            return service.prepare(db,self.household,self.user,body or self.body())

    def add_one(self):
        with Session(self.engine) as db, db.begin():
            self.purchase(db,self.day-timedelta(days=5),[(None,"Milk","1",150)])

    def legacy(self):
        plan=self.create(self.body("manual"))
        with Session(self.engine) as db,db.begin():
            row=db.get(fixture.WeeklyPlan,plan.id)
            row.version=2
            row.content={**row.content,"generation_method":"no_history"}
        return self.read(plan.id)

    def test_listing_first_user_creates_nothing(self):
        self.clear()
        with Session(self.engine) as db:
            self.assertEqual(service.list_plans(db,self.household,self.user,self.day),{"plans":[]})
            self.assertEqual(db.scalar(select(func.count()).select_from(fixture.WeeklyPlan)),0)

    def test_no_history_suggestion_creates_no_draft_or_checkpoint(self):
        self.clear()
        with self.assertRaises(HTTPException) as caught:self.create()
        self.assertEqual(caught.exception.status_code,409)
        with Session(self.engine) as db:
            for table in [fixture.WeeklyPlan,fixture.PlannerCheckpoint,fixture.BudgetCommitment]:
                self.assertEqual(db.scalar(select(func.count()).select_from(table)),0)

    def test_manual_draft_requires_explicit_mode_and_skips_model(self):
        self.clear()
        plan=self.create(self.body("manual"))
        with patch.object(workflow.model,"select_items") as model:
            with Session(self.engine) as db:result=workflow.run(db,self.household,self.user,plan.id)
        model.assert_not_called()
        self.assertEqual((result.phase,result.content["generation_method"]),("review","manual"))
        self.assertEqual(result.reservation_cents,0)

    def test_one_unmatched_purchase_is_enough(self):
        self.clear();self.add_one()
        plan=self.create()
        with Session(self.engine) as db:result=workflow.run(db,self.household,self.user,plan.id)
        self.assertEqual(len(result.content["items"]),1)
        self.assertEqual(result.content["items"][0]["name"],"Milk")
        self.assertEqual(result.reservation_cents,0)

    def test_legacy_empty_plan_reuses_id_and_restarts_current_history(self):
        self.clear();old=self.legacy();self.add_one()
        with Session(self.engine) as db:
            old_thread=db.get(fixture.WeeklyPlan,old.id).thread_id
        plan=self.create()
        self.assertEqual(plan.id,old.id)
        self.assertEqual(plan.phase,"preparing")
        with Session(self.engine) as db:
            self.assertNotEqual(db.get(fixture.WeeklyPlan,plan.id).thread_id,old_thread)
            self.assertEqual(db.scalar(select(func.count()).select_from(fixture.WeeklyPlan)),1)
        with Session(self.engine) as db:result=workflow.run(db,self.household,self.user,plan.id)
        self.assertEqual(len(result.content["items"]),1)

    def test_legacy_retry_same_request_also_recovers(self):
        self.clear();old=self.legacy();self.add_one()
        with Session(self.engine) as db:request_id=db.get(fixture.WeeklyPlan,old.id).client_request_id
        result=self.create(self.body(request_id=request_id))
        self.assertEqual((result.id,result.phase),(old.id,"preparing"))

    def test_edited_empty_draft_is_not_restarted(self):
        self.clear();old=self.legacy();self.add_one()
        with Session(self.engine) as db,db.begin():
            row=db.get(fixture.WeeklyPlan,old.id);row.version=3
            row.content={**row.content,"buffer_cents":250}
        result=self.create()
        self.assertEqual((result.phase,result.version),("review",3))
        self.assertEqual(result.content["buffer_cents"],250)

    def test_manual_draft_not_overwritten_by_suggestion_prepare(self):
        self.clear();manual=self.create(self.body("manual"));self.add_one()
        result=self.create()
        self.assertEqual(result.id,manual.id)
        self.assertEqual(result.content["generation_method"],"manual")
        self.assertEqual(result.content["items"],[])

    def test_refresh_without_history_does_not_mutate_plan(self):
        self.clear();old=self.legacy()
        with Session(self.engine) as db,self.assertRaises(HTTPException):
            service.refresh_empty(db,self.household,self.user,old.id,Version(expected_version=old.version))
        result=self.read(old.id)
        self.assertEqual((result.version,result.phase),(old.version,old.phase))

    def test_cross_household_prepare_is_rejected(self):
        with Session(self.engine) as db,self.assertRaises(HTTPException) as caught:
            service.prepare(db,self.household,self.other_user,self.body("manual"))
        self.assertEqual(caught.exception.status_code,404)
