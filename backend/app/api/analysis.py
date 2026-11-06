from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Response
from sqlalchemy.orm import Session

from app.ai.analysis_assistant import ask_bonwise
from app.core.auth import CurrentUser, get_current_user
from app.db.session import get_db
from app.schemas.analysis import AnalysisInput, AnalysisOut


router = APIRouter(prefix="/households/{household_id}/analysis", tags=["analysis"])


@router.post("/ask", response_model=AnalysisOut)
def ask(household_id: UUID, body: AnalysisInput, response: Response,
        background_tasks: BackgroundTasks,
        user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    return ask_bonwise(db, household_id, user.id, body, background_tasks=background_tasks)