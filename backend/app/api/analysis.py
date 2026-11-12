from uuid import UUID
from datetime import date

from fastapi import APIRouter, BackgroundTasks, Depends, Response, Query
from sqlalchemy.orm import Session

from app.ai.analysis_assistant import ask_bonwise
from app.core.auth import CurrentUser, get_current_user
from app.db.session import get_db
from app.schemas.analysis import AnalysisInput, AnalysisOut, AnalysisBudgetContext
from app.schemas.insights import InsightRequest, InsightsOut


router = APIRouter(prefix="/households/{household_id}/analysis", tags=["analysis"])


@router.post("/insights", response_model=InsightsOut)
def insights(household_id: UUID, body: InsightRequest, response: Response,
             background_tasks: BackgroundTasks,
             user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    from app.ai.spending_insights import generate
    response.headers["Cache-Control"] = "no-store"
    return generate(db, household_id, user.id, body, background_tasks)


@router.post("/ask", response_model=AnalysisOut)
def ask(household_id: UUID, body: AnalysisInput, response: Response,
        background_tasks: BackgroundTasks,
        user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    return ask_bonwise(db, household_id, user.id, body, background_tasks=background_tasks)

@router.get("/budget-context", response_model=AnalysisBudgetContext)
def budget_context(household_id: UUID, response: Response, start_date: date = Query(...),
                   end_date: date = Query(...), user: CurrentUser = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    from app.services.analysis_budget import for_period
    response.headers["Cache-Control"] = "no-store"
    return for_period(db, household_id, user.id, start_date, end_date)
