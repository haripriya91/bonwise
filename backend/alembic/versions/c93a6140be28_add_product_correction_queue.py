"""Queue confirmed product examples for household-scoped semantic retrieval.

Revision ID: c93a6140be28
Revises: d8120a7b49c6
"""
from alembic import op
import sqlalchemy as sa

revision = "c93a6140be28"
down_revision = "d8120a7b49c6"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("product_corrections",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("household_id", sa.Uuid(), sa.ForeignKey("households.id", ondelete="CASCADE"), nullable=False),
        sa.Column("purchase_item_id", sa.Uuid(), sa.ForeignKey("purchase_items.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("indexed_fingerprint", sa.String(64)),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("last_error_code", sa.String(40)),
        sa.CheckConstraint("status IN ('pending','indexed','failed','blocked')", name="valid_status"),
        sa.CheckConstraint("attempts >= 0", name="nonnegative_attempts"),
    )
    op.create_index("ix_product_corrections_household_id", "product_corrections", ["household_id"])
    op.create_index("ix_product_corrections_status", "product_corrections", ["status"])
    op.execute('ALTER TABLE public.product_corrections ENABLE ROW LEVEL SECURITY')
    op.execute('REVOKE ALL ON TABLE public.product_corrections FROM PUBLIC, anon, authenticated')


def downgrade():
    op.drop_table("product_corrections")
