from uuid import UUID

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.ai.purchase_extractor import extract_text
from app.core.auth import CurrentUser, get_current_user
from app.core.privacy import redact_text
from app.db.session import get_db
from app.schemas.extraction import (
    ExtractionInput,
    ExtractionOut,
    PrivacyInput,
    PrivacyPreview,
)
from app.services.purchases import require_member


router = APIRouter(
    prefix="/households/{household_id}/extraction",
    tags=["extraction"],
)


def authorize(db, household_id, user_id):
    # Release the transaction before external inference.
    with db.begin():
        require_member(db, household_id, user_id)


@router.post(
    "/privacy-preview",
    response_model=PrivacyPreview,
)
def privacy_preview(
    household_id: UUID,
    body: PrivacyInput,
    response: Response,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    authorize(db, household_id, user.id)
    response.headers["Cache-Control"] = "no-store"

    clean, findings = redact_text(body.text)

    return {
        "reviewed_text": clean,
        "findings": findings,
        "requires_review": True,
    }


@router.post(
    "/text",
    response_model=ExtractionOut,
)
def text_extraction(
    household_id: UUID,
    body: ExtractionInput,
    response: Response,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    authorize(db, household_id, user.id)
    response.headers["Cache-Control"] = "no-store"

    return extract_text(body.reviewed_text)