"""Labelled synthetic text tests plus optional time-split history backtest.

No scores are fabricated: --live explicitly makes model calls and costs tokens.
Only numeric aggregate metrics are optionally sent to LangSmith, never cases or records.
"""
import argparse
import json
from datetime import date, timedelta
from pathlib import Path
from uuid import UUID

from langsmith import tracing_context
from sqlalchemy import select

from app.ai import planner as model
from app.ai.analysis_tracing import AnalysisTrace
from app.core.config import get_settings
from app.db.models import Purchase, PurchaseDraft, PurchaseItem
from app.db.session import SessionLocal
from app.services.planner_context import collect, normal
from app.services.purchases import require_member

ROOT=Path(__file__).resolve().parents[1]


def canonical_action(action, items):
    row=action.model_dump() if hasattr(action,"model_dump") else dict(action)
    kind=row["action"]
    if kind=="add":return (kind,normal(row.get("name") or ""),row.get("quantity"),row.get("unit"))
    item=next((item for item in items if item["id"]==row.get("item_id")),None)
    return (kind,row.get("item_id"),row.get("quantity") if kind=="change" else None,
        (row.get("unit") or (item["unit"] if item else None)) if kind=="change" else None)


def text_evaluation(live,limit):
    data=json.loads((ROOT/"data/evaluations/planner_text_edits.json").read_text())
    cases=data["cases"][:limit]
    if not live:
        return dict(text_cases_available=len(data["cases"]),text_cases_selected=len(cases),text_model_calls=0,
            note="Fixtures loaded only. Use --live to measure actual model behavior.")
    trace=AnalysisTrace(model.PROMPT_VERSION,get_settings().llm_model,root_name="planner_evaluation")
    exact,errors=0,0
    try:
        with tracing_context(enabled=False):
            for case in cases:
                try:
                    output=model.interpret_changes(case["text"],data["items"],trace)
                    actual=sorted((canonical_action(action,data["items"]) for action in output.actions),key=str)
                    expected=sorted((canonical_action(action,data["items"]) for action in case["expected"]),key=str)
                    exact+=int(actual==expected and output.clarification_required==case["clarification_required"])
                except Exception:
                    errors+=1
        result=dict(text_cases=len(cases),text_exact_matches=exact,text_failures=errors,
            text_exact_match_rate=exact/len(cases) if cases else 0)
        trace.finish();trace.payload[0]["extra"]["metadata"]["evaluation_metrics"]=result
        return result
    finally:
        if not trace.finished:trace.finish()
        trace.send()


def backtest(household_id,user_id,start,live):
    trace=AnalysisTrace(model.PROMPT_VERSION,get_settings().llm_model,root_name="planner_evaluation")
    try:
        with SessionLocal() as db,db.begin():
            require_member(db,household_id,user_id)
            context=collect(db,household_id,start)
            actual=set(str(pid) for pid in db.scalars(select(PurchaseItem.product_id)
                .join(Purchase,Purchase.id==PurchaseItem.purchase_id)
                .join(PurchaseDraft,(PurchaseDraft.id==Purchase.draft_id)&(PurchaseDraft.household_id==Purchase.household_id))
                .where(Purchase.household_id==household_id,PurchaseDraft.status=="confirmed",Purchase.purchased_on>=start,
                    Purchase.purchased_on<=start+timedelta(days=6),PurchaseItem.product_id.is_not(None))).all())
        lookup={row["candidate_id"]:row for row in context["candidates"]}
        baseline={row["product_id"] for row in context["candidates"][:12] if row["recorded_weeks"]>=2 and row["product_id"]}
        def metrics(predicted):
            hits=len(predicted&actual)
            return dict(precision=hits/len(predicted) if predicted else 0,recall=hits/len(actual) if actual else 0,
                suggested=len(predicted),actual_linked_products=len(actual),hits=hits)
        result={"history_baseline":metrics(baseline),"history_cutoff":context["cutoff"].isoformat(),
            "holdout_week":start.isoformat(),"scope":"Linked product identities only; synthetic history is not independent real-world validation."}
        if live:
            with tracing_context(enabled=False):proposal,_=model.select_items(context,trace)
            predicted={lookup[selection.candidate_id]["product_id"] for selection in proposal.selections if lookup[selection.candidate_id]["product_id"]}
            result["model"]=metrics(predicted)
        trace.finish()
        trace.payload[0]["extra"]["metadata"]["evaluation_metrics"]={
            f"{label}_{key}":value for label in ("history_baseline","model") if label in result for key,value in result[label].items()}
        return result
    finally:
        if not trace.finished:trace.finish()
        trace.send()


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live",action="store_true")
    parser.add_argument("--limit",type=int,default=25)
    parser.add_argument("--household-id",type=UUID)
    parser.add_argument("--user-id",type=UUID)
    parser.add_argument("--week-start",type=date.fromisoformat)
    parser.add_argument("--output",type=Path)
    args=parser.parse_args()
    if not 1<=args.limit<=25:parser.error("--limit must be 1–25")
    result={"live_model_evaluation":args.live,"text":text_evaluation(args.live,args.limit)}
    if args.household_id or args.user_id or args.week_start:
        if not all([args.household_id,args.user_id,args.week_start]):parser.error("Backtesting requires household ID, user ID and week start together")
        if args.week_start.weekday()!=0 or args.week_start+timedelta(days=6)>=date.today():parser.error("Use a completed Monday–Sunday week")
        result["backtest"]=backtest(args.household_id,args.user_id,args.week_start,args.live)
    output=json.dumps(result,indent=2)
    print(output)
    if args.output:args.output.write_text(output+"\n")
