"""Conservative rules; mandatory human review is still required."""

import re
from collections import Counter


LINE = re.compile(
    r"(?i)\b(customer|kunde|kundin|kundenname|name|recipient|empfänger|"
    r"rechnung\s+an|lieferung\s+an|"
    r"bill(?:ing)?\s+to|ship(?:ping)?\s+to|rechnungsadresse|lieferadresse|"
    r"anschrift|address|adresse|cashier|kassierer(?:in)?|"
    r"bedient\s+von|served\s+by|contact|kontakt|"
    r"telefon|telephone|phone|mobil|e-?mail|"
    r"card|karte|kartennummer|visa|mastercard|iban|bic|konto|account|"
    r"membership|member\s*id|mitglied(?:snummer|s-?id)?|"
    r"loyalty|payback|deutschlandcard|kunden[\s-]*nr|kundennummer|"
    r"bestellnummer|bestell-?nr|order\s*(?:id|number|no)|"
    r"invoice\s*(?:id|number|no)|rechnungsnummer|rechnungs-?nr)\b"
)

BLOCK = re.compile(
    r"(?i)\b(bill(?:ing)?\s+to|ship(?:ping)?\s+to|recipient|empfänger|"
    r"rechnung\s+an|lieferung\s+an|"
    r"rechnungsadresse|lieferadresse|anschrift|address|adresse)\b"
)

RULES = {
    "email": re.compile(
        r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b"
    ),
    "url": re.compile(r"(?i)\b(?:https?://|www\.)\S+"),
    "iban": re.compile(
        r"(?i)\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]){11,30}\b"
    ),
    "phone": re.compile(
        r"(?<!\w)(?:\+\d{1,3}[ .()/\-]*|0\d{2,5}[ .()/\-]+)"
        r"\d(?:[ .()/\-]*\d){5,13}(?!\w)"
    ),
    "long_identifier": re.compile(r"(?<!\w)\d{7,}(?!\w)"),
    "card_number": re.compile(
        r"(?<!\w)(?:\d[ -]?){12,18}\d(?!\w)"
    ),
    "masked_card": re.compile(
        r"(?i)(?:[x*•][ -]?){3,}\d{2,6}\b"
    ),
    "street": re.compile(
        r"(?i)\b[\wäöüß.-]+"
        r"(?:straße|strasse|str\.|weg|street|road)\s+\d+\w?\b"
    ),
    "postcode_city": re.compile(
        r"\b\d{5}\s+[A-ZÄÖÜ][\wäöüß-]+\b"
    ),
    "person_title": re.compile(
        r"(?i)\b(?:Herr|Frau|Mr\.?|Mrs\.?|Ms\.?)\s+[^\n,;]+"
    ),
}


def redact_text(text):
    counts = Counter()
    output = []
    address_block = False

    for line in text.splitlines():
        if not line.strip():
            address_block = False
            output.append("")
            continue

        if address_block or LINE.search(line):
            counts["sensitive_line"] += 1
            address_block = address_block or bool(BLOCK.search(line))
            continue

        for kind, pattern in RULES.items():
            line, count = pattern.subn("[REMOVED]", line)
            counts[kind] += count

        output.append(line)

    return (
        "\n".join(output).strip(),
        {kind: count for kind, count in counts.items() if count},
    )


def has_detectable_pii(value):
    if isinstance(value, str):
        return bool(redact_text(value)[1]) or "[REMOVED]" in value

    if isinstance(value, dict):
        return any(has_detectable_pii(v) for v in value.values())

    if isinstance(value, list):
        return any(has_detectable_pii(v) for v in value)

    return False