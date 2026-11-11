from fastapi import HTTPException
from sqlalchemy import select, func
from app.db.models import Household, HouseholdMember, Product
from app.db.household_settings import AccountProfile, HouseholdSettings, HouseholdNeed
from app.services.purchases import require_member
from app.services.budgets import lock_household
from app.core.privacy import has_detectable_pii


def create_household(db, user_id, body):
    with db.begin():
        from app.db.base import Base
        exists = db.scalar(select(Base.metadata.tables["auth.users"].c.id).where(
            Base.metadata.tables["auth.users"].c.id == user_id).with_for_update())
        if exists is None:
            raise HTTPException(401, "Sign in again before creating a household.")
        prior = db.get(Household, body.client_request_id)
        if prior:
            role = db.scalar(select(HouseholdMember.role).where(
                HouseholdMember.household_id == prior.id, HouseholdMember.user_id == user_id))
            if role != "owner":
                raise HTTPException(404, "Household not available.")
            if prior.name != body.name:
                raise HTTPException(409, "This creation request was already used.")
            return settings_value(db, prior.id, user_id)
        count = db.scalar(select(func.count()).select_from(HouseholdMember).where(
            HouseholdMember.user_id == user_id, HouseholdMember.role == "owner"))
        if count >= 5:
            raise HTTPException(422, "At most five owned households are supported in this version.")
        row = Household(id=body.client_request_id, name=body.name)
        db.add(row)
        db.flush()
        db.add(HouseholdMember(household_id=row.id, user_id=user_id, role="owner"))
        db.add(HouseholdSettings(household_id=row.id))
        db.flush()
        return settings_value(db, row.id, user_id)


def settings_value(db, household_id, user_id):
    require_member(db, household_id, user_id)
    household = db.get(Household, household_id)
    row = db.get(HouseholdSettings, household_id)
    # require_member's return value is intentionally not relied on.
    role = db.scalar(select(HouseholdMember.role).where(HouseholdMember.household_id == household_id, HouseholdMember.user_id == user_id))
    return dict(household_id=str(household_id), name=household.name, currency=household.currency,
        shopping_day=row.shopping_day if row else None, setup_completed=row.setup_completed if row else False,
        version=row.version if row else 0, can_edit=role == "owner")


def read_settings(db, household_id, user_id):
    with db.begin():
        return settings_value(db, household_id, user_id)


def update_settings(db, household_id, user_id, body):
    with db.begin():
        lock_household(db, household_id, user_id)
        current = settings_value(db, household_id, user_id)
        if not current["can_edit"]:
            raise HTTPException(403, "Only a household owner can change shared settings.")
        if current["version"] != body.expected_version:
            raise HTTPException(409, "Household settings changed. Reload before saving.")
        row = db.get(HouseholdSettings, household_id)
        if row is None:
            row = HouseholdSettings(household_id=household_id, version=0)
            db.add(row)
        db.get(Household, household_id).name = body.name
        row.shopping_day, row.setup_completed = body.shopping_day, body.setup_completed
        row.version += 1
        db.flush()
        return settings_value(db, household_id, user_id)


def profile_value(db, user_id):
    row = db.get(AccountProfile, user_id)
    return dict(display_name=row.display_name if row else "", version=row.version if row else 0)


def read_profile(db, user_id):
    with db.begin():
        return profile_value(db, user_id)


def update_profile(db, user_id, body):
    with db.begin():
        # The auth row serializes first-profile creation without exposing email/password fields.
        from app.db.base import Base
        db.execute(select(Base.metadata.tables["auth.users"].c.id).where(
            Base.metadata.tables["auth.users"].c.id == user_id).with_for_update()).one()
        row = db.get(AccountProfile, user_id)
        if (row.version if row else 0) != body.expected_version:
            raise HTTPException(409, "Your profile changed. Reload before saving.")
        if row is None:
            row = AccountProfile(user_id=user_id, version=0)
            db.add(row)
        row.display_name = body.display_name
        row.version += 1
        db.flush()
        return profile_value(db, user_id)


def read_needs(db, household_id, user_id):
    with db.begin():
        require_member(db, household_id, user_id)
        rows = db.scalars(select(HouseholdNeed).where(HouseholdNeed.household_id == household_id)
            .order_by(HouseholdNeed.created_at, HouseholdNeed.id).limit(50)).all()
        return {"needs": [dict(id=str(row.id), item=row.item) for row in rows]}


def save_needs(db, household_id, user_id, body):
    with db.begin():
        lock_household(db, household_id, user_id)
        count = db.scalar(select(func.count()).select_from(HouseholdNeed).where(HouseholdNeed.household_id == household_id))
        for need in body.needs:
            row = db.get(HouseholdNeed, need.id)
            if row:
                if row.household_id != household_id:
                    raise HTTPException(404, "Saved need not found.")
                if row.item != need.item.model_dump(mode="json"):
                    raise HTTPException(409, "This saved-need request has already been used.")
                continue
            if count >= 50:
                raise HTTPException(422, "Keep at most 50 upcoming needs. Remove older entries first.")
            if has_detectable_pii(need.item.name):
                raise HTTPException(422, "Save item names without personal details.")
            if need.item.product_id and db.get(Product, need.item.product_id) is None:
                raise HTTPException(422, "Choose a current catalogue product.")
            db.add(HouseholdNeed(id=need.id, household_id=household_id, item=need.item.model_dump(mode="json")))
            count += 1
        db.flush()
    return read_needs(db, household_id, user_id)


def delete_need(db, household_id, user_id, need_id):
    with db.begin():
        lock_household(db, household_id, user_id)
        row = db.scalar(select(HouseholdNeed).where(HouseholdNeed.id == need_id, HouseholdNeed.household_id == household_id))
        if row is None:
            raise HTTPException(404, "Saved need not found.")
        db.delete(row)
    return {"deleted": True}
