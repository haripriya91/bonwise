from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    Date,
    ForeignKey,
    ForeignKeyConstraint,
    Numeric,
    String,
    Table,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Record


CATEGORY_CHECK = (
    "category IN ('groceries','clothing','household',"
    "'personal_care','transport','dining_out','other')"
)

# Reference only. Supabase owns and manages this table.
Table(
    "users",
    Base.metadata,
    Column("id", Uuid, primary_key=True),
    schema="auth",
)


class Household(Record, Base):
    __tablename__ = "households"

    name: Mapped[str] = mapped_column(String(120))
    currency: Mapped[str] = mapped_column(
        String(3),
        server_default="EUR",
    )
    timezone: Mapped[str] = mapped_column(
        String(50),
        server_default="Europe/Berlin",
    )

    __table_args__ = (
        CheckConstraint(
            "currency = 'EUR'",
            name="currency_eur",
        ),
    )


class HouseholdMember(Record, Base):
    __tablename__ = "household_members"

    household_id: Mapped[UUID] = mapped_column(
        ForeignKey("households.id", ondelete="CASCADE"),
        index=True,
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("auth.users.id", ondelete="CASCADE"),
        index=True,
    )
    role: Mapped[str] = mapped_column(
        String(16),
        server_default="member",
    )

    __table_args__ = (
        UniqueConstraint("household_id", "user_id"),
        CheckConstraint(
            "role IN ('owner','member')",
            name="valid_role",
        ),
    )


class Product(Record, Base):
    __tablename__ = "products"

    canonical_name: Mapped[str] = mapped_column(String(200))
    name_de: Mapped[str | None] = mapped_column(String(200))
    name_en: Mapped[str | None] = mapped_column(String(200))
    brand: Mapped[str | None] = mapped_column(String(120))
    category: Mapped[str] = mapped_column(String(24))
    base_unit: Mapped[str | None] = mapped_column(String(12))
    pack_base_quantity: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 4)
    )
    attributes: Mapped[dict] = mapped_column(
        JSONB,
        default=dict,
        server_default="{}",
    )

    __table_args__ = (
        CheckConstraint(
            CATEGORY_CHECK,
            name="valid_category",
        ),
        CheckConstraint(
            "pack_base_quantity > 0",
            name="positive_pack",
        ),
    )


class PurchaseDraft(Record, Base):
    __tablename__ = "purchase_drafts"

    household_id: Mapped[UUID] = mapped_column(
        ForeignKey("households.id", ondelete="CASCADE"),
        index=True,
    )
    created_by: Mapped[UUID] = mapped_column(
        ForeignKey("auth.users.id")
    )
    source_type: Mapped[str] = mapped_column(String(12))
    status: Mapped[str] = mapped_column(
        String(16),
        server_default="pending",
    )
    data: Mapped[dict] = mapped_column(
        JSONB,
        default=dict,
        server_default="{}",
    )
    file_key: Mapped[str | None] = mapped_column(String(500))
    file_sha256: Mapped[str | None] = mapped_column(
        String(64),
        index=True,
    )
    client_request_id: Mapped[UUID | None] = mapped_column()

    __table_args__ = (
        UniqueConstraint(
            "household_id",
            "id",
            name="uq_draft_household_id",
        ),
        UniqueConstraint(
            "household_id",
            "client_request_id",
        ),
        CheckConstraint(
            "source_type IN ('image','voice','text','seed')",
            name="valid_source",
        ),
        CheckConstraint(
            "status IN ('pending','confirmed','rejected')",
            name="valid_status",
        ),
    )


class Purchase(Record, Base):
    __tablename__ = "purchases"

    household_id: Mapped[UUID] = mapped_column(
        ForeignKey("households.id", ondelete="CASCADE"),
        index=True,
    )
    draft_id: Mapped[UUID] = mapped_column(unique=True)
    merchant: Mapped[str | None] = mapped_column(String(200))
    purchased_on: Mapped[date] = mapped_column(
        Date,
        index=True,
    )
    total_cents: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(
        String(3),
        server_default="EUR",
    )
    adjustments: Mapped[list] = mapped_column(
        JSONB,
        default=list,
        server_default="[]",
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["household_id", "draft_id"],
            ["purchase_drafts.household_id", "purchase_drafts.id"],
        ),
        CheckConstraint(
            "total_cents >= 0",
            name="nonnegative_total",
        ),
        CheckConstraint(
            "currency = 'EUR'",
            name="currency_eur",
        ),
    )


class PurchaseItem(Record, Base):
    __tablename__ = "purchase_items"

    purchase_id: Mapped[UUID] = mapped_column(
        ForeignKey("purchases.id", ondelete="CASCADE"),
        index=True,
    )
    product_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("products.id", ondelete="SET NULL"),
        index=True,
    )
    original_text: Mapped[str] = mapped_column(String(300))
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    quantity_unit: Mapped[str] = mapped_column(
        String(12),
        server_default="pack",
    )
    unit_price_cents: Mapped[int | None] = mapped_column(
        BigInteger
    )
    line_total_cents: Mapped[int] = mapped_column(BigInteger)
    category: Mapped[str] = mapped_column(String(24))

    __table_args__ = (
        CheckConstraint(
            "quantity > 0",
            name="positive_quantity",
        ),
        CheckConstraint(
            "unit_price_cents >= 0",
            name="nonnegative_unit_price",
        ),
        CheckConstraint(
            "line_total_cents >= 0",
            name="nonnegative_line_total",
        ),
        CheckConstraint(
            CATEGORY_CHECK,
            name="valid_category",
        ),
    )

from app.db import commerce as _commerce  # noqa: F401