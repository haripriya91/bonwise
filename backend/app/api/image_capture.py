from uuid import UUID

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.ai.image_extractor import extract_image
from app.core.auth import CurrentUser, get_current_user
from app.db.session import get_db
from app.schemas.image_capture import (
    ImageExtractionOut,
    ImageExtractionRequest,
)
from app.services.purchases import require_member


router = APIRouter(
    prefix="/households/{household_id}/extraction",
    tags=["extraction"],
)


@router.post("/image", response_model=ImageExtractionOut)
def image_extraction(
    household_id: UUID,
    body: ImageExtractionRequest,
    response: Response,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    with db.begin():
        require_member(db, household_id, user.id)

    response.headers["Cache-Control"] = "no-store"

    return extract_image(body.image_base64)