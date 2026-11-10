"""Deterministic ranking checks: totals, ties, frequency, evidence and scope."""
import unittest
from datetime import date
from uuid import uuid4
from app.schemas.analysis import AnalysisPlan
from app.ai.analysis_logic import execute


def plan(**overrides):
    values = dict(operation="items", language="en", start_date=date(2026,11,1),
        end_date=date(2026,11,10), category=None, product_query=None, merchant_query=None,
        metric="spending", top_n=1, comparison_start_date=None, comparison_end_date=None,
        clarification_reason="none", sort_direction="lowest")
    return AnalysisPlan(**{**values, **overrides})


def purchase(merchant, lines):
    items = [dict(id=str(uuid4()), product_id=None, description=name, quantity="1",
        quantity_unit="pack", unit_price_cents=cents, line_total_cents=cents,
        category="groceries") for name, cents in lines]
    return dict(id=str(uuid4()), merchant=merchant, purchased_on=date(2026,11,5),
        total_cents=sum(i["line_total_cents"] for i in items), adjustments=[], items=items)


class RankingTests(unittest.TestCase):
    def test_lowest_uses_aggregate_not_cheapest_single_line(self):
        rows = [purchase("A", [("Milk", 100),("Rice",150)]), purchase("A",[("Milk",100)])]
        result = execute(plan(), rows)
        self.assertIn("spent the least on Rice",result["answer"])
        self.assertEqual([r.item_description for r in result["evidence"]],["Rice"])
        self.assertEqual(result["evidence"][0].total_cents,150)

    def test_lowest_preserves_all_ties_and_top_n(self):
        rows=[purchase("A",[("Milk",100),("Rice",100),("Bread",250),("Coffee",500)])]
        result=execute(plan(top_n=1),rows)
        self.assertIn("lowest spending",result["answer"])
        self.assertEqual({r.item_description for r in result["evidence"]},{"Milk","Rice"})
        result=execute(plan(top_n=3),rows)
        self.assertEqual({r.item_description for r in result["evidence"]},{"Milk","Rice","Bread"})

    def test_default_highest_unchanged(self):
        result=execute(plan(sort_direction="highest"),[purchase("A",[("Milk",100),("Rice",200)])])
        self.assertIn("spent the most on Rice",result["answer"])

    def test_lowest_frequency_counts_trips_not_quantity(self):
        result=execute(plan(metric="purchase_frequency"),[
            purchase("A",[("Milk",100),("Rice",200)]),purchase("A",[("Milk",100)])])
        self.assertIn("Rice least often",result["answer"])

    def test_lowest_merchant_and_language(self):
        result=execute(plan(operation="merchants",language="de"),[
            purchase("A",[("Milk",100)]),purchase("B",[("Rice",200)])])
        self.assertIn("Am wenigsten",result["answer"])
        self.assertEqual({r.merchant for r in result["evidence"]},{"A"})

    def test_empty_history_does_not_invent_answer(self):
        self.assertEqual(execute(plan(),[])["status"],"no_data")
