"""Read-only mapping of the existing, separately scoped synthetic retailer dataset."""

import hashlib
import json
from decimal import Decimal

from sqlalchemy import Boolean, Column, Date, MetaData, Numeric, String, Table, Uuid
from sqlalchemy.dialects.postgresql import JSONB

metadata = MetaData(schema="bonwise_retail_demo")
stores = Table(
    "stores",
    metadata,
    Column("store_id", String),
    Column("chain", String),
    Column("store_name", String),
    Column("city", String),
    Column("postcode", String),
)
offers = Table(
    "published_approved_offers",
    metadata,
    *[
        Column(k, String)
        for k in (
            "offer_id",
            "batch_id",
            "chain",
            "product_name",
            "brand",
            "variant",
            "pack_unit",
            "price_basis",
            "store_scope",
            "country_code",
            "national_scope_basis",
            "source",
        )
    ],
    Column("is_organic", Boolean),
    Column("quantity_is_approximate", Boolean),
    Column("membership_required", Boolean),
    Column("pack_quantity", Numeric),
    Column("price_eur", Numeric),
    Column("deposit_eur_per_pack", Numeric),
    Column("valid_from", Date),
    Column("valid_until", Date),
    Column("multi_buy", JSONB),
    Column("store_ids", JSONB)
)

links = Table(
    "offer_product_links",
    metadata,
    Column("batch_id", String),
    Column("offer_id", String),
    Column("product_id", Uuid),
)


def package(amount, unit):
    quantity = Decimal(str(amount))
    if not quantity.is_finite() or quantity <= 0:
        raise ValueError("Invalid package contents.")
    if unit == "g":
        quantity /= 1000
        unit = "kg"
    elif unit == "ml":
        quantity /= 1000
        unit = "l"
    elif unit == "piece":
        unit = "each"
    if unit not in {"kg", "l", "each", "wash", "roll"}:
        raise ValueError("Unsupported comparison unit.")
    return quantity, unit


def signature(row):
    if row["brand"] is None or type(row["is_organic"]) is not bool:
        return None
    amount, unit = package(row["pack_quantity"], row["pack_unit"])
    identity = dict(
        name=row["product_name"],
        brand=row["brand"],
        variant=row["variant"],
        organic=row["is_organic"],
        base_unit=unit,
        pack_base_quantity=format(amount.normalize(), "f"),
    )
    return hashlib.sha256(
        json.dumps(
            identity, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ).encode()
    ).hexdigest()


def cents(amount):
    if isinstance(amount, bool):
        raise ValueError("Invalid price.")
    value = Decimal(str(amount)) * 100
    if (
        not value.is_finite()
        or value != value.to_integral_value()
        or not 0 <= value <= 10**12
    ):
        raise ValueError("Invalid price.")
    return int(value)