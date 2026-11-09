"""Import-only synthetic fixture; link explicit public catalogue identities to SQL offers."""

import argparse
import hashlib
import json
import zipfile
from datetime import date
from decimal import Decimal
from pathlib import Path
from sqlalchemy import MetaData, Table, select
from sqlalchemy.dialects.postgresql import insert
from app.core.config import get_settings
from app.db.session import make_db_engine
from app.services.offer_data import signature


def canonical(value):
    if type(value) in (int, float, Decimal):
        return {"number": format(Decimal(str(value)).normalize(), "f")}
    if isinstance(value, dict):
        return {k: canonical(v) for k, v in value.items()}
    if isinstance(value, list):
        return [canonical(v) for v in value]
    return value


def digest(value):
    return hashlib.sha256(
        json.dumps(canonical(value), sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("archive")
    args = p.parse_args()

    def read(z, name, decimals=False):
        info = z.getinfo("bonwise_retail_dataset/" + name)
        if info.file_size > 10_000_000:
            raise ValueError("Fixture file too large.")
        return json.loads(
            z.read(info),
            parse_float=Decimal if decimals else float,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError("Invalid JSON number.")
            ),
        )

    with zipfile.ZipFile(args.archive) as z:
        retailers = read(z, "data/retailers.json")
        stores = read(z, "data/stores.json")
        batches = read(z, "data/batches.json")
        normalized = read(z, "data/offers_normalized_reference.json", True)
        raw = {
            b["batch_id"]: read(z, f"data/raw/{b['batch_id']}/offers.json")
            for b in batches
        }
    if (
        len(retailers),
        len(stores),
        len(batches),
        len(normalized),
        sum(map(len, raw.values())),
    ) != (4, 8, 3, 480, 480):
        raise ValueError("Expected the prepared 480-row synthetic fixture.")
    if any(r.get("is_synthetic") is not True for r in [*retailers, *stores, *batches]):
        raise ValueError("Only explicitly synthetic fixtures are supported here.")
    engine = make_db_engine(get_settings().migration_database_url)
    linked = 0
    unlinked = 0
    with engine.begin() as db:
        db.exec_driver_sql(
            Path(__file__).with_name("demo_offer_schema.sql").read_text()
        )
        meta = MetaData(schema="bonwise_retail_demo")
        tables = {
            n: Table(n, meta, autoload_with=db)
            for n in [
                "retailers",
                "stores",
                "batches",
                "raw_offers",
                "offers",
                "offer_product_links",
            ]
        }
        product = Table("products", MetaData(schema="public"), autoload_with=db)
        public_products = db.execute(select(product)).mappings().all()
        by_signature = {}
        for r in public_products:
            attrs = r["attributes"]
            if attrs.get("catalogue") == "retail-demo-catalogue-v1" and attrs.get(
                "retail_signature"
            ):
                sig = attrs["retail_signature"]
                if sig in by_signature:
                    raise ValueError(
                        "Duplicate catalogue identity; resolve it before importing."
                    )
                by_signature[sig] = r["id"]
        if not by_signature:
            raise ValueError(
                "Import the reviewed retail product catalogue before offers."
            )
        for table_name, rows, pk in [
            (
                "retailers",
                [{k: r[k] for k in ["chain", "chain_name"]} for r in retailers],
                "chain",
            ),
            ("stores", stores, "store_id"),
        ]:
            t = tables[table_name]
            for r in rows:
                existing = (
                    db.execute(select(t).where(t.c[pk] == r[pk])).mappings().first()
                )
                if existing and any(existing[k] != v for k, v in r.items()):
                    raise ValueError(
                        "Existing retailer/store differs; no data overwritten."
                    )
                if not existing:
                    db.execute(t.insert().values(**r))
        for b in batches:
            batch = b["batch_id"]
            bt = tables["batches"]
            rt = tables["raw_offers"]
            ot = tables["offers"]
            lt = tables["offer_product_links"]
            current = db.execute(
                select(bt).where(bt.c.batch_id == batch).with_for_update()
            ).first()
            nrows = [r for r in normalized if r["batch_id"] == batch]
            if current:
                originals = {
                    r["offer_id"]: r["payload"]
                    for r in db.execute(
                        select(rt).where(rt.c.batch_id == batch)
                    ).mappings()
                }
                if set(originals) != {r["id"] for r in raw[batch]} or any(
                    digest(originals[r["id"]]) != digest(r) for r in raw[batch]
                ):
                    raise ValueError(
                        "Existing weekly source differs. Existing data retained; use an explicit reviewed refresh process."
                    )
                if set(
                    db.scalars(select(ot.c.offer_id).where(ot.c.batch_id == batch))
                ) != {r["offer_id"] for r in nrows}:
                    raise ValueError(
                        "Existing normalized batch differs; no rows overwritten."
                    )
                # Existing normalized facts are authoritative; do not silently replace them from the fixture.
                nrows = [
                    dict(r)
                    for r in db.execute(
                        select(ot).where(ot.c.batch_id == batch)
                    ).mappings()
                ]
            else:
                db.execute(bt.insert().values(**dict(b, status="staged")))
                db.execute(
                    rt.insert(),
                    [
                        dict(batch_id=batch, offer_id=r["id"], payload=r)
                        for r in raw[batch]
                    ],
                )
                prepared = []
                for r in nrows:
                    if (
                        r["source"] != "demo"
                        or r["review_status"] == "approved"
                        and r["validation_flags"]
                    ):
                        raise ValueError("Flagged/non-demo row cannot be approved.")
                    if r.get("store_scope") == "national":
                        source_row = next(
                            (x for x in raw[batch] if x["id"] == r["offer_id"]), None
                        )
                        coverage = (
                            source_row.get("demoCoverage", {}) if source_row else {}
                        )
                        if (
                            r.get("country_code") != "DE"
                            or r.get("national_scope_basis")
                            != "synthetic_national_fixture_v1"
                            or coverage
                            != {
                                "scope": "national",
                                "country_code": "DE",
                                "basis": "synthetic_national_fixture_v1",
                            }
                            or r.get("store_ids")
                            or source_row.get("storeId") is not None
                        ):
                            raise ValueError(
                                "National fixture coverage evidence is missing or contradictory."
                            )
                    item = {k: v for k, v in r.items() if k in ot.c}
                    for k in ["valid_from", "valid_until"]:
                        item[k] = date.fromisoformat(item[k]) if item[k] else None
                    item["multi_buy"] = json.loads(
                        json.dumps(item["multi_buy"], default=str)
                    )
                    prepared.append(item)
                db.execute(ot.insert(), prepared)
                db.execute(
                    bt.update().where(bt.c.batch_id == batch).values(status="published")
                )
            for r in nrows:
                if r["review_status"] != "approved" or r["validation_flags"]:
                    continue
                try:
                    sig = signature(r)
                except (ValueError, TypeError, ArithmeticError):
                    sig = None
                pid = by_signature.get(sig)
                if pid is None:
                    unlinked += 1
                    continue
                existing = db.execute(
                    select(lt.c.product_id).where(
                        lt.c.batch_id == batch, lt.c.offer_id == r["offer_id"]
                    )
                ).scalar_one_or_none()
                if existing is not None and existing != pid:
                    raise ValueError(
                        "Existing reviewed product link conflicts; no link overwritten."
                    )
                db.execute(
                    insert(lt)
                    .values(
                        batch_id=batch,
                        offer_id=r["offer_id"],
                        product_id=pid,
                        match_source="reviewed_demo_identity",
                    )
                    .on_conflict_do_nothing()
                )
                linked += 1
    engine.dispose()
    print(
        json.dumps(
            dict(
                source="synthetic",
                linked_or_existing_approved_offers=linked,
                unlinked_approved_offers=unlinked,
                note="Only approved product-linked offers are comparable. Existing source/normalized facts were not overwritten.",
            )
        )
    )


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, zipfile.BadZipFile) as exc:
        raise SystemExit(str(exc)) from None
    except Exception:
        raise SystemExit(
            "Demo import failed. Check database permissions and schema privately; no secrets are printed."
        ) from None
