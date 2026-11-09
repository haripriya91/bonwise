from datetime import date
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    ForeignKey,
    ForeignKeyConstraint,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Record


class HouseholdBudget(Record, Base):
    __tablename__ = "household_budgets"
    household_id: Mapped[UUID] = mapped_column(
        ForeignKey("households.id", ondelete="CASCADE"), index=True
    )
    client_request_id: Mapped[UUID] = mapped_column()
    creation_digest: Mapped[str] = mapped_column(String(64))
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    amount_cents: Mapped[int] = mapped_column(BigInteger)
    version: Mapped[int] = mapped_column(default=1)
    __table_args__ = (
        UniqueConstraint("household_id", "id", name="uq_budget_household_id"),
        UniqueConstraint("household_id", "client_request_id", name="uq_budget_request"),
        CheckConstraint("end_date >= start_date", name="valid_period"),
        CheckConstraint(
            "amount_cents >= 0 AND amount_cents <= 1000000000000", name="valid_amount"
        ),
        CheckConstraint("version > 0", name="positive_version"),
    )


class BudgetCommitment(Record, Base):
    __tablename__ = "budget_commitments"
    household_id: Mapped[UUID] = mapped_column(index=True)
    budget_id: Mapped[UUID] = mapped_column(index=True)
    client_request_id: Mapped[UUID] = mapped_column()
    creation_digest: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(120))
    planned_on: Mapped[date] = mapped_column(Date)
    amount_cents: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(12), default="open")
    purchase_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("purchases.id"), unique=True
    )
    __table_args__ = (
        ForeignKeyConstraint(
            ["household_id", "budget_id"],
            ["household_budgets.household_id", "household_budgets.id"],
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "household_id", "client_request_id", name="uq_commitment_request"
        ),
        CheckConstraint(
            "amount_cents > 0 AND amount_cents <= 1000000000000", name="valid_amount"
        ),
        CheckConstraint(
            "status IN ('open','settled','cancelled')", name="valid_status"
        ),
        CheckConstraint(
            "(status = 'settled' AND purchase_id IS NOT NULL) OR "
            "(status <> 'settled' AND purchase_id IS NULL)",
            name="settlement_link",
        ),
    )