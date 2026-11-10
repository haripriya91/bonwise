"""Explicit content-free spans; no decorators inspect function arguments."""
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import lru_cache
from time import perf_counter
from uuid import uuid4

from fastapi import HTTPException
from langsmith import Client

from app.core.config import get_settings

NAMES = {
    "load_product_catalogue": "tool",
    "check_catalogue_index": "tool",
    "embed_catalogue_text": "embedding",
    "upsert_catalogue_vectors": "tool",
    "retrieve_product_candidates": "retriever",
    "lookup_product_memory": "tool",
    "embed_correction_text": "embedding",
    "upsert_correction_vectors": "tool",
    "retrieve_correction_examples": "retriever",
    "verify_correction_examples": "tool",
    "interpret_product_wording": "llm",
    "check_product_candidates": "tool",
    "authorize_household": "tool",
    "check_question_privacy": "tool",
    "analysis_question_routing": "llm",
    "load_confirmed_purchases": "tool",
    "load_comparison_purchases": "tool",
    "calculate_answer": "tool",
    "prepare_response": "chain",
    "planner_load_context": "tool",
    "planner_propose_list": "llm",
    "planner_repair_proposal": "llm",
    "planner_validate_list": "tool",
    "planner_recheck_and_reserve": "tool",
    "planner_interpret_changes": "llm",
}
OUTCOMES = {"answered", "no_data", "unsupported", "clarification_required",
            "suggested", "review_required", "no_match", "awaiting_review", "approved"}
OPERATIONS = {"summary", "highest_category", "items", "merchants", "records",
              "quantity", "compare", "clarify", "unsupported"}


def error_code(exc):
    # Never serialize str(exc), traceback, SQL or HTTP detail.
    if isinstance(exc, HTTPException):
        return {401: "authentication_required", 403: "access_denied",
                404: "scope_unavailable", 422: "input_rejected",
                502: "provider_or_output_error"}.get(exc.status_code, "operation_failed")
    return "operation_failed"


def stamp(when, run_id):
    return when.strftime("%Y%m%dT%H%M%S%fZ") + str(run_id)


@lru_cache
def trace_client():
    settings = get_settings()
    return Client(api_key=settings.langsmith_api_key.get_secret_value(),
                  api_url=str(settings.langsmith_endpoint),
                  hide_inputs=True, hide_outputs=True,
                  auto_batch_tracing=False, timeout_ms=1500,
                  omit_traced_runtime_info=True)


class AnalysisTrace:
    def __init__(self, prompt_version, model, *, root_name="ask_bonwise"):
        if root_name not in {"ask_bonwise", "catalogue_index", "catalogue_retrieval", "product_suggestions", "weekly_planner", "planner_text_changes", "planner_evaluation", "correction_index", "correction_evaluation"}:
            raise ValueError("Unsupported trace root.")
        self.root_name = root_name
        self.id = uuid4()
        self.start = datetime.now(timezone.utc)
        self.clock = perf_counter()
        self.order = stamp(self.start, self.id)
        self.prompt_version = prompt_version
        self.model = model
        self.children = []
        self.outcome = None
        self.operation = None
        self.finished = False
        self.payload = None

    @contextmanager
    def span(self, name):
        kind = NAMES[name]  # Caller supplies a fixed label, never model text.
        run_id, start, clock = uuid4(), datetime.now(timezone.utc), perf_counter()
        metadata = {"prompt_version": self.prompt_version}
        if kind in {"llm", "embedding"}:
            metadata.update(ls_provider="openai", ls_model_name=getattr(get_settings(), "embedding_model", self.model) if kind == "embedding" else self.model)
        row = dict(id=run_id, name=name, run_type=kind, inputs={}, outputs={},
                   start_time=start, trace_id=self.id, parent_run_id=self.id,
                   dotted_order=self.order + "." + stamp(start, run_id),
                   extra={"metadata": metadata})
        # The yielded object accepts only numeric token usage via set_usage below.
        try:
            yield row
        except Exception as exc:
            row["error"] = error_code(exc)
            raise
        finally:
            row["end_time"] = datetime.now(timezone.utc)
            metadata["latency_ms"] = round((perf_counter() - clock) * 1000)
            metadata["status"] = "error" if row.get("error") else "success"
            self.children.append(row)

    @staticmethod
    def set_usage(row, usage):
        safe = {key: value for key, value in usage.items()
                if key in {"input_tokens", "output_tokens", "total_tokens"}
                and type(value) is int and value >= 0}
        # Missing provider usage stays absent, never fabricated as zero.
        if safe:
            row["extra"]["metadata"]["usage_metadata"] = safe

    def finish(self, exc=None):
        if self.finished:
            return
        self.finished = True
        metadata = {"prompt_version": self.prompt_version,
                    "status": "error" if exc else "success",
                    "latency_ms": round((perf_counter() - self.clock) * 1000)}
        if self.outcome in OUTCOMES:
            metadata["outcome"] = self.outcome
        if self.operation in OPERATIONS:
            metadata["operation"] = self.operation
        root = dict(id=self.id, name=self.root_name, run_type="chain",
                    inputs={}, outputs={}, start_time=self.start,
                    end_time=datetime.now(timezone.utc), trace_id=self.id,
                    dotted_order=self.order, extra={"metadata": metadata})
        if exc:
            root["error"] = error_code(exc)
        self.payload = [root, *self.children]

    def enabled(self):
        return bool(get_settings().langsmith_tracing)

    def send(self):
        """Best effort; called after a successful HTTP response, or on failure."""
        try:
            if not self.enabled() or not self.finished:
                return False
            settings = get_settings()
            rows = [dict(row, session_name=settings.langsmith_project)
                    for row in self.payload]
            trace_client().batch_ingest_runs(create=rows)
            return True
        except Exception:
            # Observability must not replace a successful answer or original error.
            return False
