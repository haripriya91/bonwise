from datetime import date, datetime
from uuid import UUID, uuid4

from sqlalchemy import Date, DateTime, ForeignKey, Integer, JSON, LargeBinary, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Record

Json = JSON().with_variant(JSONB(), "postgresql")


class WeeklyPlan(Record, Base):
    __tablename__ = "weekly_plans"
    household_id: Mapped[UUID] = mapped_column(ForeignKey("households.id", ondelete="CASCADE"), index=True)
    client_request_id: Mapped[UUID] = mapped_column()
    week_start: Mapped[date] = mapped_column(Date)
    planned_on: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(12), default="draft")
    phase: Mapped[str] = mapped_column(String(24), default="preparing")
    version: Mapped[int] = mapped_column(default=1)
    thread_id: Mapped[UUID] = mapped_column(default=uuid4)
    content: Mapped[dict] = mapped_column(Json, default=dict)
    commitment_id: Mapped[UUID | None] = mapped_column(ForeignKey("budget_commitments.id"))
    approved_snapshot: Mapped[dict | None] = mapped_column(Json)
    approval_request_id: Mapped[UUID | None] = mapped_column()
    approval_digest: Mapped[str | None] = mapped_column(String(64))
    pending_text: Mapped[dict | None] = mapped_column(Json)
    run_token: Mapped[UUID | None] = mapped_column()
    run_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (UniqueConstraint("household_id", "client_request_id"),)


class PlannerCheckpoint(Base):
    __tablename__ = "planner_checkpoints"
    thread_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    checkpoint_ns: Mapped[str] = mapped_column(String(200), primary_key=True)
    checkpoint_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    plan_id: Mapped[UUID] = mapped_column(ForeignKey("weekly_plans.id", ondelete="CASCADE"), index=True)
    household_id: Mapped[UUID] = mapped_column(ForeignKey("households.id", ondelete="CASCADE"), index=True)
    parent_id: Mapped[str | None] = mapped_column(String(64))
    data_type: Mapped[str] = mapped_column(String(32))
    data: Mapped[bytes] = mapped_column(LargeBinary)
    metadata_type: Mapped[str] = mapped_column(String(32))
    metadata_blob: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PlannerWrite(Base):
    __tablename__ = "planner_checkpoint_writes"
    thread_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    checkpoint_ns: Mapped[str] = mapped_column(String(200), primary_key=True)
    checkpoint_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    task_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    idx: Mapped[int] = mapped_column(Integer, primary_key=True)
    plan_id: Mapped[UUID] = mapped_column(ForeignKey("weekly_plans.id", ondelete="CASCADE"), index=True)
    household_id: Mapped[UUID] = mapped_column(ForeignKey("households.id", ondelete="CASCADE"), index=True)
    channel: Mapped[str] = mapped_column(String(200))
    data_type: Mapped[str] = mapped_column(String(32))
    data: Mapped[bytes] = mapped_column(LargeBinary)
