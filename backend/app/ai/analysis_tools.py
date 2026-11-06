from datetime import date
from uuid import UUID

from fastapi import HTTPException
from langchain_core.tools import tool
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.privacy import redact_text
from app.db.models import Purchase, PurchaseDraft, PurchaseItem
from app.schemas.purchase import SpendingSummary
from app.services.purchases import require_member, spending_summary


class PeriodInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_date: date = Field(
        description="Inclusive purchase date, YYYY-MM-DD."
    )
    end_date: date = Field(
        description="Inclusive purchase date, YYYY-MM-DD."
    )

    @model_validator(mode="after")
    def validate_period(self):
        if self.end_date < self.start_date:
            raise ValueError(
                "end_date must be on or after start_date."
            )
        return self


class EvidenceInput(PeriodInput):
    limit: int = Field(
        default=5,
        ge=1,
        le=10,
        description="Maximum supporting purchases to return.",
    )


def safe_text(value: str | None) -> str | None:
    if value is None:
        return None

    clean, _ = redact_text(value)
    return clean


def build_analysis_tools(
    db: Session,
    household_id: UUID,
    user_id: UUID,
):
    """Create request-scoped tools bound to the authenticated household."""

    @tool(args_schema=PeriodInput)
    def get_spending_summary(
        start_date: date,
        end_date: date,
    ) -> dict:
        """Get exact confirmed spending and category totals for a period.

        Use this for total spending, category spending and highest-category
        questions. Amounts are integer EUR cents. Empty results mean no
        confirmed purchases in this period, not proof that nothing was bought.
        """
        result = spending_summary(
            db,
            household_id,
            user_id,
            start_date,
            end_date,
        )

        summary = SpendingSummary.model_validate(result)

        return {
            "evidence_type": "period_summary",
            "evidence_id": (
                f"summary:{start_date.isoformat()}:"
                f"{end_date.isoformat()}"
            ),
            **summary.model_dump(mode="json"),
        }

    @tool(args_schema=EvidenceInput)
    def get_purchase_evidence(
        start_date: date,
        end_date: date,
        limit: int = 5,
    ) -> dict:
        """Get supporting confirmed purchases and their recorded items.

        Results are newest first and may be truncated. Purchase totals include
        adjustments. Never sum this limited sample as the whole period total.
        Individual item rows are also capped; use the summary tool for totals.
        """
        if end_date < start_date:
            raise HTTPException(422, "Invalid date range.")

        with db.begin():
            require_member(db, household_id, user_id)

            purchases = db.scalars(
                select(Purchase)
                .join(
                    PurchaseDraft,
                    (PurchaseDraft.id == Purchase.draft_id)
                    & (
                        PurchaseDraft.household_id
                        == Purchase.household_id
                    ),
                )
                .where(
                    Purchase.household_id == household_id,
                    Purchase.purchased_on >= start_date,
                    Purchase.purchased_on <= end_date,
                    PurchaseDraft.status == "confirmed",
                )
                .order_by(
                    Purchase.purchased_on.desc(),
                    Purchase.id.desc(),
                )
                .limit(limit + 1)
            ).all()

            more_purchases = len(purchases) > limit
            records = []

            for purchase in purchases[:limit]:
                items = db.scalars(
                    select(PurchaseItem)
                    .where(
                        PurchaseItem.purchase_id == purchase.id
                    )
                    .order_by(PurchaseItem.id)
                    .limit(21)
                ).all()

                adjustments = [
                    {
                        "kind": adjustment.kind,
                        "label": safe_text(adjustment.label),
                        "amount_cents": adjustment.amount_cents,
                        "category": adjustment.category,
                    }
                    for adjustment in (
                        # Validate persisted adjustment structure.
                        # Imported here to keep the schema use explicit.
                        _validated_adjustments(
                            purchase.adjustments
                        )
                    )
                ]

                records.append(
                    {
                        "evidence_id": f"purchase:{purchase.id}",
                        "purchase_id": str(purchase.id),
                        "merchant": safe_text(purchase.merchant),
                        "purchased_on": (
                            purchase.purchased_on.isoformat()
                        ),
                        "currency": purchase.currency,
                        "total_cents": purchase.total_cents,
                        "items_truncated": len(items) > 20,
                        "items": [
                            {
                                "item_id": str(item.id),
                                "description": safe_text(
                                    item.original_text
                                ),
                                "quantity": str(item.quantity),
                                "quantity_unit": (
                                    item.quantity_unit
                                ),
                                "unit_price_cents": (
                                    item.unit_price_cents
                                ),
                                "line_total_cents": (
                                    item.line_total_cents
                                ),
                                "category": item.category,
                            }
                            for item in items[:20]
                        ],
                        "adjustments": adjustments,
                    }
                )

        return {
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "purchases_truncated": more_purchases,
            "returned_purchase_count": len(records),
            "purchases": records,
        }

    return [get_spending_summary, get_purchase_evidence]


def _validated_adjustments(raw: list):
    from pydantic import ValidationError

    from app.schemas.purchase import AdjustmentInput

    if not isinstance(raw, list) or len(raw) > 30:
        raise HTTPException(
            500,
            "Purchase evidence could not be read.",
        )

    try:
        return [
            AdjustmentInput.model_validate(value)
            for value in raw
        ]
    except (ValidationError, TypeError):
        raise HTTPException(
            500,
            "Purchase evidence could not be read.",
        ) from None