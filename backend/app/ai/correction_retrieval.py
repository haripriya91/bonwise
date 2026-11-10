"""Household-isolated vectors locate accepted SQL examples, never replace SQL facts."""
import hashlib
import math
from langsmith import tracing_context
from sqlalchemy import select
from app.db.corrections import ProductCorrection

from app.ai.catalogue import embedding_client, vector_index
from app.core.config import get_settings
from app.core.privacy import has_detectable_pii
from app.services.correction_memory import namespace, verified_examples
from app.services.product_memory import key


def embed_wording(wording, category, quantity_unit, trace):
    if has_detectable_pii(wording):
        raise ValueError("Private wording is not eligible for correction retrieval.")
    settings = get_settings()
    with trace.span("embed_correction_text") as span:
        response = embedding_client().embeddings.create(model=settings.embedding_model,
            dimensions=settings.embedding_dimensions, encoding_format="float",
            input=[f"Receipt product wording: {wording}\nCategory: {category}\nPurchase unit: {quantity_unit}"])
        if response.usage:
            trace.set_usage(span, {"input_tokens": response.usage.prompt_tokens, "total_tokens": response.usage.total_tokens})
        if len(response.data) != 1 or response.data[0].index != 0:
            raise ValueError("Incomplete correction embedding.")
        vector = response.data[0].embedding
        if len(vector) != settings.embedding_dimensions or not all(math.isfinite(x) for x in vector) or not any(vector):
            raise ValueError("Invalid correction embedding.")
        return vector


def merchant_digest(merchant):
    return hashlib.sha256(key(merchant).encode()).hexdigest()


def upsert_example(household_id, correction_id, digest, example, trace):
    with tracing_context(enabled=False):
        vector = embed_wording(example["wording"], example["category"], example["quantity_unit"], trace)
        with trace.span("upsert_correction_vectors"):
            result = vector_index().upsert(namespace=namespace(household_id), vectors=[{
                "id": f"{correction_id}:{digest[:16]}", "values": vector,
                "metadata": {"kind": "correction", "fingerprint": digest, "category": example["category"],
                    "quantity_unit": example["quantity_unit"], "merchant_key": merchant_digest(example["merchant"])},
            }])
            if result.upserted_count != 1:
                raise ValueError("Incomplete correction index write.")


def retrieve_examples(db, household_id, wording, category, quantity_unit, merchant, trace):
    if not get_settings().correction_rag_enabled:
        return [], "disabled"
    try:
        with db.begin():
            ready = db.scalar(select(ProductCorrection.id).where(ProductCorrection.household_id == household_id,
                ProductCorrection.status == "indexed").limit(1))
        if ready is None:
            return [], "not_found"
        with tracing_context(enabled=False):
            vector = embed_wording(wording, category, quantity_unit, trace)
            conditions = {"kind": {"$eq": "correction"}, "category": {"$eq": category},
                "quantity_unit": {"$eq": quantity_unit}}
            if merchant is not None:
                conditions["merchant_key"] = {"$eq": merchant_digest(merchant)}
            with trace.span("retrieve_correction_examples"):
                response = vector_index().query(namespace=namespace(household_id), vector=vector, top_k=5,
                    filter=conditions, include_metadata=True, include_values=False)
                matches = []
                for match in response.matches:
                    score = float(match.score)
                    metadata = match.metadata or {}
                    # A heuristic retrieval cutoff, not a probability or measured confidence.
                    if math.isfinite(score) and score >= 0.65 and isinstance(metadata.get("fingerprint"), str):
                        matches.append(dict(id=match.id, fingerprint=metadata["fingerprint"]))
            with trace.span("verify_correction_examples"):
                with db.begin():
                    examples, conflict = verified_examples(db, household_id, matches, category, quantity_unit, merchant)
            return examples, "conflict" if conflict else "retrieved" if examples else "not_found"
    except Exception:
        if db.in_transaction():
            db.rollback()
        return [], "unavailable"  # Existing catalogue matching remains usable.
