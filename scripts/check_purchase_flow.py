"""Creates one confirmed test purchase in A; never writes to B."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime
from getpass import getpass
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
from dotenv import dotenv_values


ROOT = Path(__file__).resolve().parents[1]

HOUSEHOLDS = [
    "20000000-0000-4000-8000-000000000001",
    "20000000-0000-4000-8000-000000000002",
]


def expect(response, status, label):
    if response.status_code != status:
        raise RuntimeError(
            f"{label}: expected HTTP {status}, "
            f"got {response.status_code}. Inspect backend logs."
        )

    print(f"[PASS] {label}")
    return response.json()


def main():
    config = dotenv_values(ROOT / "frontend" / ".env.local")

    base = config.get(
        "VITE_API_BASE_URL",
        "http://localhost:8000",
    ).rstrip("/")

    supabase = config["VITE_SUPABASE_URL"].rstrip("/")
    key = config["VITE_SUPABASE_PUBLISHABLE_KEY"]

    headers = []

    with httpx.Client(timeout=30) as client:
        for label, household in zip(
            ["A", "B"], HOUSEHOLDS, strict=True
        ):
            email = input(f"Demo {label} email: ").strip()
            password = getpass(f"Demo {label} password: ")

            login = client.post(
                f"{supabase}/auth/v1/token?grant_type=password",
                headers={"apikey": key},
                json={"email": email, "password": password},
            )
            del password

            if login.status_code != 200:
                raise RuntimeError(
                    f"Demo {label}: login failed, "
                    f"HTTP {login.status_code}."
                )

            auth = {
                "Authorization":
                    f"Bearer {login.json()['access_token']}"
            }
            del login

            me = expect(
                client.get(f"{base}/me", headers=auth),
                200,
                f"Demo {label} login",
            )

            actual = {
                str(h["id"]) for h in me["households"]
            }

            if actual != {household}:
                raise RuntimeError(
                    f"Demo {label} household mapping is wrong. "
                    "No purchase writes attempted."
                )

            headers.append(auth)

        def request(method, path, who=0, **kwargs):
            return client.request(
                method,
                base + path,
                headers=headers[who],
                **kwargs,
            )

        a = f"/households/{HOUSEHOLDS[0]}"
        b = f"/households/{HOUSEHOLDS[1]}"

        before = expect(
            request("GET", a + "/purchases"),
            200,
            "A history",
        )["total"]

        fresh = expect(
            request("GET", b + "/purchases", who=1),
            200,
            "B history",
        )["total"]

        if fresh:
            raise RuntimeError(
                "Demo B already has purchases. "
                "Stopping without changing it."
            )

        content = {
            "merchant": "Bonwise setup test",
            "purchased_on": str(
                datetime.now(
                    ZoneInfo("Europe/Berlin")
                ).date()
            ),
            "items": [
                {
                    "original_text": "Milk",
                    "quantity": "2",
                    "unit_price_cents": 119,
                    "category": "groceries",
                },
                {
                    "original_text": "Bread",
                    "quantity": "1",
                    "unit_price_cents": 189,
                    "category": "groceries",
                },
            ],
            "adjustments": [
                {
                    "kind": "discount",
                    "label": "Coupon",
                    "amount_cents": -50,
                    "category": "groceries",
                },
                {
                    "kind": "deposit",
                    "label": "Pfand",
                    "amount_cents": 25,
                    "category": "groceries",
                },
            ],
            "reported_total_cents": 400,
        }

        payload = {
            "client_request_id": str(uuid4()),
            "content": content,
        }

        draft = expect(
            request(
                "POST",
                a + "/purchase-drafts",
                json=payload,
            ),
            201,
            "Create draft",
        )

        path = a + "/purchase-drafts/" + draft["id"]

        if draft["calculated_total_cents"] != 402:
            raise RuntimeError(
                "Calculation should be 402 cents."
            )

        retry = expect(
            request(
                "POST",
                a + "/purchase-drafts",
                json=payload,
            ),
            201,
            "Retry draft creation",
        )

        if retry["id"] != draft["id"]:
            raise RuntimeError(
                "Retry created another draft."
            )

        history = expect(
            request("GET", a + "/purchases"),
            200,
            "Pending draft excluded",
        )["total"]

        if history != before:
            raise RuntimeError(
                "Pending draft affected purchase history."
            )

        expect(
            request(
                "POST",
                path + "/confirm",
                json={"revision": draft["revision"]},
            ),
            422,
            "Total mismatch blocked",
        )

        expect(
            request("GET", path, who=1),
            404,
            "B cannot read A draft",
        )

        expect(
            request(
                "GET",
                b + "/purchase-drafts/" + draft["id"],
                who=1,
            ),
            404,
            "Draft cannot cross households",
        )

        expect(
            request(
                "POST",
                a + "/purchase-drafts",
                who=1,
                json=payload,
            ),
            404,
            "B cannot write to A",
        )

        corrected = deepcopy(content)
        corrected["reported_total_cents"] = 402

        reviewed = expect(
            request(
                "PATCH",
                path,
                json={
                    "revision": draft["revision"],
                    "content": corrected,
                },
            ),
            200,
            "Edit draft",
        )

        expect(
            request(
                "POST",
                path + "/confirm",
                json={"revision": draft["revision"]},
            ),
            409,
            "Stale review blocked",
        )

        def confirm(_):
            with httpx.Client(timeout=30) as worker:
                return worker.post(
                    base + path + "/confirm",
                    headers=headers[0],
                    json={
                        "revision": reviewed["revision"]
                    },
                )

        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(
                pool.map(confirm, range(2))
            )

        purchases = [
            expect(r, 200, "Concurrent confirmation")
            for r in responses
        ]

        if (
            purchases[0]["id"] != purchases[1]["id"]
            or purchases[0]["total_cents"] != 402
        ):
            raise RuntimeError(
                "Confirmation was not idempotent "
                "or total was wrong."
            )

        expect(
            request(
                "PATCH",
                path,
                json={
                    "revision": reviewed["revision"],
                    "content": corrected,
                },
            ),
            409,
            "Confirmed draft cannot be edited",
        )

        after_a = expect(
            request("GET", a + "/purchases"),
            200,
            "A final history",
        )["total"]

        after_b = expect(
            request("GET", b + "/purchases", who=1),
            200,
            "B final history",
        )["total"]

        if after_a != before + 1 or after_b != 0:
            raise RuntimeError(
                "Unexpected final purchase counts."
            )

        print(
            "[PASS] Exactly one confirmed test purchase "
            "added to A; B remains empty."
        )
        print("Test purchase ID:", purchases[0]["id"])


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, httpx.HTTPError, KeyError) as error:
        if isinstance(error, RuntimeError):
            print(f"[FAIL] {error}")
        else:
            print(
                f"[FAIL] {type(error).__name__}; "
                "check configuration and service availability."
            )
        raise SystemExit(1)