"""Read-only insight facts. All figures, wording, actions and evidence are verified code output."""
import hashlib
import json

from app.ai.analysis_logic import LABELS, money, normal, period, summarize
from app.schemas.insights import InsightCard, InsightEvidence, RankedInsightItem
from app.services.analysis import load_snapshot
from app.services.analysis_budget import for_period

NOTICE = "Saved receipts are a partial picture of household spending. High spending alone does not mean overspending."


def revision(records, budget):
    data = json.dumps({"records": records, "budget": budget}, default=str, sort_keys=True)
    return hashlib.sha256(data.encode()).hexdigest()


def load_facts(db, household_id, user_id, body):
    records = load_snapshot(db, household_id, user_id, body.start_date, body.end_date)
    context = for_period(db, household_id, user_id, body.start_date, body.end_date)
    return actionable_facts(build_facts(records, context, body), body)


def evidence(records, ids):
    selected = [p for p in records if p["id"] in ids]
    return [InsightEvidence(purchase_id=p["id"], purchased_on=p["purchased_on"],
                            total_cents=p["total_cents"]) for p in selected[:20]], len(selected) > 20


def build_facts(records, context, body):
    summary = summarize(records, body.start_date, body.end_date)
    result = dict(records=records, context=context, revision=revision(records, context),
                  summary=summary, cards=[], top_items=[], candidates=[])
    if not records:
        return result

    totals = {c.category: c.total_cents for c in summary.categories}
    present = {i["category"] for p in records for i in p["items"]}
    present.update(a["category"] for p in records for a in p["adjustments"])
    largest = max(totals[c] for c in present)
    winners = sorted(c for c in present if totals[c] == largest)
    names = ", ".join(LABELS["en"][c] for c in winners)
    share = (round(largest / summary.total_cents * 100, 1)
             if summary.total_cents > 0 and all(v >= 0 for v in totals.values()) else None)
    share_text = f" ({share}% of recorded spending{' each' if len(winners) > 1 else ''})" if share is not None else ""
    ids = {p["id"] for p in records if any(i["category"] in winners for i in p["items"])
           or any(a["category"] in winners for a in p["adjustments"])}
    refs, truncated = evidence(records, ids)
    category = InsightCard(id="category", title="Largest spending contributor" if len(winners) == 1 else "Categories tied for highest spending",
        detail=f"{names}: {money(largest)}{' each' if len(winners) > 1 else ''}{share_text}. Selected dates: {period(body.start_date, body.end_date)}.",
        action="review", evidence=refs, evidence_truncated=truncated)

    groups = {}
    for p in records:
        for i in p["items"]:
            # Unmatched wording remains separate across category/unit; do not invent semantic identity.
            key = (("product", i["product_id"]) if i["product_id"] else
                   ("text", normal(i["description"]) or i["id"], i["category"], i["quantity_unit"]))
            row = groups.setdefault(key, dict(label=(i["description"] or "Unnamed item")[:120], total=0, ids=set(), lines=[]))
            row["total"] += i["line_total_cents"]
            row["ids"].add(p["id"])
            row["lines"].append((p, i))
    ranked = sorted(groups.values(), key=lambda x: (-x["total"], x["label"].casefold()))
    result["groups"] = ranked
    top = ranked[:3]
    more_ties = len(ranked) > 3 and ranked[3]["total"] == top[-1]["total"]
    refs, truncated = evidence(records, set().union(*(x["ids"] for x in top)))
    item_card = InsightCard(id="items", title="Top recorded items",
        detail="; ".join(f"{x['label']}: {money(x['total'])}" for x in top)
        + ". Across the entire selected period; item line totals exclude receipt-level adjustments."
        + (" More items tie at the third amount; this view shows three." if more_ties else ""),
        action="review", evidence=refs, evidence_truncated=truncated)
    result["top_items"] = [RankedInsightItem(label=x["label"], total_cents=x["total"], purchase_count=len(x["ids"])) for x in top]

    budget = context["budget"]
    if budget:
        # The budget ledger is for its own dates, never a selected-subperiod remainder.
        scope = period(budget["start_date"], budget["end_date"])
        over = budget["spent_cents"] > budget["amount_cents"]
        detail = (f"Budget period {scope}: target {money(budget['amount_cents'])}; recorded spending {money(budget['spent_cents'])}; "
                  f"open reservations {money(budget['reserved_cents'])}; available after reservations {money(budget['available_cents'])}.")
        if over:
            detail += f" Recorded spending is over this period's target by {money(budget['spent_cents'] - budget['amount_cents'])}."
        elif budget["available_cents"] < 0:
            detail += " Recorded spending is within the target; open reservations take the available amount below zero."
        detail += " These figures cover the full budget period."
        budget_card = InsightCard(id="budget", title="Budget context", detail=detail, action="budget")
        budget_choices = ["review_budget"]
    else:
        detail = ("No single budget covers the selected dates." if context["status"] == "partial"
                  else "No budget target is set for the selected dates.")
        budget_card = InsightCard(id="budget", title="Choose a spending target", detail=detail + " Category contribution alone cannot establish overspending.", action="budget")
        budget_choices = ["set_target"]
    result["cards"] = [category, item_card, budget_card]
    # Model sees numeric aggregate facts and opaque IDs only, not product/merchant wording or purchase IDs.
    result["candidates"] = [
        dict(fact_id="category", category_codes=winners, total_cents=largest, share_percent=share, allowed=["review"]),
        dict(fact_id="items", ranked_items=[dict(total_cents=x["total"], purchase_count=len(x["ids"])) for x in top],
             allowed=["review", "compare"] + (["check_home"] if any(len(x["ids"]) > 1 for x in top) else [])),
        dict(fact_id="budget", status=context["status"], budget=None if not budget else {
            k: budget[k] for k in ["start_date", "end_date", "amount_cents", "spent_cents", "reserved_cents", "available_cents"]}, allowed=budget_choices),
    ]
    return result


SUGGESTIONS = {
    "review": ("Review the contributing purchases before deciding whether to change your spending.", "review"),
    "check_home": ("Some top items appear in multiple purchases. Check what is already at home before buying them again.", "review"),
    "compare": ("If you plan to buy these items again, compare compatible unit-price offers. Availability and prices may differ.", "offers"),
    "set_target": ("Consider setting a target for the relevant dates so future spending has a meaningful comparison.", "budget"),
    "review_budget": ("Review this budget period's purchases and open reservations before committing more funds.", "budget"),
    "plan_check": ("Keep only the items you still intend to buy when reviewing your next shopping list.", "planner"),
}


def actionable_facts(facts, body):
    """New information or a specific decision, rather than repeating chart/stat tiles."""
    records, budget = facts["records"], facts["context"]["budget"]
    if not records:
        return facts
    cards, candidates = [], []

    def add(fact_id, title, detail, choice, action, ids=(), **aggregates):
        refs, truncated = evidence(records, set(ids))
        cards.append(InsightCard(id=fact_id, title=title, detail=detail, action=action,
                                 evidence=refs, evidence_truncated=truncated))
        candidates.append(dict(fact_id=fact_id, allowed=[choice], **aggregates))

    # Only exceptional budget conditions warrant another card; a healthy balance
    # is already visible above. Reservations are never described as spending.
    if budget and budget["available_cents"] < 0:
        scope = period(budget["start_date"], budget["end_date"])
        if budget["spent_cents"] > budget["amount_cents"]:
            detail = f"Recorded spending is {money(budget['spent_cents'] - budget['amount_cents'])} over the explicit target for {scope}. Review this budget period's purchases before adding commitments."
        else:
            detail = f"Open reservations take available funds {money(-budget['available_cents'])} below zero for {scope}; recorded spending itself has not exceeded the target. Review those reservations before approving another plan."
        add("budget_pressure", "Check commitments before your next shop", detail, "review_budget", "budget",
            scope_start=budget["start_date"], scope_end=budget["end_date"],
            target_cents=budget["amount_cents"], spent_cents=budget["spent_cents"],
            reserved_cents=budget["reserved_cents"], available_cents=budget["available_cents"])

    repeats = []
    for group in facts.get("groups", []):
        positive = [(p, i) for p, i in group["lines"] if i["line_total_cents"] > 0]
        ids = {p["id"] for p, _ in positive}
        if len(ids) >= 2:
            dates = sorted({p["purchased_on"] for p, _ in positive})
            repeats.append((group, ids, dates))
    if repeats:
        chosen = repeats[:3]
        detail = "; ".join(f"{g['label']} appears on {len(ids)} purchase records between {dates[0]:%d %b %Y} and {dates[-1]:%d %b %Y}" for g, ids, dates in chosen)
        detail += ". Check what is already at home before adding these items again. Recorded purchases do not establish consumption or waste."
        add("repeat_purchases", "Check repeat purchases against what is at home", detail, "check_home", "review",
            set().union(*(ids for _, ids, _ in chosen)),
            anonymous_items=[dict(purchase_count=len(ids), first_date=dates[0], last_date=dates[-1]) for _,ids,dates in chosen])

    changes = []
    for group in facts.get("groups", []):
        # Compare only a confirmed product identity and the same recorded unit.
        # Multiple prices for one date are ambiguous; do not pick one arbitrarily.
        units = {}
        for p, item in group["lines"]:
            if not item["product_id"] or item["line_total_cents"] < 0 or item["unit_price_cents"] is None:
                continue
            key = (item["product_id"], item["quantity_unit"])
            units.setdefault(key, {}).setdefault(p["purchased_on"], []).append((p, item))
        for (_, unit), days in units.items():
            dates = sorted(days)
            if len(dates) < 2:
                continue
            previous, latest = dates[-2:]
            before, after = days[previous], days[latest]
            old = {i["unit_price_cents"] for _, i in before}
            new = {i["unit_price_cents"] for _, i in after}
            if len(old) != 1 or len(new) != 1:
                continue
            old_price, new_price = next(iter(old)), next(iter(new))
            if new_price > old_price:
                changes.append((new_price-old_price, group["label"], unit, previous, latest,
                                old_price, new_price, {p["id"] for p,_ in before+after}))
    if changes:
        diff, label, unit, previous, latest, old, new, ids = max(changes, key=lambda row: row[0])
        detail = f"{label}: the recorded unit price rose from {money(old)} per {unit} on {previous:%d %b %Y} to {money(new)} on {latest:%d %b %Y} ({money(diff)} more per {unit}). Compare compatible offers if buying again. Receipt-level discounts may affect checkout totals; the records do not establish why prices differ."
        add("price_change", "Compare a recently higher recorded unit price", detail, "compare", "offers", ids,
            previous_date=previous, latest_date=latest, previous_unit_price_cents=old,
            latest_unit_price_cents=new, absolute_change_cents=diff, unit=unit)

    clothing = [(p,i) for p in records for i in p["items"] if i["category"] == "clothing" and i["line_total_cents"] > 0]
    if clothing:
        names = list(dict.fromkeys((i["description"] or "Clothing item")[:120] for _,i in clothing))[:3]
        detail = "These receipts include " + ", ".join(names) + ". Past clothing purchases are not evidence that you need the same items in your next weekly grocery shop. Review these candidates before copying purchase history into a list."
        add("planning_selection", "Check clothing before repeating this shop", detail, "plan_check", "planner",
            {p["id"] for p,_ in clothing}, category="clothing", item_count=len(clothing))

    if budget is None:
        detail = ("No single target covers these dates. Choose a budget period that matches the spending you want to assess before deciding what is over target."
                  if facts["context"]["status"] == "partial" else
                  "Set a target for the dates you want to manage before deciding whether to reduce spending. Receipts alone cannot establish what is affordable for your household.")
        add("missing_target", "Give future spending a meaningful target", detail, "set_target", "budget",
            budget_status=facts["context"]["status"])

    facts["cards"], facts["candidates"] = cards[:3], candidates
    # Fallback remains useful and bounded; the model may prioritize any three
    # eligible findings. No finding is created simply to fill three spaces.
    facts["eligible_cards"] = cards
    if not cards:
        facts["notice"] = "No additional supported suggestions for these dates yet. More confirmed history may reveal repeat purchases or comparable price changes. Saved receipts remain a partial spending record."
    return facts


def apply_selection(facts, selection):
    allowed = {c["fact_id"]: set(c["allowed"]) for c in facts["candidates"]}
    seen = set()
    selected = {}
    for row in selection.selections:
        if row.fact_id in seen or row.suggestion_id not in allowed.get(row.fact_id, set()):
            raise ValueError("Unverified suggestion reference.")
        seen.add(row.fact_id)
        selected[row.fact_id] = row.suggestion_id
    cards = []
    eligible = facts.get("eligible_cards", facts["cards"])
    for card in eligible:
        if "eligible_cards" in facts and card.id not in selected:
            continue
        if card.id in selected:
            text, action = SUGGESTIONS[selected[card.id]]
            card = card.model_copy(update={"suggestion": text, "action": action})
        cards.append(card)
    if "eligible_cards" in facts and any(c.id == "budget_pressure" for c in eligible) and "budget_pressure" not in selected:
        raise ValueError("A verified budget constraint was omitted.")
    return cards
