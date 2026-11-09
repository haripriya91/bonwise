"""Catalogue identity -> exact current-week SQL offers. No weekly offer vectors."""

from datetime import datetime, timedelta
from decimal import Decimal, localcontext, ROUND_HALF_UP
from fractions import Fraction
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from app.ai.catalogue import ACTIVE_CATALOGUES
from app.db.models import Household, Product, Purchase, PurchaseDraft, PurchaseItem
from app.schemas.commerce import CompareInput
from app.services.purchases import require_member
from app.services import offer_data as data


def week(today):
    start = today - timedelta(days=today.weekday())
    return dict(
        as_of=today, week_start=start, week_end=start + timedelta(days=6), source="demo"
    )


def clock(db, household_id):
    zone = db.scalar(select(Household.timezone).where(Household.id == household_id))
    try:
        return datetime.now(ZoneInfo(zone or "Europe/Berlin")).date()
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        raise HTTPException(500, "Household timezone is unavailable.") from None


def products(db):
    return db.scalars(
        select(Product)
        .where(Product.attributes["catalogue"].astext.in_(ACTIVE_CATALOGUES))
        .order_by(Product.canonical_name)
        .limit(1001)
    ).all()


def history(db, household_id, today):
    """Distinct baskets per explicitly linked product, never spend/quantity multiplied by item joins."""
    start = today - timedelta(days=89)
    rows = db.execute(
        select(
            PurchaseItem.product_id,
            func.count(func.distinct(Purchase.id)),
            func.max(Purchase.purchased_on),
        )
        .join(Purchase, Purchase.id == PurchaseItem.purchase_id)
        .join(
            PurchaseDraft,
            (PurchaseDraft.id == Purchase.draft_id)
            & (PurchaseDraft.household_id == Purchase.household_id),
        )
        .where(
            Purchase.household_id == household_id,
            PurchaseDraft.status == "confirmed",
            Purchase.purchased_on >= start,
            Purchase.purchased_on <= today,
            PurchaseItem.product_id.is_not(None),
        )
        .group_by(PurchaseItem.product_id)
    ).all()
    return {
        pid: dict(purchase_count=int(count), last_purchased_on=last)
        for pid, count, last in rows
    }


def load(db, household_id, today):
    context = week(today)
    try:
        stores = db.execute(select(data.stores)).mappings().all()
        rows = (
            db.execute(
                select(data.offers, data.links.c.product_id)
                .join(
                    data.links,
                    (data.links.c.batch_id == data.offers.c.batch_id)
                    & (data.links.c.offer_id == data.offers.c.offer_id),
                )
                .where(
                    data.offers.c.valid_from <= context["week_end"],
                    data.offers.c.valid_until >= today,
                    data.offers.c.store_scope == "national",
                    data.offers.c.country_code == "DE",
                    data.offers.c.national_scope_basis.in_(
                        ("synthetic_national_fixture_v1", "reviewed_source_national")
                    ),
                )
                .limit(10001)
            )
            .mappings()
            .all()
        )
    except SQLAlchemyError:
        raise HTTPException(
            503, "Demo offers are not loaded or accessible yet."
        ) from None
    catalogue = products(db)
    if len(catalogue) > 1000 or len(rows) > 10000:
        raise HTTPException(503, "Demo data exceeds the current comparison limit.")
    return catalogue, stores, rows, history(db, household_id, today)


def compatible_alternative(target, candidate):
    a, b = target.attributes, candidate.attributes
    return (
        all(a.get(k) is not None for k in ("family", "variant_code", "organic"))
        and all(a.get(k) == b.get(k) for k in ("family", "variant_code", "organic"))
        and target.base_unit is not None
        and target.base_unit == candidate.base_unit
    )


def evaluate(target, catalogue, rows, body, store_map, today):
    by_id = {p.id: p for p in catalogue}
    cards = []
    end = week(today)["week_end"]
    for row in rows:
        if (
            row["source"] != "demo"
            or row["quantity_is_approximate"]
            or row["store_scope"] != "national"
            or row.get("country_code") != "DE"
            or row.get("national_scope_basis")
            not in ("synthetic_national_fixture_v1", "reviewed_source_national")
            or row["membership_required"] is None
            or row["price_basis"] not in {"per_pack", "bundle_total"}
            or row["brand"] is None
            or row["pack_quantity"] is None
            or row["price_eur"] is None
        ):
            continue
        if (
            row["valid_from"] is None
            or row["valid_until"] is None
            or row["valid_from"] > end
            or row["valid_until"] < today
        ):
            continue
        if not isinstance(row["store_ids"], list) or row["store_ids"]:
            raise ValueError("National offer has contradictory branch restrictions.")
        candidate = by_id.get(row["product_id"])
        if candidate is None:
            raise ValueError("Offer product link is unavailable.")
        relation = (
            "same_product" if candidate.id == target.id else "compatible_alternative"
        )
        if relation != "same_product" and (
            not body.include_alternatives
            or not compatible_alternative(target, candidate)
        ):
            continue
        quantity, unit = data.package(row["pack_quantity"], row["pack_unit"])
        if candidate.base_unit != unit or candidate.pack_base_quantity != quantity:
            raise ValueError("Linked product package conflicts with the offer.")
        price, packs = data.cents(row["price_eur"]), 1
        if row["price_basis"] == "bundle_total":
            multi = row["multi_buy"]
            if (
                not isinstance(multi, dict)
                or type(multi.get("required_packs")) is not int
                or not 2 <= multi["required_packs"] <= 1000
                or data.cents(multi.get("bundle_price_eur")) != price
            ):
                raise ValueError("Invalid bundle conditions.")
            packs = multi["required_packs"]
        elif row["multi_buy"] is not None:
            raise ValueError("Unexpected multi-buy condition.")
        rate = Fraction(price, 100) / (Fraction(quantity) * packs)
        with localcontext() as ctx:
            ctx.prec = 40
            display_rate = format(
                (Decimal(rate.numerator) / Decimal(rate.denominator)).quantize(
                    Decimal("0.0001"), rounding=ROUND_HALF_UP
                ),
                "f",
            )
        deposit = (
            None
            if row["deposit_eur_per_pack"] is None
            else data.cents(row["deposit_eur_per_pack"]) * packs
        )
        card = dict(
            offer_id=row["offer_id"],
            batch_id=row["batch_id"],
            product_id=candidate.id,
            product_name=candidate.canonical_name,
            brand=candidate.brand,
            chain=row["chain"],
            store_scope="national",
            country_code="DE",
            relation=relation,
            valid_from=row["valid_from"],
            valid_until=row["valid_until"],
            availability=(
                "active" if row["valid_from"] <= today else "upcoming_this_week"
            ),
            pack_base_quantity=format(quantity.normalize(), "f"),
            comparison_unit=unit,
            unit_price_eur=display_rate,
            advertised_price_cents=price,
            required_packs=packs,
            deposit_total_cents=deposit,
            minimum_checkout_cents=None if deposit is None else price + deposit,
            membership_required=row["membership_required"],
            price_access="membership" if row["membership_required"] else "standard",
            source="demo",
        )
        cards.append((rate, card))
    # Do not rank a member-only or future price as a price available to everyone today.
    cards.sort(
        key=lambda pair: (
            pair[1]["availability"] != "active",
            pair[1]["membership_required"],
            pair[0],
            pair[1]["advertised_price_cents"],
            pair[1]["chain"],
            pair[1]["offer_id"],
        )
    )
    standard = [
        r
        for r, c in cards
        if c["availability"] == "active" and not c["membership_required"]
    ]
    lowest = min(standard) if standard else None
    for rate, card in cards:
        card["is_best_unit_price"] = (
            lowest is not None
            and rate == lowest
            and card["availability"] == "active"
            and not card["membership_required"]
        )
    return dict(
        product_id=target.id,
        product_name=target.canonical_name,
        status="offers_found" if cards else "no_compatible_offer",
        message=(
            "Prices for national offer listings this week. Check validity, bundle and app/card conditions."
            if cards
            else "No compatible current-week offer in the national offer listings."
        ),
        offers=[card for _, card in cards[:20]],
        results_truncated=len(cards) > 20,
    )


def product_options(catalogue, rows, past):
    counts = {}
    for row in rows:
        if (
            row["store_scope"] == "national"
            and row.get("country_code") == "DE"
            and row.get("national_scope_basis")
            in ("synthetic_national_fixture_v1", "reviewed_source_national")
        ):
            counts[row["product_id"]] = counts.get(row["product_id"], 0) + 1
    result = []
    for p in catalogue:
        entry = dict(
            id=p.id,
            name=p.canonical_name,
            brand=p.brand,
            pack_base_quantity=(
                format(p.pack_base_quantity.normalize(), "f")
                if p.pack_base_quantity is not None
                else None
            ),
            base_unit=p.base_unit,
            purchase_count=0,
            last_purchased_on=None,
            suggestion_reason=None,
            current_offer_count=counts.get(p.id, 0),
        )
        if p.id in past:
            entry.update(past[p.id])
            entry["suggestion_reason"] = (
                "Bought in confirmed purchases in the last 90 days."
            )
        result.append(entry)
    result.sort(
        key=lambda p: (
            p["purchase_count"] == 0,
            -p["purchase_count"],
            -p["last_purchased_on"].toordinal() if p["last_purchased_on"] else 0,
            p["name"],
        )
    )
    return result


def options(db, household_id, user_id):
    with db.begin():
        require_member(db, household_id, user_id)
        today = clock(db, household_id)
        catalogue, stores, rows, past = load(db, household_id, today)
        opts = product_options(catalogue, rows, past)
        return dict(
            **week(today),
            products=opts,
            suggested_product_ids=[p["id"] for p in opts if p["purchase_count"] > 0][
                :12
            ],
            history_start=today - timedelta(days=89),
            history_end=today,
            history_linked_product_count=sum(p["purchase_count"] > 0 for p in opts),
            explanation="Suggestions use product-linked confirmed purchases from the last 90 days. Browse the catalogue for other products. No location is requested. Only explicitly national offers are listed."
        )


def recommendations_locked(db, household_id, today, limit=6):
    catalogue, stores, rows, past = load(db, household_id, today)
    opts = product_options(catalogue, rows, past)
    by_id = {p.id: p for p in catalogue}
    # Frequent purchases first; lack of an offer is a real result, not a fabricated suggestion.
    suggestions = [p for p in opts if p["purchase_count"] > 0][:limit]
    body = (
        CompareInput(
            product_ids=[p["id"] for p in suggestions],
            include_alternatives=False,
        )
        if suggestions
        else None
    )
    lookup = {r["store_id"]: r["chain"] for r in stores}
    result = []
    for entry in suggestions:
        comparison = (
            evaluate(by_id[entry["id"]], catalogue, rows, body, lookup, today)
            if body
            else dict(
                product_id=entry["id"],
                product_name=entry["name"],
                status="no_compatible_offer",
                message="No approved national offers are available.",
                offers=[],
                results_truncated=False,
            )
        )
        result.append(dict(product=entry, comparison=comparison))
    return dict(
        **week(today),
        history_start=today - timedelta(days=89),
        history_end=today,
        history_linked_product_count=sum(p["purchase_count"] > 0 for p in opts),
        explanation="Based on confirmed purchases from the last 90 days. Browse national German offers without sharing your location. Retailer conditions apply. Fictional demo prices; no live stock information.",
        recommendations=result
    )


def recommendations(db, household_id, user_id):
    with db.begin():
        require_member(db, household_id, user_id)
        return recommendations_locked(db, household_id, clock(db, household_id))


def compare(db, household_id, user_id, body):
    with db.begin():
        require_member(db, household_id, user_id)
        today = clock(db, household_id)
        catalogue, stores, rows, past = load(db, household_id, today)
        lookup = {r["store_id"]: r["chain"] for r in stores}
        by_id = {p.id: p for p in catalogue}
        if not set(body.product_ids) <= set(by_id):
            raise HTTPException(
                422, "A selected product is unavailable. Reload options."
            )
        try:
            results = [
                evaluate(by_id[pid], catalogue, rows, body, lookup, today)
                for pid in dict.fromkeys(body.product_ids)
            ]
        except (ValueError, TypeError, ArithmeticError, KeyError):
            raise HTTPException(
                500, "Offer data is inconsistent; comparison could not be completed."
            ) from None
        return dict(**week(today), currency="EUR", results=results)