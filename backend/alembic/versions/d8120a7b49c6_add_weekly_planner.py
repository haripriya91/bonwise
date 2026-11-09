"""Add weekly plans and plan-scoped durable LangGraph checkpoints.

Revision ID: d8120a7b49c6
Revises: ee1c183bad07
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "d8120a7b49c6"
down_revision = "ee1c183bad07"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("weekly_plans",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("household_id", sa.Uuid(), sa.ForeignKey("households.id", ondelete="CASCADE"), nullable=False),
        sa.Column("client_request_id", sa.Uuid(), nullable=False),
        sa.Column("week_start", sa.Date(), nullable=False), sa.Column("planned_on", sa.Date(), nullable=False),
        sa.Column("status", sa.String(12), nullable=False), sa.Column("phase", sa.String(24), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False), sa.Column("thread_id", sa.Uuid(), nullable=False),
        sa.Column("content", JSONB(), nullable=False),
        sa.Column("commitment_id", sa.Uuid(), sa.ForeignKey("budget_commitments.id")),
        sa.Column("approved_snapshot", JSONB()), sa.Column("approval_request_id", sa.Uuid()),
        sa.Column("approval_digest", sa.String(64)), sa.Column("pending_text", JSONB()),
        sa.Column("run_token", sa.Uuid()), sa.Column("run_until", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("household_id", "client_request_id"),
    )
    op.create_index("ix_weekly_plans_household_id", "weekly_plans", ["household_id"])
    op.create_table("planner_checkpoints",
        sa.Column("thread_id", sa.String(64), primary_key=True),
        sa.Column("checkpoint_ns", sa.String(200), primary_key=True),
        sa.Column("checkpoint_id", sa.String(64), primary_key=True),
        sa.Column("plan_id", sa.Uuid(), sa.ForeignKey("weekly_plans.id", ondelete="CASCADE"), nullable=False),
        sa.Column("household_id", sa.Uuid(), sa.ForeignKey("households.id", ondelete="CASCADE"), nullable=False),
        sa.Column("parent_id", sa.String(64)), sa.Column("data_type", sa.String(32), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=False), sa.Column("metadata_type", sa.String(32), nullable=False),
        sa.Column("metadata_blob", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table("planner_checkpoint_writes",
        sa.Column("thread_id", sa.String(64), primary_key=True),
        sa.Column("checkpoint_ns", sa.String(200), primary_key=True),
        sa.Column("checkpoint_id", sa.String(64), primary_key=True),
        sa.Column("task_id", sa.String(100), primary_key=True), sa.Column("idx", sa.Integer(), primary_key=True),
        sa.Column("plan_id", sa.Uuid(), sa.ForeignKey("weekly_plans.id", ondelete="CASCADE"), nullable=False),
        sa.Column("household_id", sa.Uuid(), sa.ForeignKey("households.id", ondelete="CASCADE"), nullable=False),
        sa.Column("channel", sa.String(200), nullable=False), sa.Column("data_type", sa.String(32), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=False),
    )
    for table in ("planner_checkpoints", "planner_checkpoint_writes"):
        for column in ("plan_id", "household_id"):
            op.create_index(f"ix_{table}_{column}", table, [column])
    for table in ("weekly_plans", "planner_checkpoints", "planner_checkpoint_writes"):
        op.execute(f'ALTER TABLE public."{table}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'REVOKE ALL ON TABLE public."{table}" FROM PUBLIC, anon, authenticated')


def downgrade():
    op.drop_table("planner_checkpoint_writes")
    op.drop_table("planner_checkpoints")
    op.drop_table("weekly_plans")
