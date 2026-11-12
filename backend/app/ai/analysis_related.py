"""At most two read-only questions over one server-scoped snapshot. No model maths."""
import re

from app.ai.analysis_logic import (LABELS, execute, filtered_lines, item_groups,
    item_identity, join_names, rank_groups)


def child_plan(plan):
    child = plan.related
    return plan.model_copy(update={**child.model_dump(exclude={"scope", "unsupported_reason"}),
        "related": None, "reference_scope": None,
        "clarification_reason": "none", "clarification_target": None,
        "comparison_start_date": None, "comparison_end_date": None})


def valid_related(plan, valid_single):
    child = plan.related
    if child is None:
        return True
    if plan.reference_scope or plan.operation not in {
        "summary", "highest_category", "items", "merchants", "records", "quantity", "budget"}:
        return False
    if child.operation == "unsupported":
        return (child.unsupported_reason != "none" and child.scope == "period"
            and not any((child.category, child.product_query, child.merchant_query, child.quantity_unit)))
    if child.unsupported_reason != "none" or not valid_single(child_plan(plan)):
        return False
    if child.scope != "period":
        return (plan.operation == "highest_category" and child.operation in {"items", "records"}
            and child.category is None and child.merchant_query in {None, plan.merchant_query})
    return True


def scope_question(language, records=False, reference=None):
    if records and reference and not (reference.operation == "highest_category" and reference.related.operation == "items"):
        return ("Meinen Sie die Einkäufe hinter dem ersten oder dem zweiten Ergebnis?"
            if language == "de" else "Do you mean purchases behind the first result or the second result?")
    if language == "de":
        return ("Meinen Sie die Einkäufe der höchsten Kategorie oder die Einkäufe hinter den rangierten Artikeln?"
            if records else "Soll der zweite Teil den gesamten Zeitraum oder nur die höchste Kategorie umfassen?")
    return ("Do you mean purchases in the highest category, or purchases behind the ranked items?"
        if records else "Should the second part cover the whole period, or only the highest category?")


def select_category_records(records, categories, merchant=None):
    # Keep original purchase facts intact; only eligible item lines are selected.
    from app.ai.analysis_logic import matches
    return [{**p, "items": [i for i in p["items"] if i["category"] in categories]}
        for p in records if matches(p["merchant"], merchant)
        and any(i["category"] in categories for i in p["items"])]


def categories_for(plan, records):
    primary = plan.model_copy(update={"related": None, "reference_scope": None})
    result = execute(primary, records)
    return {r.category for r in result["evidence"] if r.category}


def related_selection(plan, records, categories=None):
    child = child_plan(plan)
    if plan.related.scope == "highest_category":
        if categories is None:
            categories = categories_for(plan, records)
        records = select_category_records(records, categories, plan.merchant_query)
        child = child.model_copy(update={"merchant_query": plan.merchant_query})
        if child.operation == "records":
            child = child.model_copy(update={"reference_scope": "highest_category"})
    return child, records


def heading(plan, secondary=False):
    de = plan.language == "de"
    labels = {"summary": ("Recorded spending", "Erfasste Ausgaben"),
        "highest_category": ("Highest spending category", "Kategorie mit den höchsten Ausgaben"),
        "items": ("Item ranking", "Artikelrangliste"), "merchants": ("Stores", "Händler"),
        "records": ("Supporting purchases", "Zugehörige Einkäufe"),
        "quantity": ("Recorded quantities", "Erfasste Mengen"), "budget": ("Budget", "Budget")}
    operation = plan.related.operation if secondary else plan.operation
    title = labels.get(operation, ("Second question", "Zweite Frage"))[de]
    if secondary and plan.related.scope == "period":
        title += " — gesamter angefragter Zeitraum" if de else " — across the requested period"
    return title


def combine(first, second, titles):
    groups = {}
    for index, part in enumerate((first, second), 1):
        for ref in part.get("evidence", []):
            facts = groups.setdefault(ref.evidence_id, {})
            facts.setdefault(ref.model_dump_json(), (index, ref))
    evidence = []
    for identity, facts in groups.items():
        for index, ref in facts.values():
            # Two store-filtered summaries can have the same legacy ID but
            # different facts. Keep both; exact duplicate purchase facts merge.
            evidence.append(ref.model_copy(update={"evidence_id": f"part:{index}:{identity}"}) if len(facts)>1 else ref)
    summaries = {s.model_dump_json(): s for part in (first, second) for s in part.get("summaries", [])}
    notes = list(dict.fromkeys(part["calculation_note"] for part in (first, second) if part.get("calculation_note")))
    status = ("clarification_required" if any(p["status"] == "clarification_required" for p in (first, second)) else
              "answered" if any(p["status"] == "answered" for p in (first, second)) else "no_data")
    return dict(status=status, answer=f"{titles[0]}\n{first['answer']}\n\n{titles[1]}\n{second['answer']}",
        evidence=evidence[:40], summaries=list(summaries.values()),
        evidence_truncated=len(evidence)>40 or any(p.get("evidence_truncated", False) for p in (first, second)),
        calculation_note="\n".join(notes) or None)


def execute_related(plan, records, budget_reader=None):
    primary = plan.model_copy(update={"related": None})
    first = budget_reader(primary) if primary.operation == "budget" else execute(primary, records)
    categories = {r.category for r in first.get("evidence", []) if r.category} if primary.operation=="highest_category" else None
    child = plan.related
    if child.operation == "unsupported":
        en = {"offers": "I checked the supported part. Offer comparisons need the Compare offers page.",
            "prediction": "I checked the supported part. I cannot predict future spending or prices from these records.",
            "write": "I checked the supported part. This chat does not change purchases, budgets or reservations.",
            "causal": "I checked the supported part. Purchase records do not establish why prices or spending changed.",
            "quantity_ranking": "I checked the supported part. I cannot rank different products by incompatible quantities; ask for spending or purchase frequency instead.",
            "unrelated": "I checked the spending part. The other request is outside purchase and spending analysis.",
            "limit": "I checked the first part. Ask at most two related questions about one period; send the remaining questions separately."}
        de = {"offers": "Der unterstützte Teil wurde geprüft. Angebote vergleichen Sie auf der Angebotsseite.",
            "prediction": "Der unterstützte Teil wurde geprüft. Künftige Ausgaben oder Preise kann ich nicht vorhersagen.",
            "write": "Der unterstützte Teil wurde geprüft. Dieser Chat ändert keine Einkäufe, Budgets oder Reservierungen.",
            "causal": "Der unterstützte Teil wurde geprüft. Einkaufsdaten belegen nicht, warum sich Preise oder Ausgaben geändert haben.",
            "quantity_ranking": "Der unterstützte Teil wurde geprüft. Verschiedene Produkte mit unterschiedlichen Einheiten lassen sich nicht nach Menge rangieren; fragen Sie nach Ausgaben oder Kaufhäufigkeit.",
            "unrelated": "Der Ausgabenteil wurde geprüft. Die andere Anfrage liegt außerhalb der Einkaufsanalyse.",
            "limit": "Der erste Teil wurde geprüft. Stellen Sie höchstens zwei zusammenhängende Fragen zu einem Zeitraum; die übrigen bitte separat."}
        first["answer"] = heading(plan) + "\n" + first["answer"] + "\n\n" + (de if plan.language=="de" else en)[child.unsupported_reason]
        return first
    if child.scope == "clarify":
        second = dict(status="clarification_required", answer=scope_question(plan.language), evidence=[])
    else:
        subplan, selected = related_selection(plan, records, categories)
        second = budget_reader(subplan) if subplan.operation == "budget" else execute(subplan, selected)
        if child.scope == "highest_category" and records and not selected and categories:
            second["answer"] = ("Der Kategoriebetrag stammt aus Einkaufsanpassungen; dieser Kategorie sind keine Artikelzeilen zugeordnet."
                if plan.language=="de" else "The category total comes from purchase-level adjustments; no item lines were assigned to this category.")
    title = heading(plan, secondary=True)
    if child.scope == "highest_category":
        names = join_names([LABELS[plan.language][c] for c in sorted(categories)], plan.language=="de")
        title += ((" — höchste Kategorie(n): " if plan.language=="de" else " — highest category/categories: ") + names)
    return combine(first, second, (heading(plan), title))


def execute_reference(plan, reference, records):
    """Recalculate focus from current records, never from truncated evidence or client totals."""
    if reference.related.scope == "clarify" and plan.reference_scope in {"second_result", "ranked_items"}:
        return dict(status="clarification_required", answer=scope_question(plan.language), evidence=[], summaries=[], evidence_truncated=False)
    primary = reference.model_copy(update={"related": None, "reference_scope": None})
    if plan.reference_scope == "first_result":
        step, candidates = primary, records
    elif plan.reference_scope == "second_result":
        step, candidates = related_selection(reference, records)
    elif plan.reference_scope == "highest_category":
        step, candidates = (primary, records) if primary.operation == "highest_category" else related_selection(reference, records)
        if step.operation != "highest_category":
            return dict(status="clarification_required", answer=scope_question(plan.language, True, reference), evidence=[], summaries=[], evidence_truncated=False)
    else:
        if primary.operation == "items":
            step, candidates = primary, records
        else:
            step, candidates = related_selection(reference, records)
        if step.operation != "items" or reference.related.scope == "clarify":
            return dict(status="clarification_required", answer=scope_question(plan.language, True, reference), evidence=[], summaries=[], evidence_truncated=False)
    if step.operation in {"budget", "unsupported"}:
        return dict(status="clarification_required", answer=(
            "Dieses Ergebnis bezeichnet keine bestimmten Einkäufe. Nennen Sie bitte Kategorie, Artikel oder Händler."
            if plan.language=="de" else "That result does not identify particular purchases. Specify a category, item or store."), evidence=[], summaries=[], evidence_truncated=False)
    if step.operation == "highest_category":
        categories = categories_for(step, candidates)
        selected = select_category_records(candidates, categories, step.merchant_query)
        scope = "Einkäufe der höchsten Kategorie(n)" if plan.language=="de" else "Purchases in the highest category/categories"
    elif step.operation == "items":
        winners = rank_groups(item_groups(step, candidates), step)[:10]
        identities = {g["identity"] for g in winners}
        eligible = {i["id"] for _, i in filtered_lines(candidates, step) if item_identity(i) in identities}
        selected = [{**p, "items": [i for i in p["items"] if i["id"] in eligible]}
            for p in candidates if any(i["id"] in eligible for i in p["items"])]
        scope = "Einkäufe hinter den rangierten Artikeln" if plan.language=="de" else "Purchases behind the ranked items"
    elif step.operation == "merchants":
        result = execute(step, candidates)
        if result["evidence_truncated"]:
            return dict(status="clarification_required", answer=("Bitte nennen Sie einen Händler für die Einkaufsdetails." if plan.language=="de" else "Please name a store to narrow the purchase details."), evidence=[], summaries=[], evidence_truncated=False)
        ids = {str(r.purchase_id) for r in result["evidence"]}
        eligible = {i["id"] for _, i in filtered_lines(candidates, step)}
        selected = [{**p, "items": [i for i in p["items"] if i["id"] in eligible]}
            for p in candidates if str(p["id"]) in ids and any(i["id"] in eligible for i in p["items"])]
        scope = "Einkäufe der rangierten Händler" if plan.language=="de" else "Purchases at the ranked stores"
    else:
        eligible = {i["id"] for _, i in filtered_lines(candidates, step)}
        selected = [{**p, "items": [i for i in p["items"] if i["id"] in eligible]}
            for p in candidates if any(i["id"] in eligible for i in p["items"])]
        scope = "Einkäufe hinter dem gewählten Ergebnis" if plan.language=="de" else "Purchases behind the selected result"
    result = execute(plan.model_copy(update={"category": None, "product_query": None,
        "merchant_query": None, "quantity_unit": None}), selected)
    result["answer"] = scope + "\n" + result["answer"]
    return result


OVERALL = {"overall", "all items", "whole period", "across the whole period", "gesamter zeitraum", "alle artikel", "insgesamt"}
CATEGORY = {"highest category", "the category", "that category", "in that category", "within that category", "höchste kategorie", "diese kategorie", "in dieser kategorie"}
ITEMS = {"ranked items", "the ranked items", "top items", "those items", "rangierte artikel", "die rangierten artikel", "top artikel"}
FIRST = {"first result", "the first result", "first", "erstes ergebnis", "das erste ergebnis"}
SECOND = {"second result", "the second result", "second", "zweites ergebnis", "das zweite ergebnis"}
CATEGORY_RECORDS = {"show purchases in the highest category", "show purchases for that category", "zeige einkäufe der höchsten kategorie"}
ITEM_RECORDS = {"show purchases behind the ranked items", "show purchases for those items", "zeige einkäufe der rangierten artikel"}


def resolve_related(question, plan, memory, body, valid_plan):
    clean = re.sub(r"[?.!]+$", "", question.strip().casefold()).strip()
    clean = re.sub(r"^(?:please|bitte)\s+|\s+(?:please|bitte)$", "", clean).strip()
    source = memory.compound_plan if memory else None
    if source is None or source.related is None or not valid_plan(source):
        return plan
    page_changed = bool(memory.selected_period and (memory.selected_period.start_date != body.start_date or memory.selected_period.end_date != body.end_date))
    start, end = ((body.start_date, body.end_date) if page_changed else
        (memory.pending.start_date, memory.pending.end_date) if memory.pending and memory.pending.reason=="scope" and memory.pending.start_date and memory.pending.end_date else
        (memory.last_plan.start_date, memory.last_plan.end_date) if memory.last_plan else (source.start_date, source.end_date))
    if memory.pending and memory.pending.reason == "scope":
        if memory.pending.target in {"items", "records"} and source.related.scope == "clarify" and clean in OVERALL | CATEGORY:
            return source.model_copy(update={"start_date": start, "end_date": end,
                "related": source.related.model_copy(update={"scope": "period" if clean in OVERALL else "highest_category"})})
        if memory.pending.target == "records" and clean in CATEGORY | ITEMS | FIRST | SECOND:
            plan = source.model_copy(update={"operation": "records", "related": None,
                "reference_scope": ("highest_category" if clean in CATEGORY else "ranked_items" if clean in ITEMS else "first_result" if clean in FIRST else "second_result"),
                "detail": memory.pending.detail, "category": None, "product_query": None,
                "clarification_reason": "none", "clarification_target": None,
                "start_date": start, "end_date": end})
            return plan
    from app.ai.analysis_followups import MERCHANT, PURCHASE_DATE, RECORDS
    if clean in CATEGORY_RECORDS | ITEM_RECORDS:
        return source.model_copy(update={"operation": "records", "related": None,
            "reference_scope": "highest_category" if clean in CATEGORY_RECORDS else "ranked_items",
            "detail": None, "category": None, "product_query": None,
            "start_date": start, "end_date": end})
    deictic = (plan.operation == "records" and plan.reference_scope is None
        and not any((plan.category, plan.product_query, plan.merchant_query))
        and re.search(r"\b(those|these|that spending|diese|diesen|dazu)\b", clean))
    if clean in RECORDS | MERCHANT | PURCHASE_DATE or deictic:
        if source.related.operation == "unsupported":
            return plan.model_copy(update={"reference_scope": "first_result", "related": None})
        previous_scope = memory.last_plan.reference_scope if memory.last_plan else None
        if previous_scope:
            return plan.model_copy(update={"reference_scope": previous_scope, "related": None})
        if deictic:
            from app.ai.analysis_followups import MONTH_YEAR
            if (MONTH_YEAR.search(question) or re.search(r"\d{4}-\d{2}-\d{2}|\b(last|previous|this|letzten|vorigen|diesen)\s+(month|week|monat|woche)\b", question, re.I)) and plan.start_date and plan.end_date:
                start, end = plan.start_date, plan.end_date
        return plan.model_copy(update={"operation": "clarify", "related": None,
            "reference_scope": None, "clarification_reason": "scope", "clarification_target": "records",
            "start_date": start, "end_date": end})
    return plan
