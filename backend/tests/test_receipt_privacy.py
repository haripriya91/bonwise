"""Receipt identifiers must not discard an otherwise usable extraction."""
import unittest
from types import SimpleNamespace as Obj
from unittest.mock import Mock, patch

from fastapi import HTTPException

from app.core.privacy import has_detectable_pii
from app.core.receipt_privacy import omit_item_identifiers
from app.schemas.extraction import ExtractedPurchase


def result(text="K Socken 7er-0540560", merchant="Lidl"):
    return ExtractedPurchase(document_type="receipt",merchant=merchant,purchased_on="2026-11-12",currency="EUR",
        items=[dict(original_text=text,quantity="1",quantity_unit="pack",unit_price_cents=359,line_total_cents=359,category="clothing")],
        adjustments=[],reported_total_cents=359)


class ReceiptPrivacyTests(unittest.TestCase):
    def test_visible_socks_code_removed_without_changing_amounts(self):
        original=result();clean,changed=omit_item_identifiers(original)
        self.assertTrue(changed)
        self.assertEqual(clean.items[0].original_text,"K Socken 7er")
        self.assertEqual(clean.items[0].line_total_cents,359)
        self.assertEqual(clean.reported_total_cents,359)
        self.assertFalse(has_detectable_pii(clean.model_dump(mode="json")))
        self.assertIn("0540560",original.items[0].original_text)

    def test_normal_product_and_package_size_unchanged(self):
        for text in ["White Tiger Garnelen", "Milk 1.5% 1000ml", "Bio potatoes 2kg"]:
            clean,changed=omit_item_identifiers(result(text))
            self.assertFalse(changed)
            self.assertEqual(clean.items[0].original_text,text)

    def test_only_identifier_without_product_name_still_blocked(self):
        clean,changed=omit_item_identifiers(result("0540560"))
        self.assertFalse(changed)
        self.assertTrue(has_detectable_pii(clean.model_dump(mode="json")))

    def test_contact_payment_membership_address_and_markers_still_blocked(self):
        for text in ["Milk alice@example.com", "Milk Tel 089 12345678", "Milk card 1234 5678 1234 5678",
                     "Kundennummer 1234567", "Ludwig-Dill-Straße 69", "85221 Dachau", "Milk [REMOVED]"]:
            with self.subTest(text=text):
                clean,_=omit_item_identifiers(result(text))
                self.assertTrue(has_detectable_pii(clean.model_dump(mode="json")))

    def test_merchant_address_is_not_whitelisted(self):
        clean,_=omit_item_identifiers(result(merchant="Lidl Ludwig-Dill-Straße 69"))
        self.assertTrue(has_detectable_pii(clean.model_dump(mode="json")))

    def test_global_detector_is_not_weakened(self):
        self.assertTrue(has_detectable_pii("K Socken 7er-0540560"))

    def image(self, extracted):
        from app.ai import image_extractor as image
        provider=Mock();provider.responses.parse.return_value=Obj(output_parsed=extracted,status="completed",usage=None)
        with patch.object(image,"prepare_image",return_value=b"offline-test-image"), \
             patch.object(image,"get_settings",return_value=Obj(vision_model="offline")), \
             patch.object(image,"model_client",return_value=provider), \
             patch.object(image,"write_trace",return_value=False):
            return image.extract_image("offline")

    def test_image_extraction_reaches_review_with_code_omitted(self):
        out=self.image(result())
        self.assertTrue(out["can_create_draft"])
        self.assertEqual(out["candidate"]["items"][0]["original_text"],"K Socken 7er")
        self.assertTrue(any("identifiers" in text for text in out["warnings"]))
        self.assertFalse(out["saved"])

    def test_image_extraction_still_rejects_contact_information(self):
        with self.assertRaises(HTTPException) as caught:self.image(result("Milk alice@example.com"))
        self.assertEqual(caught.exception.status_code,422)

    def test_text_model_output_uses_same_sanitized_review_flow(self):
        from app.ai import purchase_extractor as text
        provider=Mock();provider.responses.parse.return_value=Obj(output_parsed=result(),status="completed",usage=None)
        with patch.object(text,"get_settings",return_value=Obj(llm_model="offline")), \
             patch.object(text,"model_client",return_value=provider), \
             patch.object(text,"write_trace",return_value=False):
            out=text.extract_text("Lidl, socks 3.59 EUR, 12 November 2026")
        self.assertTrue(out["can_create_draft"])
        self.assertEqual(out["candidate"]["items"][0]["original_text"],"K Socken 7er")
