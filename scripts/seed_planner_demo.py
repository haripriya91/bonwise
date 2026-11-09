"""Optional recurring synthetic history in a NEW, dedicated demo household.

Default is a dry run. Never updates/deletes an existing household's purchases.
"""
import argparse
from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID, NAMESPACE_URL, uuid5

from sqlalchemy import select

from app.db.models import Household, HouseholdMember, Product, PurchaseDraft, Purchase, PurchaseItem
from app.db.commerce import HouseholdBudget
from app.db.session import SessionLocal
from app.services.planner import monday
from app.schemas.purchase import PurchaseContent


def run(user_id, apply=False):
    today = date.today()
    household_id = uuid5(NAMESPACE_URL, f"bonwise:planner-demo-v1:{user_id}")
    with SessionLocal() as db, db.begin():
        products = db.scalars(select(Product).where(Product.category == "groceries").order_by(Product.id)).all()
        def choose(words, excluded=()):
            return next((p for p in products if any(w in p.canonical_name.casefold() for w in words)
                and not any(w in p.canonical_name.casefold() for w in excluded)), None)
        milk = choose(["whole milk"], ["lactose"])
        bread = choose(["bread"], ["roll", "milk bread"])
        coffee = choose(["coffee"])
        rice = choose(["rice"])
        if not all([milk,bread,coffee,rice]):
            raise SystemExit("Import the existing catalogue first; milk, bread, coffee and rice are required.")
        baskets = []
        for back in range(8,0,-1):
            when = monday(today)-timedelta(weeks=back)+timedelta(days=5)
            lines = [(milk,"2",119),(bread,"1",249)]
            if back%2 == 0: lines.append((coffee,"1",599))
            if back in (7,2): lines.append((rice,"1",179))
            baskets.append((when,lines))
        result = dict(dry_run=not apply, household_id=str(household_id), household_name="Planner routine · synthetic demo",
            weeks=8, baskets=len(baskets), note="Fictional prices and deliberate recurring purchases. Existing households are not modified.")
        if not apply:
            return result
        household = db.get(Household, household_id)
        if household is None:
            db.add(Household(id=household_id,name=result["household_name"]))
            db.flush()
        member = db.scalar(select(HouseholdMember).where(HouseholdMember.household_id==household_id,HouseholdMember.user_id==user_id))
        if not member:
            db.add(HouseholdMember(household_id=household_id,user_id=user_id,role="owner"))
        for when,lines in baskets:
            did = uuid5(household_id,"basket:"+when.isoformat())
            if db.get(PurchaseDraft,did): continue
            content = PurchaseContent.model_validate(dict(merchant="Bonwise routine demo (fictional)",purchased_on=when,currency="EUR",items=[
                dict(original_text=p.canonical_name,product_id=p.id,quantity=q,quantity_unit="pack",unit_price_cents=price,category=p.category)
                for p,q,price in lines],adjustments=[],reported_total_cents=sum(int(Decimal(q)*price) for _,q,price in lines)))
            draft = PurchaseDraft(id=did,household_id=household_id,created_by=user_id,source_type="seed",status="confirmed",
                client_request_id=did,data={"content":content.model_dump(mode="json"),"warnings":[]})
            db.add(draft);db.flush()
            purchase = Purchase(id=uuid5(did,"purchase"),household_id=household_id,draft_id=did,
                merchant=content.merchant,purchased_on=when,total_cents=content.reported_total_cents,adjustments=[])
            db.add(purchase);db.flush()
            for index,(p,q,price) in enumerate(lines):
                db.add(PurchaseItem(id=uuid5(did,f"item:{index}"),purchase_id=purchase.id,product_id=p.id,
                    original_text=p.canonical_name,quantity=Decimal(q),quantity_unit="pack",unit_price_cents=price,
                    line_total_cents=int(Decimal(q)*price),category=p.category))
        start=today.replace(day=1)
        end=(start.replace(year=start.year+1,month=1) if start.month==12 else start.replace(month=start.month+1))-timedelta(days=1)
        request_id=uuid5(household_id,"budget:"+start.isoformat())
        if not db.scalar(select(HouseholdBudget.id).where(HouseholdBudget.household_id==household_id,
                HouseholdBudget.start_date<=end,HouseholdBudget.end_date>=start)):
            db.add(HouseholdBudget(household_id=household_id,client_request_id=request_id,creation_digest="0"*64,
                start_date=start,end_date=end,amount_cents=50000,version=1))
        return result


if __name__=="__main__":
    import json
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user-id",type=UUID,required=True,help="Your existing Supabase auth user UUID, not a token or key")
    parser.add_argument("--apply",action="store_true")
    args=parser.parse_args()
    print(json.dumps(run(args.user_id,args.apply),indent=2))
