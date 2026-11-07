from uuid import UUID

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.core.auth import CurrentUser, get_current_user
from app.db.session import get_db
from app.schemas.purchase import DraftOut
from app.schemas.purchase_details import PurchaseDetailsOut, ReceiptAttachInput, ReceiptRemoveInput
from app.services import purchase_details as service

router = APIRouter(prefix="/households/{household_id}", tags=["purchase details"])


def receipt_response(content):
    return Response(content=content, media_type="image/jpeg", headers={
        "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
        "Content-Disposition": 'inline; filename="reviewed-receipt.jpg"',
    })


@router.get("/purchases/{purchase_id}", response_model=PurchaseDetailsOut)
def details(household_id: UUID, purchase_id: UUID, response: Response,
            user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    return service.get_details(db, household_id, user.id, purchase_id)


@router.get("/purchases/{purchase_id}/receipt", response_class=Response, responses={200: {"content": {"image/jpeg": {"schema": {"type": "string", "format": "binary"}}}}})
def receipt(household_id: UUID, purchase_id: UUID,
            user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    return receipt_response(service.get_receipt(db, household_id, user.id, purchase_id=purchase_id))


@router.put("/purchase-drafts/{draft_id}/receipt", response_model=DraftOut)
def attach(household_id: UUID, draft_id: UUID, body: ReceiptAttachInput, response: Response,
           user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    return service.attach_receipt(db, household_id, user.id, draft_id, body)


@router.delete("/purchase-drafts/{draft_id}/receipt", response_model=DraftOut)
def remove(household_id: UUID, draft_id: UUID, body: ReceiptRemoveInput, response: Response,
           user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    return service.remove_receipt(db, household_id, user.id, draft_id, body.revision)


@router.get("/purchase-drafts/{draft_id}/receipt", response_class=Response, responses={200: {"content": {"image/jpeg": {"schema": {"type": "string", "format": "binary"}}}}})
def draft_receipt(household_id: UUID, draft_id: UUID,
                  user: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    return receipt_response(service.get_receipt(db, household_id, user.id, draft_id=draft_id))