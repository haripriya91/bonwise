from uuid import UUID

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.core.auth import CurrentUser, get_current_user
from app.db.session import get_db
from app.schemas.commerce import (
    BudgetCreate,
    BudgetUpdate,
    BudgetOut,
    BudgetPage,
    CommitmentCreate,
    CommitmentSettle,
    VersionInput,
    CompareInput,
    CompareOut,
    OfferOptions,
    Recommendations,
    ShoppingOverview,
)
from app.services import budgets, offers, shopping_overview

router = APIRouter(prefix="/households/{household_id}", tags=["budget and demo offers"])


def no_store(response):
    response.headers["Cache-Control"] = "no-store"


@router.get("/offer-options", response_model=OfferOptions)
def offer_options(
    household_id: UUID,
    response: Response,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    no_store(response)
    return offers.options(db, household_id, user.id)


@router.get("/offers/recommendations", response_model=Recommendations)
def recommended_offers(
    household_id: UUID,
    response: Response,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    no_store(response)
    return offers.recommendations(db, household_id, user.id)


@router.get("/shopping-overview", response_model=ShoppingOverview)
def home_overview(
    household_id: UUID,
    response: Response,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    no_store(response)
    return shopping_overview.overview(db, household_id, user.id)


@router.post("/offers/compare", response_model=CompareOut)
def compare_offers(
    household_id: UUID,
    body: CompareInput,
    response: Response,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    no_store(response)
    return offers.compare(db, household_id, user.id, body)


@router.post("/budgets", response_model=BudgetOut, status_code=201)
def create_budget(
    household_id: UUID,
    body: BudgetCreate,
    response: Response,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    no_store(response)
    return budgets.create_budget(db, household_id, user.id, body)


@router.get("/budgets", response_model=BudgetPage)
def list_budgets(
    household_id: UUID,
    response: Response,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    no_store(response)
    return budgets.list_budgets(db, household_id, user.id)


@router.get("/budgets/{budget_id}", response_model=BudgetOut)
def get_budget(
    household_id: UUID,
    budget_id: UUID,
    response: Response,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    no_store(response)
    return budgets.get_budget(db, household_id, user.id, budget_id)


@router.patch("/budgets/{budget_id}", response_model=BudgetOut)
def update_budget(
    household_id: UUID,
    budget_id: UUID,
    body: BudgetUpdate,
    response: Response,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    no_store(response)
    return budgets.update_budget(db, household_id, user.id, budget_id, body)


@router.post(
    "/budgets/{budget_id}/commitments", response_model=BudgetOut, status_code=201
)
def reserve(
    household_id: UUID,
    budget_id: UUID,
    body: CommitmentCreate,
    response: Response,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    no_store(response)
    return budgets.create_commitment(db, household_id, user.id, budget_id, body)


@router.post(
    "/budgets/{budget_id}/commitments/{commitment_id}/cancel", response_model=BudgetOut
)
def cancel(
    household_id: UUID,
    budget_id: UUID,
    commitment_id: UUID,
    body: VersionInput,
    response: Response,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    no_store(response)
    return budgets.change_commitment(
        db, household_id, user.id, budget_id, commitment_id, body, settle=False
    )


@router.post(
    "/budgets/{budget_id}/commitments/{commitment_id}/settle", response_model=BudgetOut
)
def settle(
    household_id: UUID,
    budget_id: UUID,
    commitment_id: UUID,
    body: CommitmentSettle,
    response: Response,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    no_store(response)
    return budgets.change_commitment(
        db, household_id, user.id, budget_id, commitment_id, body, settle=True
    )