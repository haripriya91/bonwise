import hashlib
import json
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.models import (
    HouseholdMember,
    Product,
    Purchase,
    PurchaseDraft,
    PurchaseItem,
)
from app.schemas.purchase import MAX_TOTAL, PurchaseContent


def digest(value):
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(encoded.encode()).hexdigest()


def calculate(content):
    lines = [
        int(
            (item.quantity * Decimal(item.unit_price_cents)).quantize(
                Decimal("1"),
                rounding=ROUND_HALF_UP,
            )
        )
        for item in content.items
    ]

    total = sum(lines) + sum(
        adjustment.amount_cents
        for adjustment in content.adjustments
    )

    warnings = []

    if not 0 <= total <= MAX_TOTAL:
        warnings.append(
            "Calculated total is outside the supported range."
        )

    if (
        content.reported_total_cents is not None
        and content.reported_total_cents != total
    ):
        warnings.append(
            "Reported receipt total does not match the calculated total."
        )

    today = datetime.now(ZoneInfo("Europe/Berlin")).date()

    if content.purchased_on > today:
        warnings.append("Purchase date is in the future.")

    return lines, total, warnings


def require_member(db, household_id, user_id, lock=False):
    query = select(HouseholdMember).where(
        HouseholdMember.household_id == household_id,
        HouseholdMember.user_id == user_id,
    )

    if lock:
        query = query.with_for_update(read=True)

    if db.scalar(query) is None:
        raise HTTPException(
            404,
            "Household not found or not accessible.",
        )


def check_products(db, content):
    product_ids = {
        item.product_id
        for item in content.items
        if item.product_id is not None
    }

    if not product_ids:
        return

    existing_ids = set(
        db.scalars(
            select(Product.id).where(Product.id.in_(product_ids))
        )
    )

    if existing_ids != product_ids:
        raise HTTPException(
            422,
            "A selected product no longer exists. Review the draft.",
        )


def load_draft(db, household_id, draft_id, lock=False):
    query = select(PurchaseDraft).where(
        PurchaseDraft.id == draft_id,
        PurchaseDraft.household_id == household_id,
    )

    if lock:
        query = query.with_for_update().execution_options(
            populate_existing=True
        )

    draft = db.scalar(query)

    if draft is None:
        raise HTTPException(404, "Draft not found.")

    return draft


def draft_view(db, draft):
    content = PurchaseContent.model_validate(draft.data["content"])
    _, total, warnings = calculate(content)

    purchase_id = db.scalar(
        select(Purchase.id).where(
            Purchase.household_id == draft.household_id,
            Purchase.draft_id == draft.id,
        )
    )

    return {
        "id": draft.id,
        "household_id": draft.household_id,
        "status": draft.status,
        "source_type": draft.source_type,
        "client_request_id": draft.client_request_id,
        "revision": digest(draft.data["content"]),
        "content": content.model_dump(mode="json"),
        "calculated_total_cents": total,
        "warnings": warnings,
        "purchase_id": purchase_id,
    }


def purchase_view(db, purchase):
    items = db.scalars(
        select(PurchaseItem)
        .where(PurchaseItem.purchase_id == purchase.id)
        .order_by(PurchaseItem.created_at, PurchaseItem.id)
    ).all()

    return {
        "id": purchase.id,
        "household_id": purchase.household_id,
        "draft_id": purchase.draft_id,
        "merchant": purchase.merchant,
        "purchased_on": purchase.purchased_on,
        "currency": purchase.currency,
        "total_cents": purchase.total_cents,
        "adjustments": purchase.adjustments,
        "items": [
            {
                "id": item.id,
                "product_id": item.product_id,
                "original_text": item.original_text,
                "quantity": str(item.quantity),
                "quantity_unit": item.quantity_unit,
                "unit_price_cents": item.unit_price_cents,
                "line_total_cents": item.line_total_cents,
                "category": item.category,
            }
            for item in items
        ],
    }


def create_draft(
    db: Session,
    household_id: UUID,
    user_id: UUID,
    body,
):
    with db.begin():
        require_member(db, household_id, user_id, lock=True)

        content = body.content.model_dump(mode="json")
        creation_digest = digest(content)

        statement = (
            insert(PurchaseDraft)
            .values(
                id=uuid4(),
                household_id=household_id,
                created_by=user_id,
                client_request_id=body.client_request_id,
                source_type="text",
                status="pending",
                data={
                    "schema_version": 1,
                    "creation_digest": creation_digest,
                    "content": content,
                },
            )
            .on_conflict_do_nothing(
                index_elements=[
                    "household_id",
                    "client_request_id",
                ]
            )
            .returning(PurchaseDraft.id)
        )

        draft_id = db.scalar(statement)

        if draft_id is None:
            draft = db.scalar(
                select(PurchaseDraft).where(
                    PurchaseDraft.household_id == household_id,
                    PurchaseDraft.client_request_id
                    == body.client_request_id,
                )
            )

            if (
                draft.data.get("creation_digest") != creation_digest
                or draft.source_type != "text"
            ):
                raise HTTPException(
                    409,
                    "Request ID already used for different input.",
                )
        else:
            check_products(db, body.content)
            draft = load_draft(db, household_id, draft_id)

        return draft_view(db, draft)


def get_draft(db, household_id, user_id, draft_id):
    with db.begin():
        require_member(db, household_id, user_id)
        draft = load_draft(db, household_id, draft_id)
        return draft_view(db, draft)


def update_draft(db, household_id, user_id, draft_id, body):
    with db.begin():
        require_member(db, household_id, user_id, lock=True)
        draft = load_draft(db, household_id, draft_id, lock=True)

        if draft.status != "pending":
            raise HTTPException(
                409,
                "Only pending drafts can be edited.",
            )

        if body.revision != digest(draft.data["content"]):
            raise HTTPException(
                409,
                "Draft changed. Reload it before editing.",
            )

        check_products(db, body.content)

        draft.data = {
            **draft.data,
            "content": body.content.model_dump(mode="json"),
        }

        db.flush()
        return draft_view(db, draft)


def confirm_draft(db, household_id, user_id, draft_id, body):
    with db.begin():
        require_member(db, household_id, user_id, lock=True)
        draft = load_draft(db, household_id, draft_id, lock=True)

        if body.revision != digest(draft.data["content"]):
            raise HTTPException(
                409,
                "Draft changed. Review its current details before confirming.",
            )

        if draft.status == "confirmed":
            purchase = db.scalar(
                select(Purchase).where(
                    Purchase.household_id == household_id,
                    Purchase.draft_id == draft_id,
                )
            )

            if purchase is None:
                raise HTTPException(
                    409,
                    "Confirmed draft has no purchase. Contact support.",
                )

            return purchase_view(db, purchase)

        if draft.status != "pending":
            raise HTTPException(
                409,
                "This draft cannot be confirmed.",
            )

        content = PurchaseContent.model_validate(
            draft.data["content"]
        )
        lines, total, warnings = calculate(content)

        if warnings:
            raise HTTPException(
                422,
                {
                    "message": "Correct the draft before confirmation.",
                    "warnings": warnings,
                },
            )

        check_products(db, content)

        purchase = Purchase(
            id=uuid4(),
            household_id=household_id,
            draft_id=draft_id,
            merchant=content.merchant,
            purchased_on=content.purchased_on,
            total_cents=total,
            currency=content.currency,
            adjustments=[
                adjustment.model_dump(mode="json")
                for adjustment in content.adjustments
            ],
        )

        db.add(purchase)
        db.flush()

        for item, amount in zip(content.items, lines, strict=True):
            db.add(
                PurchaseItem(
                    purchase_id=purchase.id,
                    product_id=item.product_id,
                    original_text=item.original_text,
                    quantity=item.quantity,
                    quantity_unit=item.quantity_unit,
                    unit_price_cents=item.unit_price_cents,
                    line_total_cents=amount,
                    category=item.category,
                )
            )

        draft.status = "confirmed"
        db.flush()

        return purchase_view(db, purchase)


def list_purchases(
    db,
    household_id,
    user_id,
    limit=20,
    offset=0,
):
    with db.begin():
        require_member(db, household_id, user_id)

        total = db.scalar(
            select(func.count())
            .select_from(Purchase)
            .where(Purchase.household_id == household_id)
        )

        purchases = db.scalars(
            select(Purchase)
            .where(Purchase.household_id == household_id)
            .order_by(
                Purchase.purchased_on.desc(),
                Purchase.id.desc(),
            )
            .limit(limit)
            .offset(offset)
        ).all()

        return {
            "total": total,
            "limit": limit,
            "offset": offset,
            "purchases": [
                purchase_view(db, purchase)
                for purchase in purchases
            ],
        }