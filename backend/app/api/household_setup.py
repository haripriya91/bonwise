from uuid import UUID
from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session
from app.core.auth import CurrentUser, get_current_user
from app.db.session import get_db
from app.schemas.household_settings import HouseholdCreate, SettingsEdit, ProfileEdit, NeedsPreview, SaveNeeds
from app.services import household_settings as service, seasonal_needs

router = APIRouter(tags=["household setup"])


@router.post("/households", status_code=201)
def create_household(body: HouseholdCreate, response: Response,
                     user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    return service.create_household(db, user.id, body)


@router.get("/profile")
def profile(response: Response, user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    return service.read_profile(db, user.id)


@router.patch("/profile")
def update_profile(body: ProfileEdit, response: Response, user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    return service.update_profile(db, user.id, body)


@router.get("/households/{household_id}/settings")
def settings(household_id: UUID, response: Response, user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    return service.read_settings(db, household_id, user.id)


@router.patch("/households/{household_id}/settings")
def update_settings(household_id: UUID, body: SettingsEdit, response: Response, user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    return service.update_settings(db, household_id, user.id, body)


@router.get("/households/{household_id}/upcoming-needs")
def needs(household_id: UUID, response: Response, user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    return service.read_needs(db, household_id, user.id)


@router.post("/households/{household_id}/upcoming-needs")
def save_needs(household_id: UUID, body: SaveNeeds, response: Response, user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    return service.save_needs(db, household_id, user.id, body)


@router.delete("/households/{household_id}/upcoming-needs/{need_id}")
def delete_need(household_id: UUID, need_id: UUID, response: Response, user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    return service.delete_need(db, household_id, user.id, need_id)


@router.post("/households/{household_id}/weekly-plans/{plan_id}/needs-preview")
def preview(household_id: UUID, plan_id: UUID, body: NeedsPreview, response: Response,
            user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    return seasonal_needs.preview(db, household_id, user.id, plan_id, body)
