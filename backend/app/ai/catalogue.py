"""Admin catalogue import/index and reusable public-product retrieval."""
import argparse
import hashlib
import json
import math
from contextlib import contextmanager, nullcontext
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Literal
from uuid import NAMESPACE_URL, uuid5

from langsmith import tracing_context
from openai import OpenAI
from pinecone import Pinecone
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.ai.analysis_tracing import AnalysisTrace
from app.core.config import get_settings
from app.core.privacy import redact_text
from app.db.models import Product
from app.db.session import SessionLocal
from app.schemas.purchase import Category

CATALOGUE = "retail-demo-catalogue-v1"
ACTIVE_CATALOGUES = (CATALOGUE, "receipt-catalogue-v1")
TEXT_VERSION = "bilingual-product-v3"


class CatalogueError(ValueError):
    pass


class Entry(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    catalogue: Literal["retail-demo-catalogue-v1", "receipt-catalogue-v1"] = CATALOGUE
    code: str = Field(pattern=r"^[a-z0-9-]{1,60}$")
    name_de: str = Field(min_length=1, max_length=200)
    name_en: str = Field(min_length=1, max_length=200)
    category: Category
    brand: str | None = Field(default=None, min_length=1, max_length=120)
    family: str = Field(pattern=r"^[a-z_]{1,60}$")
    organic: bool | None = Field(default=None, strict=True)
    variant_code: str | None = Field(default=None, pattern=r"^[a-z0-9_]{1,80}$")
    retail_signature: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    base_unit: Literal["kg", "l", "each", "wash", "roll"] | None = None
    pack_base_quantity: Decimal | None = Field(
        default=None, gt=0, max_digits=12, decimal_places=4)
    variant: str = Field(min_length=1, max_length=200)
    aliases: list[str] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def validate_source(self):
        if any(not alias.strip() or len(alias) > 300 for alias in self.aliases):
            raise ValueError("Invalid product alias.")
        if self.pack_base_quantity is not None and self.base_unit is None:
            raise ValueError("Package quantity needs a unit.")
        if self.catalogue == CATALOGUE:
            if any(value is None for value in (
                self.brand, self.organic, self.variant_code,
                self.retail_signature, self.base_unit, self.pack_base_quantity,
            )):
                raise ValueError("Retail fixture identity is incomplete.")
        elif self.retail_signature is not None:
            raise ValueError("Receipt products do not have verified retail identities.")
        return self


def values(entry):
    attributes = {
        "catalogue": entry.catalogue, "code": entry.code,
        "variant": entry.variant,
        "source": "synthetic" if entry.catalogue == CATALOGUE else "receipt_review",
        "family": entry.family, "organic": entry.organic,
        "variant_code": entry.variant_code,
    }
    if entry.retail_signature is not None:
        attributes["retail_signature"] = entry.retail_signature
    if entry.aliases:
        attributes["aliases"] = entry.aliases
    return dict(
        id=uuid5(NAMESPACE_URL, f"bonwise:{entry.catalogue}:{entry.code}"),
        canonical_name=entry.name_en, name_de=entry.name_de,
        name_en=entry.name_en, brand=entry.brand, category=entry.category,
        base_unit=entry.base_unit, pack_base_quantity=entry.pack_base_quantity,
        attributes=attributes,
    )


def import_catalogue(path):
    entries = TypeAdapter(list[Entry]).validate_json(Path(path).read_text())
    if not 1 <= len(entries) <= 1000 or len({e.code for e in entries}) != len(entries):
        raise CatalogueError("Provide 1–1000 catalogue entries with unique codes.")
    for entry in entries:
        if entry.catalogue != CATALOGUE:
            continue
        identity = dict(
            name=entry.name_de, brand=entry.brand, variant=entry.variant_code,
            organic=entry.organic, base_unit=entry.base_unit,
            pack_base_quantity=format(entry.pack_base_quantity.normalize(), "f"),
        )
        signature = hashlib.sha256(json.dumps(
            identity, sort_keys=True, ensure_ascii=False,
            separators=(",", ":"),
        ).encode()).hexdigest()
        if signature != entry.retail_signature or entry.code != "retail-" + signature[:16]:
            raise CatalogueError("Catalogue identity signature does not match its fields.")
    with SessionLocal.begin() as db:
        for entry in entries:
            expected = values(entry)
            db.execute(insert(Product).values(**expected).on_conflict_do_nothing(
                index_elements=[Product.id]))
            actual = db.get(Product, expected["id"])
            if any(getattr(actual, k) != v for k, v in expected.items()):
                raise CatalogueError("Existing catalogue entry differs; no rows were overwritten.")
    return {"imported_or_existing": len(entries)}


def snapshot():
    with SessionLocal() as db:
        products = db.scalars(select(Product).where(
            Product.attributes["catalogue"].astext.in_(ACTIVE_CATALOGUES)
        ).order_by(Product.id).limit(1001)).all()
        if not products or len(products) > 1000:
            raise CatalogueError("Import the catalogue first; maximum 1000 entries.")
        rows = []
        for p in products:
            row = {k: getattr(p, k) for k in (
                "canonical_name", "name_de", "name_en", "brand", "category",
                "base_unit", "attributes")}
            row.update(id=str(p.id), pack_base_quantity=format(
                p.pack_base_quantity.normalize(), "f") if p.pack_base_quantity is not None else None)
            rows.append(row)
    return rows


def namespace(rows):
    s = get_settings()
    payload = [ACTIVE_CATALOGUES, TEXT_VERSION, s.embedding_model, s.embedding_dimensions, rows]
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True,
        ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()[:24]
    return "catalogue-" + digest


@lru_cache
def vector_index():
    s = get_settings()
    pc = Pinecone(api_key=s.pinecone_api_key.get_secret_value(), timeout=20)
    info = pc.indexes.describe(name=s.pinecone_index_name)
    if info.dimension != s.embedding_dimensions or info.metric != "cosine":
        raise CatalogueError("Pinecone must use the configured dimension and cosine metric.")
    if "_values" not in info.schema.fields:
        raise CatalogueError("Use the existing external-vector index for this step.")
    host = info.host.removeprefix("https://").rstrip("/")
    configured = s.pinecone_index_host.removeprefix("https://").rstrip("/")
    if configured and configured != host:
        raise CatalogueError("Configured Pinecone index name and host do not match.")
    return pc.index(host=info.host)


@lru_cache
def embedding_client():
    return OpenAI(api_key=get_settings().openai_api_key.get_secret_value(),
                  timeout=30, max_retries=0)


@contextmanager
def operation(name):
    trace = AnalysisTrace(f"{CATALOGUE}:{TEXT_VERSION}", get_settings().embedding_model, root_name=name)
    try:
        with tracing_context(enabled=False):
            yield trace
    except Exception as exc:
        trace.finish(exc)
        raise
    else:
        trace.finish()
    finally:
        trace.send()


def embed(texts, trace):
    s = get_settings()
    with trace.span("embed_catalogue_text") as span:
        result = embedding_client().embeddings.create(
            model=s.embedding_model, input=texts,
            dimensions=s.embedding_dimensions, encoding_format="float")
        if result.usage:
            trace.set_usage(span, {"input_tokens": result.usage.prompt_tokens,
                                   "total_tokens": result.usage.total_tokens})
        data = sorted(result.data, key=lambda item: item.index)
        if [item.index for item in data] != list(range(len(texts))):
            raise CatalogueError("Incomplete embedding response.")
        vectors = [item.embedding for item in data]
        for vector in vectors:
            if (len(vector) != s.embedding_dimensions
                or not all(math.isfinite(v) for v in vector) or not any(vector)):
                raise CatalogueError("Invalid embedding vector.")
        return vectors


def index_catalogue():
    with operation("catalogue_index") as trace:
        with trace.span("load_product_catalogue"):
            rows = snapshot()
        with trace.span("check_catalogue_index"):
            index = vector_index()
        ns = namespace(rows)
        for offset in range(0, len(rows), 50):
            batch = rows[offset:offset + 50]
            # Both languages, variant and package contents; no prices or evaluation labels.
            texts = []
            for row in batch:
                amount, unit = row["pack_base_quantity"], row["base_unit"]
                size = f"{amount} {unit}" if amount is not None and unit else "not established"
                organic = row["attributes"].get("organic")
                organic_text = "not established" if organic is None else str(organic)
                texts.append(
                    f"{row['name_de']}. {row['name_en']}. "
                    f"Brand: {row['brand'] or 'not established'}. "
                    f"Family: {row['attributes']['family']}. "
                    f"Variant: {row['attributes']['variant']}. "
                    f"Organic/Bio: {organic_text}. Package contents: {size}. "
                    f"Receipt aliases: {'; '.join(row['attributes'].get('aliases', []))}."
                )
            vectors = embed(texts, trace)
            with trace.span("upsert_catalogue_vectors"):
                result = index.upsert(namespace=ns, vectors=[
                    {"id": r["id"], "values": v,
                     "metadata": {"kind": "product", "revision": ns}}
                    for r, v in zip(batch, vectors, strict=True)])
                if result.upserted_count != len(batch):
                    raise CatalogueError("Incomplete vector upload; rerun indexing.")
        # Publish readiness only after every product upload succeeds.
        with trace.span("upsert_catalogue_vectors"):
            result = index.upsert(namespace=ns, vectors=[{
                "id": "catalogue-ready", "values": vectors[0],
                "metadata": {"kind": "manifest", "count": len(rows)}}])
            if result.upserted_count != 1:
                raise CatalogueError("Catalogue readiness upload failed.")
        return {"indexed": len(rows), "namespace": ns}


def retrieve_candidates(wording, top_k=5, *, trace=None):
    if not isinstance(wording, str) or not 1 <= len(wording.strip()) <= 300:
        raise CatalogueError("Provide 1–300 characters of product wording.")
    if type(top_k) is not int or not 1 <= top_k <= 10:
        raise CatalogueError("top_k must be between 1 and 10.")
    clean, findings = redact_text(wording)
    if findings or not clean or "[REMOVED]" in clean:
        raise CatalogueError("Remove personal details before product search.")
    with (nullcontext(trace) if trace is not None else operation("catalogue_retrieval")) as trace:
        with trace.span("load_product_catalogue"):
            rows = snapshot()
        with trace.span("check_catalogue_index"):
            index = vector_index()
            ns = namespace(rows)
            marker = index.fetch(ids=["catalogue-ready"], namespace=ns)
            ready = marker.vectors.get("catalogue-ready")
            if ready is None or (ready.metadata or {}).get("count") != len(rows):
                raise CatalogueError("Index this catalogue first, or retry after indexing becomes visible.")
        vector = embed([clean], trace)[0]
        with trace.span("retrieve_product_candidates"):
            result = index.query(namespace=ns, vector=vector, top_k=top_k,
                filter={"kind": {"$eq": "product"}},
                include_metadata=True, include_values=False)
            by_id = {r["id"]: r for r in rows}
            candidates = []
            for match in result.matches:
                if match.id not in by_id or (match.metadata or {}).get("revision") != ns:
                    raise CatalogueError("Stale catalogue candidate; rerun indexing.")
                score = float(match.score)
                if not math.isfinite(score):
                    raise CatalogueError("Invalid retrieval score.")
                candidates.append({**by_id[match.id], "similarity_score": score})
            if not candidates:
                raise CatalogueError("No vectors visible yet; retry or check the index.")
        return {"status": "candidates_only", "candidates": candidates}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["import", "index", "query"])
    parser.add_argument("value", nargs="?")
    args = parser.parse_args()
    try:
        if args.action == "import":
            if not args.value:
                raise CatalogueError("Supply the catalogue JSON path.")
            output = import_catalogue(args.value)
        elif args.action == "index":
            output = index_catalogue()
        else:
            output = retrieve_candidates(args.value)
        print(json.dumps(output, ensure_ascii=False, indent=2))
    except CatalogueError as exc:
        raise SystemExit(str(exc)) from None
    except Exception:
        raise SystemExit("Catalogue operation failed. Check database, index and provider setup privately.") from None


if __name__ == "__main__":
    main()