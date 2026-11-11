"""Household settings, minimal profiles, saved needs and backend-only RLS.

Revision ID: a47f83d20e91
Revises: c93a6140be28
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "a47f83d20e91"
down_revision = "c93a6140be28"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("household_settings",
        sa.Column("household_id", sa.Uuid(), sa.ForeignKey("households.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("shopping_day", sa.Integer()),
        sa.Column("setup_completed", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.CheckConstraint("shopping_day BETWEEN 0 AND 6", name="ck_household_settings_shopping_day"))
    op.create_table("account_profiles",
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("auth.users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("display_name", sa.String(80), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"))
    op.create_table("household_needs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("household_id", sa.Uuid(), sa.ForeignKey("households.id", ondelete="CASCADE"), nullable=False),
        sa.Column("item", JSONB(), nullable=False))
    op.create_index("ix_household_needs_household_id", "household_needs", ["household_id"])
    for table in ("household_settings", "account_profiles", "household_needs",
                  "household_budgets", "budget_commitments", "bonwise_alembic_version"):
        op.execute(f'ALTER TABLE public."{table}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'REVOKE ALL ON TABLE public."{table}" FROM PUBLIC, anon, authenticated')


def downgrade():
    op.drop_index("ix_household_needs_household_id", table_name="household_needs")
    op.drop_table("household_needs")
    op.drop_table("account_profiles")
    op.drop_table("household_settings")
    # Existing financial/version tables deliberately retain their security hardening.
