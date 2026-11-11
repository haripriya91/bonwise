"""Exact ledger/reservation calculations; reusable by a future approved planner."""

from sqlalchemy import func, select
from fastapi import HTTPException

from app.core.privacy import has_detectable_pii
from app.db.models import Household, Purchase, PurchaseDraft
from app.db.commerce import HouseholdBudget, BudgetCommitment
from app.services.purchases import digest, require_member


def lock_household(db, household_id, user_id):
    require_member(db, household_id, user_id, lock=True)
    if (
        db.scalar(
            select(Household).where(Household.id == household_id).with_for_update()
        )
        is None
    ):
        raise HTTPException(404, "Household not found.")


def read_household(db, household_id, user_id):
    require_member(db, household_id, user_id)
    db.scalar(
        select(Household).where(Household.id == household_id).with_for_update(read=True)
    )


def load_budget(db, household_id, budget_id):
    budget = db.scalar(
        select(HouseholdBudget)
        .where(
            HouseholdBudget.household_id == household_id,
            HouseholdBudget.id == budget_id,
        )
        .with_for_update()
    )
    if budget is None:
        raise HTTPException(404, "Budget not found.")
    return budget


def snapshot(db, budget):
    """Call inside a transaction after scope authorization; amounts are integer cents."""
    spent, count = db.execute(
        select(
            func.coalesce(func.sum(Purchase.total_cents), 0), func.count(Purchase.id)
        )
        .join(
            PurchaseDraft,
            (PurchaseDraft.id == Purchase.draft_id)
            & (PurchaseDraft.household_id == Purchase.household_id),
        )
        .where(
            Purchase.household_id == budget.household_id,
            PurchaseDraft.status == "confirmed",
            Purchase.purchased_on >= budget.start_date,
            Purchase.purchased_on <= budget.end_date,
        )
    ).one()
    commitments = db.scalars(
        select(BudgetCommitment)
        .where(
            BudgetCommitment.household_id == budget.household_id,
            BudgetCommitment.budget_id == budget.id,
        )
        .order_by(BudgetCommitment.planned_on, BudgetCommitment.id)
    ).all()
    from app.db.planner import WeeklyPlan
    shopping_ids = set(db.scalars(select(WeeklyPlan.commitment_id).where(
        WeeklyPlan.household_id == budget.household_id,
        WeeklyPlan.commitment_id.in_([c.id for c in commitments]))).all())
    reserved = sum(c.amount_cents for c in commitments if c.status == "open")
    return dict(
        id=budget.id,
        start_date=budget.start_date,
        end_date=budget.end_date,
        currency="EUR",
        amount_cents=budget.amount_cents,
        spent_cents=int(spent),
        reserved_cents=reserved,
        available_cents=budget.amount_cents - int(spent) - reserved,
        purchase_count=count,
        version=budget.version,
        commitments=[
            dict(
                id=c.id,
                title=c.title,
                kind="shopping_plan" if c.id in shopping_ids else "commitment",
                planned_on=c.planned_on,
                amount_cents=c.amount_cents,
                status=c.status,
                purchase_id=c.purchase_id,
            )
            for c in commitments
        ],
    )


def check_version(budget, expected):
    if budget.version != expected:
        raise HTTPException(
            409, "Budget changed. Reload its current amounts before continuing."
        )


def create_budget(db, household_id, user_id, body):
    creation = digest(body.model_dump(mode="json", exclude={"client_request_id"}))
    with db.begin():
        lock_household(db, household_id, user_id)
        existing = db.scalar(
            select(HouseholdBudget).where(
                HouseholdBudget.household_id == household_id,
                HouseholdBudget.client_request_id == body.client_request_id,
            )
        )
        if existing:
            if existing.creation_digest != creation:
                raise HTTPException(
                    409, "Request ID already used for a different budget."
                )
            return snapshot(db, existing)
        overlap = db.scalar(
            select(HouseholdBudget.id).where(
                HouseholdBudget.household_id == household_id,
                HouseholdBudget.start_date <= body.end_date,
                HouseholdBudget.end_date >= body.start_date,
            )
        )
        if overlap:
            raise HTTPException(409, "Budget periods cannot overlap in this version.")
        budget = HouseholdBudget(
            household_id=household_id,
            client_request_id=body.client_request_id,
            creation_digest=creation,
            start_date=body.start_date,
            end_date=body.end_date,
            amount_cents=body.amount_cents,
            version=1,
        )
        db.add(budget)
        db.flush()
        return snapshot(db, budget)


def list_budgets(db, household_id, user_id):
    with db.begin():
        read_household(db, household_id, user_id)
        budgets = db.scalars(
            select(HouseholdBudget)
            .where(HouseholdBudget.household_id == household_id)
            .order_by(HouseholdBudget.start_date.desc())
            .limit(25)
        ).all()
        return {"budgets": [snapshot(db, b) for b in budgets]}


def get_budget(db, household_id, user_id, budget_id):
    with db.begin():
        read_household(db, household_id, user_id)
        return snapshot(db, load_budget(db, household_id, budget_id))


def update_budget(db, household_id, user_id, budget_id, body):
    with db.begin():
        lock_household(db, household_id, user_id)
        budget = load_budget(db, household_id, budget_id)
        check_version(budget, body.expected_version)
        budget.amount_cents = body.amount_cents
        budget.version += 1
        db.flush()
        # Negative availability is reported honestly, not clamped to zero.
        return snapshot(db, budget)


def reserve_locked(db, budget, body):
    """Planner extension point: caller holds household/budget locks and authorization."""
    creation = digest(
        body.model_dump(mode="json", exclude={"client_request_id", "expected_version"})
    )
    existing = db.scalar(
        select(BudgetCommitment).where(
            BudgetCommitment.household_id == budget.household_id,
            BudgetCommitment.client_request_id == body.client_request_id,
        )
    )
    if existing:
        if existing.budget_id != budget.id or existing.creation_digest != creation:
            raise HTTPException(
                409, "Request ID already used for a different commitment."
            )
        return snapshot(db, budget)
    check_version(budget, body.expected_version)
    if has_detectable_pii({"title": body.title}):
        raise HTTPException(422, "Remove personal details from the commitment title.")
    if not budget.start_date <= body.planned_on <= budget.end_date:
        raise HTTPException(422, "The planned date must be within the budget period.")
    if (
        db.scalar(
            select(func.count())
            .select_from(BudgetCommitment)
            .where(BudgetCommitment.budget_id == budget.id)
        )
        >= 200
    ):
        raise HTTPException(409, "This budget has reached its commitment limit.")
    if body.amount_cents > snapshot(db, budget)["available_cents"]:
        raise HTTPException(409, "This reservation exceeds the available budget.")
    db.add(
        BudgetCommitment(
            household_id=budget.household_id,
            budget_id=budget.id,
            client_request_id=body.client_request_id,
            creation_digest=creation,
            title=body.title,
            planned_on=body.planned_on,
            amount_cents=body.amount_cents,
            status="open",
        )
    )
    budget.version += 1
    db.flush()
    return snapshot(db, budget)


def create_commitment(db, household_id, user_id, budget_id, body):
    with db.begin():
        lock_household(db, household_id, user_id)
        return reserve_locked(db, load_budget(db, household_id, budget_id), body)


def change_commitment(
    db, household_id, user_id, budget_id, commitment_id, body, *, settle
):
    with db.begin():
        lock_household(db, household_id, user_id)
        budget = load_budget(db, household_id, budget_id)
        commitment = db.scalar(
            select(BudgetCommitment)
            .where(
                BudgetCommitment.household_id == household_id,
                BudgetCommitment.budget_id == budget_id,
                BudgetCommitment.id == commitment_id,
            )
            .with_for_update()
        )
        if commitment is None:
            raise HTTPException(404, "Commitment not found.")
        desired = "settled" if settle else "cancelled"
        if commitment.status == desired:
            if settle and commitment.purchase_id != body.purchase_id:
                raise HTTPException(
                    409, "This commitment is already linked to another purchase."
                )
            return snapshot(db, budget)
        if commitment.status != "open":
            raise HTTPException(
                409, "Only open commitments can be settled or cancelled."
            )
        check_version(budget, body.expected_version)
        if settle:
            purchase = db.scalar(
                select(Purchase)
                .join(
                    PurchaseDraft,
                    (PurchaseDraft.id == Purchase.draft_id)
                    & (PurchaseDraft.household_id == Purchase.household_id),
                )
                .where(
                    Purchase.id == body.purchase_id,
                    Purchase.household_id == household_id,
                    PurchaseDraft.status == "confirmed",
                    Purchase.purchased_on >= budget.start_date,
                    Purchase.purchased_on <= budget.end_date,
                )
            )
            if purchase is None:
                raise HTTPException(
                    422,
                    "Choose a confirmed household purchase within this budget period.",
                )
            linked = db.scalar(
                select(BudgetCommitment.id).where(
                    BudgetCommitment.purchase_id == purchase.id
                )
            )
            if linked:
                raise HTTPException(
                    409, "That purchase already settles another commitment."
                )
            commitment.purchase_id = purchase.id
        commitment.status = desired
        budget.version += 1
        db.flush()
        return snapshot(db, budget)