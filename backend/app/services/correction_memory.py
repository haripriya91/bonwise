"""Confirmed examples and bounded indexing jobs. No provider calls in purchase transactions."""
import hashlib
import json
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select, or_, func
from sqlalchemy.orm import Session

from app.ai.catalogue import ACTIVE_CATALOGUES
from app.core.config import get_settings
from app.core.privacy import has_detectable_pii
from app.db.corrections import ProductCorrection
from app.db.models import Product, Purchase, PurchaseDraft, PurchaseItem
from app.services.product_memory import key

VERSION = "correction-wording-v1"


def settings_revision():
    settings = get_settings()
    return [VERSION, settings.embedding_model, settings.embedding_dimensions]


def namespace(household_id):
    # Server-derived only; clients cannot supply another household's namespace.
    scope = hashlib.sha256(str(UUID(str(household_id))).encode()).hexdigest()[:32]
    revision = hashlib.sha256(json.dumps(settings_revision()).encode()).hexdigest()[:12]
    return f"corrections-{scope}-{revision}"


def source_query():
    return (select(PurchaseItem, Purchase, PurchaseDraft, Product)
        .join(Purchase, Purchase.id == PurchaseItem.purchase_id)
        .join(PurchaseDraft, (PurchaseDraft.id == Purchase.draft_id) & (PurchaseDraft.household_id == Purchase.household_id))
        .join(Product, Product.id == PurchaseItem.product_id))


def source_value(row):
    if row is None:
        return None
    item, purchase, draft, product = row
    attrs = product.attributes or {}
    if (draft.status != "confirmed" or draft.source_type not in {"image", "text", "voice"}
        or item.category != product.category or attrs.get("catalogue") not in ACTIVE_CATALOGUES
        or attrs.get("source") not in {"synthetic", "receipt_review"}
        or not attrs.get("code") or not attrs.get("family")):
        return None
    # Scan content only, never opaque product IDs or digests.
    if has_detectable_pii([item.original_text, purchase.merchant or "", product.canonical_name,
        product.name_de or "", product.name_en or "", product.brand or "", attrs.get("variant") or ""]):
        return None
    return dict(item_id=str(item.id), household_id=str(purchase.household_id), product_id=str(product.id),
        wording=item.original_text, merchant=key(purchase.merchant), category=item.category,
        quantity_unit=item.quantity_unit,
        product=dict(id=str(product.id), canonical_name=product.canonical_name, name_de=product.name_de,
            name_en=product.name_en or product.canonical_name, brand=product.brand, category=product.category,
            pack_base_quantity=format(product.pack_base_quantity.normalize(), "f") if product.pack_base_quantity is not None else None,
            base_unit=product.base_unit, attributes=attrs))


def fingerprint(value):
    return hashlib.sha256(json.dumps([settings_revision(), value], sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def source_for(db, household_id, item_id):
    return source_value(db.execute(source_query().where(Purchase.household_id == household_id,
        PurchaseItem.id == item_id)).first())


def enqueue_purchase(db, household_id, purchase_id):
    """Caller holds household confirmation lock and owns the transaction."""
    rows = db.execute(source_query().where(Purchase.household_id == household_id, Purchase.id == purchase_id)).all()
    count = 0
    for row in rows:
        value = source_value(row)
        if not value:
            continue
        existing = db.scalar(select(ProductCorrection).where(ProductCorrection.purchase_item_id == row[0].id,
            ProductCorrection.household_id == household_id))
        digest = fingerprint(value)
        if existing is None:
            db.add(ProductCorrection(household_id=household_id, purchase_item_id=row[0].id, fingerprint=digest,
                status="pending", attempts=0))
            count += 1
        elif existing.fingerprint != digest:
            existing.fingerprint, existing.status = digest, "pending"
            existing.indexed_fingerprint, existing.lease_until, existing.last_error_code, existing.attempts = None, None, None, 0
            count += 1
    db.flush()
    return count


def verified_examples(db, household_id, matches, category, quantity_unit, merchant):
    """Vectors locate SQL evidence. Never trust their text, ownership or product facts."""
    examples, conflict = [], False
    for match in matches[:5]:
        vector_id, digest = match["id"], match["fingerprint"]
        try:
            correction_id = UUID(vector_id.split(":")[0])
        except (ValueError, AttributeError):
            continue
        row = db.scalar(select(ProductCorrection).where(ProductCorrection.id == correction_id,
            ProductCorrection.household_id == household_id, ProductCorrection.status == "indexed",
            ProductCorrection.fingerprint == digest, ProductCorrection.indexed_fingerprint == digest))
        if row is None or vector_id != f"{row.id}:{digest[:16]}":
            continue
        value = source_for(db, household_id, row.purchase_item_id)
        if (value is None or fingerprint(value) != digest or value["category"] != category
            or value["quantity_unit"] != quantity_unit or merchant is not None and value["merchant"] != key(merchant)):
            continue
        # Verify all confirmed choices for this wording, not merely the top vector.
        choices = db.execute(source_query().where(Purchase.household_id == household_id,
            PurchaseDraft.status == "confirmed", PurchaseDraft.source_type.in_(("text", "image", "voice")),
            PurchaseItem.category == category, PurchaseItem.quantity_unit == quantity_unit).limit(5001)).all()
        if len(choices) > 5000:
            continue
        ids = {str(r[0].product_id) for r in choices if key(r[0].original_text) == key(value["wording"])
            and (merchant is None or key(r[1].merchant) == key(merchant))}
        if ids != {value["product_id"]}:
            conflict = True
            continue
        if not any(e["product_id"] == value["product_id"] and key(e["wording"]) == key(value["wording"]) for e in examples):
            examples.append(value)
    return examples[:3], conflict


def process_pending(bind, household_id, user_id, *, limit=5):
    """Own sessions; bounded retries and leases. Safe as an after-response task or CLI job."""
    from app.ai.analysis_tracing import AnalysisTrace
    from app.ai.correction_retrieval import upsert_example
    from app.services.purchases import require_member
    trace = AnalysisTrace(VERSION, get_settings().embedding_model, root_name="correction_index")
    counts = dict(indexed=0, failed=0, blocked=0)
    try:
        if not get_settings().correction_rag_enabled:
            return counts
        for _ in range(min(max(limit, 0), 50)):
            now = datetime.now(timezone.utc)
            with Session(bind) as db, db.begin():
                require_member(db, household_id, user_id)
                row = db.scalar(select(ProductCorrection).where(ProductCorrection.household_id == household_id,
                    ProductCorrection.status.in_(("pending", "failed")), ProductCorrection.attempts < 5,
                    or_(ProductCorrection.lease_until.is_(None), ProductCorrection.lease_until < now))
                    .order_by(ProductCorrection.created_at, ProductCorrection.id).limit(1).with_for_update(skip_locked=True))
                if row is None:
                    break
                value = source_for(db, household_id, row.purchase_item_id)
                if value is None:
                    row.status, row.last_error_code = "blocked", "source_unavailable_or_private"
                    counts["blocked"] += 1
                    continue
                row.fingerprint = digest = fingerprint(value)
                row.attempts += 1
                row.lease_until = lease = now + timedelta(seconds=120)
                correction_id = row.id
            try:
                upsert_example(household_id, correction_id, digest, value, trace)
                success = True
            except Exception:
                success = False  # Never expose provider exception text or request content.
            with Session(bind) as db, db.begin():
                row = db.scalar(select(ProductCorrection).where(ProductCorrection.id == correction_id,
                    ProductCorrection.household_id == household_id).with_for_update())
                if row is None:
                    continue
                # An expired worker must not overwrite a newer claim or changed example.
                stored_lease = row.lease_until
                if stored_lease is not None:
                    stored_lease = stored_lease.replace(tzinfo=timezone.utc) if stored_lease.tzinfo is None else stored_lease.astimezone(timezone.utc)
                same_lease = stored_lease == lease
                if row.fingerprint != digest or not same_lease:
                    continue
                row.lease_until = None
                row.status = "indexed" if success else "failed"
                row.indexed_fingerprint = digest if success else None
                row.last_error_code = None if success else "index_provider_unavailable"
                counts["indexed" if success else "failed"] += 1
            if not success:
                break  # No repeated provider retry loop in one request.
        trace.finish()
    except Exception as error:
        trace.finish(error)
        # A background job must never turn a saved purchase into a failed response.
    finally:
        if trace.finished:
            trace.send()
    return counts


def queue_status(db, household_id):
    return dict(db.execute(select(ProductCorrection.status, func.count()).where(
        ProductCorrection.household_id == household_id).group_by(ProductCorrection.status)).all())
