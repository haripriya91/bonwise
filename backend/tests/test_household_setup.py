"""Offline scope, versioning, proposal and ledger checks. No provider calls."""
import unittest
from unittest.mock import patch
from uuid import uuid4
from fastapi import HTTPException
from sqlalchemy import select, func
from sqlalchemy.orm import Session
import test_planner as fixture

with patch("app.core.config.get_settings", return_value=fixture.SETTINGS):
    from app.db.household_settings import HouseholdSettings, AccountProfile, HouseholdNeed
    from app.db.models import HouseholdMember, Product
    from app.db.commerce import BudgetCommitment
    from app.schemas.household_settings import HouseholdCreate, SettingsEdit, ProfileEdit, SaveNeeds, NeedsPreview, NeedProposal
    from app.schemas.planner import ItemEdit, EditPlan
    from app.services import household_settings as service, seasonal_needs, planner


class HouseholdSetupTests(unittest.TestCase):
    setUp = fixture.PlannerTests.setUp
    tearDown = fixture.PlannerTests.tearDown
    purchase = fixture.PlannerTests.purchase
    prepare = fixture.PlannerTests.prepare

    def settings(self, user=None, hh=None):
        with Session(self.engine) as db:
            return service.read_settings(db, hh or self.household, user or self.user)

    def update(self, version=0, **kw):
        with Session(self.engine) as db:
            return service.update_settings(db, self.household, self.user,
                SettingsEdit(expected_version=version, name="Test household", shopping_day=5, **kw))

    def saved_body(self):
        return SaveNeeds(needs=[dict(id=uuid4(), item=ItemEdit(name="Tea", quantity="1", unit="pack"))])

    def proposal(self, code=None, name="Tea", quantity="1"):
        return NeedProposal(title="A modest seasonal checklist", explanation="Consider a warm drink as an optional addition for your occasion.", suggestions=[dict(product_code=code, name=name,
            reason="Optional for your stated occasion.", quantity=quantity, unit="pack", purpose="drink")])

    def preview(self, plan, proposal=None, user=None, hh=None, version=None, text="A modest winter checklist", allowance=None):
        with patch.object(seasonal_needs, "get_settings", return_value=fixture.SETTINGS), \
             patch("app.ai.analysis_tracing.get_settings", return_value=fixture.SETTINGS), \
             patch.object(seasonal_needs.model, "parse", return_value=proposal or self.proposal()) as provider, \
             Session(self.engine) as db:
            value = seasonal_needs.preview(db, hh or self.household, user or self.user, plan.id,
                NeedsPreview(expected_version=version or plan.version, occasion="seasonal", text=text, privacy_reviewed=True, extra_budget_cents=allowance))
            return value, provider

    def test_defaults_and_no_read_side_effects(self):
        self.assertEqual(self.settings()["version"], 0)
        with Session(self.engine) as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(HouseholdSettings)), 0)

    def test_owner_updates_with_version_and_preserves_finances(self):
        before = self.settings()
        result = self.update(setup_completed=True)
        self.assertEqual(result["shopping_day"], 5)
        self.assertEqual(result["version"], before["version"] + 1)
        self.assertTrue(result["setup_completed"])
        with Session(self.engine) as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(BudgetCommitment)), 0)

    def test_stale_settings_rejected(self):
        self.update()
        with self.assertRaises(HTTPException) as e:
            self.update()
        self.assertEqual(e.exception.status_code, 409)

    def test_nonmember_cannot_read_or_write_settings(self):
        with self.assertRaises(HTTPException):
            self.settings(user=self.other_user)
        with Session(self.engine) as db, self.assertRaises(HTTPException):
            service.update_settings(db, self.household, self.other_user,
                SettingsEdit(expected_version=0, name="No access"))

    def test_member_reads_but_cannot_change_owner_settings(self):
        with Session(self.engine) as db, db.begin():
            db.add(HouseholdMember(household_id=self.household, user_id=self.other_user, role="member"))
        self.assertFalse(self.settings(user=self.other_user)["can_edit"])
        with Session(self.engine) as db, self.assertRaises(HTTPException) as e:
            service.update_settings(db, self.household, self.other_user,
                SettingsEdit(expected_version=0, name="Not owner"))
        self.assertEqual(e.exception.status_code, 403)

    def test_profile_is_account_scoped_and_versioned(self):
        with Session(self.engine) as db:
            result = service.update_profile(db, self.user, ProfileEdit(expected_version=0, display_name="Hari"))
            self.assertEqual(result["version"], 1)
            self.assertEqual(service.read_profile(db, self.other_user)["display_name"], "")
            with self.assertRaises(HTTPException):
                service.update_profile(db, self.user, ProfileEdit(expected_version=0, display_name="Stale"))

    def test_saved_needs_are_idempotent_and_do_not_reserve(self):
        body = self.saved_body()
        with Session(self.engine) as db:
            service.save_needs(db, self.household, self.user, body)
            result = service.save_needs(db, self.household, self.user, body)
            self.assertEqual(len(result["needs"]), 1)
            self.assertEqual(db.scalar(select(func.count()).select_from(BudgetCommitment)), 0)

    def test_saved_need_cross_household_reads_and_delete_rejected(self):
        body = self.saved_body()
        with Session(self.engine) as db:
            service.save_needs(db, self.household, self.user, body)
            with self.assertRaises(HTTPException):
                service.read_needs(db, self.household, self.other_user)
        with Session(self.engine) as db, self.assertRaises(HTTPException):
            service.delete_need(db, self.other_household, self.other_user, body.needs[0].id)

    def test_saved_need_foreign_id_cannot_be_reused(self):
        body = self.saved_body()
        with Session(self.engine) as db:
            service.save_needs(db, self.household, self.user, body)
            with self.assertRaises(HTTPException):
                service.save_needs(db, self.other_household, self.other_user, body)

    def test_saved_need_rejects_personal_information(self):
        body = SaveNeeds(needs=[dict(id=uuid4(), item=ItemEdit(name="test@example.com", quantity="1", unit="pack"))])
        with Session(self.engine) as db, self.assertRaises(HTTPException):
            service.save_needs(db, self.household, self.user, body)

    def test_preview_unknown_price_and_no_persistence(self):
        plan = self.prepare()
        result, provider = self.preview(plan)
        self.assertEqual(result["items"][0]["estimate"]["basis"], "unknown")
        self.assertIsNone(result["items"][0]["estimate"]["line_total_cents"])
        self.assertEqual(result["base_version"], plan.version)
        provider.assert_called_once()
        with Session(self.engine) as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(BudgetCommitment)), 0)
            self.assertEqual(db.scalar(select(func.count()).select_from(HouseholdNeed)), 0)
        with Session(self.engine) as db:
            self.assertEqual(planner.get_plan(db, self.household, self.user, plan.id).version, plan.version)

    def test_preview_explains_idea_basis_and_item_role(self):
        result, provider = self.preview(self.prepare())
        self.assertEqual(result["title"], "A modest seasonal checklist")
        self.assertIn("optional addition", result["explanation"])
        self.assertEqual(result["items"][0]["purpose"], "drink")
        self.assertIn("does not know what is in your kitchen", result["basis"])
        payload = provider.call_args.args[2]
        self.assertIn("existing_draft_items", payload)
        self.assertNotIn("existing_items", payload)
        self.assertNotIn("household_id", payload)
        self.assertNotIn("history", payload)

    def test_preview_rejects_personal_details_in_overview(self):
        proposal = self.proposal().model_copy(update={"explanation": "Contact person@example.com for the party."})
        with self.assertRaises(HTTPException) as caught:
            self.preview(self.prepare(), proposal)
        self.assertEqual(caught.exception.status_code, 422)
        with Session(self.engine) as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(BudgetCommitment)), 0)

    def test_proposal_requires_an_explanation_and_valid_purpose(self):
        from pydantic import ValidationError
        raw = self.proposal().model_dump()
        del raw["explanation"]
        with self.assertRaises(ValidationError):
            NeedProposal.model_validate(raw)
        raw = self.proposal().model_dump()
        raw["suggestions"][0]["purpose"] = "guaranteed pantry stock"
        with self.assertRaises(ValidationError):
            NeedProposal.model_validate(raw)

    def test_cross_household_preview_makes_no_provider_call(self):
        plan = self.prepare()
        with patch.object(seasonal_needs.model, "parse") as provider:
            with self.assertRaises(HTTPException):
                self.preview(plan, user=self.other_user)
            provider.assert_not_called()

    def test_preview_rejects_stale_version(self):
        plan = self.prepare()
        with self.assertRaises(HTTPException) as e:
            self.preview(plan, version=plan.version+1)
        self.assertEqual(e.exception.status_code, 409)

    def test_preview_rejects_unlisted_catalogue_code(self):
        with self.assertRaises(HTTPException) as e:
            self.preview(self.prepare(), self.proposal(code="p9999"))
        self.assertEqual(e.exception.status_code, 422)

    def test_preview_rejects_semantically_invalid_quantity(self):
        with self.assertRaises(HTTPException) as e:
            self.preview(self.prepare(), self.proposal(quantity="-4"))
        self.assertEqual(e.exception.status_code, 422)

    def test_preview_rejects_personal_prompt_before_provider(self):
        with self.assertRaises(HTTPException):
            self.preview(self.prepare(), text="Send shopping to test@example.com")

    def test_suggested_catalogue_identity_uses_sql_and_history(self):
        plan = self.prepare()
        # Remove historical milk from the draft so it can be proposed as an optional need.
        with Session(self.engine) as db:
            row = db.get(fixture.WeeklyPlan, plan.id)
            row.content = {**row.content, "items": []}
            db.commit()
            products = db.scalars(select(Product).order_by(Product.canonical_name).limit(160)).all()
            code = "p"+str(next(i for i,p in enumerate(products) if p.id == self.milk))
        result, _ = self.preview(plan, self.proposal(code=code, name="Invented product name"))
        item = result["items"][0]
        self.assertEqual(item["product_id"], str(self.milk))
        self.assertNotEqual(item["name"], "Invented product name")
        self.assertEqual(item["estimate"]["basis"], "history")

    def test_existing_catalogue_item_is_not_proposed_twice(self):
        plan = self.prepare()
        with Session(self.engine) as db:
            products = db.scalars(select(Product).order_by(Product.canonical_name).limit(160)).all()
            code = "p"+str(next(i for i,p in enumerate(products) if p.id == self.milk))
        result, _ = self.preview(plan, self.proposal(code=code))
        self.assertEqual(result["items"], [])

    def test_new_household_owner_and_idempotence(self):
        body=HouseholdCreate(client_request_id=uuid4(), name="New private household")
        with Session(self.engine) as db:
            first=service.create_household(db,self.user,body)
            again=service.create_household(db,self.user,body)
            self.assertEqual(first,again)
            self.assertTrue(first["can_edit"])
            self.assertFalse(first["setup_completed"])
        with Session(self.engine) as db,self.assertRaises(HTTPException):
            service.read_settings(db,body.client_request_id,self.other_user)

    def test_household_creation_cannot_claim_existing_foreign_id(self):
        body=HouseholdCreate(client_request_id=self.other_household,name="Claim foreign household")
        with Session(self.engine) as db,self.assertRaises(HTTPException) as e:
            service.create_household(db,self.user,body)
        self.assertEqual(e.exception.status_code,404)

    def test_extra_allowance_filters_priced_proposals_without_treating_unknown_as_free(self):
        plan=self.prepare()
        with Session(self.engine) as db:
            row=db.get(fixture.WeeklyPlan,plan.id);row.content={**row.content,"items":[]};db.commit()
            products=db.scalars(select(Product).order_by(Product.canonical_name).limit(160)).all()
            code="p"+str(next(i for i,p in enumerate(products) if p.id==self.milk))
        result,_=self.preview(plan,self.proposal(code=code),allowance=50)
        self.assertEqual(result["items"],[])
        self.assertEqual(result["omitted_over_allowance"],1)
        unknown,_=self.preview(plan,self.proposal(),allowance=50)
        self.assertEqual(unknown["summary"]["unknown_count"],1)
        self.assertIn("affordability remains unverified",unknown["message"])
