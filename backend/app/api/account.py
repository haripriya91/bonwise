from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import CurrentUser, get_current_user
from app.db.models import Household, HouseholdMember
from app.db.session import get_db


router = APIRouter(tags=["account"])


@router.get("/me")
def me(
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows = db.execute(
        select(
            Household.id,
            Household.name,
            HouseholdMember.role,
        )
        .join(
            HouseholdMember,
            HouseholdMember.household_id == Household.id,
        )
        .where(HouseholdMember.user_id == user.id)
        .order_by(Household.name)
    ).all()

    return {
        "user_id": user.id,
        "households": [
            {
                "id": row.id,
                "name": row.name,
                "role": row.role,
            }
            for row in rows
        ],
    }


@router.get("/households/{household_id}")
def household(
    household_id: UUID,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    result = db.scalar(
        select(Household)
        .join(
            HouseholdMember,
            HouseholdMember.household_id == Household.id,
        )
        .where(
            Household.id == household_id,
            HouseholdMember.user_id == user.id,
        )
    )

    if result is None:
        raise HTTPException(
            status_code=404,
            detail="Household not found or not accessible.",
        )

    return {
        "id": result.id,
        "name": result.name,
        "currency": result.currency,
    }