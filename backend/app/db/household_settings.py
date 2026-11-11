"""Small account profile and household-owned preferences/needs."""
from uuid import UUID
from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Integer, String, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base, Record


class HouseholdSettings(Base):
    __tablename__ = "household_settings"
    household_id: Mapped[UUID] = mapped_column(ForeignKey("households.id", ondelete="CASCADE"), primary_key=True)
    shopping_day: Mapped[int | None] = mapped_column(Integer)
    setup_completed: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    __table_args__ = (CheckConstraint("shopping_day BETWEEN 0 AND 6", name="shopping_day"),)


class AccountProfile(Base):
    __tablename__ = "account_profiles"
    user_id: Mapped[UUID] = mapped_column(ForeignKey("auth.users.id", ondelete="CASCADE"), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(80), default="")
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")


class HouseholdNeed(Record, Base):
    __tablename__ = "household_needs"
    household_id: Mapped[UUID] = mapped_column(ForeignKey("households.id", ondelete="CASCADE"), index=True)
    item: Mapped[dict] = mapped_column(JSONB)
