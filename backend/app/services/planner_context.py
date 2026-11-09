"""Household evidence and exact estimates. The model never supplies prices."""
import hashlib
import re
import unicodedata
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from fastapi import HTTPException
from sqlalchemy import select

from app.core.privacy import has_detectable_pii
from app.db.models import Product, Purchase, PurchaseDraft, PurchaseItem
from app.db.commerce import HouseholdBudget
from app.schemas.commerce import CompareInput
from app.services import budgets, offers


def normal(text):
    return " ".join(re.sub(r"[^\w]+", " ", unicodedata.normalize("NFKC", text).casefold()).split())


def money(value):
    return int(Decimal(value).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def collect(db, household_id, planned_on):
    today = offers.clock(db, household_id)
    cutoff = min(today, planned_on - timedelta(days=1))
    products = db.scalars(select(Product).order_by(Product.canonical_name).limit(1001)).all()
    if len(products) > 1000:
        raise HTTPException(503, "The product catalogue exceeds the current planner limit.")
    by_id = {str(p.id): p for p in products}
    records = db.execute(select(PurchaseItem, Purchase, PurchaseDraft.source_type)
        .join(Purchase, Purchase.id == PurchaseItem.purchase_id)
        .join(PurchaseDraft, (PurchaseDraft.id == Purchase.draft_id) & (PurchaseDraft.household_id == Purchase.household_id))
        .where(Purchase.household_id == household_id, PurchaseDraft.status == "confirmed",
            Purchase.purchased_on <= cutoff, Purchase.purchased_on >= cutoff - timedelta(days=89))
        .order_by(Purchase.purchased_on.desc(), PurchaseItem.created_at.desc(), PurchaseItem.id).limit(5001)).all()
    if len(records) > 5000:
        raise HTTPException(503, "The recent history exceeds the current planner limit.")
    groups = {}
    for item, purchase, source in records:
        if has_detectable_pii(item.original_text):
            continue
        pid = str(item.product_id) if item.product_id and str(item.product_id) in by_id else None
        key = (pid or normal(item.original_text), item.quantity_unit)
        if key not in groups:
            product = by_id.get(pid)
            cid = hashlib.sha256(repr(key).encode()).hexdigest()[:24]
            groups[key] = dict(candidate_id=cid, product_id=pid,
                name=product.canonical_name if product else item.original_text,
                category=product.category if product else item.category,
                unit=item.quantity_unit, suggested_quantity=format(item.quantity.normalize(), "f"),
                trips=set(), weeks=set(), last_purchased_on=purchase.purchased_on.isoformat(),
                purchase_id=str(purchase.id), purchase_item_id=str(item.id),
                unit_price_cents=item.unit_price_cents, demo=source == "seed")
        groups[key]["trips"].add(str(purchase.id))
        groups[key]["weeks"].add((purchase.purchased_on - timedelta(days=purchase.purchased_on.weekday())).isoformat())
    candidates = []
    for group in groups.values():
        group["purchase_count"] = len(group.pop("trips"))
        group["recorded_weeks"] = len(group.pop("weeks"))
        group["reason"] = f"Bought on {group['purchase_count']} recorded shopping trip{'s' if group['purchase_count'] != 1 else ''}; last bought {group['last_purchased_on']}."
        if group["product_id"] is None:
            group["reason"] += " Product link not confirmed."
        candidates.append(group)
    candidates.sort(key=lambda c: (-c["recorded_weeks"], -c["purchase_count"], -date.fromisoformat(c["last_purchased_on"]).toordinal(), c["name"]))
    budget = db.scalar(select(HouseholdBudget).where(HouseholdBudget.household_id == household_id,
        HouseholdBudget.start_date <= planned_on, HouseholdBudget.end_date >= planned_on))
    return dict(today=today, cutoff=cutoff, planned_on=planned_on, candidates=candidates[:60], products=by_id,
        budget=budgets.snapshot(db, budget) if budget else None)


def offer_cards(db, household_id, product_id, planned_on):
    """Optional offers. A missing offer schema does not break historical planning."""
    try:
        with db.begin_nested():
            catalogue, stores, rows, _ = offers.load(db, household_id, planned_on)
            target = next((p for p in catalogue if p.id == product_id), None)
            if target is None:
                return []
            result = offers.evaluate(target, catalogue, rows,
                CompareInput(product_ids=[product_id], include_alternatives=True),
                {store["store_id"]: store for store in stores}, planned_on)
            return [card for card in result["offers"] if card["valid_from"] <= planned_on <= card["valid_until"]
                and not card["membership_required"] and card["minimum_checkout_cents"] is not None]
    except HTTPException as error:
        if error.status_code != 503:
            raise
        return []
    except (ValueError, TypeError, KeyError):
        return []


def price_item(db, household_id, planned_on, item, context, original=None):
    result = dict(item)
    pid, unit = item.get("product_id"), item.get("unit")
    product = context["products"].get(pid)
    if pid and product is None:
        raise HTTPException(422, "A selected product is no longer available. Choose it again or keep the item unmatched.")
    if product:
        result["name"] = product.canonical_name
    if has_detectable_pii(result["name"]):
        raise HTTPException(422, "Remove personal details from product wording.")
    result["identity_status"] = "linked" if product else "unmatched"
    same = original and original.get("product_id") == pid and normal(original["name"]) == normal(result["name"])
    result["reason"] = original.get("reason", "Added by you.") if same else "Added or changed by you."
    result["history_candidate_id"] = original.get("history_candidate_id") if same else None
    rate, evidence = None, None
    if item.get("manual_unit_price_cents") is not None:
        rate = Decimal(item["manual_unit_price_cents"])
        evidence = dict(basis="manual", label="Your estimate", reference=None)
    elif item.get("offer_id"):
        if product is None:
            raise HTTPException(422, "Choose a product before selecting an offer.")
        cards = offer_cards(db, household_id, product.id, planned_on)
        card = next((c for c in cards if c["offer_id"] == item["offer_id"] and c["batch_id"] == item["offer_batch_id"]
            and str(c["product_id"]) == pid), None)
        if card:
            # Buy full required bundles; never invent a single-pack promotion price.
            count = Decimal(item["quantity"]) if item.get("quantity") is not None else None
            if count is not None:
                if unit == "pack":
                    bundles = (count / card["required_packs"]).to_integral_value(rounding="ROUND_CEILING")
                elif unit == card["comparison_unit"]:
                    bundles = (count / (Decimal(card["pack_base_quantity"]) * card["required_packs"])).to_integral_value(rounding="ROUND_CEILING")
                else:
                    raise HTTPException(422, "This offer does not support the selected quantity unit.")
                rate = None
                evidence = dict(basis="offer", label="Selected demonstration offer; check its conditions", reference=None,
                    offer_id=card["offer_id"], batch_id=card["batch_id"], valid_until=card["valid_until"].isoformat(),
                    required_packs=card["required_packs"], checkout_packs=int(bundles) * card["required_packs"])
                result["estimate"] = dict(**evidence, unit_price_cents=None,
                    line_total_cents=int(bundles) * card["minimum_checkout_cents"])
                return result
        evidence = dict(basis="unknown", label="The selected offer is unavailable for this shopping date", reference=None)
    else:
        matches = [c for c in context["candidates"] if
            (pid is not None and c["product_id"] == pid or pid is None and c["product_id"] is None and normal(c["name"]) == normal(item["name"]))]
        matches.sort(key=lambda c: c["last_purchased_on"], reverse=True)
        for candidate in matches:
            if candidate["unit_price_cents"] is None:
                continue
            value = Decimal(candidate["unit_price_cents"])
            if candidate["unit"] == unit:
                rate = value
            elif product and product.pack_base_quantity and unit == product.base_unit and candidate["unit"] == "pack":
                rate = value / product.pack_base_quantity
            elif product and product.pack_base_quantity and unit == "pack" and candidate["unit"] == product.base_unit:
                rate = value * product.pack_base_quantity
            else:
                continue
            evidence = dict(basis="history", label=f"Based on your purchase on {candidate['last_purchased_on']}; today's price may differ",
                reference=candidate["purchase_id"], purchased_on=candidate["last_purchased_on"],
                purchase_item_id=candidate["purchase_item_id"], demo=candidate["demo"])
            break
    if evidence is None:
        evidence = dict(basis="unknown", label="No previous price available", reference=None)
    total = money(rate * Decimal(item["quantity"])) if rate is not None and item.get("quantity") is not None else None
    result["estimate"] = dict(**evidence, unit_price_cents=money(rate) if rate is not None else None, line_total_cents=total)
    return result


def totals(items, buffer_cents, target_cents):
    included = [item for item in items if not item.get("excluded")]
    priced = [item for item in included if item.get("estimate", {}).get("line_total_cents") is not None]
    subtotal = sum(item["estimate"]["line_total_cents"] for item in priced)
    return dict(item_count=len(included), priced_count=len(priced), unknown_count=len(included)-len(priced),
        missing_quantities=sum(item.get("quantity") is None or item.get("unit") is None for item in included),
        subtotal_cents=subtotal, buffer_cents=buffer_cents, minimum_reservation_cents=subtotal+buffer_cents,
        over_target=target_cents is not None and subtotal+buffer_cents > target_cents)
