"""Remove unnecessary long numeric identifiers without relaxing the PII detector."""
from app.core.privacy import RULES, redact_text


def omit_item_identifiers(result):
    items, changed = [], False
    for item in result.items:
        text = item.original_text
        _, findings = redact_text(text)
        # A SKU/EAN can resemble an identifier. Do not decide which it is: omit
        # the number entirely. Contact/payment/address/label rules still block.
        if set(findings) == {"long_identifier"}:
            clean = RULES["long_identifier"].sub("", text).strip(" -/:;,.")
            if clean and any(c.isalpha() for c in clean):
                item = item.model_copy(update={"original_text": clean})
                changed = True
        items.append(item)
    return result.model_copy(update={"items": items}), changed
