import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

import httpx
import psycopg
from pydantic import ValidationError


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.core.config import get_settings


class CheckFailed(Exception):
    """Contains only safe diagnostic messages authored in this script."""


def check_database(secret_url):
    uri = secret_url.get_secret_value().replace(
        "postgresql+psycopg://",
        "postgresql://",
        1,
    )

    with psycopg.connect(
        uri,
        connect_timeout=10,
        prepare_threshold=None,
        sslmode="require",
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION READ ONLY")
            cursor.execute("SELECT 1")

            if cursor.fetchone() != (1,):
                raise CheckFailed(
                    "Database returned an unexpected result."
                )

    return "Connected; read-only SELECT 1 succeeded."


def main():
    try:
        settings = get_settings()
    except ValidationError as exc:
        for error in exc.errors(
            include_input=False,
            include_url=False,
        ):
            field = ".".join(map(str, error["loc"]))
            print(f"[CONFIG] {field}: {error['type']}")

        print("Fix these settings in backend/.env and run again.")
        return 1

    failures = 0

    with httpx.Client(
        timeout=20,
        follow_redirects=False,
    ) as http:

        def run_check(name, function):
            nonlocal failures

            try:
                message = function()
                print(f"[PASS] {name}: {message}")

            except CheckFailed as exc:
                failures += 1
                print(f"[FAIL] {name}: {exc}")

            except Exception as exc:
                failures += 1

                # Avoid printing credentials, URLs or response bodies.
                response = getattr(exc, "response", None)
                status = getattr(response, "status_code", None)

                message = type(exc).__name__
                if status is not None:
                    message += f" HTTP {status}"

                print(f"[FAIL] {name}: {message}")

        def get_json(url, headers=None):
            response = http.get(url, headers=headers)
            response.raise_for_status()
            return response.json()

        def check_storage():
            key = settings.supabase_secret_key.get_secret_value()
            headers = {"apikey": key}

            # Legacy service-role keys are JWTs.
            # Modern sb_secret keys are supplied through apikey.
            if key.startswith("eyJ"):
                headers["Authorization"] = f"Bearer {key}"

            base = str(settings.supabase_url).rstrip("/")
            bucket = quote(
                settings.supabase_storage_bucket,
                safe="",
            )

            information = get_json(
                f"{base}/storage/v1/bucket/{bucket}",
                headers=headers,
            )

            if information.get("public") is not False:
                raise CheckFailed(
                    "The receipts bucket must be private."
                )

            return (
                "Private bucket exists. "
                "File upload/access policies still need testing."
            )

        def check_signing_keys():
            information = get_json(
                settings.supabase_jwks_url
            )

            if not information.get("keys"):
                raise CheckFailed(
                    "No public signing keys returned. "
                    "Check Supabase JWT signing configuration."
                )

            return (
                "Public signing keys reachable. "
                "User login is not tested by this check."
            )

        def check_pinecone():
            index_name = quote(
                settings.pinecone_index_name,
                safe="",
            )

            information = get_json(
                f"https://api.pinecone.io/indexes/{index_name}",
                headers={
                    "Api-Key": (
                        settings.pinecone_api_key.get_secret_value()
                    ),
                    "X-Pinecone-Api-Version": "2026-07",
                },
            )

            if not information.get("status", {}).get("ready"):
                raise CheckFailed(
                    "The index is not ready yet."
                )

            fields = (
                information.get("schema") or {}
            ).get("fields", {})

            integrated_embedding = information.get("embed") or any(
                field.get("embed") or field.get("sparse_embed")
                for field in fields.values()
                if isinstance(field, dict)
            )

            if integrated_embedding:
                raise CheckFailed(
                    "Integrated embedding model detected. "
                    "We need an external-vector index. "
                    "Nothing was changed."
                )

            vector_field = fields.get("_values", {})

            dimension = vector_field.get(
                "dimension",
                information.get("dimension"),
            )
            metric = vector_field.get(
                "metric",
                information.get("metric"),
            )

            if dimension != settings.embedding_dimensions:
                raise CheckFailed(
                    "Index dimensions do not match "
                    "EMBEDDING_DIMENSIONS, or the expected "
                    "_values vector field is missing."
                )

            if metric != "cosine":
                raise CheckFailed(
                    "The expected similarity metric is cosine."
                )

            actual_host = (
                str(information.get("host", ""))
                .removeprefix("https://")
                .rstrip("/")
            )
            configured_host = (
                settings.pinecone_index_host
                .removeprefix("https://")
                .rstrip("/")
            )

            if (
                configured_host
                and configured_host != actual_host
            ):
                raise CheckFailed(
                    "PINECONE_INDEX_HOST does not match "
                    "PINECONE_INDEX_NAME."
                )

            return (
                f"Schema matches {dimension} dimensions "
                "and cosine. No records written."
            )

        def check_model(model_name):
            information = get_json(
                "https://api.openai.com/v1/models/"
                + quote(model_name, safe=""),
                headers={
                    "Authorization": (
                        "Bearer "
                        + settings.openai_api_key.get_secret_value()
                    ),
                },
            )

            if not information.get("id"):
                raise CheckFailed(
                    "Model metadata did not contain an ID."
                )

            return (
                "Model visible. Actual inference and billing "
                "still need a smoke test."
            )

        def check_langsmith():
            if not settings.langsmith_tracing:
                raise CheckFailed(
                    "Set LANGSMITH_TRACING=true."
                )

            timestamp = datetime.now(timezone.utc).isoformat()

            response = http.post(
                str(settings.langsmith_endpoint).rstrip("/")
                + "/runs",
                headers={
                    "x-api-key": (
                        settings.langsmith_api_key.get_secret_value()
                    ),
                },
                json={
                    "id": str(uuid4()),
                    "name": "bonwise_setup_check",
                    "run_type": "chain",
                    "session_name": settings.langsmith_project,
                    "start_time": timestamp,
                    "end_time": timestamp,
                    "inputs": {
                        "synthetic_check": True,
                    },
                    "outputs": {
                        "ok": True,
                    },
                },
            )
            response.raise_for_status()

            return (
                "Synthetic trace accepted. Look for "
                "bonwise_setup_check in LangSmith."
            )

        run_check(
            "Application database",
            lambda: check_database(settings.database_url),
        )

        run_check(
            "Migration database",
            lambda: check_database(
                settings.migration_database_url
            ),
        )

        run_check("Supabase Storage", check_storage)
        run_check("Supabase signing keys", check_signing_keys)
        run_check("Pinecone", check_pinecone)

        model_names = {
            settings.llm_model,
            settings.vision_model,
            settings.transcription_model,
            settings.embedding_model,
        }

        for model_name in sorted(model_names):
            run_check(
                f"OpenAI model {model_name}",
                lambda name=model_name: check_model(name),
            )

        run_check("LangSmith", check_langsmith)

    print(f"\nCompleted with {failures} failed check(s).")
    print("No database tables or Pinecone indexes were changed.")

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())