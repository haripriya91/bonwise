"""Offline facts, authorization, privacy, stale results and simulated model responses."""
import json
import unittest
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import test_planner as fixture
from test_analysis_ranking import purchase
from app.schemas.insights import InsightRequest, InsightSelection
from app.services.insights import apply_selection, build_facts, load_facts
from app.ai import spending_insights as ai


def request(start=date(2026, 11, 1), end=date(2026, 11, 12)):
    return InsightRequest(start_date=start, end_date=end)


def select_suggestions(*rows):
    return InsightSelection(selections=[dict(fact_id=fact, suggestion_id=suggestion) for fact, suggestion in rows])


class InsightFactsTests(unittest.TestCase):
    def facts(self, records, context=None):
        return build_facts(records, context or dict(status="not_set", budget=None), request())

    def test_empty(self):
        f = self.facts([])
        self.assertEqual(f["cards"], [])
        self.assertEqual(f["summary"].purchase_count, 0)

    def test_net_zero_returns_still_have_findings(self):
        f = self.facts([purchase("A", [("Milk", 100)]), purchase("A", [("Milk", -100)])])
        self.assertEqual(f["summary"].total_cents, 0)
        self.assertEqual(len(f["cards"]), 3)
        self.assertNotIn("%", f["cards"][0].detail)

    def test_all_negative_no_share_or_overspending_claim(self):
        f = self.facts([purchase("A", [("Milk", -100)])])
        self.assertNotIn("%", f["cards"][0].detail)
        self.assertNotIn("over this period", str(f["cards"]))

    def test_tied_categories(self):
        row = purchase("A", [("Milk", 100), ("Shirt", 100)])
        row["items"][1]["category"] = "clothing"
        f = self.facts([row])
        self.assertIn("tied", f["cards"][0].title)
        self.assertIn("Groceries", f["cards"][0].detail)
        self.assertIn("Clothing", f["cards"][0].detail)

    def test_mixed_negative_categories_do_not_show_misleading_share(self):
        row = purchase("A", [("Milk", 200), ("Shirt", -100)])
        row["items"][1]["category"] = "clothing"
        self.assertNotIn("%", self.facts([row])["cards"][0].detail)

    def test_top_items_aggregate_across_receipts_and_scope_is_explicit(self):
        f = self.facts([purchase("A", [("Milk", 200), ("Rice", 300)]), purchase("B", [("Milk", 200)])])
        self.assertEqual(f["top_items"][0].total_cents, 400)
        self.assertEqual(f["top_items"][0].purchase_count, 2)
        self.assertIn("entire selected period", f["cards"][1].detail)
        self.assertIn("check_home", f["candidates"][1]["allowed"])

    def test_receipt_adjustments_count_in_categories_not_item_totals(self):
        row = purchase("A", [("Milk", 200)])
        row["adjustments"] = [dict(category="groceries", amount_cents=-50, kind="discount", label="Discount")]
        row["total_cents"] = 150
        f = self.facts([row])
        self.assertEqual(f["summary"].total_cents, 150)
        self.assertEqual(f["top_items"][0].total_cents, 200)

    def test_top_three_tie_is_disclosed(self):
        f = self.facts([purchase("A", [(x, 100) for x in ["A", "B", "C", "D"]])])
        self.assertEqual(len(f["top_items"]), 3)
        self.assertIn("More items tie", f["cards"][1].detail)

    def test_same_confirmed_product_aggregates_money_across_units(self):
        row = purchase("A", [("Milk pack", 200), ("Milk litre", 100)])
        product = str(uuid4())
        for item in row["items"]:item["product_id"] = product
        row["items"][1]["quantity_unit"] = "l"
        f = self.facts([row])
        self.assertEqual(len(f["top_items"]), 1)
        self.assertEqual(f["top_items"][0].total_cents, 300)

    def test_repeated_lines_same_receipt_not_repeated_purchase(self):
        f = self.facts([purchase("A", [("Milk", 100), ("Milk", 100)])])
        self.assertNotIn("check_home", f["candidates"][1]["allowed"])

    def test_evidence_is_bounded_and_links_real_contributors(self):
        rows = [purchase("A", [("Milk", 100)]) for _ in range(25)]
        card = self.facts(rows)["cards"][0]
        self.assertEqual(len(card.evidence), 20)
        self.assertTrue(card.evidence_truncated)
        self.assertTrue({str(e.purchase_id) for e in card.evidence}.issubset({p["id"] for p in rows}))

    def test_budget_uses_full_actual_dates(self):
        b = dict(start_date=date(2026,11,1), end_date=date(2026,11,30), amount_cents=10000,
                 spent_cents=9000, reserved_cents=2000, available_cents=-1000, version=1)
        f = self.facts([purchase("A", [("Milk", 100)])], dict(status="covered", budget=b))
        text = f["cards"][2].detail
        self.assertIn("30.11.2026", text)
        self.assertIn("full budget period", text)
        self.assertIn("within the target", text)
        self.assertNotIn("over this period", text)

    def test_explicit_target_required_for_over_target(self):
        b = dict(start_date=date(2026,11,1), end_date=date(2026,11,30), amount_cents=10000,
                 spent_cents=11000, reserved_cents=0, available_cents=-1000, version=1)
        f = self.facts([purchase("A", [("Milk", 100)])], dict(status="covered", budget=b))
        self.assertIn("over this period's target", f["cards"][2].detail)
        f = self.facts([purchase("A", [("Milk", 100)])], dict(status="partial", budget=None))
        self.assertIn("No single budget", f["cards"][2].detail)

    def test_selection_cannot_invent_evidence_or_figures(self):
        with self.assertRaises(ValidationError):
            InsightSelection.model_validate({"selections": [{"fact_id":"fake", "suggestion_id":"review", "total_cents":1}]})

    def test_duplicate_or_ineligible_selection_rejected(self):
        f = self.facts([purchase("A", [("Milk", 100)])])
        for selection in [select_suggestions(("items","check_home")), select_suggestions(("items","review"),("items","compare")), select_suggestions(("budget","review_budget"))]:
            with self.assertRaises(ValueError):
                apply_selection(f, selection)

    def test_valid_selection_keeps_verified_figures_and_evidence(self):
        f = self.facts([purchase("A", [("Milk", 100)])])
        cards = apply_selection(f, select_suggestions(("items","compare"),("budget","set_target")))
        self.assertEqual(cards[1].detail, f["cards"][1].detail)
        self.assertEqual(cards[1].evidence, f["cards"][1].evidence)
        self.assertEqual(cards[1].action, "offers")

    def test_range_limit(self):
        for start,end in [(date(2026,11,2), date(2026,11,1)), (date(2024,1,1),date(2026,1,1))]:
            with self.assertRaises(ValidationError): request(start,end)


class InsightIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.PlannerTests()
        self.f.setUp()
        self.settings = patch.object(ai, "get_settings", return_value=fixture.SETTINGS)
        self.settings.start()
        self.body = request(self.f.day-timedelta(days=30), self.f.day)
        self.model = Mock()
        self.model.invoke.return_value = dict(parsed=select_suggestions(("repeat_purchases","check_home")), parsing_error=None, raw=None)
        self.provider = patch.object(ai, "insights_model", return_value=self.model)
        self.provider.start()

    def tearDown(self):
        self.provider.stop(); self.settings.stop(); self.f.tearDown()

    def generate(self, body=None, household=None, user=None):
        with Session(self.f.engine) as db:
            return ai.generate(db, household or self.f.household, user or self.f.user, body or self.body, BackgroundTasks())

    def test_success_one_model_call_and_no_record_writes(self):
        with Session(self.f.engine) as db:
            before = db.scalar(select(func.count()).select_from(fixture.Purchase))
        out = self.generate()
        self.assertEqual(out.status, "ready")
        self.assertEqual(out.model_calls, 1)
        self.assertEqual(out.purchase_count, 4)
        self.model.invoke.assert_called_once()
        with Session(self.f.engine) as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(fixture.Purchase)), before)

    def test_empty_never_calls_ai(self):
        out = self.generate(request(self.f.day+timedelta(days=100), self.f.day+timedelta(days=110)))
        self.assertEqual((out.status, out.model_calls, out.cards), ("no_data",0,[]))
        self.model.invoke.assert_not_called()

    def test_unauthorized_household_never_calls_ai(self):
        with self.assertRaises(HTTPException) as caught:
            self.generate(household=self.f.other_household)
        self.assertEqual(caught.exception.status_code, 404)  # Membership guard hides other households.
        self.model.invoke.assert_not_called()

    def test_model_failure_falls_back_without_error_leak(self):
        self.model.invoke.side_effect = RuntimeError("SECRET PROVIDER DETAIL")
        out = self.generate()
        self.assertEqual(out.status, "fallback")
        self.assertGreater(len(out.cards), 0)
        self.assertTrue(all(c.suggestion is None for c in out.cards))
        self.assertNotIn("SECRET", out.model_dump_json())
        self.model.invoke.assert_called_once()

    def test_invalid_model_selection_falls_back(self):
        self.model.invoke.return_value = dict(parsed=select_suggestions(("category","compare")), parsing_error=None)
        self.assertEqual(self.generate().status, "fallback")

    def test_parser_error_falls_back(self):
        self.model.invoke.return_value = dict(parsed=None, parsing_error="bad")
        self.assertEqual(self.generate().status, "fallback")

    def test_model_receives_no_names_ids_or_raw_receipts(self):
        self.generate()
        text = self.model.invoke.call_args.args[0][1].content
        for private in [str(self.f.household),str(self.f.user),str(self.f.milk),"Whole milk", "Fictional demo", "purchase_id", "commitments", "original_text"]:
            self.assertNotIn(private, text)
        self.assertIn("purchase_count", text)

    def test_revision_changes_discard_result(self):
        with Session(self.f.engine) as db:
            facts = load_facts(db,self.f.household,self.f.user,self.body)
        changed = {**facts, "revision":"different"}
        with patch.object(ai, "load_facts", side_effect=[facts,changed]):
            with self.assertRaises(HTTPException) as caught:self.generate()
        self.assertEqual(caught.exception.status_code,409)

    def test_budget_write_changes_revision(self):
        with Session(self.f.engine) as db:
            before = load_facts(db,self.f.household,self.f.user,self.body)["revision"]
            with db.begin():
                b = db.scalar(select(fixture.HouseholdBudget))
                b.amount_cents += 100
                b.version += 1
            after = load_facts(db,self.f.household,self.f.user,self.body)["revision"]
        self.assertNotEqual(before,after)

    def test_endpoint_contract_no_store_and_invalid_dates(self):
        with patch("app.core.config.get_settings",return_value=fixture.SETTINGS):
            from app.api.analysis import router
            from app.core.auth import get_current_user, CurrentUser
            from app.db.session import get_db
        app=FastAPI();app.include_router(router)
        app.dependency_overrides[get_current_user]=lambda:CurrentUser(id=self.f.user)
        def database():
            with Session(self.f.engine) as db:yield db
        app.dependency_overrides[get_db]=database
        with TestClient(app) as client:
            url=f"/households/{self.f.household}/analysis/insights"
            response=client.post(url,json=self.body.model_dump(mode="json"))
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(response.headers["cache-control"],"no-store")
            self.assertEqual(client.post(url,json={"start_date":"2026-11-12","end_date":"2026-11-01"}).status_code,422)
            app.dependency_overrides.pop(get_current_user)
            self.assertEqual(client.post(url,json=self.body.model_dump(mode="json")).status_code,401)
        app.dependency_overrides.clear()
