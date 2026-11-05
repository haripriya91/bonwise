"""Synthetic inference checks. No purchase records are created."""

from datetime import datetime
from decimal import Decimal
from getpass import getpass
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from dotenv import dotenv_values


ROOT = Path(__file__).resolve().parents[1]
HOUSEHOLD = "20000000-0000-4000-8000-000000000001"


def expect(response, status):
    if response.status_code != status:
        raise RuntimeError(
            f"Expected HTTP {status}, received "
            f"{response.status_code}. Check backend logs "
            "without enabling body logging."
        )

    return response.json()


def main():
    config = dotenv_values(
        ROOT / "frontend" / ".env.local"
    )

    base = config.get(
        "VITE_API_BASE_URL",
        "http://localhost:8000",
    ).rstrip("/")

    supabase = config["VITE_SUPABASE_URL"].rstrip("/")

    email = input("Demo A email: ").strip()
    password = getpass("Demo A password: ")

    with httpx.Client(timeout=90) as client:
        login = client.post(
            supabase + "/auth/v1/token?grant_type=password",
            headers={
                "apikey":
                    config["VITE_SUPABASE_PUBLISHABLE_KEY"]
            },
            json={
                "email": email,
                "password": password,
            },
        )
        del password

        token = expect(login, 200)["access_token"]
        headers = {
            "Authorization": f"Bearer {token}"
        }
        del token, login

        me = expect(
            client.get(base + "/me", headers=headers),
            200,
        )

        if {
            str(h["id"]) for h in me["households"]
        } != {HOUSEHOLD}:
            raise RuntimeError(
                "Use the Demo A account. No model calls made."
            )

        root = f"{base}/households/{HOUSEHOLD}"

        before = expect(
            client.get(
                root + "/purchases",
                headers=headers,
            ),
            200,
        )["total"]

        prefix = root + "/extraction"

        preview = expect(
            client.post(
                prefix + "/privacy-preview",
                headers=headers,
                json={
                    "text": (
                        "Name: Alice Example\n"
                        "Email: alice@example.invalid\n"
                        "Mitgliedsnummer: 123456\n\n"
                        "Lidl\n2 Milch je 1,19 EUR"
                    )
                },
            ),
            200,
        )

        if (
            "Alice" in preview["reviewed_text"]
            or "123456" in preview["reviewed_text"]
        ):
            raise RuntimeError(
                "Synthetic privacy preview failed."
            )

        print(
            "[PASS] Privacy preview removed "
            "labelled personal details."
        )

        expect(
            client.post(
                prefix + "/text",
                headers=headers,
                json={
                    "reviewed_text":
                        "Email: alice@example.invalid",
                    "privacy_reviewed": True,
                },
            ),
            422,
        )

        print(
            "[PASS] Detectable PII blocked before inference."
        )

        today = str(
            datetime.now(
                ZoneInfo("Europe/Berlin")
            ).date()
        )

        samples = [
            (
                "English",
                (
                    f"Lidl, {today}, EUR. Bought 2 cartons "
                    "of milk at 1.19 EUR each and 1 loaf "
                    "of bread at 1.89 EUR each. "
                    "Receipt total 4.27 EUR."
                ),
                [
                    (Decimal("1"), 189),
                    (Decimal("2"), 119),
                ],
                427,
            ),
            (
                "German",
                (
                    f"Lidl, {today}, EUR. "
                    "2 Packungen Milch zu je 1,19 EUR "
                    "und 1 Brot zu 1,89 EUR. "
                    "Gesamt 4,27 EUR."
                ),
                [
                    (Decimal("1"), 189),
                    (Decimal("2"), 119),
                ],
                427,
            ),
            (
                "Invoice",
                (
                    f"Amazon EU invoice dated {today}. "
                    "Currency EUR. 2 USB cables at "
                    "5.99 EUR each, line total 11.98 EUR. "
                    "Total paid 11.98 EUR, VAT included. "
                    "No additional charges."
                ),
                [(Decimal("2"), 599)],
                1198,
            ),
        ]

        for label, text, expected_items, expected_total in samples:
            result = expect(
                client.post(
                    prefix + "/text",
                    headers=headers,
                    json={
                        "reviewed_text": text,
                        "privacy_reviewed": True,
                    },
                ),
                200,
            )

            if (
                not result["can_create_draft"]
                or result["saved"]
            ):
                raise RuntimeError(
                    f"{label}: extraction needs investigation; "
                    f"missing={result['missing_fields']}, "
                    f"warnings={result['warnings']}"
                )

            content = result["candidate"]

            actual_items = sorted(
                (
                    Decimal(item["quantity"]),
                    item["unit_price_cents"],
                )
                for item in content["items"]
            )

            if (
                actual_items != expected_items
                or content["reported_total_cents"]
                != expected_total
                or content["purchased_on"] != today
            ):
                raise RuntimeError(
                    f"{label}: quantities, prices, date "
                    "or total disagree with ground truth."
                )

            if not result["trace_sent"]:
                raise RuntimeError(
                    f"{label}: extraction worked but "
                    "LangSmith trace was not sent."
                )

            print(
                f"[PASS] {label}; "
                f"run_id={result['run_id']}"
            )

        incomplete = expect(
            client.post(
                prefix + "/text",
                headers=headers,
                json={
                    "reviewed_text":
                        "Heute bei Lidl Milch gekauft.",
                    "privacy_reviewed": True,
                },
            ),
            200,
        )

        if (
            incomplete["can_create_draft"]
            or not incomplete["missing_fields"]
            or not incomplete["extracted"]["items"]
            or any(
                item["unit_price_cents"] is not None
                for item in incomplete["extracted"]["items"]
            )
        ):
            raise RuntimeError(
                "Incomplete purchase was treated as complete."
            )

        print(
            "[PASS] Missing purchase details require review."
        )

        after = expect(
            client.get(
                root + "/purchases",
                headers=headers,
            ),
            200,
        )["total"]

        if before != after:
            raise RuntimeError(
                "Purchase count changed unexpectedly."
            )

        print(
            "[PASS] No purchases saved. Demo B was not used."
        )


if __name__ == "__main__":
    try:
        main()

    except (RuntimeError, httpx.HTTPError, KeyError) as error:
        if isinstance(error, RuntimeError):
            print(f"[FAIL] {error}")
        else:
            print(
                f"[FAIL] {type(error).__name__}; "
                "check configuration."
            )

        raise SystemExit(1)