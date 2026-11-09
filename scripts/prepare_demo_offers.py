"""Prepare a clearly synthetic three-week fixture for the current calendar week. No API calls."""

import argparse
import hashlib
import json
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


def main():
    p = argparse.ArgumentParser()
    p.add_argument("template")
    p.add_argument("output")
    p.add_argument("--week")
    args = p.parse_args()
    today = datetime.now(ZoneInfo("Europe/Berlin")).date()
    if args.week:
        year, w = map(int, args.week.split("-W"))
        anchor = date.fromisocalendar(year, w, 1)
    else:
        anchor = today - timedelta(days=today.weekday())
    if Path(args.output).exists():
        raise SystemExit(
            "Output already exists. Keep the previous snapshot; choose a new output path."
        )
    base = "bonwise_retail_dataset/"
    with zipfile.ZipFile(args.template) as z:
        data = {n: json.loads(z.read(n)) for n in z.namelist() if n.endswith(".json")}
    old = sorted(b["batch_id"] for b in data[base + "data/batches.json"])
    if len(old) != 3:
        raise SystemExit("Use the three-week synthetic template.")
    starts = {b: anchor + timedelta(weeks=i - 1) for i, b in enumerate(old)}
    new = {
        b: f"national-demo-{d.isocalendar().year}-W{d.isocalendar().week:02}"
        for b, d in starts.items()
    }
    rows = data[base + "data/offers_normalized_reference.json"]
    for row in rows:
        before = row["batch_id"]
        start = starts[before]
        row["store_scope"] = "national"
        row["store_ids"] = []
        row["country_code"] = "DE"
        row["national_scope_basis"] = "synthetic_national_fixture_v1"
        row["validation_flags"] = [
            f for f in row["validation_flags"] if f != "store_scope_unknown"
        ]
        row["review_status"] = "needs_review" if row["validation_flags"] else "approved"
        row["batch_id"] = new[before]
        row["offer_id"] = row["offer_id"].replace(before, new[before])
        if row["valid_from"]:
            row["valid_from"] = start.isoformat()
        if row["valid_until"]:
            row["valid_until"] = (start + timedelta(days=6)).isoformat()
    newdata = {
        base + n: data[base + n] for n in ["data/retailers.json", "data/stores.json"]
    }
    newdata[base + "data/batches.json"] = [
        dict(b, batch_id=new[b["batch_id"]]) for b in data[base + "data/batches.json"]
    ]
    newdata[base + "data/offers_normalized_reference.json"] = rows
    for before in old:
        raw = data[base + f"data/raw/{before}/offers.json"]
        start = starts[before]
        for row in raw:
            row["storeId"] = None
            row["demoCoverage"] = {
                "scope": "national",
                "country_code": "DE",
                "basis": "synthetic_national_fixture_v1",
            }
            row["id"] = row["id"].replace(before, new[before])
            row["sourceUrl"] = row["sourceUrl"].replace(before, new[before])
            if row["validFrom"]:
                row["validFrom"] = start.isoformat()
            if row["validTo"]:
                row["validTo"] = (start + timedelta(days=6)).isoformat()
            row["scrapedAt"] = start.isoformat() + "T06:00:00Z"
        newdata[base + f"data/raw/{new[before]}/offers.json"] = raw
    newdata[base + "data/preparation.json"] = dict(
        source="synthetic",
        template_sha256=hashlib.sha256(Path(args.template).read_bytes()).hexdigest(),
        anchor_monday=anchor.isoformat(),
        prepared_on=today.isoformat(),
        note="New fictional national-offer records, not relabelled real/branch offers. Weekly windows run Monday–Sunday. This does not establish branch opening hours or stock. Prices remain synthetic template prices, not market observations.",
    )
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.output, "w", zipfile.ZIP_DEFLATED) as z:
        for n, value in newdata.items():
            z.writestr(n, json.dumps(value, ensure_ascii=False, indent=2))
    print(
        json.dumps(
            dict(
                output=args.output, anchor_monday=anchor.isoformat(), source="synthetic"
            )
        )
    )


if __name__ == "__main__":
    main()
