from uuid import UUID

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.ai.transcriber import transcribe
from app.core.auth import CurrentUser, get_current_user
from app.db.session import get_db
from app.schemas.voice_capture import (
    TranscriptionOut,
    TranscriptionRequest,
)
from app.services.purchases import require_member


router = APIRouter(
    prefix="/households/{household_id}/capture",
    tags=["capture"],
)


@router.post(
    "/transcribe",
    response_model=TranscriptionOut,
)
def transcribe_audio(
    household_id: UUID,
    body: TranscriptionRequest,
    response: Response,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    with db.begin():
        require_member(db, household_id, user.id)

    response.headers["Cache-Control"] = "no-store"

    return transcribe(body)