"""Authorize a household, backfill confirmed accepted links and retry bounded indexing.

Default: SQL-only dry run. --apply may call OpenAI embeddings and Pinecone.
"""
import argparse
import json
from uuid import UUID
from sqlalchemy import select, update
from app.db.corrections import ProductCorrection
from app.db.models import Purchase
from app.db.session import SessionLocal, engine
from app.services.purchases import require_member
from app.services.correction_memory import enqueue_purchase, process_pending, queue_status


def run(household_id, user_id, *, apply=False, limit=20, retry_failed=False):
    with SessionLocal() as db, db.begin():
        require_member(db, household_id, user_id, lock=apply)
        if apply:
            purchases = db.scalars(select(Purchase.id).where(Purchase.household_id == household_id)
                .order_by(Purchase.id).limit(1001)).all()
            if len(purchases) > 1000:
                raise ValueError("Backfill limit exceeded; use a reviewed batch migration.")
            queued = sum(enqueue_purchase(db, household_id, purchase_id) for purchase_id in purchases)
            if retry_failed:
                db.execute(update(ProductCorrection).where(ProductCorrection.household_id == household_id,
                    ProductCorrection.status.in_(("failed", "blocked"))).values(status="pending", attempts=0,
                        lease_until=None, last_error_code=None))
        else:
            queued = 0
        status = queue_status(db, household_id)
    result = dict(mode="apply" if apply else "dry_run", queued=queued, before=status, provider_calls_allowed=apply)
    if apply:
        result["processed"] = process_pending(engine, household_id, user_id, limit=limit)
        with SessionLocal() as db, db.begin():
            require_member(db, household_id, user_id)
            result["after"] = queue_status(db, household_id)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--household-id", type=UUID, required=True)
    parser.add_argument("--user-id", type=UUID, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()
    if not 1 <= args.limit <= 50:
        parser.error("Use --limit between 1 and 50.")
    try:
        print(json.dumps(run(args.household_id, args.user_id, apply=args.apply,
            limit=args.limit, retry_failed=args.retry_failed), indent=2))
    except Exception:
        raise SystemExit("Correction indexing failed. Check access, migration and provider configuration privately.") from None


if __name__ == "__main__":
    main()
