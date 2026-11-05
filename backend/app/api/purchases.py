from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.auth import CurrentUser, get_current_user
from app.db.session import get_db
from app.schemas.purchase import (
    ConfirmDraft,
    DraftCreate,
    DraftOut,
    DraftUpdate,
    PurchaseOut,
    PurchasePage,
    SpendingSummary,
)
from app.services import purchases as service


router = APIRouter(
    prefix="/households/{household_id}",
    tags=["purchases"],
)


@router.post(
    "/purchase-drafts",
    status_code=201,
    response_model=DraftOut,
)
def create(
    household_id: UUID,
    body: DraftCreate,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return service.create_draft(
        db, household_id, user.id, body
    )


@router.get(
    "/purchase-drafts/{draft_id}",
    response_model=DraftOut,
)
def get(
    household_id: UUID,
    draft_id: UUID,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return service.get_draft(
        db, household_id, user.id, draft_id
    )


@router.patch(
    "/purchase-drafts/{draft_id}",
    response_model=DraftOut,
)
def update(
    household_id: UUID,
    draft_id: UUID,
    body: DraftUpdate,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return service.update_draft(
        db, household_id, user.id, draft_id, body
    )


@router.post(
    "/purchase-drafts/{draft_id}/confirm",
    response_model=PurchaseOut,
)
def confirm(
    household_id: UUID,
    draft_id: UUID,
    body: ConfirmDraft,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return service.confirm_draft(
        db, household_id, user.id, draft_id, body
    )


@router.get(
    "/purchases",
    response_model=PurchasePage,
)
def history(
    household_id: UUID,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return service.list_purchases(
        db, household_id, user.id, limit, offset
    )


@router.get(
    "/spending-summary",
    response_model=SpendingSummary,
)
def spending_summary(
    household_id: UUID,
    start_date: date = Query(...),
    end_date: date = Query(...),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return service.spending_summary(
        db,
        household_id,
        user.id,
        start_date,
        end_date,
    )