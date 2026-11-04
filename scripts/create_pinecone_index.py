import sys
import time
from pathlib import Path
from urllib.parse import quote

import httpx


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.core.config import get_settings


def main():
    settings = get_settings()

    # Fixed target prevents accidental changes to another project index.
    name = "bonwise-vectors-dev"

    if settings.pinecone_index_name != name:
        print(
            "[FAIL] Set PINECONE_INDEX_NAME="
            "bonwise-vectors-dev in backend/.env first."
        )
        return 1

    headers = {
        "Api-Key": settings.pinecone_api_key.get_secret_value(),
        "X-Pinecone-Api-Version": "2026-07",
    }

    base = "https://api.pinecone.io/indexes"
    index_url = f"{base}/{quote(name, safe='')}"

    try:
        with httpx.Client(
            headers=headers,
            timeout=20,
            follow_redirects=False,
        ) as client:
            response = client.get(index_url)

            if response.status_code == 404:
                response = client.post(
                    base,
                    json={
                        "name": name,
                        "deployment": {
                            "deployment_type": "managed",
                            "cloud": "aws",
                            "region": "us-east-1",
                        },
                        "schema": {
                            "fields": {
                                "_values": {
                                    "type": "dense_vector",
                                    "dimension": (
                                        settings.embedding_dimensions
                                    ),
                                    "metric": "cosine",
                                }
                            }
                        },
                        "tags": {
                            "application": "bonwise",
                            "environment": "development",
                        },
                    },
                )

                # Another request may have created it meanwhile.
                if response.status_code != 409:
                    response.raise_for_status()

                print("Creation requested. Waiting for readiness...")
            else:
                response.raise_for_status()
                print("Index exists; checking its configuration...")

            for _ in range(15):
                response = client.get(index_url)

                if response.status_code == 404:
                    time.sleep(2)
                    continue

                response.raise_for_status()
                information = response.json()

                if not information.get("status", {}).get("ready"):
                    time.sleep(2)
                    continue

                fields = (
                    information.get("schema") or {}
                ).get("fields", {})

                integrated = information.get("embed") or any(
                    field.get("embed") or field.get("sparse_embed")
                    for field in fields.values()
                    if isinstance(field, dict)
                )

                vector = fields.get("_values", {})
                dimension = vector.get(
                    "dimension",
                    information.get("dimension"),
                )
                metric = vector.get(
                    "metric",
                    information.get("metric"),
                )

                if (
                    integrated
                    or dimension != settings.embedding_dimensions
                    or metric != "cosine"
                ):
                    print(
                        "[FAIL] Existing index is incompatible. "
                        "Nothing was deleted or reconfigured."
                    )
                    return 1

                host = information.get("host")
                if not host:
                    print("[FAIL] Index has no host yet. Run again.")
                    return 1

                print("[PASS] External-vector index is ready.")
                print("Update backend/.env with:")
                print(f"PINECONE_INDEX_NAME={name}")
                print(f"PINECONE_INDEX_HOST={host}")
                return 0

            print(
                "[WAIT] Index is still provisioning. "
                "Run this script again shortly."
            )
            return 1

    except httpx.HTTPStatusError as exc:
        print(
            "[FAIL] Pinecone returned HTTP",
            exc.response.status_code,
        )
        print(
            "Check API-key permissions, index quota and "
            "region availability in your Pinecone account."
        )
        return 1

    except httpx.RequestError:
        print("[FAIL] Network request failed. Try again.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())