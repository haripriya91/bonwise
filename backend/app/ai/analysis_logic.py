"""Deterministic analysis over confirmed, reconciled purchase snapshots."""
import unicodedata
from decimal import Decimal, InvalidOperation
from typing import get_args

from app.schemas.analysis import EvidenceReference
from app.schemas.purchase import Category, SpendingSummary

LABELS = {
    "en": dict(groceries="Groceries", clothing="Clothing & accessories",
               household="Household", personal_care="Personal care",
               transport="Transport", dining_out="Dining out", other="Other"),
    "de": dict(groceries="Lebensmittel", clothing="Kleidung & Accessoires",
               household="Haushalt", personal_care="Körperpflege",
               transport="Mobilität", dining_out="Essen außer Haus", other="Sonstiges"),
}

# Explicit lexical translations, not semantic/canonical product matching.
TERMS = {"milk": ("milk", "milch"), "milch": ("milk", "milch"),
         "bread": ("bread", "brot"), "brot": ("bread", "brot"),
         "rice": ("rice", "reis"), "reis": ("rice", "reis")}


def normal(value):
    return " ".join(unicodedata.normalize("NFKC", value or "").casefold().split())


def matches(value, query, product=False):
    if not query:
        return True
    query = normal(query)
    choices = TERMS.get(query, (query,)) if product else (query,)
    return any(term in normal(value) for term in choices)


def money(cents):
    euros, rest = divmod(abs(cents), 100)
    grouped = f"{euros:,}".replace(",", ".")
    return f"{'-' if cents < 0 else ''}{grouped},{rest:02d} €"


def period(start, end):
    return f"{start:%d.%m.%Y}–{end:%d.%m.%Y}"


def join_names(names, german=False):
    if len(names) < 2:
        return "".join(names)
    return ", ".join(names[:-1]) + (" und " if german else " and ") + names[-1]


def summarize(records, start, end):
    totals = dict.fromkeys(get_args(Category), 0)
    for p in records:
        for item in p["items"]:
            totals[item["category"]] += item["line_total_cents"]
        for a in p["adjustments"]:
            totals[a["category"]] += a["amount_cents"]
    total = sum(p["total_cents"] for p in records)
    if sum(totals.values()) != total:
        raise ValueError("Inconsistent confirmed category totals.")
    return SpendingSummary(start_date=start, end_date=end, currency="EUR",
                           purchase_count=len(records), total_cents=total,
                           categories=[{"category": k, "total_cents": v}
                                       for k, v in totals.items()])


def amount(summary, category):
    return (summary.total_cents if category is None else
            next(c.total_cents for c in summary.categories if c.category == category))


def summary_ref(summary, category):
    return EvidenceReference(
        evidence_id=f"summary:{summary.start_date}:{summary.end_date}:{category or 'all'}",
        kind="summary", start_date=summary.start_date, end_date=summary.end_date,
        category=category, total_cents=amount(summary, category),
        purchase_count=summary.purchase_count)


def purchase_ref(p, plan, item=None):
    return EvidenceReference(
        evidence_id=f"purchase:{p['id']}" + (f":item:{item['id']}" if item else ""),
        kind="purchase", start_date=plan.start_date, end_date=plan.end_date,
        purchase_id=p["id"], merchant=p["merchant"], purchased_on=p["purchased_on"],
        total_cents=item["line_total_cents"] if item else p["total_cents"],
        category=item["category"] if item else None,
        item_description=item["description"] if item else None,
        item_id=item["id"] if item else None,
        quantity=str(item["quantity"]) if item else None,
        quantity_unit=item["quantity_unit"] if item else None)


def filtered_lines(records, plan, include_other_units=False):
    return [(p, i) for p in records for i in p["items"]
            if (plan.category is None or i["category"] == plan.category)
            and matches(i["description"], plan.product_query, product=True)
            and matches(p["merchant"], plan.merchant_query)
            and (include_other_units or getattr(plan, "quantity_unit", None) is None
                 or i["quantity_unit"] == plan.quantity_unit)]


def quantity_text(value):
    whole, _, fraction = format(value, "f").partition(".")
    fraction = fraction.rstrip("0")
    return f"{int(whole):,}".replace(",", ".") + ("," + fraction if fraction else "")


def unit_text(unit, value, german):
    if german:
        return {"pack": "Packung" if value == 1 else "Packungen",
                "each": "Stück", "kg": "kg", "l": "Liter"}[unit]
    return {"pack": "pack" if value == 1 else "packs",
            "each": "item" if value == 1 else "items", "kg": "kg",
            "l": "litre" if value == 1 else "litres"}[unit]


def quantity_answer(plan, records):
    de = plan.language == "de"
    base = {"status": "answered", "evidence": [], "summaries": [],
            "evidence_truncated": False, "quantity_totals": []}
    # Gather all matching units before selecting the requested one. Never hide
    # litres/each or silently treat them as packs.
    lines = filtered_lines(records, plan, include_other_units=True)
    if not lines:
        return {**base, "status": "no_data", "answer": (
            f"Für diesen Zeitraum sind keine Einkäufe von „{plan.product_query}“ gespeichert."
            if de else f"No purchases of “{plan.product_query}” are recorded for this period.")}
    totals = {}
    try:
        for _, item in lines:
            unit = item["quantity_unit"]
            if unit not in {"pack", "each", "kg", "l"}:
                raise ValueError("Unknown recorded unit")
            value = Decimal(str(item["quantity"]))
            if not value.is_finite() or value <= 0:
                raise ValueError("Invalid recorded quantity")
            totals[unit] = totals.get(unit, Decimal("0")) + value
    except (InvalidOperation, ValueError, TypeError, KeyError):
        return {**base, "status": "clarification_required", "answer": (
            "Bei einigen passenden Artikeln fehlt eine gültige Menge oder Einheit. Bitte prüfen Sie die Einkaufsdetails."
            if de else "Some matching items need their quantity or unit checked. Please review their purchase details.")}
    base["quantity_totals"] = [{"quantity": format(totals[u].normalize(), "f"),
                                "quantity_unit": u} for u in sorted(totals)]
    base["evidence"] = [purchase_ref(p, plan, item) for p, item in lines[:20]]
    base["evidence_truncated"] = len(lines) > 20
    requested = plan.quantity_unit
    def label(unit):
        return f"{quantity_text(totals[unit])} {unit_text(unit, totals[unit], de)}"
    if requested is None:
        quantities = join_names([label(u) for u in sorted(totals)], de)
        answer = (f"Ihre gespeicherten Einkäufe von „{plan.product_query}“ ergeben {quantities}."
                  if de else f"Your saved purchases of “{plan.product_query}” total {quantities}.")
    elif requested in totals:
        answer = (f"Sie haben {label(requested)} {plan.product_query} gekauft."
                  if de else f"You bought {label(requested)} of {plan.product_query}.")
    else:
        quantities = join_names([label(u) for u in sorted(totals)], de)
        answer = (f"Gespeichert sind {quantities} „{plan.product_query}“. Die angefragte Einheit ist nicht erfasst."
                  if de else f"Your purchases show {quantities} of “{plan.product_query}”, but not the requested unit.")
    others = [label(u) for u in sorted(totals) if requested and u != requested]
    if requested in totals and others:
        answer += (" Zusätzlich getrennt erfasst: " if de else " Also recorded separately: ") + join_names(others, de) + "."
    if requested and others:
        base["calculation_note"] = (
            "Unterschiedliche Einheiten werden nicht ineinander umgerechnet; für Packungen fehlt eine verlässliche Größenangabe."
            if de else "Different units are kept separate. A pack count cannot be inferred from weight or volume without a verified pack size.")
    base["answer"] = answer
    return base


def execute(plan, records, baseline=None):
    """Returns answer/status/evidence without any model-generated figures."""
    de = plan.language == "de"
    label = LABELS[plan.language]
    base = {"status": "answered", "evidence": [], "summaries": [],
            "evidence_truncated": False}
    when = period(plan.start_date, plan.end_date)
    records = [p for p in records if matches(p["merchant"], plan.merchant_query)]
    qualifier = ""
    if plan.merchant_query:
        qualifier = (f" Für Händler mit „{plan.merchant_query}“ im Namen." if de else
                     f" For stores with “{plan.merchant_query}” in their name.")

    if plan.operation == "quantity":
        return quantity_answer(plan, records)

    if not records:
        return {**base, "status": "no_data", "answer": (
            "Für diesen Zeitraum sind keine Einkäufe gespeichert." if de else
            "You have no saved purchases for this period.") + qualifier}

    if plan.operation in {"summary", "highest_category", "compare"}:
        current = summarize(records, plan.start_date, plan.end_date)
        base["summaries"] = [current]
        base["evidence"] = [summary_ref(current, plan.category)]
        name = label[plan.category] if plan.category else ("Gesamtausgaben" if de else "Total spending")
        if plan.operation == "highest_category":
            peak = max(c.total_cents for c in current.categories)
            winners = [c for c in current.categories if c.total_cents == peak]
            names = join_names([label[c.category] for c in winners], de)
            if len(winners) == 1:
                answer = (f"Am meisten haben Sie für {names} ausgegeben: {money(peak)}."
                          if de else f"You spent the most on {names}: {money(peak)}.")
            else:
                answer = (f"{names} liegen gleichauf: jeweils {money(peak)}."
                          if de else f"{names} are tied for highest spending: {money(peak)} each.")
            base["evidence"] = [summary_ref(current, c.category) for c in winners]
        elif plan.operation == "compare":
            previous_records = [p for p in baseline or []
                                if matches(p["merchant"], plan.merchant_query)]
            previous = summarize(previous_records, plan.comparison_start_date,
                                 plan.comparison_end_date)
            base["summaries"].append(previous)
            base["evidence"].append(summary_ref(previous, plan.category))
            if not previous_records:
                return {**base, "status": "no_data", "answer": (
                    "Für den Vergleichszeitraum sind keine Einkäufe gespeichert. Deshalb kann ich die Ausgaben noch nicht vergleichen."
                    if de else "You have no saved purchases for the comparison period, so I cannot compare the spending yet.")}
            now, before = amount(current, plan.category), amount(previous, plan.category)
            delta = now - before
            change = ("+" if delta > 0 else "") + money(delta)
            older = period(previous.start_date, previous.end_date)
            answer = (f"{name}: {money(now)} ({when}) gegenüber {money(before)} ({older}). Differenz: {change}."
                      if de else f"{name}: {money(now)} ({when}) versus {money(before)} ({older}). Difference: {change}.")
        else:
            answer = (f"{name}: {money(amount(current, plan.category))}." if plan.category else
                      (f"Sie haben insgesamt {money(current.total_cents)} ausgegeben." if de else
                       f"You spent {money(current.total_cents)} in total."))
        base["answer"] = answer + qualifier
        return base

    lines = filtered_lines(records, plan)
    if plan.operation == "records":
        if plan.category or plan.product_query:
            refs = [purchase_ref(p, plan, i) for p, i in lines]
            rows = [f"• {i['description']} — {money(i['line_total_cents'])}"
                    + ((f" bei {p['merchant']}" if de else f" at {p['merchant']}")
                       if p["merchant"] else "") for p, i in lines[:10]]
            heading = "Sie haben gekauft:" if de else "You bought:"
        else:
            refs = [purchase_ref(p, plan) for p in records]
            rows = []
            for p in records[:10]:
                merchant = p["merchant"] or ("Händler nicht angegeben" if de else "Store not recorded")
                names = ", ".join(i["description"] for i in p["items"][:3])
                if len(p["items"]) > 3:
                    names += " …"
                rows.append(f"• {merchant} — {money(p['total_cents'])}: {names}")
            heading = "Ihre Einkäufe:" if de else "Your purchases:"
        base["evidence"] = refs[:10]
        base["evidence_truncated"] = len(refs) > 10
        base["status"] = "answered" if refs else "no_data"
        base["answer"] = ((heading + "\n" + "\n".join(rows)) if refs else (
            "Ich habe keine passenden gespeicherten Artikel gefunden." if de else
            "I could not find any matching saved items.")) + qualifier
        if len(refs) > 10:
            base["answer"] += " Maximal 10 angezeigt." if de else " Showing at most 10."
        return base

    groups = {}
    if plan.operation == "items":
        for p, i in lines:
            key = ("product", i["product_id"]) if i["product_id"] else ("text", normal(i["description"]))
            g = groups.setdefault(key, {"label": i["description"], "total": 0,
                                        "purchases": set(), "refs": []})
            g["total"] += i["line_total_cents"]
            g["purchases"].add(p["id"])
            g["refs"].append(purchase_ref(p, plan, i))
    else:  # merchants
        for p in records:
            relevant = [i for i in p["items"] if plan.category is None or i["category"] == plan.category]
            adjustment_total = sum(a["amount_cents"] for a in p["adjustments"]
                                   if plan.category is None or a["category"] == plan.category)
            if plan.category and not relevant and adjustment_total == 0:
                continue
            total = sum(i["line_total_cents"] for i in relevant) + adjustment_total
            # Missing merchant names do not establish a common merchant identity.
            key = normal(p["merchant"]) if p["merchant"] else ("unknown", p["id"])
            g = groups.setdefault(key, {"label": p["merchant"] or ("Händler unbekannt" if de else "Merchant not recorded"),
                                        "total": 0, "purchases": set(), "refs": []})
            g["total"] += total
            g["purchases"].add(p["id"])
            # The source is the full purchase; category aggregates are explained separately.
            g["refs"].append(purchase_ref(p, plan))

    if not groups:
        return {**base, "status": "no_data", "answer": (
            "Ich habe keine passenden gespeicherten Artikel oder Händler gefunden." if de else
            "I could not find any matching saved items or stores.") + qualifier}
    frequency = plan.metric == "purchase_frequency"
    score = (lambda g: len(g["purchases"])) if frequency else (lambda g: g["total"])
    ranked = sorted(groups.values(), key=lambda g: (-score(g), normal(g["label"])))
    cutoff = score(ranked[min(plan.top_n, len(ranked)) - 1])
    winners = [g for g in ranked if score(g) >= cutoff]  # Preserve ties.
    displayed = winners[:10]
    names = join_names([g["label"] for g in displayed], de)
    single_rank = plan.top_n == 1 and len(winners) <= 3
    if frequency:
        count = len(displayed[0]["purchases"])
        if single_rank:
            if plan.operation == "items":
                answer = (f"Am häufigsten haben Sie {names} gekauft — "
                          f"{'jeweils ' if len(displayed) > 1 else ''}in {count} Einkäufen."
                          if de else
                          f"You bought {names} most often — "
                          f"{count} {'purchase' if count == 1 else 'purchases'}"
                          f"{' each' if len(displayed) > 1 else ''}.")
            else:
                answer = (f"Am häufigsten haben Sie bei {names} eingekauft — "
                          f"{'jeweils ' if len(displayed) > 1 else ''}{count} Einkäufe."
                          if de else f"You shopped most often at {names} — "
                          f"{count} {'purchase' if count == 1 else 'purchases'}"
                          f"{' each' if len(displayed) > 1 else ''}.")
        else:
            heading = (("Ihre am häufigsten gekauften Artikel:" if de else
                        "Your most frequently purchased items:") if plan.operation == "items"
                       else ("Hier haben Sie am häufigsten eingekauft:" if de else
                             "The stores you shopped at most often:"))
            rows = [f"• {g['label']} — {len(g['purchases'])} " + (
                    "Einkäufe" if de else
                    ("purchase" if len(g['purchases']) == 1 else "purchases"))
                    for g in displayed]
            answer = heading + "\n" + "\n".join(rows)
    elif plan.operation == "items" and plan.product_query:
        total = sum(g["total"] for g in groups.values())
        answer = (f"Die passenden Artikel für „{plan.product_query}“ kosten insgesamt {money(total)}:"
                  if de else f"The items found for “{plan.product_query}” total {money(total)}:")
        answer += "\n" + "\n".join(f"• {g['label']} — {money(g['total'])}" for g in displayed)
    elif single_rank:
        total = displayed[0]["total"]
        if len(displayed) > 1:
            answer = (f"{names} liegen bei den höchsten Ausgaben gleichauf: jeweils {money(total)}."
                      if de else f"{names} are tied for your highest spending: {money(total)} each.")
        elif plan.operation == "items":
            answer = (f"Am meisten haben Sie für {names} ausgegeben: {money(total)}."
                      if de else f"You spent the most on {names}: {money(total)}.")
        else:
            answer = (f"Am meisten haben Sie bei {names} ausgegeben: {money(total)}."
                      if de else f"You spent the most at {names}: {money(total)}.")
    else:
        heading = ("Dafür haben Sie am meisten ausgegeben:" if de else
                   "Your highest spending was on:") if plan.operation == "items" else (
                   "Ihre Ausgaben nach Händler:" if de else "Your spending by store:")
        answer = heading + "\n" + "\n".join(
            f"• {g['label']} — {money(g['total'])}" for g in displayed)

    if plan.category and plan.operation in {"items", "merchants"}:
        answer += (f" Kategorie: {label[plan.category]}." if de else
                   f" Category: {label[plan.category]}.")
    refs = [r for g in displayed for r in g["refs"]]
    base["evidence"] = refs[:20]
    base["evidence_truncated"] = len(refs) > 20 or len(winners) > 10
    base["answer"] = answer + qualifier
    if plan.operation == "items" and not frequency:
        base["calculation_note"] = (
            "Rabatte für den gesamten Einkauf und Pfand sind nicht auf einzelne Artikel verteilt."
            if de else "Whole-purchase discounts and deposits are not split between individual items.")
    if len(winners) > 10:
        base["answer"] += " Weitere Artikel liegen gleichauf; die ersten 10 werden angezeigt." if de else " More results are tied; showing the first 10."
    return base