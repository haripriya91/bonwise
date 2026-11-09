from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app.core.auth import CurrentUser, get_current_user
from app.db.session import get_db
from app.schemas.planner import (AcceptText, ApprovePlan, ChecklistEdit, EditPlan, PlanOut, PlanPage,
    PreparePlan, SettlePlan, TextEdit, TextPreviewOut, Version)
from app.services import planner as service
from app.workflows import weekly_planner as workflow

router = APIRouter(prefix="/households/{household_id}", tags=["weekly planner"])


@router.get("/weekly-plans", response_model=PlanPage)
def lists(household_id: UUID, response: Response, week_start: date = Query(...),
          user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    return service.list_plans(db, household_id, user.id, week_start)


@router.post("/weekly-plans", response_model=PlanOut)
def prepare(household_id: UUID, body: PreparePlan, response: Response,
            user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    plan = service.prepare(db, household_id, user.id, body)
    return workflow.run(db, household_id, user.id, plan.id)


@router.get("/weekly-plans/products")
def products(household_id: UUID, response: Response, q: str = Query(min_length=2, max_length=120),
             user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    return service.products(db, household_id, user.id, q)


@router.get("/weekly-plans/{plan_id}", response_model=PlanOut)
def get(household_id: UUID, plan_id: UUID, response: Response,
        user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    return service.get_plan(db, household_id, user.id, plan_id)


@router.patch("/weekly-plans/{plan_id}", response_model=PlanOut)
def edit(household_id: UUID, plan_id: UUID, body: EditPlan, response: Response,
         user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    return service.edit(db, household_id, user.id, plan_id, body)


@router.post("/weekly-plans/{plan_id}/retry", response_model=PlanOut)
def retry(household_id: UUID, plan_id: UUID, response: Response,
          user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    return workflow.run(db, household_id, user.id, plan_id)


@router.post("/weekly-plans/{plan_id}/approve", response_model=PlanOut)
def approve(household_id: UUID, plan_id: UUID, body: ApprovePlan, response: Response,
            user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    return workflow.run(db, household_id, user.id, plan_id, approval=body)


@router.post("/weekly-plans/{plan_id}/cancel", response_model=PlanOut)
def cancel(household_id: UUID, plan_id: UUID, body: Version, response: Response,
           user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    return service.cancel(db, household_id, user.id, plan_id, body)


@router.post("/weekly-plans/{plan_id}/checklist", response_model=PlanOut)
def checklist(household_id: UUID, plan_id: UUID, body: ChecklistEdit, response: Response,
              user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    return service.checklist(db, household_id, user.id, plan_id, body)


@router.post("/weekly-plans/{plan_id}/settle", response_model=PlanOut)
def settle(household_id: UUID, plan_id: UUID, body: SettlePlan, response: Response,
           user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    return service.settle(db, household_id, user.id, plan_id, body)


@router.get("/weekly-plans/{plan_id}/offers")
def offers(household_id: UUID, plan_id: UUID, item_id: UUID, response: Response,
           user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    return service.alternatives(db, household_id, user.id, plan_id, item_id)


@router.post("/weekly-plans/{plan_id}/text-preview", response_model=TextPreviewOut)
def text_preview(household_id: UUID, plan_id: UUID, body: TextEdit, response: Response,
                 user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    return workflow.preview_changes(db, household_id, user.id, plan_id, body)


@router.post("/weekly-plans/{plan_id}/accept-text", response_model=PlanOut)
def accept_text(household_id: UUID, plan_id: UUID, body: AcceptText, response: Response,
                user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    return service.accept_text(db, household_id, user.id, plan_id, body)
