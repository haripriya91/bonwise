from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Response
from sqlalchemy.orm import Session

from app.ai.product_matching import suggest_products
from app.core.auth import CurrentUser, get_current_user
from app.db.session import get_db
from app.schemas.product_matching import ProductSuggestionInput, ProductSuggestionOut

router = APIRouter(prefix="/households/{household_id}", tags=["product matching"])


@router.post("/product-suggestions", response_model=ProductSuggestionOut)
def suggestions(household_id: UUID, body: ProductSuggestionInput,
                response: Response, background_tasks: BackgroundTasks,
                user: CurrentUser = Depends(get_current_user),
                db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    return suggest_products(db, household_id, user.id, body,
                            background_tasks=background_tasks)
