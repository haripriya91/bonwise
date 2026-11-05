"""Two synthetic image calls; no purchases saved."""

import base64
import hashlib
from datetime import datetime
from decimal import Decimal
from getpass import getpass
from io import BytesIO
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from dotenv import dotenv_values
from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
HOUSEHOLD = "20000000-0000-4000-8000-000000000001"


def expect(response, label):
    if response.status_code != 200:
        raise RuntimeError(
            f"{label}: HTTP {response.status_code}. "
            "Check the backend error without enabling "
            "request-body logging."
        )

    print(f"[PASS] {label}")
    return response.json()


def receipt(language, today):
    image = Image.new("RGB", (950, 800), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=28)

    lines = [
        "DEMO MARKT",
        "SYNTHETIC TEST RECEIPT",
        f"Date: {today}",
        "Currency: EUR",
        "",
    ]

    if language == "de":
        lines += [
            "Milch  2 Pack x 1,19 EUR    2,38 EUR",
            "Brot   1 Stk x 1,89 EUR     1,89 EUR",
            "",
            "GESAMT: 4,27 EUR",
        ]
    else:
        lines += [
            "Milk  2 pack x 1.19 EUR    2.38 EUR",
            "Bread 1 each x 1.89 EUR    1.89 EUR",
            "",
            "TOTAL: 4.27 EUR",
        ]

    for index, text in enumerate(lines):
        draw.text(
            (45, 40 + index * 65),
            text,
            font=font,
            fill="black",
        )

    output = BytesIO()
    image.save(output, format="JPEG", quality=95)

    return base64.b64encode(
        output.getvalue()
    ).decode("ascii")


def main():
    config = dotenv_values(
        ROOT / "frontend" / ".env.local"
    )

    base = config.get(
        "VITE_API_BASE_URL",
        "http://localhost:8000",
    ).rstrip("/")

    email = input("Demo A email: ").strip()
    password = getpass("Demo A password: ")

    with httpx.Client(timeout=120) as client:
        login = client.post(
            config["VITE_SUPABASE_URL"].rstrip("/")
            + "/auth/v1/token?grant_type=password",
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

        token = expect(login, "Login")["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        del token, login

        me = expect(
            client.get(base + "/me", headers=headers),
            "Identity",
        )

        if {
            str(h["id"]) for h in me["households"]
        } != {HOUSEHOLD}:
            raise RuntimeError(
                "Use Demo A; no image calls made."
            )

        root = f"{base}/households/{HOUSEHOLD}"

        before = expect(
            client.get(
                root + "/purchases",
                headers=headers,
            ),
            "History before",
        )["total"]

        today = str(
            datetime.now(
                ZoneInfo("Europe/Berlin")
            ).date()
        )

        for language in ["en", "de"]:
            result = expect(
                client.post(
                    root + "/extraction/image",
                    headers=headers,
                    json={
                        "image_base64":
                            receipt(language, today),
                        "privacy_reviewed": True,
                    },
                ),
                f"{language} image extraction",
            )

            content = result["candidate"]

            if (
                not result["can_create_draft"]
                or content is None
            ):
                raise RuntimeError(
                    f"{language}: missing fields or warnings "
                    "require investigation."
                )

            actual = sorted(
                (
                    Decimal(item["quantity"]),
                    item["unit_price_cents"],
                )
                for item in content["items"]
            )

            if (
                actual
                != [(Decimal("1"), 189), (Decimal("2"), 119)]
                or content["reported_total_cents"] != 427
                or content["purchased_on"] != today
                or content["currency"] != "EUR"
                or content["adjustments"] != []
            ):
                raise RuntimeError(
                    f"{language}: output disagrees with "
                    "synthetic ground truth."
                )

            if result["saved"] or not result["trace_sent"]:
                raise RuntimeError(
                    "Unexpected save or missing LangSmith trace."
                )

            prefix = "data:image/jpeg;base64,"

            if not result["source_image_data_url"].startswith(prefix):
                raise RuntimeError(
                    "Expected prepared JPEG source."
                )

            source = base64.b64decode(
                result["source_image_data_url"][len(prefix):],
                validate=True,
            )

            if (
                hashlib.sha256(source).hexdigest()
                != result["source_sha256"]
            ):
                raise RuntimeError(
                    "Source fingerprint mismatch."
                )

            print(
                "[PASS] Ground truth and source verified; "
                "run_id:",
                result["run_id"],
            )

        after = expect(
            client.get(
                root + "/purchases",
                headers=headers,
            ),
            "History after",
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
                "check configuration."
            )

        raise SystemExit(1)