from getpass import getpass
from pathlib import Path

import httpx
from dotenv import dotenv_values


ROOT = Path(__file__).resolve().parents[1]
frontend = dotenv_values(ROOT / "frontend" / ".env.local")

SUPABASE_URL = (
    frontend.get("VITE_SUPABASE_URL") or ""
).rstrip("/")

PUBLISHABLE_KEY = frontend.get(
    "VITE_SUPABASE_PUBLISHABLE_KEY"
)

API_URL = "http://localhost:8000"

HOUSEHOLDS = {
    "A": "20000000-0000-4000-8000-000000000001",
    "B": "20000000-0000-4000-8000-000000000002",
}


def main():
    if not SUPABASE_URL or not PUBLISHABLE_KEY:
        print(
            "[FAIL] Set VITE_SUPABASE_URL and "
            "VITE_SUPABASE_PUBLISHABLE_KEY "
            "in frontend/.env.local."
        )
        return 1

    failures = 0

    with httpx.Client(timeout=20) as client:
        for label, headers in [
            ("Missing token", {}),
            ("Invalid token", {"Authorization": "Bearer invalid"}),
        ]:
            response = client.get(
                f"{API_URL}/me",
                headers=headers,
            )
            passed = response.status_code == 401
            failures += int(not passed)
            print(
                f"[{'PASS' if passed else 'FAIL'}] "
                f"{label}: HTTP {response.status_code}"
            )

        for profile in ("A", "B"):
            print(f"\nDemo {profile}")
            email = input("Email: ").strip()
            password = getpass("Password: ")

            login = client.post(
                f"{SUPABASE_URL}/auth/v1/token",
                params={"grant_type": "password"},
                headers={"apikey": PUBLISHABLE_KEY},
                json={"email": email, "password": password},
            )
            del password

            if login.status_code != 200:
                print(
                    f"[FAIL] Demo {profile} login: "
                    f"HTTP {login.status_code}. "
                    "Check credentials and email confirmation."
                )
                failures += 1
                continue

            token = login.json().get("access_token")
            if not token:
                print("[FAIL] No access token returned.")
                failures += 1
                continue

            headers = {"Authorization": f"Bearer {token}"}

            response = client.get(
                f"{API_URL}/me",
                headers=headers,
            )

            if response.status_code != 200:
                print(
                    f"[FAIL] Demo {profile} /me: "
                    f"HTTP {response.status_code}"
                )
                failures += 1
                continue

            actual = {
                item["id"]
                for item in response.json()["households"]
            }
            expected = {HOUSEHOLDS[profile]}
            print("API user ID:", response.json()["user_id"])
            print("Expected household IDs:", sorted(expected))
            print("Actual household IDs:", sorted(actual))
            passed = actual == expected
            failures += int(not passed)

            print(
                f"[{'PASS' if passed else 'FAIL'}] "
                f"Demo {profile} sees only its own household."
            )

            own = client.get(
                f"{API_URL}/households/{HOUSEHOLDS[profile]}",
                headers=headers,
            )
            print("Own household HTTP status:", own.status_code)
            passed = own.status_code == 200
            failures += int(not passed)
            print(
                f"[{'PASS' if passed else 'FAIL'}] "
                "Own household accessible."
            )

            other = "B" if profile == "A" else "A"
            denied = client.get(
                f"{API_URL}/households/{HOUSEHOLDS[other]}",
                headers=headers,
            )
            print("Other household HTTP status:", denied.status_code)
            passed = denied.status_code == 404
            failures += int(not passed)
            print(
                f"[{'PASS' if passed else 'FAIL'}] "
                "Other household inaccessible."
            )

    print(f"\nCompleted with {failures} failed check(s).")
    print("No purchases or history were added.")
    return 1 if failures else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except httpx.RequestError:
        print(
            "[FAIL] Network request failed. "
            "Check that FastAPI is running and Supabase is reachable."
        )
        raise SystemExit(1)