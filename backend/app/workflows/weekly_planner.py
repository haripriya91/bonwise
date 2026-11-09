"""Bounded LangGraph proposal -> validation -> durable review -> approval."""
from typing import TypedDict
from uuid import UUID

from fastapi import HTTPException
from langgraph.graph import StateGraph, START, END
from langgraph.types import Command, interrupt
from langsmith import tracing_context
from sqlalchemy.orm import sessionmaker
from pydantic import ValidationError

from app.ai.analysis_tracing import AnalysisTrace
from app.ai import planner as model
from app.core.config import get_settings
from app.schemas.planner import ApprovePlan, ItemEdit
from app.services import planner as service
from app.workflows.planner_checkpoint import PlannerSaver


class State(TypedDict, total=False):
    plan_id: str
    mode: str
    selections: list[dict]
    method: str
    approved: bool


def build_graph(sessions, household_id, user_id, plan_id, token, thread_id, trace):
    def load_context(state):
        with trace.span("planner_load_context"), sessions() as db:
            service.context(db, household_id, user_id, plan_id)
        return {}

    def propose(state):
        with sessions() as db:
            context = service.context(db, household_id, user_id, plan_id)
        proposal, method = model.select_items(context, trace)
        return {"selections": proposal.model_dump(mode="json")["selections"], "method": method}

    def validate(state):
        from app.schemas.planner import Proposal
        with trace.span("planner_validate_list"), sessions() as db:
            service.store_proposal(db, household_id, user_id, plan_id, token,
                Proposal(selections=state["selections"]), state["method"])
        return {"selections": []}  # No history payload is retained in graph state.

    def review(state):
        decision = interrupt({"plan_id": str(plan_id), "action": "review_shopping_list"})
        body = ApprovePlan.model_validate(decision)
        with trace.span("planner_recheck_and_reserve"), sessions() as db:
            try:
                approved = service.approve(db, household_id, user_id, plan_id, token, body)
            except HTTPException as error:
                if error.status_code not in {409, 422}:
                    raise
                # A failed financial check commits no reservation. Return to a new
                # review interrupt so corrected decisions can resume safely.
                with db.begin():
                    plan = service.load(db, household_id, user_id, plan_id, lock=True)
                    service.ensure_idle(plan, token)
                    plan.content = {**plan.content, "notice": str(error.detail)}
                    service.touch(plan)
                approved = False
        return {"approved": approved}

    builder = StateGraph(State)
    builder.add_node("load_context", load_context)
    builder.add_node("propose", propose)
    builder.add_node("validate", validate)
    builder.add_node("review", review)
    builder.add_conditional_edges(START, lambda state: "load_context" if state["mode"] == "generate" else "review")
    builder.add_edge("load_context", "propose")
    builder.add_edge("propose", "validate")
    builder.add_edge("validate", "review")
    builder.add_conditional_edges("review", lambda state: END if state.get("approved") else "review")
    return builder.compile(checkpointer=PlannerSaver(sessions, plan_id, household_id, thread_id))


def run(db, household_id, user_id, plan_id, approval=None):
    if approval:
        cached = service.approval_already_saved(db, household_id, user_id, plan_id, approval)
        if cached:
            return cached
    current = service.get_plan(db, household_id, user_id, plan_id)
    if current.status != "draft":
        if approval:
            raise HTTPException(409, "This list is already approved or closed. Reload it.")
        return current
    trace = AnalysisTrace(model.PROMPT_VERSION, get_settings().llm_model, root_name="weekly_planner")
    sessions = sessionmaker(bind=db.get_bind(), expire_on_commit=False)
    token = None
    failed = True
    try:
        token, thread_id = service.start_run(db, household_id, user_id, plan_id)
        with tracing_context(enabled=False):
            graph = build_graph(sessions, household_id, user_id, plan_id, token, thread_id, trace)
            config = {"configurable": {"thread_id": str(thread_id)}, "recursion_limit": 12}
            state = graph.get_state(config)
            if not state.next:
                mode = "generate" if current.phase in {"preparing", "retry_required"} and not current.content.get("items") else "review"
                graph.invoke({"plan_id": str(plan_id), "mode": mode}, config=config, durability="sync")
            elif current.phase in {"preparing", "retry_required"}:
                graph.invoke(None, config=config, durability="sync")
            if approval:
                graph.invoke(Command(resume=approval.model_dump(mode="json")), config=config, durability="sync")
            trace.outcome = "approved" if graph.get_state(config).values.get("approved") else "awaiting_review"
        trace.finish()
        failed = False
    except ValidationError as error:
        trace.finish(error)
        raise HTTPException(422, "Check the requested item quantities and units, or edit the list directly.") from None
    except Exception as error:
        trace.finish(error)
        raise
    finally:
        if token:
            try:
                service.finish_run(db, household_id, user_id, plan_id, token, error=failed)
            except Exception:
                if not failed:
                    raise
        trace.send()
    return service.get_plan(db, household_id, user_id, plan_id)


def preview_changes(db, household_id, user_id, plan_id, body):
    trace = AnalysisTrace(model.PROMPT_VERSION, get_settings().llm_model, root_name="planner_text_changes")
    try:
        current = service.get_plan(db, household_id, user_id, plan_id)
        if current.status not in {"draft", "approved"}:
            raise HTTPException(409, "This list is closed.")
        if current.version != body.expected_version:
            raise HTTPException(409, "This list changed. Reload it before interpreting changes.")
        items = [ItemEdit.model_validate({key: value for key, value in item.items() if key in ItemEdit.model_fields}).model_dump(mode="json")
            for item in current.content.get("items", [])]
        with tracing_context(enabled=False):
            actions = model.interpret_changes(body.text, items, trace)
            changes, clarification = [], None
            matching_attempts = 0
            if actions.clarification_required or not actions.actions:
                clarification = "Name the item and the change, for example: add 2 packs of milk, remove rice, or change bread to 1 pack. Use Compare alternatives for product swaps."
            for action in actions.actions if not clarification else []:
                item = next((item for item in items if item["id"] == action.item_id), None)
                if action.action == "add":
                    raw = ItemEdit(name=action.name, quantity=action.quantity if action.unit else None,
                        unit=action.unit if action.quantity else None).model_dump(mode="json")
                    # Reuse the existing retrieval-assisted matching flow. Ambiguous
                    # matches stay unmatched; the user can choose a product explicitly.
                    from app.ai.product_matching import suggest_products
                    from app.schemas.product_matching import ProductSuggestionInput
                    if matching_attempts < 2:
                        matching_attempts += 1
                        try:
                            result = suggest_products(db, household_id, user_id, ProductSuggestionInput(
                                original_text=action.name, category="groceries", quantity_unit=action.unit or "pack"))
                            if result.status == "suggested":
                                raw["product_id"] = str(result.suggested_product_id)
                        except HTTPException as error:
                            if error.status_code not in {422, 502, 503}:
                                raise
                    items.append(raw)
                    changes.append(f"Add {raw['name']}" + (f" — {raw['quantity']} {raw['unit']}" if raw["quantity"] else " — quantity to confirm"))
                elif item is None:
                    clarification = "Which item do you mean? Use its name as shown in the list."
                    break
                elif action.action == "remove":
                    changes.append(f"Remove {item['name']}")
                    items.remove(item)
                elif action.action == "already_have":
                    item["excluded"] = True
                    changes.append(f"Already have: {item['name']} — exclude from this shop")
                elif action.action == "change":
                    if action.quantity is None or not (action.unit or item.get("unit")):
                        clarification = f"How much {item['name']} would you like, and in which unit?"
                        break
                    before = f"{item.get('quantity') or '?'} {item.get('unit') or ''}"
                    item["quantity"], item["unit"] = action.quantity, action.unit or item["unit"]
                    ItemEdit.model_validate(item)
                    changes.append(f"{item['name']}: {before} → {item['quantity']} {item['unit']}")
            if len(items) > 40:
                raise HTTPException(422, "Keep the shopping list to at most 40 items.")
            result = service.save_text_preview(db, household_id, user_id, plan_id, body.expected_version,
                items, changes, clarification)
        trace.outcome = "clarification_required" if clarification else "answered"
        trace.finish()
        return result
    except ValidationError as error:
        trace.finish(error)
        raise HTTPException(422, "Check the requested quantities and units, or edit the items directly.") from None
    except Exception as error:
        trace.finish(error)
        raise
    finally:
        trace.send()
