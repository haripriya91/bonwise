"""Durable indexing queue; accepted wording remains in confirmed purchase items."""
from datetime import datetime
from uuid import UUID
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base, Record


class ProductCorrection(Record, Base):
    __tablename__ = "product_corrections"
    household_id: Mapped[UUID] = mapped_column(ForeignKey("households.id", ondelete="CASCADE"), index=True)
    purchase_item_id: Mapped[UUID] = mapped_column(ForeignKey("purchase_items.id", ondelete="CASCADE"), unique=True)
    fingerprint: Mapped[str] = mapped_column(String(64))
    indexed_fingerprint: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(12), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(default=0)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(40))
    __table_args__ = (
        CheckConstraint("status IN ('pending','indexed','failed','blocked')", name="valid_status"),
        CheckConstraint("attempts >= 0", name="nonnegative_attempts"),
    )
