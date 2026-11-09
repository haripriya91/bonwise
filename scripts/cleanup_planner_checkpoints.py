"""Operator-only, dry-run-first cleanup of old closed-plan checkpoints.

Approved/draft plans and financial records are never deleted by this command.
"""
import argparse
from datetime import datetime, timedelta, timezone
from sqlalchemy import delete, select
from app.db.session import SessionLocal
from app.db.planner import WeeklyPlan, PlannerCheckpoint, PlannerWrite

if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days",type=int,default=30)
    parser.add_argument("--apply",action="store_true")
    args=parser.parse_args()
    if args.days<7:parser.error("Use a retention period of at least 7 days")
    with SessionLocal() as db,db.begin():
        plans=db.scalars(select(WeeklyPlan).where(WeeklyPlan.status.in_(("cancelled","settled")),
            WeeklyPlan.updated_at<datetime.now(timezone.utc)-timedelta(days=args.days))).all()
        print({"closed_plans_eligible":len(plans),"dry_run":not args.apply})
        if args.apply:
            ids=[plan.id for plan in plans]
            for table in (PlannerWrite,PlannerCheckpoint):
                db.execute(delete(table).where(table.plan_id.in_(ids)))
        # Expired text previews have no raw input text and can be removed safely.
        for plan in db.scalars(select(WeeklyPlan).where(WeeklyPlan.pending_text.is_not(None))):
            if plan.pending_text and datetime.fromisoformat(plan.pending_text["expires_at"])<datetime.now(timezone.utc) and args.apply:
                plan.pending_text=None
