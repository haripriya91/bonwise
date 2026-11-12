"""Offline planner tests. Temporary databases and simulated provider responses only."""
import tempfile
import unittest
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from fastapi import HTTPException
from pydantic import SecretStr, ValidationError
from sqlalchemy import create_engine, event, insert, select, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session

SETTINGS = SimpleNamespace(database_url=SecretStr("postgresql://offline:offline@localhost/offline"),
    llm_model="offline-test-model", langsmith_tracing=False)

# Imports must not load a developer's .env or connect to their services.
with patch("app.core.config.get_settings", return_value=SETTINGS):
    from app.db.base import Base
    from app.db.models import Household, HouseholdMember, Product, Purchase, PurchaseDraft, PurchaseItem
    from app.db.commerce import HouseholdBudget, BudgetCommitment
    from app.db.planner import WeeklyPlan, PlannerCheckpoint
    from app.services import planner as service, planner_context as evidence
    from app.workflows import weekly_planner as workflow
    from app.ai import planner as model
    from app.schemas.planner import PreparePlan, ApprovePlan, EditPlan, ItemEdit, Version, ChecklistEdit, SettlePlan, Proposal


@compiles(JSONB, "sqlite")
def sqlite_json(element, compiler, **kw):
    return "JSON"


class PlannerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.engine = create_engine("sqlite:///" + self.temp.name + "/planner.db", connect_args={"check_same_thread": False})
        auth_path = self.temp.name + "/auth.db"
        @event.listens_for(self.engine, "connect")
        def attach(connection, record):
            connection.execute("ATTACH DATABASE ? AS auth", (auth_path,))
        Base.metadata.create_all(self.engine)
        self.household, self.other_household, self.user, self.other_user = (uuid4() for _ in range(4))
        self.day = date.today()
        self.milk, self.bread = uuid4(), uuid4()
        with Session(self.engine) as db, db.begin():
            db.execute(insert(Base.metadata.tables["auth.users"]), [{"id": self.user}, {"id": self.other_user}])
            db.add_all([Household(id=self.household, name="Planner Demo A"), Household(id=self.other_household, name="Planner Demo B")])
            db.flush()
            db.add_all([HouseholdMember(household_id=self.household, user_id=self.user, role="owner"),
                HouseholdMember(household_id=self.other_household, user_id=self.other_user, role="owner")])
            db.add_all([Product(id=self.milk, canonical_name="Whole milk 1L", name_de="Vollmilch 1L", category="groceries", base_unit="l", pack_base_quantity=1, attributes={}),
                Product(id=self.bread, canonical_name="Bread", category="groceries", base_unit="kg", pack_base_quantity=Decimal("0.5"), attributes={})])
            db.add(HouseholdBudget(household_id=self.household, client_request_id=uuid4(), creation_digest="0"*64,
                start_date=self.day-timedelta(days=30), end_date=self.day+timedelta(days=30), amount_cents=10000, version=1))
            db.flush()
            for offset in [1, 8, 15, 22]:
                self.purchase(db, self.day-timedelta(days=offset), [(self.milk,"Whole milk 1L","2",100),(self.bread,"Bread","1",149)])
        self.patches = [patch("app.ai.analysis_tracing.get_settings", return_value=SETTINGS),
            patch.object(workflow,"get_settings", return_value=SETTINGS), patch.object(model,"get_settings", return_value=SETTINGS),
            patch.object(model,"select_items", side_effect=lambda context,trace: (Proposal(selections=[
                {"candidate_id": row["candidate_id"], "quantity": row["suggested_quantity"]} for row in context["candidates"][:2]]),"model" if context["candidates"] else "no_history"))]
        for mock in self.patches: mock.start()

    def tearDown(self):
        for mock in reversed(self.patches): mock.stop()
        self.engine.dispose()
        self.temp.cleanup()

    def purchase(self, db, when, lines, household=None):
        household = household or self.household
        draft=PurchaseDraft(household_id=household, created_by=self.user, source_type="seed",status="confirmed",data={})
        db.add(draft); db.flush()
        purchase=Purchase(household_id=household,draft_id=draft.id,purchased_on=when,merchant="Fictional demo",total_cents=sum(int(Decimal(q)*p) for _,_,q,p in lines),adjustments=[])
        db.add(purchase); db.flush()
        for pid,name,q,price in lines:
            db.add(PurchaseItem(purchase_id=purchase.id,product_id=pid,original_text=name,quantity=Decimal(q),quantity_unit="pack",unit_price_cents=price,line_total_cents=int(Decimal(q)*price),category="groceries"))
        db.flush()
        return purchase.id

    def prepare(self, mode="suggested"):
        with Session(self.engine) as db:
            plan=service.prepare(db,self.household,self.user,PreparePlan(client_request_id=uuid4(),planned_on=self.day,mode=mode))
            return workflow.run(db,self.household,self.user,plan.id)

    def read(self, pid):
        with Session(self.engine) as db:
            return service.get_plan(db,self.household,self.user,pid)

    def approve(self, plan, amount=None, **kwargs):
        body=ApprovePlan(expected_version=plan.version, client_request_id=uuid4(),expected_budget_version=plan.budget["version"],
            reservation_cents=amount or plan.content["summary"]["minimum_reservation_cents"],quantities_reviewed=True,**kwargs)
        with Session(self.engine) as db:
            return workflow.run(db,self.household,self.user,plan.id,body), body

    def test_suggested_list_pauses_and_persists_without_reserving(self):
        plan=self.prepare()
        self.assertEqual(plan.status,"draft")
        self.assertEqual(len(plan.content["items"]),2)
        self.assertEqual(plan.reservation_cents,0)
        self.assertIn("4 recorded shopping trips",plan.content["items"][0]["reason"])
        self.assertEqual(plan.content["summary"]["subtotal_cents"],349)
        with Session(self.engine) as db:
            self.assertGreater(db.scalar(select(func.count()).select_from(PlannerCheckpoint)),0)
            self.assertEqual(db.scalar(select(func.count()).select_from(BudgetCommitment)),0)

    def test_approval_survives_new_graph_and_retry_reserves_once(self):
        plan=self.prepare()
        approved, body=self.approve(plan)
        self.assertEqual(approved.status,"approved")
        with Session(self.engine) as db:
            again=workflow.run(db,self.household,self.user,plan.id,body)
            self.assertEqual(again.commitment_id,approved.commitment_id)
            self.assertEqual(db.scalar(select(func.count()).select_from(BudgetCommitment)),1)

    def test_budget_failure_returns_to_review_without_reservation(self):
        plan=self.prepare()
        failed,_=self.approve(plan,amount=999999)
        self.assertEqual(failed.status,"draft")
        self.assertEqual(failed.reservation_cents,0)
        self.assertIn("insufficient",failed.content["notice"])
        final,_=self.approve(failed)
        self.assertEqual(final.status,"approved")

    def test_stale_budget_requires_review(self):
        plan=self.prepare()
        with Session(self.engine) as db,db.begin():
            budget=db.scalar(select(HouseholdBudget));budget.version+=1
        result,_=self.approve(plan)
        self.assertEqual(result.status,"draft")
        self.assertEqual(result.reservation_cents,0)

    def test_price_change_requires_new_approval(self):
        plan=self.prepare()
        with Session(self.engine) as db,db.begin():
            for item in db.scalars(select(PurchaseItem).where(PurchaseItem.product_id==self.milk)):
                item.unit_price_cents=200
        changed,_=self.approve(plan)
        self.assertEqual(changed.status,"draft")
        self.assertIn("Price references changed",changed.content["notice"])
        self.assertEqual(changed.content["summary"]["subtotal_cents"],549)
        approved,_=self.approve(changed)
        self.assertEqual(approved.reservation_cents,549)

    def test_revision_adjusts_one_existing_reservation(self):
        plan,_=self.approve(self.prepare())
        items=[ItemEdit.model_validate({k:v for k,v in item.items() if k in ItemEdit.model_fields}) for item in plan.content["items"]]
        milk=next(item for item in items if item.product_id==self.milk);milk.quantity="3"
        with Session(self.engine) as db:
            revised=service.edit(db,self.household,self.user,plan.id,EditPlan(expected_version=plan.version,planned_on=plan.planned_on,items=items))
        self.assertTrue(revised.is_revision)
        self.assertEqual(revised.reservation_cents,349)
        approved,_=self.approve(revised)
        self.assertEqual(approved.reservation_cents,449)
        self.assertEqual(approved.commitment_id,plan.commitment_id)

    def test_cancel_releases_funds_and_is_idempotent(self):
        plan,_=self.approve(self.prepare())
        with Session(self.engine) as db:
            cancelled=service.cancel(db,self.household,self.user,plan.id,Version(expected_version=plan.version))
            again=service.cancel(db,self.household,self.user,plan.id,Version(expected_version=plan.version))
        self.assertEqual(cancelled.reservation_cents,0)
        self.assertEqual(again.status,"cancelled")

    def test_checklist_does_not_record_spending_or_release_funds(self):
        plan,_=self.approve(self.prepare())
        spent=plan.budget["spent_cents"]
        with Session(self.engine) as db:
            checked=service.checklist(db,self.household,self.user,plan.id,ChecklistEdit(expected_version=plan.version,item_id=plan.content["items"][0]["id"],done=True))
        self.assertEqual(checked.budget["spent_cents"],spent)
        self.assertEqual(checked.reservation_cents,349)

    def test_explicit_purchase_link_releases_reservation(self):
        plan,_=self.approve(self.prepare())
        with Session(self.engine) as db,db.begin():
            pid=self.purchase(db,self.day,[(self.milk,"Whole milk 1L","2",110)])
        with Session(self.engine) as db:
            settled=service.settle(db,self.household,self.user,plan.id,SettlePlan(expected_version=plan.version,purchase_id=pid))
        self.assertEqual(settled.status,"settled")
        self.assertEqual(settled.reservation_cents,0)
        self.assertEqual(settled.budget["spent_cents"],plan.budget["spent_cents"]+220)

    def test_cross_household_access_and_approval_rejected(self):
        plan=self.prepare()
        for op in ["read","cancel","run"]:
            with self.subTest(op=op),Session(self.engine) as db,self.assertRaises(HTTPException) as error:
                if op=="read":service.get_plan(db,self.household,self.other_user,plan.id)
                elif op=="cancel":service.cancel(db,self.household,self.other_user,plan.id,Version(expected_version=plan.version))
                else:workflow.run(db,self.household,self.other_user,plan.id)
            self.assertEqual(error.exception.status_code,404)

    def test_unknown_prices_require_explicit_allowance(self):
        plan=self.prepare()
        items=[ItemEdit(name="Unmatched new item",quantity="1",unit="each")]
        with Session(self.engine) as db:
            plan=service.edit(db,self.household,self.user,plan.id,EditPlan(expected_version=plan.version,planned_on=plan.planned_on,items=items))
        self.assertEqual(plan.content["summary"]["unknown_count"],1)
        failed,_=self.approve(plan,amount=500)
        self.assertEqual(failed.status,"draft")
        approved,_=self.approve(failed,amount=500,acknowledge_unknown_prices=True)
        self.assertEqual(approved.status,"approved")

    def test_invalid_product_id_and_stale_edit_rejected(self):
        plan=self.prepare()
        with Session(self.engine) as db,self.assertRaises(HTTPException) as error:
            service.edit(db,self.household,self.user,plan.id,EditPlan(expected_version=plan.version,planned_on=plan.planned_on,items=[ItemEdit(name="Invented",product_id=uuid4())]))
        self.assertEqual(error.exception.status_code,422)
        with Session(self.engine) as db,self.assertRaises(HTTPException) as error:
            service.edit(db,self.household,self.user,plan.id,EditPlan(expected_version=plan.version-1,planned_on=plan.planned_on,items=[]))
        self.assertEqual(error.exception.status_code,409)

    def test_already_have_reduces_estimate_without_spending(self):
        plan=self.prepare()
        edits=[ItemEdit.model_validate({k:v for k,v in item.items() if k in ItemEdit.model_fields}) for item in plan.content["items"]]
        edits[0].excluded=True
        with Session(self.engine) as db:
            edited=service.edit(db,self.household,self.user,plan.id,EditPlan(expected_version=plan.version,planned_on=plan.planned_on,items=edits))
        self.assertEqual(edited.budget["spent_cents"],plan.budget["spent_cents"])
        self.assertLess(edited.content["summary"]["subtotal_cents"],349)

    def test_prepare_reuses_existing_active_list(self):
        first=self.prepare();second=self.prepare()
        self.assertEqual(first.id,second.id)
        self.assertEqual(first.version,second.version)

    def test_empty_history_allows_manual_draft(self):
        with Session(self.engine) as db,db.begin():
            db.query(PurchaseItem).delete();db.query(Purchase).delete();db.query(PurchaseDraft).delete()
        plan=self.prepare(mode="manual")
        self.assertEqual(plan.content["items"],[])
        self.assertEqual(plan.content["generation_method"],"manual")

    def test_target_buffer_and_manual_estimate(self):
        plan=self.prepare()
        with Session(self.engine) as db:
            edited=service.edit(db,self.household,self.user,plan.id,EditPlan(expected_version=plan.version,
                planned_on=plan.planned_on,items=[ItemEdit(name="Manual bread",quantity="2",unit="pack",manual_unit_price_cents=150)],
                target_cents=350,buffer_cents=100))
        self.assertTrue(edited.content["summary"]["over_target"])
        self.assertEqual(edited.content["summary"]["minimum_reservation_cents"],400)
        failed,_=self.approve(edited,amount=400)
        self.assertEqual(failed.reservation_cents,0)

    def test_text_preview_requires_acceptance_and_cannot_be_reused(self):
        from app.schemas.planner import TextEdit, TextActions, AcceptText
        plan=self.prepare()
        target=plan.content["items"][0]
        parsed=TextActions(actions=[dict(action="remove",item_id=target["id"],name=None,quantity=None,unit=None,quote=target["name"])],clarification_required=False)
        with patch.object(model,"interpret_changes",return_value=parsed),Session(self.engine) as db:
            preview=workflow.preview_changes(db,self.household,self.user,plan.id,TextEdit(expected_version=plan.version,text="Remove "+target["name"],privacy_reviewed=True))
            self.assertEqual(len(self.read(plan.id).content["items"]),2)
            applied=service.accept_text(db,self.household,self.user,plan.id,AcceptText(expected_version=plan.version,token=preview["token"]))
            self.assertEqual(len(applied.content["items"]),1)
            with self.assertRaises(HTTPException):service.accept_text(db,self.household,self.user,plan.id,AcceptText(expected_version=plan.version,token=preview["token"]))

    def test_accepting_removal_of_all_items_is_allowed(self):
        from app.schemas.planner import AcceptText
        plan=self.prepare()
        with Session(self.engine) as db:
            preview=service.save_text_preview(db,self.household,self.user,plan.id,plan.version,[],["Remove all items"],None)
            empty=service.accept_text(db,self.household,self.user,plan.id,AcceptText(expected_version=plan.version,token=preview["token"]))
        self.assertEqual(empty.content["summary"]["item_count"],0)

    def test_text_prompt_identifiers_blocked_before_provider(self):
        with patch.object(model,"client") as provider,self.assertRaises(HTTPException) as error:
            model.interpret_changes("Send milk to test@example.com",[],None)
        self.assertEqual(error.exception.status_code,422)
        provider.assert_not_called()

    def test_quantity_and_quote_grounding_on_text_response(self):
        from app.schemas.planner import TextActions
        from app.ai.analysis_tracing import AnalysisTrace
        trace=AnalysisTrace("test","offline",root_name="planner_text_changes")
        output=TextActions(actions=[dict(action="add",item_id=None,name="milk",quantity="9",unit="pack",quote="2 packs of milk")],clarification_required=False)
        response=SimpleNamespace(status="completed",usage=None,output_parsed=output)
        with patch.object(model,"client") as provider,self.assertRaises(HTTPException):
            provider.return_value.responses.parse.return_value=response
            model.interpret_changes("Add 2 packs of milk",[],trace)

    def test_durable_checkpoint_rejects_another_thread(self):
        from sqlalchemy.orm import sessionmaker
        from app.workflows.planner_checkpoint import PlannerSaver
        plan=self.prepare()
        saver=PlannerSaver(sessionmaker(bind=self.engine),plan.id,self.household,uuid4())
        with self.assertRaises(ValueError):saver.get_tuple({"configurable":{"thread_id":str(uuid4())}})

    def test_scaled_history_price_and_missing_offer_fallback(self):
        plan=self.prepare()
        milk=next(item for item in plan.content["items"] if item["product_id"]==str(self.milk))
        raw=ItemEdit.model_validate({k:v for k,v in milk.items() if k in ItemEdit.model_fields})
        raw.quantity="1.5";raw.unit="l"
        with Session(self.engine) as db:
            edited=service.edit(db,self.household,self.user,plan.id,EditPlan(expected_version=plan.version,planned_on=plan.planned_on,items=[raw]))
        self.assertEqual(edited.content["summary"]["subtotal_cents"],150)

    def test_offer_requires_whole_bundle_and_expiry_recheck(self):
        plan=self.prepare()
        milk=next(item for item in plan.content["items"] if item["product_id"]==str(self.milk))
        raw=ItemEdit.model_validate({k:v for k,v in milk.items() if k in ItemEdit.model_fields})
        raw.quantity="3";raw.offer_id="demo-offer";raw.offer_batch_id="batch"
        card=dict(offer_id="demo-offer",batch_id="batch",product_id=self.milk,required_packs=2,
            minimum_checkout_cents=150,comparison_unit="l",pack_base_quantity="1",valid_until=self.day)
        with patch.object(evidence,"offer_cards",return_value=[card]),Session(self.engine) as db:
            edited=service.edit(db,self.household,self.user,plan.id,EditPlan(expected_version=plan.version,planned_on=plan.planned_on,items=[raw]))
        self.assertEqual(edited.content["items"][0]["estimate"]["checkout_packs"],4)
        self.assertEqual(edited.content["summary"]["subtotal_cents"],300)
        with patch.object(evidence,"offer_cards",return_value=[]):
            changed,_=self.approve(edited,amount=300)
        self.assertEqual(changed.status,"draft")
        self.assertEqual(changed.content["summary"]["unknown_count"],1)

    def test_seed_is_separate_and_idempotent(self):
        import importlib.util
        from sqlalchemy.orm import sessionmaker
        with Session(self.engine) as db,db.begin():
            db.add_all([Product(canonical_name="Ground coffee",category="groceries",base_unit="kg",pack_base_quantity=Decimal("0.5"),attributes={}),
                Product(canonical_name="Rice",category="groceries",base_unit="kg",pack_base_quantity=1,attributes={})])
        path=Path(__file__).resolve().parents[2]/"scripts/seed_planner_demo.py"
        spec=importlib.util.spec_from_file_location("planner_seed",path)
        seed=importlib.util.module_from_spec(spec);spec.loader.exec_module(seed)
        with patch.object(seed,"SessionLocal",sessionmaker(bind=self.engine)):
            preview=seed.run(self.user)
            self.assertTrue(preview["dry_run"])
            result=seed.run(self.user,True);seed.run(self.user,True)
        with Session(self.engine) as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(Purchase).where(Purchase.household_id==self.household)),4)
            self.assertEqual(db.scalar(select(func.count()).select_from(Purchase).where(Purchase.household_id==__import__('uuid').UUID(result["household_id"]))),8)

    def test_model_selection_repairs_invalid_ids_once(self):
        from app.ai.analysis_tracing import AnalysisTrace
        self.patches[-1].stop()
        context={"candidates":[dict(candidate_id="allowed",name="Milk",category="groceries",unit="pack",suggested_quantity="2",
            purchase_count=3,recorded_weeks=3,last_purchased_on="2026-11-01",unit_price_cents=100)],"budget":{"available_cents":1000}}
        trace=AnalysisTrace("test","offline",root_name="weekly_planner")
        with patch.object(model,"parse",side_effect=[Proposal(selections=[dict(candidate_id="invented",quantity="1")]),
                Proposal(selections=[dict(candidate_id="allowed",quantity="2")])]) as parse:
            proposal,method=model.select_items(context,trace)
        self.assertEqual(proposal.selections[0].candidate_id,"allowed")
        self.assertEqual(parse.call_count,2)

    def test_model_repeated_invalid_products_are_rejected(self):
        from app.ai.analysis_tracing import AnalysisTrace
        self.patches[-1].stop()
        context={"candidates":[dict(candidate_id="allowed",name="Milk",category="groceries",unit="pack",suggested_quantity="2",
            purchase_count=3,recorded_weeks=3,last_purchased_on="2026-11-01",unit_price_cents=100)],"budget":None}
        with patch.object(model,"parse",return_value=Proposal(selections=[dict(candidate_id="invented",quantity="1")])) as parse,self.assertRaises(HTTPException):
            model.select_items(context,AnalysisTrace("test","offline",root_name="weekly_planner"))
        self.assertEqual(parse.call_count,2)

    def test_backtest_context_excludes_holdout_week(self):
        with Session(self.engine) as db,db.begin():
            self.purchase(db,self.day+timedelta(days=1),[(self.milk,"Whole milk 1L","9",999)])
        with Session(self.engine) as db,db.begin():
            context=evidence.collect(db,self.household,self.day)
        milk=next(row for row in context["candidates"] if row["product_id"]==str(self.milk))
        self.assertEqual(milk["unit_price_cents"],100)
        self.assertEqual(milk["purchase_count"],4)

    def test_traces_do_not_contain_items_or_household_ids(self):
        from app.ai.analysis_tracing import AnalysisTrace
        traces=[]
        with patch.object(AnalysisTrace,"send",lambda trace: traces.append(trace.payload)):
            self.prepare()
        content=str(traces)
        self.assertNotIn(str(self.household),content)
        self.assertNotIn("Whole milk",content)
        for row in traces[0]:
            self.assertEqual(row["inputs"],{})
            self.assertEqual(row["outputs"],{})

    def test_http_routes_no_store_and_unauthorized_access(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from fastapi.exceptions import RequestValidationError
        from app.core.errors import safe_validation_error
        from app.core.auth import CurrentUser,get_current_user
        from app.api import planner as api
        app=FastAPI();app.include_router(api.router)
        app.add_exception_handler(RequestValidationError,safe_validation_error)
        def database():
            with Session(self.engine) as db:yield db
        app.dependency_overrides[api.get_db]=database
        with TestClient(app) as client:
            path=f"/households/{self.household}/weekly-plans"
            self.assertEqual(client.get(path,params={"week_start":self.day.isoformat()}).status_code,401)
            app.dependency_overrides[get_current_user]=lambda:CurrentUser(id=self.user)
            response=client.post(path,json={"client_request_id":str(uuid4()),"planned_on":self.day.isoformat()})
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.headers["cache-control"],"no-store")
            plan=response.json()
            invalid=client.patch(path+"/"+plan["id"],json={"expected_version":plan["version"],"planned_on":self.day.isoformat(),"items":[{"name":"sensitive-marker","quantity":"bad","unit":"pack"}]})
            self.assertEqual(invalid.status_code,422)
            self.assertNotIn("sensitive-marker",invalid.text)
            app.dependency_overrides[get_current_user]=lambda:CurrentUser(id=self.other_user)
            self.assertEqual(client.get(path+"/"+plan["id"]).status_code,404)

    def test_migration_has_three_private_tables_and_correct_parent(self):
        import importlib.util
        from io import StringIO
        from alembic.migration import MigrationContext
        from alembic.operations import Operations
        path=Path(__file__).resolve().parents[1]/"alembic/versions/d8120a7b49c6_add_weekly_planner.py"
        spec=importlib.util.spec_from_file_location("planner_migration",path)
        migration=importlib.util.module_from_spec(spec);spec.loader.exec_module(migration)
        self.assertEqual(migration.down_revision,"ee1c183bad07")
        output=StringIO();context=MigrationContext.configure(dialect_name="postgresql",opts={"as_sql":True,"output_buffer":output})
        with Operations.context(context):migration.upgrade()
        sql=output.getvalue()
        self.assertEqual(sql.count("ENABLE ROW LEVEL SECURITY"),3)
        self.assertEqual(sql.count("REVOKE ALL"),3)
        self.assertNotIn("DROP TABLE",sql)

    def test_text_dataset_contains_25_well_formed_cases(self):
        import json
        from app.schemas.planner import TextActions
        data=json.loads((Path(__file__).resolve().parents[2]/"data/evaluations/planner_text_edits.json").read_text())
        self.assertEqual(len(data["cases"]),25)
        for case in data["cases"]:
            actions=[{**action,"quote":case["text"]} for action in case["expected"]]
            TextActions(actions=actions,clarification_required=case["clarification_required"])

    def test_added_text_items_are_not_saved_until_acceptance(self):
        from app.schemas.planner import TextEdit,TextActions,AcceptText
        from app.schemas.product_matching import ProductSuggestionOut
        from app.ai import product_matching
        plan=self.prepare()
        actions=TextActions(actions=[dict(action="add",item_id=None,name="oats",quantity="2",unit="pack",quote="2 packs of oats")],clarification_required=False)
        with patch.object(model,"interpret_changes",return_value=actions),patch.object(product_matching,"suggest_products",return_value=ProductSuggestionOut(status="no_match",message="No reliable match")),Session(self.engine) as db:
            preview=workflow.preview_changes(db,self.household,self.user,plan.id,TextEdit(text="Add 2 packs of oats",expected_version=plan.version,privacy_reviewed=True))
            self.assertEqual(len(self.read(plan.id).content["items"]),2)
            accepted=service.accept_text(db,self.household,self.user,plan.id,AcceptText(token=preview["token"],expected_version=plan.version))
        self.assertEqual(len(accepted.content["items"]),3)
        self.assertEqual(accepted.content["items"][-1]["identity_status"],"unmatched")
        self.assertIsNone(accepted.content["items"][-1]["estimate"]["line_total_cents"])

    def test_unknown_unit_price_is_not_invented(self):
        with Session(self.engine) as db,db.begin():
            for item in db.scalars(select(PurchaseItem).where(PurchaseItem.product_id==self.milk)):
                item.unit_price_cents=None
        plan=self.prepare()
        self.assertEqual(plan.content["summary"]["unknown_count"],1)


if __name__=="__main__":unittest.main()
