import base64
import binascii
import hashlib
import re

from fastapi import HTTPException
from sqlalchemy import select

from app.ai.image_extractor import MAX_INPUT_BYTES, prepare_image
from app.db.models import Product, Purchase, PurchaseDraft
from app.services import receipt_storage as storage
from app.services.purchases import (
    draft_revision, draft_view, load_draft, purchase_view, require_member,
)


def receipt_key(draft):
    sha = draft.file_sha256
    if not isinstance(sha, str) or re.fullmatch(r"[a-f0-9]{64}", sha) is None:
        return None
    expected = f"{draft.household_id}/drafts/{draft.id}/receipt-{sha}.jpg"
    return expected if draft.file_key == expected else None


def check_pending(draft, revision):
    if draft_revision(draft) != revision:
        raise HTTPException(409, "Draft changed. Reload it before changing its receipt.")
    if draft.status != "pending":
        raise HTTPException(409, "Only pending drafts can change their receipt.")
    if draft.source_type != "image":
        raise HTTPException(422, "Only image purchases can attach a receipt in this version.")


def attach_receipt(db, household_id, user_id, draft_id, body):
    with db.begin():
        require_member(db, household_id, user_id, lock=True)
        draft = load_draft(db, household_id, draft_id, lock=True)
        check_pending(draft, body.revision)
        old_key = receipt_key(draft)
        if draft.file_key and old_key is None:
            raise HTTPException(409, "Receipt reference is invalid. Contact support.")
        try:
            original = base64.b64decode(body.image_base64, validate=True)
        except (ValueError, binascii.Error):
            raise HTTPException(422, "Invalid base64 image.") from None
        if not original or len(original) > MAX_INPUT_BYTES:
            raise HTTPException(413, "Image must be non-empty and at most 2 MB.")
        if hashlib.sha256(original).hexdigest() != body.source_sha256:
            raise HTTPException(422, "Reviewed image changed. Review it again before saving.")
        # Reuse the current JPEG/PNG, animation, size, pixel and metadata checks.
        prepared = prepare_image(body.image_base64)
        sha = hashlib.sha256(prepared).hexdigest()
        key = f"{household_id}/drafts/{draft_id}/receipt-{sha}.jpg"
        try:
            storage.upload(key, prepared, sha)
            if old_key is not None and old_key != key:
                storage.delete(old_key)
        except storage.ReceiptStorageError:
            raise HTTPException(503, "Receipt could not be saved. Retry or continue without saving the receipt.") from None
        draft.file_key = key
        draft.file_sha256 = sha
        db.flush()
        return draft_view(db, draft)


def remove_receipt(db, household_id, user_id, draft_id, revision):
    with db.begin():
        require_member(db, household_id, user_id, lock=True)
        draft = load_draft(db, household_id, draft_id, lock=True)
        check_pending(draft, revision)
        key = receipt_key(draft)
        if draft.file_key and key is None:
            raise HTTPException(409, "Receipt reference is invalid. Contact support.")
        if key:
            try:
                storage.delete(key)
            except storage.ReceiptStorageError:
                raise HTTPException(503, "Receipt could not be removed. Retry before confirming.") from None
        draft.file_key = None
        draft.file_sha256 = None
        db.flush()
        return draft_view(db, draft)


def load_confirmed(db, household_id, purchase_id):
    row = db.execute(select(Purchase, PurchaseDraft).join(
        PurchaseDraft, (PurchaseDraft.id == Purchase.draft_id)
        & (PurchaseDraft.household_id == Purchase.household_id)
    ).where(Purchase.id == purchase_id, Purchase.household_id == household_id,
            PurchaseDraft.status == "confirmed")).one_or_none()
    if row is None:
        raise HTTPException(404, "Purchase not found.")
    return row


def get_details(db, household_id, user_id, purchase_id):
    with db.begin():
        require_member(db, household_id, user_id)
        purchase, draft = load_confirmed(db, household_id, purchase_id)
        result = purchase_view(db, purchase)
        ids = {item["product_id"] for item in result["items"] if item["product_id"]}
        products = db.scalars(select(Product).where(Product.id.in_(ids))).all() if ids else []
        result.update(source_type=draft.source_type, receipt_available=receipt_key(draft) is not None,
            products=[dict(id=p.id, name=p.canonical_name, brand=p.brand,
                           pack_base_quantity=p.pack_base_quantity, base_unit=p.base_unit)
                      for p in products])
        return result


def get_receipt(db, household_id, user_id, *, purchase_id=None, draft_id=None):
    with db.begin():
        require_member(db, household_id, user_id)
        if purchase_id is not None:
            _, draft = load_confirmed(db, household_id, purchase_id)
        else:
            draft = load_draft(db, household_id, draft_id)
        key, sha = receipt_key(draft), draft.file_sha256
        if key is None:
            raise HTTPException(404, "No saved receipt is available for this purchase.")
    # Do not keep a database transaction open while serving a download.
    try:
        return storage.download(key, sha)
    except storage.ReceiptMissing:
        raise HTTPException(404, "The saved receipt is unavailable.") from None
    except storage.ReceiptStorageError:
        raise HTTPException(503, "Receipt unavailable right now. Please retry.") from None