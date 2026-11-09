"""Read-only live privacy checks. Tokens are environment input; no bodies/IDs/tokens in reports."""

import argparse
import json
import os
from datetime import date
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, build_opener, HTTPRedirectHandler
from uuid import UUID


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--base-url", default="http://localhost:8000")
    p.add_argument("--household-a", type=UUID, required=True)
    p.add_argument("--household-b", type=UUID, required=True)
    p.add_argument("--out", default="validation/privacy-access-results.json")
    args = p.parse_args()
    parsed = urlparse(args.base_url)
    if (
        parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.scheme not in {"http", "https"}
        or parsed.scheme == "http"
        and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
    ):
        raise SystemExit(
            "Use localhost HTTP or a trusted HTTPS backend URL, without credentials/query/fragment."
        )
    a = os.environ.get("BONWISE_PRIVACY_TOKEN_A")
    b = os.environ.get("BONWISE_PRIVACY_TOKEN_B")
    if not a or not b or a == b or args.household_a == args.household_b:
        raise SystemExit(
            "Use two distinct accounts/tokens and households. Account A must not be a member of B, or vice versa."
        )
    opener = build_opener(NoRedirect())
    stamp = date.today().isoformat()
    paths = [
        ("purchase_history", "/purchases?limit=1&offset=0"),
        ("spending_summary", f"/spending-summary?start_date={stamp}&end_date={stamp}"),
        ("budgets", "/budgets"),
        ("home", "/shopping-overview"),
        ("offer_options", "/offer-options"),
        ("offer_recommendations", "/offers/recommendations"),
    ]
    results = []

    def request(household, suffix, token):
        headers = {"Authorization": "Bearer " + token} if token else {}
        req = Request(
            args.base_url.rstrip("/") + f"/households/{household}" + suffix,
            headers=headers,
        )
        try:
            with opener.open(req, timeout=20) as r:
                return r.status, r.headers.get("Cache-Control", "")
        except HTTPError as e:
            return e.code, e.headers.get("Cache-Control", "")
        except (URLError, TimeoutError, OSError):
            return None, ""

    for label, suffix in paths:
        for mode, household, token, expected in [
            ("own_a", args.household_a, a, {200}),
            ("own_b", args.household_b, b, {200}),
            ("cross_a_to_b", args.household_b, a, {403, 404}),
            ("cross_b_to_a", args.household_a, b, {403, 404}),
            ("unauthenticated", args.household_a, None, {401, 403}),
        ]:
            status, cache = request(household, suffix, token)
            results.append(
                dict(
                    check=label + ":" + mode,
                    status=status,
                    access_pass=status in expected,
                    no_store_pass="no-store" in cache.lower(),
                    passed=status in expected and "no-store" in cache.lower(),
                )
            )
    report = dict(
        scope="Read access and cache headers only; not a GDPR compliance/security certification.",
        passed=sum(r["passed"] for r in results),
        checks=len(results),
        results=results,
    )
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(report, indent=2))
    print(
        json.dumps(
            dict(passed=report["passed"], checks=report["checks"], report=args.out)
        )
    )
    if report["passed"] != report["checks"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
