"""A plan-bound durable LangGraph checkpointer using the existing SQLAlchemy DB.

Only backend-created thread IDs are accepted. Authorization occurs before a
workflow is constructed, and every row is additionally scoped to plan/household.
No raw input text, prompts, audio or receipts are put into graph state.
"""
from sqlalchemy import delete, select
from langgraph.checkpoint.base import BaseCheckpointSaver, CheckpointTuple, WRITES_IDX_MAP

from app.db.planner import PlannerCheckpoint, PlannerWrite


class PlannerSaver(BaseCheckpointSaver):
    def __init__(self, session_factory, plan_id, household_id, thread_id):
        super().__init__()
        self.sessions = session_factory
        self.plan_id, self.household_id, self.thread_id = plan_id, household_id, str(thread_id)

    def keys(self, config):
        values = config.get("configurable", {})
        if values.get("thread_id") != self.thread_id:
            raise ValueError("Invalid planner thread scope.")
        return self.thread_id, values.get("checkpoint_ns", ""), values.get("checkpoint_id")

    def scoped(self, model):
        return select(model).where(model.plan_id == self.plan_id,
            model.household_id == self.household_id, model.thread_id == self.thread_id)

    def get_tuple(self, config):
        thread, ns, cid = self.keys(config)
        with self.sessions() as db:
            query = self.scoped(PlannerCheckpoint).where(PlannerCheckpoint.checkpoint_ns == ns)
            if cid:
                query = query.where(PlannerCheckpoint.checkpoint_id == cid)
            row = db.scalar(query.order_by(PlannerCheckpoint.checkpoint_id.desc()).limit(1))
            if row is None:
                return None
            actual = {"configurable": {"thread_id": thread, "checkpoint_ns": ns, "checkpoint_id": row.checkpoint_id}}
            parent = {"configurable": {"thread_id": thread, "checkpoint_ns": ns, "checkpoint_id": row.parent_id}} if row.parent_id else None
            writes = db.scalars(self.scoped(PlannerWrite).where(PlannerWrite.checkpoint_ns == ns,
                PlannerWrite.checkpoint_id == row.checkpoint_id).order_by(PlannerWrite.task_id, PlannerWrite.idx)).all()
            return CheckpointTuple(config=actual,
                checkpoint=self.serde.loads_typed((row.data_type, row.data)),
                metadata=self.serde.loads_typed((row.metadata_type, row.metadata_blob)), parent_config=parent,
                pending_writes=[(w.task_id, w.channel, self.serde.loads_typed((w.data_type, w.data))) for w in writes])

    def put(self, config, checkpoint, metadata, new_versions):
        thread, ns, parent = self.keys(config)
        data_type, data = self.serde.dumps_typed(checkpoint)
        metadata_type, metadata_blob = self.serde.dumps_typed(metadata)
        with self.sessions() as db, db.begin():
            previous = db.get(PlannerCheckpoint, (thread, ns, checkpoint["id"]))
            if previous and (previous.plan_id != self.plan_id or previous.household_id != self.household_id):
                raise ValueError("Invalid checkpoint ownership.")
            db.merge(PlannerCheckpoint(thread_id=thread, checkpoint_ns=ns, checkpoint_id=checkpoint["id"],
                plan_id=self.plan_id, household_id=self.household_id, parent_id=parent,
                data_type=data_type, data=data, metadata_type=metadata_type, metadata_blob=metadata_blob))
        return {"configurable": {"thread_id": thread, "checkpoint_ns": ns, "checkpoint_id": checkpoint["id"]}}

    def put_writes(self, config, writes, task_id, task_path=""):
        thread, ns, cid = self.keys(config)
        if not cid:
            raise ValueError("Missing checkpoint.")
        with self.sessions() as db, db.begin():
            for index, (channel, value) in enumerate(writes):
                idx = WRITES_IDX_MAP.get(channel, index)
                existing = db.get(PlannerWrite, (thread, ns, cid, task_id, idx))
                if existing is not None:
                    if existing.plan_id != self.plan_id or existing.household_id != self.household_id:
                        raise ValueError("Invalid checkpoint ownership.")
                    if idx >= 0:
                        continue
                data_type, data = self.serde.dumps_typed(value)
                db.merge(PlannerWrite(thread_id=thread, checkpoint_ns=ns, checkpoint_id=cid, task_id=task_id,
                    idx=idx, plan_id=self.plan_id, household_id=self.household_id,
                    channel=channel, data_type=data_type, data=data))

    def delete_thread(self, thread_id):
        if thread_id != self.thread_id:
            raise ValueError("Invalid planner thread scope.")
        with self.sessions() as db, db.begin():
            for model in (PlannerWrite, PlannerCheckpoint):
                db.execute(delete(model).where(model.thread_id == thread_id,
                    model.plan_id == self.plan_id, model.household_id == self.household_id))
