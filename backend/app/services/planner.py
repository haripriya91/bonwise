"""Plan lifecycle. All financial changes are exact, authorized transactions."""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import select

from app.core.privacy import has_detectable_pii
from app.db.models import Purchase, PurchaseDraft, Product
from app.db.commerce import BudgetCommitment, HouseholdBudget
from app.db.planner import WeeklyPlan
from app.schemas.commerce import CommitmentCreate
from app.schemas.planner import ItemEdit, PlanOut, EditPlan
from app.services import budgets
from app.services.purchases import require_member, digest
from app.services import planner_context as evidence


def monday(day):
    return day - timedelta(days=day.weekday())


def version(plan, expected):
    if plan.version != expected:
        raise HTTPException(409, "This shopping list changed. Reload it before continuing.")


def load(db, household_id, user_id, plan_id, lock=False):
    if lock:
        budgets.lock_household(db, household_id, user_id)
    else:
        require_member(db, household_id, user_id)
    query = select(WeeklyPlan).where(WeeklyPlan.id == plan_id, WeeklyPlan.household_id == household_id)
    plan = db.scalar(query.with_for_update() if lock else query)
    if plan is None:
        raise HTTPException(404, "Shopping list not found.")
    return plan


def commitment(db, plan):
    if not plan.commitment_id:
        return None
    return db.scalar(select(BudgetCommitment).where(BudgetCommitment.id == plan.commitment_id,
        BudgetCommitment.household_id == plan.household_id))


def actual_status(db, plan):
    reservation = commitment(db, plan)
    if reservation and reservation.status in {"cancelled", "settled"}:
        return reservation.status
    return plan.status


def snapshot(db, plan):
    reservation = commitment(db, plan)
    budget = db.scalar(select(HouseholdBudget).where(HouseholdBudget.household_id == plan.household_id,
        HouseholdBudget.start_date <= plan.planned_on, HouseholdBudget.end_date >= plan.planned_on))
    return PlanOut(id=plan.id, household_id=plan.household_id, week_start=plan.week_start,
        planned_on=plan.planned_on, status=actual_status(db, plan), phase=plan.phase, version=plan.version,
        content=plan.content, budget=budgets.snapshot(db, budget) if budget else None,
        reservation_cents=reservation.amount_cents if reservation and reservation.status == "open" else 0,
        commitment_id=plan.commitment_id,
        is_revision=bool(reservation and reservation.status == "open" and plan.status == "draft"))


def touch(plan):
    plan.version += 1
    plan.updated_at = datetime.now(timezone.utc)
    plan.pending_text = None


def ensure_idle(plan, token=None):
    if token and plan.run_token == token:
        return
    until = plan.run_until
    if until and until.replace(tzinfo=timezone.utc) > datetime.now(timezone.utc):
        raise HTTPException(409, "The shopping assistant is still working on this list. Wait, then reload.")
    if token:
        raise HTTPException(409, "This planner run expired. Reload the list.")


def list_plans(db, household_id, user_id, week_start):
    with db.begin():
        require_member(db, household_id, user_id)
        rows = db.scalars(select(WeeklyPlan).where(WeeklyPlan.household_id == household_id,
            WeeklyPlan.week_start == monday(week_start)).order_by(WeeklyPlan.created_at.desc()).limit(20)).all()
        return {"plans": [snapshot(db, row) for row in rows]}


def get_plan(db, household_id, user_id, plan_id):
    with db.begin():
        return snapshot(db, load(db, household_id, user_id, plan_id))


def untouched_no_history(plan):
    """Only the original automatic empty result; never an edited/manual/approved list."""
    return (plan.status == "draft" and plan.phase == "review" and plan.version == 2
            and not plan.commitment_id and not plan.content.get("items")
            and plan.content.get("generation_method") == "no_history"
            and plan.content.get("target_cents") is None
            and not plan.content.get("buffer_cents") and not plan.pending_text)


def require_history(db, household_id, planned_on):
    if not evidence.collect(db, household_id, planned_on)["candidates"]:
        raise HTTPException(409, "No eligible confirmed purchases are available for this shopping date. Add a purchase or choose Start a manual list.")


def reuse_for_preparation(db, household_id, plan, mode):
    if mode == "suggested" and untouched_no_history(plan):
        ensure_idle(plan)
        require_history(db, household_id, plan.planned_on)
        # Reset the paused empty workflow, preserving identity and avoiding duplicate plans.
        plan.thread_id = uuid4()
        plan.phase = "preparing"
        plan.content = {**plan.content, "notice": None}
        touch(plan)
        db.flush()
    return snapshot(db, plan)


def prepare(db, household_id, user_id, body):
    with db.begin():
        budgets.lock_household(db, household_id, user_id)
        today = evidence.offers.clock(db, household_id)
        if not today <= body.planned_on <= today + timedelta(days=13):
            raise HTTPException(422, "Choose a shopping date today or within the next 13 days.")
        prior = db.scalar(select(WeeklyPlan).where(WeeklyPlan.household_id == household_id,
            WeeklyPlan.client_request_id == body.client_request_id))
        if prior:
            if prior.planned_on != body.planned_on:
                raise HTTPException(409, "This request was already used for another shopping date.")
            return reuse_for_preparation(db, household_id, prior, body.mode)
        rows = db.scalars(select(WeeklyPlan).where(WeeklyPlan.household_id == household_id,
            WeeklyPlan.week_start == monday(body.planned_on)).order_by(WeeklyPlan.created_at.desc())).all()
        for row in rows:
            if actual_status(db, row) in {"draft", "approved"}:
                return reuse_for_preparation(db, household_id, row, body.mode)
        if body.mode == "suggested":
            require_history(db, household_id, body.planned_on)
        row = WeeklyPlan(household_id=household_id, client_request_id=body.client_request_id,
            week_start=monday(body.planned_on), planned_on=body.planned_on, status="draft",
            phase="review" if body.mode == "manual" else "preparing",
            content={"items": [], "target_cents": None, "buffer_cents": 0, "notice": None,
                     "generation_method": "manual" if body.mode == "manual" else None})
        db.add(row)
        db.flush()
        return snapshot(db, row)


def start_run(db, household_id, user_id, plan_id):
    with db.begin():
        plan = load(db, household_id, user_id, plan_id, lock=True)
        ensure_idle(plan)
        if actual_status(db, plan) in {"cancelled", "settled"}:
            raise HTTPException(409, "This list is closed.")
        token = uuid4()
        plan.run_token, plan.run_until = token, datetime.now(timezone.utc) + timedelta(minutes=3)
        db.flush()
        return token, plan.thread_id


def finish_run(db, household_id, user_id, plan_id, token, error=False):
    with db.begin():
        plan = load(db, household_id, user_id, plan_id, lock=True)
        if plan.run_token == token:
            plan.run_token, plan.run_until = None, None
            if error and plan.phase == "preparing":
                plan.phase = "retry_required"
                plan.content = {**plan.content, "notice": "The assistant could not prepare the list. Retry or add your items directly."}


def context(db, household_id, user_id, plan_id):
    with db.begin():
        plan = load(db, household_id, user_id, plan_id)
        return evidence.collect(db, household_id, plan.planned_on)


def store_proposal(db, household_id, user_id, plan_id, token, proposal, method):
    with db.begin():
        plan = load(db, household_id, user_id, plan_id, lock=True)
        ensure_idle(plan, token)
        if plan.content.get("items"):
            return
        ctx = evidence.collect(db, household_id, plan.planned_on)
        candidates = {row["candidate_id"]: row for row in ctx["candidates"]}
        items = []
        for selection in proposal.selections:
            candidate = candidates.get(selection.candidate_id)
            if not candidate:
                raise HTTPException(422, "A suggested item is no longer supported by your purchase records.")
            # The proposed quantity comes from the supplied last-purchase fact only.
            count = candidate["suggested_quantity"] if selection.quantity is not None else None
            raw = ItemEdit(name=candidate["name"], product_id=candidate["product_id"],
                quantity=count, unit=candidate["unit"] if count else None).model_dump(mode="json")
            original = {**raw, "reason": candidate["reason"], "history_candidate_id": candidate["candidate_id"]}
            items.append(evidence.price_item(db, household_id, plan.planned_on, raw, ctx, original))
        plan.content = {**plan.content, "items": items, "generation_method": method,
            "summary": evidence.totals(items, 0, None),
            "notice": "Check the suggested quantities. Previous purchases do not tell us what is at home." if items
                else ("There are recorded purchases, but no recurring list was selected. Review purchase history or add items manually." if candidates else "No eligible confirmed purchases were found in the 90-day history window. Add items manually to start.")}
        if method == "review_required":
            plan.content["notice"] = "The suggested estimates exceed available funds. Reduce the list before approval."
        plan.phase = "review"
        touch(plan)


def edit(db, household_id, user_id, plan_id, body, *, from_text=False):
    with db.begin():
        plan = load(db, household_id, user_id, plan_id, lock=True)
        ensure_idle(plan)
        version(plan, body.expected_version)
        if actual_status(db, plan) not in {"draft", "approved"}:
            raise HTTPException(409, "This list is closed. Create a new list instead.")
        today = evidence.offers.clock(db, household_id)
        if not today <= body.planned_on <= today + timedelta(days=13) or monday(body.planned_on) != plan.week_start:
            raise HTTPException(422, "Choose a date in this plan's week, today or later.")
        ctx = evidence.collect(db, household_id, body.planned_on)
        reservation = commitment(db, plan)
        if reservation and (not ctx["budget"] or str(ctx["budget"]["id"]) != str(reservation.budget_id)):
            raise HTTPException(422, "An approved list must remain within its existing budget period.")
        originals = {item["id"]: item for item in plan.content.get("items", [])}
        items = [evidence.price_item(db, household_id, body.planned_on,
            item.model_dump(mode="json"), ctx, originals.get(str(item.id))) for item in body.items]
        if plan.status == "approved" or plan.phase == "retry_required":
            plan.thread_id = uuid4()
        plan.status, plan.phase, plan.planned_on = "draft", "review", body.planned_on
        plan.content = {**plan.content, "items": items, "target_cents": body.target_cents,
            "buffer_cents": body.buffer_cents, "summary": evidence.totals(items, body.buffer_cents, body.target_cents),
            "notice": "Changes need approval. Your existing reservation remains until you approve or cancel." if reservation else None}
        touch(plan)
        db.flush()
        return snapshot(db, plan)


def price_signature(items):
    return digest([{key: item.get(key) for key in ("id", "product_id", "quantity", "unit", "excluded", "estimate")} for item in items])


def approve(db, household_id, user_id, plan_id, token, body):
    """Called only by the resumed graph. Financial writes and plan approval commit together."""
    with db.begin():
        plan = load(db, household_id, user_id, plan_id, lock=True)
        ensure_idle(plan, token)
        request_digest = digest(body.model_dump(mode="json", exclude={"client_request_id"}))
        if plan.approval_request_id == body.client_request_id:
            if plan.approval_digest != request_digest:
                raise HTTPException(409, "Approval request ID was reused with different details.")
            return True
        version(plan, body.expected_version)
        if actual_status(db, plan) != "draft" or plan.phase != "review":
            raise HTTPException(409, "Review the current draft before approving.")
        ctx = evidence.collect(db, household_id, plan.planned_on)
        if plan.planned_on < ctx["today"]:
            raise HTTPException(409, "This shopping date has passed. Update it before approval.")
        originals = plan.content.get("items", [])
        items = [evidence.price_item(db, household_id, plan.planned_on,
            ItemEdit.model_validate({key: value for key, value in item.items() if key in ItemEdit.model_fields}).model_dump(mode="json"), ctx, item) for item in originals]
        if price_signature(items) != price_signature(originals):
            plan.content = {**plan.content, "items": items,
                "summary": evidence.totals(items, plan.content.get("buffer_cents", 0), plan.content.get("target_cents")),
                "notice": "Price references changed. Review the updated estimates and approve again."}
            touch(plan)
            return False
        figures = evidence.totals(items, plan.content.get("buffer_cents", 0), plan.content.get("target_cents"))
        if figures["item_count"] == 0 or figures["missing_quantities"]:
            raise HTTPException(422, "Include at least one item and confirm quantities and units before approval.")
        if figures["over_target"]:
            raise HTTPException(422, "The estimates and buffer exceed your shopping target.")
        if body.reservation_cents < figures["minimum_reservation_cents"]:
            raise HTTPException(422, "Reserve at least the priced subtotal plus your buffer.")
        if figures["unknown_count"] and (not body.acknowledge_unknown_prices or body.reservation_cents <= figures["minimum_reservation_cents"]):
            raise HTTPException(422, "Add an allowance and acknowledge the unpriced items, or provide their estimates.")
        if plan.content.get("target_cents") and body.reservation_cents > plan.content["target_cents"]:
            raise HTTPException(422, "The reservation exceeds your shopping target.")
        if not ctx["budget"]:
            raise HTTPException(422, "Create a budget covering the shopping date before reserving funds.")
        budget = budgets.load_budget(db, household_id, ctx["budget"]["id"])
        budgets.check_version(budget, body.expected_budget_version)
        existing = commitment(db, plan)
        available = budgets.snapshot(db, budget)["available_cents"] + (existing.amount_cents if existing and existing.status == "open" else 0)
        if body.reservation_cents > available:
            raise HTTPException(409, "Available funds changed or are insufficient. Review the budget before approving.")
        if existing:
            if existing.status != "open" or existing.budget_id != budget.id:
                raise HTTPException(409, "The reservation is no longer open.")
            existing.amount_cents, existing.planned_on = body.reservation_cents, plan.planned_on
            budget.version += 1
        else:
            request = CommitmentCreate(client_request_id=plan.id, expected_version=budget.version,
                title=f"Shopping list {plan.week_start.isoformat()}", planned_on=plan.planned_on, amount_cents=body.reservation_cents)
            budgets.reserve_locked(db, budget, request)
            existing = db.scalar(select(BudgetCommitment).where(BudgetCommitment.household_id == household_id,
                BudgetCommitment.client_request_id == plan.id))
            plan.commitment_id = existing.id
        plan.status, plan.phase = "approved", "approved"
        plan.approved_snapshot = {**plan.content, "items": items}
        plan.content = {**plan.content, "items": items, "summary": figures, "notice": "List approved. Estimates are not guaranteed checkout prices."}
        plan.approval_request_id, plan.approval_digest = body.client_request_id, request_digest
        touch(plan)
        return True


def approval_already_saved(db, household_id, user_id, plan_id, body):
    with db.begin():
        plan = load(db, household_id, user_id, plan_id)
        if plan.approval_request_id == body.client_request_id:
            if plan.approval_digest != digest(body.model_dump(mode="json", exclude={"client_request_id"})):
                raise HTTPException(409, "Approval request ID was reused with different details.")
            return snapshot(db, plan)
        return None


def cancel(db, household_id, user_id, plan_id, body):
    with db.begin():
        plan = load(db, household_id, user_id, plan_id, lock=True)
        ensure_idle(plan)
        status = actual_status(db, plan)
        if status == "cancelled":
            return snapshot(db, plan)
        version(plan, body.expected_version)
        if status == "settled":
            raise HTTPException(409, "This list is already linked to a purchase.")
        reservation = commitment(db, plan)
        if reservation and reservation.status == "open":
            budget = budgets.load_budget(db, household_id, reservation.budget_id)
            reservation.status = "cancelled"
            budget.version += 1
        plan.status, plan.phase = "cancelled", "cancelled"
        touch(plan)
        db.flush()
        return snapshot(db, plan)


def checklist(db, household_id, user_id, plan_id, body):
    with db.begin():
        plan = load(db, household_id, user_id, plan_id, lock=True)
        ensure_idle(plan)
        version(plan, body.expected_version)
        if actual_status(db, plan) not in {"approved", "settled"}:
            raise HTTPException(409, "Approve the list before checking off purchases.")
        items = [dict(item) for item in plan.content["items"]]
        item = next((item for item in items if item["id"] == str(body.item_id)), None)
        if not item:
            raise HTTPException(404, "Shopping item not found.")
        item["done"] = body.done
        plan.content = {**plan.content, "items": items}
        touch(plan)
        db.flush()
        return snapshot(db, plan)


def settle(db, household_id, user_id, plan_id, body):
    with db.begin():
        plan = load(db, household_id, user_id, plan_id, lock=True)
        ensure_idle(plan)
        reservation = commitment(db, plan)
        if reservation and reservation.status == "settled" and reservation.purchase_id == body.purchase_id:
            return snapshot(db, plan)
        version(plan, body.expected_version)
        if actual_status(db, plan) != "approved" or reservation is None:
            raise HTTPException(409, "Link a purchase after approving the final list.")
        budget = budgets.load_budget(db, household_id, reservation.budget_id)
        purchase = db.scalar(select(Purchase).join(PurchaseDraft,
            (PurchaseDraft.id == Purchase.draft_id) & (PurchaseDraft.household_id == Purchase.household_id))
            .where(Purchase.id == body.purchase_id, Purchase.household_id == household_id,
                PurchaseDraft.status == "confirmed", Purchase.purchased_on >= plan.week_start,
                Purchase.purchased_on <= plan.week_start + timedelta(days=6),
                Purchase.purchased_on >= budget.start_date, Purchase.purchased_on <= budget.end_date))
        if purchase is None:
            raise HTTPException(422, "Choose a confirmed household purchase from this plan's week and budget period.")
        other = db.scalar(select(BudgetCommitment.id).where(BudgetCommitment.purchase_id == purchase.id))
        if other and other != reservation.id:
            raise HTTPException(409, "This purchase already settles another reservation.")
        reservation.status, reservation.purchase_id = "settled", purchase.id
        budget.version += 1
        plan.status, plan.phase = "settled", "settled"
        touch(plan)
        db.flush()
        return snapshot(db, plan)


def products(db, household_id, user_id, query):
    with db.begin():
        require_member(db, household_id, user_id)
        rows = db.scalars(select(Product).order_by(Product.canonical_name).limit(1001)).all()
        search = evidence.normal(query)
        choices = [dict(id=str(p.id), name=p.canonical_name, category=p.category,
            base_unit=p.base_unit, pack_base_quantity=str(p.pack_base_quantity) if p.pack_base_quantity else None)
            for p in rows if search and any(search in evidence.normal(value) for value in
                [p.canonical_name, p.name_de or "", p.name_en or "", *p.attributes.get("aliases", [])])][:12]
        allowed = {str(p.id): dict(id=str(p.id),name=p.canonical_name,category=p.category,
            base_unit=p.base_unit,pack_base_quantity=str(p.pack_base_quantity) if p.pack_base_quantity else None) for p in rows}
    if not choices:
        from app.ai.planner import clean_text
        clean_text(query)
        try:
            from app.ai.catalogue import retrieve_candidates
            matches = retrieve_candidates(query, top_k=5)["candidates"]
            choices = [allowed[str(candidate["id"])] for candidate in matches if str(candidate["id"]) in allowed]
        except HTTPException as error:
            if error.status_code not in {502,503}:
                raise
        except (ValueError, RuntimeError):
            pass
        except Exception:
            # Search is optional. No provider error content is returned; users
            # can retain their wording and historical/manual estimate.
            pass
    return {"products": choices}


def alternatives(db, household_id, user_id, plan_id, item_id):
    with db.begin():
        plan = load(db, household_id, user_id, plan_id)
        item = next((item for item in plan.content.get("items", []) if item["id"] == str(item_id)), None)
        if not item:
            raise HTTPException(404, "Shopping item not found.")
        if not item.get("product_id"):
            return {"offers": [], "message": "Choose a matching product first, or keep your historical estimate."}
        cards = evidence.offer_cards(db, household_id, UUID(item["product_id"]), plan.planned_on)
        return {"offers": cards, "message": "Optional demonstration offers. Check packs and conditions; nothing changes until you choose." if cards
            else "No compatible offer available. Your list and previous-purchase estimates still work."}


def save_text_preview(db, household_id, user_id, plan_id, expected_version, proposed, changes, clarification):
    token = uuid4()
    with db.begin():
        plan = load(db, household_id, user_id, plan_id, lock=True)
        ensure_idle(plan)
        version(plan, expected_version)
        if actual_status(db, plan) not in {"draft", "approved"}:
            raise HTTPException(409, "This list is closed.")
        plan.pending_text = dict(token=str(token), base_version=plan.version,
            valid=not bool(clarification),
            expires_at=(datetime.now(timezone.utc)+timedelta(minutes=20)).isoformat(),
            proposed_items=proposed if not clarification else [], changes=changes if not clarification else [])
    return dict(token=token, base_version=expected_version, proposed_items=proposed if not clarification else [],
        changes=changes if not clarification else [], clarification=clarification)


def accept_text(db, household_id, user_id, plan_id, body):
    with db.begin():
        plan = load(db, household_id, user_id, plan_id)
        ensure_idle(plan)
        version(plan, body.expected_version)
        pending = plan.pending_text
        if not pending or pending["token"] != str(body.token) or pending["base_version"] != plan.version or not pending.get("valid"):
            raise HTTPException(409, "This suggested change is no longer available. Interpret the request again.")
        if datetime.fromisoformat(pending["expires_at"]) < datetime.now(timezone.utc):
            raise HTTPException(409, "This suggested change expired. Interpret the request again.")
        edit_body = EditPlan(expected_version=plan.version, planned_on=plan.planned_on,
            items=pending["proposed_items"], target_cents=plan.content.get("target_cents"), buffer_cents=plan.content.get("buffer_cents", 0))
    return edit(db, household_id, user_id, plan_id, edit_body, from_text=True)


def history_options(db, household_id, user_id, plan_id):
    """Read-only starting points, explicitly chosen by the user; not inferred needs."""
    with db.begin():
        plan = load(db, household_id, user_id, plan_id)
        ensure_idle(plan)
        ctx = evidence.collect(db, household_id, plan.planned_on)
        result, seen = [], set()
        for candidate in ctx['candidates']:
            identity = candidate['product_id'] or evidence.normal(candidate['name'])
            if identity in seen or candidate['category'] not in {'groceries','household','personal_care'}:
                continue
            raw = ItemEdit(name=candidate['name'], product_id=candidate['product_id'],
                quantity=candidate['suggested_quantity'], unit=candidate['unit']).model_dump(mode='json')
            original = {**raw, 'reason':candidate['reason'] + ' Review whether you need it again.',
                'history_candidate_id':candidate['candidate_id']}
            result.append(evidence.price_item(db, household_id, plan.planned_on, raw, ctx, original))
            seen.add(identity)
            if len(result) == 8:
                break
        return dict(base_version=plan.version, items=result,
            message=f"From the 90-day confirmed-purchase history window ending {ctx['cutoff']:%d %b %Y}. These are not proof of a regular routine or what is at home. Choose what you need; nothing is reserved.")


def refresh_empty(db, household_id, user_id, plan_id, body):
    with db.begin():
        plan = load(db, household_id, user_id, plan_id, lock=True)
        ensure_idle(plan)
        version(plan, body.expected_version)
        if (actual_status(db, plan) != 'draft' or plan.content.get('items')
                or commitment(db, plan) is not None):
            raise HTTPException(409, 'Only an empty draft without a reservation can refresh suggestions.')
        require_history(db, household_id, plan.planned_on)
        plan.thread_id = uuid4()
        plan.phase = 'preparing'
        plan.content = {**plan.content, 'notice':None}
        touch(plan)
