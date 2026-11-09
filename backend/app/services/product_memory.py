"""Reuse confirmed, household-scoped product choices; never auto-assign them."""

import re
import unicodedata

from sqlalchemy import select

from app.ai.catalogue import ACTIVE_CATALOGUES
from app.db.models import Product, Purchase, PurchaseDraft, PurchaseItem


def key(value):
    return " ".join(
        re.sub(
            r"[\W_]+", " ", unicodedata.normalize("NFKC", value or "").casefold()
        ).split()
    )


def previous_choice(db, household_id, wording, category, quantity_unit, merchant=None):
    # Caller has authorized this household. Fresh reads only; no vectors of private history.
    with db.begin():
        rows = db.execute(
            select(PurchaseItem, Purchase.merchant)
            .join(Purchase, Purchase.id == PurchaseItem.purchase_id)
            .join(
                PurchaseDraft,
                (PurchaseDraft.id == Purchase.draft_id)
                & (PurchaseDraft.household_id == Purchase.household_id),
            )
            .where(
                Purchase.household_id == household_id,
                PurchaseDraft.status == "confirmed",
                PurchaseDraft.source_type.in_(("text", "image", "voice")),
                PurchaseItem.category == category,
                PurchaseItem.quantity_unit == quantity_unit,
                PurchaseItem.product_id.is_not(None),
            )
            .order_by(
                Purchase.purchased_on.desc(),
                PurchaseItem.created_at.desc(),
                PurchaseItem.id,
            )
            .limit(5001)
        ).all()
        if len(rows) > 5000:
            return "not_checked", None
        ids = {
            item.product_id
            for item, store in rows
            if key(item.original_text) == key(wording)
            and (merchant is None or key(store) == key(merchant))
        }
        if not ids:
            return "not_found", None
        if len(ids) != 1:
            return "conflict", None
        product = db.scalar(select(Product).where(Product.id == next(iter(ids))))
        if (
            product is None
            or product.category != category
            or product.attributes.get("catalogue") not in ACTIVE_CATALOGUES
            or product.attributes.get("source") not in {"synthetic", "receipt_review"}
        ):
            return "not_found", None
        return "reused", dict(
            product_id=product.id,
            name=product.canonical_name,
            name_de=product.name_de,
            brand=product.brand,
            category=product.category,
            base_unit=product.base_unit,
            pack_base_quantity=(
                format(product.pack_base_quantity.normalize(), "f")
                if product.pack_base_quantity is not None
                else None
            ),
            source=product.attributes["source"],
            status="check_details",
            checks=[
                "Previously chosen in this household",
                "Review before using this product",
            ],
        )