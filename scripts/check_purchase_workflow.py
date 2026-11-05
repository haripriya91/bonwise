"""One AI call; one confirmed synthetic purchase in A; no writes to B."""

from copy import deepcopy
from datetime import datetime
from decimal import Decimal
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


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def expect(response, status, label):
    require(
        response.status_code == status,
        f"{label}: expected HTTP {status}, "
        f"received {response.status_code}.",
    )
    print(f"[PASS] {label}")
    return response.json()


def login(client, base, config, label, household):
    email = input(f"Demo {label} email: ").strip()
    password = getpass(f"Demo {label} password: ")

    response = client.post(
        config["VITE_SUPABASE_URL"].rstrip("/")
        + "/auth/v1/token?grant_type=password",
        headers={
            "apikey": config["VITE_SUPABASE_PUBLISHABLE_KEY"]
        },
        json={"email": email, "password": password},
    )
    del password

    token = expect(
        response, 200, f"Demo {label} login"
    )["access_token"]

    auth = {"Authorization": f"Bearer {token}"}
    del token, response

    me = expect(
        client.get(base + "/me", headers=auth),
        200,
        f"Demo {label} identity",
    )

    require(
        {str(h["id"]) for h in me["households"]} == {household},
        f"Demo {label} household mapping is wrong; "
        "stopping before purchase writes.",
    )

    return auth


def run_workflow(client, base, auth_a, auth_b):
    a = f"{base}/households/{HOUSEHOLDS[0]}"
    b = f"{base}/households/{HOUSEHOLDS[1]}"

    def history(root, auth, offset=0):
        return expect(
            client.get(
                root + "/purchases",
                headers=auth,
                params={"limit": 100, "offset": offset},
            ),
            200,
            "Read history",
        )

    before_a = history(a, auth_a)["total"]

    require(
        history(b, auth_b)["total"] == 0,
        "Demo B already has purchases. "
        "Nothing will be deleted; stopping.",
    )

    expect(
        client.get(a + "/purchases", headers=auth_b),
        404,
        "B cannot read A history",
    )

    expect(
        client.get(b + "/purchases", headers=auth_a),
        404,
        "A cannot read B history",
    )

    today = str(
        datetime.now(ZoneInfo("Europe/Berlin")).date()
    )

    safe_fixture = (
        f"Lidl\nPurchase date: {today}\nCurrency: EUR\n"
        "2 cartons of milk at 1.19 EUR each.\n"
        "1 loaf of bread at 1.89 EUR each.\n"
        "Receipt total: 4.27 EUR."
    )

    raw_fixture = (
        "Name: Alice Example\n"
        "Email: alice@example.invalid\n"
        "Phone: +49 176 00000000\n"
        "Payback: DEMO-MEMBER-123\n"
        "Card: **** 1234\n\n"
        + safe_fixture
    )

    preview = expect(
        client.post(
            a + "/extraction/privacy-preview",
            headers=auth_a,
            json={"text": raw_fixture},
        ),
        200,
        "Privacy preview",
    )

    reviewed = preview["reviewed_text"].strip()

    require(
        reviewed == safe_fixture and bool(preview["findings"]),
        "Privacy output differs from the known-safe synthetic "
        "fixture. No model call made.",
    )

    # Exact fixture comparison simulates human privacy review
    # for SYNTHETIC data only. Never auto-approve real documents.
    result = expect(
        client.post(
            a + "/extraction/text",
            headers=auth_a,
            json={
                "reviewed_text": reviewed,
                "privacy_reviewed": True,
            },
        ),
        200,
        "Extract reviewed text",
    )

    require(
        result["can_create_draft"]
        and result["candidate"] is not None,
        "Extraction needs correction. "
        "Stop and inspect the synthetic result.",
    )

    require(
        result["saved"] is False and result["trace_sent"],
        "Extraction must not save purchases, "
        "and its LangSmith trace must be sent.",
    )

    content = deepcopy(result["candidate"])

    expected_items = [
        (Decimal("1"), 189),
        (Decimal("2"), 119),
    ]

    actual_items = sorted(
        (Decimal(item["quantity"]), item["unit_price_cents"])
        for item in content["items"]
    )

    require(
        actual_items == expected_items
        and content["purchased_on"] == today
        and content["currency"] == "EUR"
        and content["reported_total_cents"] == 427
        and content["adjustments"] == [],
        "Extraction disagrees with the labelled fixture. "
        "Do not save guessed corrections.",
    )

    require(
        history(a, auth_a)["total"] == before_a,
        "Extraction changed purchase history.",
    )

    # Inject a ONE-CENT TEST ERROR to exercise review.
    # This is not an automatic correction of a real receipt.
    draft_content = deepcopy(content)
    draft_content["reported_total_cents"] = 428

    payload = {
        "client_request_id": str(uuid4()),
        "source_type": "text",
        "content": draft_content,
    }

    draft = expect(
        client.post(
            a + "/purchase-drafts",
            headers=auth_a,
            json=payload,
        ),
        201,
        "Create pending draft from extracted fields",
    )

    path = a + "/purchase-drafts/" + draft["id"]
    print("Test draft ID:", draft["id"])

    require(
        draft["status"] == "pending"
        and draft["calculated_total_cents"] == 427
        and bool(draft["warnings"]),
        "Draft did not flag the injected total mismatch.",
    )

    require(
        history(a, auth_a)["total"] == before_a,
        "Pending draft counted as spending.",
    )

    expect(
        client.get(path, headers=auth_b),
        404,
        "B cannot read A draft",
    )

    expect(
        client.post(
            path + "/confirm",
            headers=auth_a,
            json={"revision": draft["revision"]},
        ),
        422,
        "Incorrect total blocks confirmation",
    )

    require(
        history(a, auth_a)["total"] == before_a,
        "Failed confirmation saved a purchase.",
    )

    # Simulate the user correcting the test discrepancy.
    # PATCH replaces the complete content, not individual fields.
    corrected = expect(
        client.patch(
            path,
            headers=auth_a,
            json={
                "revision": draft["revision"],
                "content": content,
            },
        ),
        200,
        "Review correction restores the verified total",
    )

    require(
        corrected["revision"] != draft["revision"]
        and not corrected["warnings"],
        "Corrected draft must have a new revision "
        "and no warnings.",
    )

    reloaded = expect(
        client.get(path, headers=auth_a),
        200,
        "Reload corrected draft",
    )

    require(
        reloaded["revision"] == corrected["revision"]
        and reloaded["content"]["reported_total_cents"] == 427,
        "Review correction did not persist.",
    )

    expect(
        client.post(
            path + "/confirm",
            headers=auth_a,
            json={"revision": draft["revision"]},
        ),
        409,
        "Old review cannot confirm changed draft",
    )

    confirmation = {"revision": corrected["revision"]}

    purchase = expect(
        client.post(
            path + "/confirm",
            headers=auth_a,
            json=confirmation,
        ),
        200,
        "Confirm reviewed purchase",
    )

    repeat = expect(
        client.post(
            path + "/confirm",
            headers=auth_a,
            json=confirmation,
        ),
        200,
        "Retry confirmation safely",
    )

    require(
        purchase["id"] == repeat["id"],
        "Confirmation retry created another purchase.",
    )

    retry_draft = expect(
        client.post(
            a + "/purchase-drafts",
            headers=auth_a,
            json=payload,
        ),
        201,
        "Retry original draft creation after confirmation",
    )

    require(
        retry_draft["id"] == draft["id"]
        and retry_draft["purchase_id"] == purchase["id"],
        "Original request ID no longer resolves "
        "to the same purchase.",
    )

    final_draft = expect(
        client.get(path, headers=auth_a),
        200,
        "Draft is confirmed",
    )

    require(
        final_draft["status"] == "confirmed",
        "Draft status did not change.",
    )

    page = history(a, auth_a)

    require(
        page["total"] == before_a + 1,
        "Expected exactly one additional purchase in A.",
    )

    # Search paginated history instead of assuming that
    # the new purchase will always be the first result.
    offset = 0

    while True:
        saved = next(
            (
                row
                for row in page["purchases"]
                if row["id"] == purchase["id"]
            ),
            None,
        )

        if saved is not None:
            break

        rows = len(page["purchases"])

        require(
            rows > 0,
            "Pagination ended before the saved purchase was found.",
        )

        offset += rows

        require(
            offset < page["total"],
            "Confirmed purchase is missing from history.",
        )

        page = history(a, auth_a, offset)

    require(
        saved["household_id"] == HOUSEHOLDS[0]
        and saved["draft_id"] == draft["id"]
        and saved["merchant"] == content["merchant"]
        and saved["total_cents"] == 427
        and saved["currency"] == "EUR"
        and saved["purchased_on"] == today
        and saved["adjustments"] == [],
        "Saved purchase header differs from "
        "the reviewed purchase.",
    )

    saved_items = sorted(
        (Decimal(item["quantity"]), item["unit_price_cents"])
        for item in saved["items"]
    )

    require(
        saved_items == expected_items
        and sorted(
            item["line_total_cents"]
            for item in saved["items"]
        ) == [189, 238],
        "Saved item quantities, prices or totals are wrong.",
    )

    def reviewed_fields(items):
        return sorted(
            (
                item["original_text"],
                Decimal(item["quantity"]),
                item["quantity_unit"],
                item["unit_price_cents"],
                item["category"],
            )
            for item in items
        )

    require(
        reviewed_fields(saved["items"])
        == reviewed_fields(content["items"]),
        "History differs from the reviewed item "
        "descriptions, units or categories.",
    )

    require(
        history(b, auth_b)["total"] == 0,
        "Demo B is no longer empty.",
    )

    print(
        "[PASS] Complete workflow: exactly one EUR 4.27 "
        "purchase added to A; B stays empty."
    )
    print("Purchase ID:", purchase["id"])
    print("Extraction run ID:", result["run_id"])


def main():
    config = dotenv_values(
        ROOT / "frontend" / ".env.local"
    )

    base = config.get(
        "VITE_API_BASE_URL",
        "http://localhost:8000",
    ).rstrip("/")

    with httpx.Client(timeout=90) as client:
        auth_a = login(
            client, base, config, "A", HOUSEHOLDS[0]
        )
        auth_b = login(
            client, base, config, "B", HOUSEHOLDS[1]
        )

        run_workflow(client, base, auth_a, auth_b)


if __name__ == "__main__":
    try:
        main()

    except (
        RuntimeError,
        httpx.HTTPError,
        KeyError,
        ValueError,
    ) as error:
        if isinstance(error, RuntimeError):
            print(f"[FAIL] {error}")
        else:
            print(
                f"[FAIL] {type(error).__name__}; "
                "check configuration and service availability."
            )

        print(
            "A pending draft or purchase may remain if the "
            "test stopped after a write. Do not delete "
            "accounts or shared data."
        )

        raise SystemExit(1)