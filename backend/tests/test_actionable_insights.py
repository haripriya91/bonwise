"""Recommendations must add a supported decision, rather than duplicate dashboard tiles."""
import unittest
from datetime import date
from unittest.mock import patch

from fastapi import BackgroundTasks
from app.schemas.insights import InsightRequest, InsightSelection
from app.services.insights import actionable_facts, apply_selection, build_facts
from app.ai import spending_insights
from test_analysis_ranking import purchase


BODY=InsightRequest(start_date=date(2026,11,1),end_date=date(2026,11,12))
BUDGET=dict(start_date=date(2026,11,1),end_date=date(2026,11,30),amount_cents=60000,
            spent_cents=11000,reserved_cents=0,available_cents=49000,version=1)


def facts(rows,budget=BUDGET):
    return actionable_facts(build_facts(rows,dict(status="covered" if budget else "not_set",budget=budget),BODY),BODY)


def card_ids(result):
    return {c.id for c in result.get("eligible_cards",result["cards"])}


def selection(*pairs):
    return InsightSelection(selections=[dict(fact_id=a,suggestion_id=b) for a,b in pairs])


class ActionableInsightTests(unittest.TestCase):
    def test_healthy_single_grocery_receipt_does_not_duplicate_chart_or_budget(self):
        f=facts([purchase("A",[("Milk",100)])])
        self.assertEqual(f["cards"],[])
        self.assertEqual(f["candidates"],[])
        self.assertIn("No additional supported",f["notice"])

    def test_no_actionable_pattern_skips_model(self):
        f=facts([purchase("A",[("Milk",100)])])
        with patch.object(spending_insights,"load_facts",return_value=f),patch.object(spending_insights,"insights_model") as model:
            out=spending_insights.generate(None,None,None,BODY,BackgroundTasks())
        self.assertEqual((out.status,out.model_calls,out.cards),("ready",0,[]))
        model.assert_not_called()

    def test_clothing_supplies_a_planning_check_from_one_receipt(self):
        row=purchase("A",[("Socks",359)]);row["items"][0]["category"]="clothing"
        f=facts([row]);self.assertEqual(card_ids(f),{"planning_selection"})
        self.assertIn("Socks",f["cards"][0].detail)
        self.assertEqual(f["cards"][0].action,"planner")
        self.assertNotIn("Socks",str(f["candidates"]))

    def test_repeated_positive_purchases_offer_stock_check(self):
        first=purchase("A",[("Milk",100)]);second=purchase("B",[("Milk",150)])
        second["purchased_on"]=date(2026,11,10)
        f=facts([first,second]);self.assertIn("repeat_purchases",card_ids(f))
        self.assertIn("2 purchase records",f["cards"][0].detail)
        self.assertNotIn("wasted",f["cards"][0].detail)

    def test_duplicate_lines_and_refunds_are_not_repeat_buying(self):
        f=facts([purchase("A",[("Milk",100),("Milk",100)]),purchase("B",[("Milk",-100)])])
        self.assertNotIn("repeat_purchases",card_ids(f))

    def price_rows(self,old=100,new=150):
        a=purchase("A",[("Milk",old)]);b=purchase("B",[("Milk",new)])
        a["purchased_on"]=date(2026,11,2);b["purchased_on"]=date(2026,11,10)
        for row in [a,b]:
            row["items"][0]["product_id"]="confirmed-product"
            row["items"][0]["unit_price_cents"]=old if row is a else new
        return a,b

    def test_price_check_uses_exact_identity_unit_dates_and_absolute_change(self):
        f=facts(list(self.price_rows()))
        card=next(c for c in f["cards"] if c.id=="price_change")
        self.assertIn("0,50 € more per pack",card.detail)
        self.assertIn("02 Nov 2026",card.detail)
        self.assertEqual(card.action,"offers")

    def test_unlinked_or_incompatible_units_do_not_prove_price_change(self):
        for mode in ["unlinked","mixed_units"]:
            a,b=self.price_rows()
            if mode=="unlinked":a["items"][0]["product_id"]=None
            else:b["items"][0]["quantity_unit"]="l"
            self.assertNotIn("price_change",card_ids(facts([a,b])))

    def test_conflicting_prices_same_day_do_not_pick_arbitrary_baseline(self):
        a,b=self.price_rows();c=purchase("C",[("Milk",120)])
        c["purchased_on"]=a["purchased_on"];c["items"][0]["product_id"]="confirmed-product"
        self.assertNotIn("price_change",card_ids(facts([a,b,c])))

    def test_zero_baseline_uses_absolute_change_without_percentage(self):
        a,b=self.price_rows(0,150)
        card=next(c for c in facts([a,b])["cards"] if c.id=="price_change")
        self.assertNotIn("%",card.detail)
        self.assertIn("1,50 € more",card.detail)

    def test_only_exceptional_budget_status_gets_another_card(self):
        budget={**BUDGET,"spent_cents":59000,"reserved_cents":2000,"available_cents":-1000}
        f=facts([purchase("A",[("Milk",100)])],budget)
        card=f["cards"][0]
        self.assertEqual(card.id,"budget_pressure")
        self.assertIn("recorded spending itself has not exceeded",card.detail)
        self.assertIn("30.11.2026",card.detail)

    def test_missing_target_offers_action_without_inventing_an_amount(self):
        f=facts([purchase("A",[("Milk",100)])],None)
        self.assertEqual(card_ids(f),{"missing_target"})
        self.assertNotIn("€",f["cards"][0].detail)

    def test_model_can_return_one_finding_without_padding(self):
        f=facts(list(self.price_rows()))
        cards=apply_selection(f,selection(("price_change","compare")))
        self.assertEqual([c.id for c in cards],["price_change"])
        self.assertEqual(cards[0].detail,next(c.detail for c in f["cards"] if c.id=="price_change"))

    def test_legacy_dashboard_fact_cannot_be_selected_as_an_action(self):
        f=facts(list(self.price_rows()))
        with self.assertRaises(ValueError):apply_selection(f,selection(("category","review")))

    def test_verified_budget_pressure_cannot_be_dropped(self):
        budget={**BUDGET,"available_cents":-1000,"reserved_cents":50000}
        f=facts(list(self.price_rows()),budget)
        with self.assertRaises(ValueError):apply_selection(f,selection(("price_change","compare")))

    def test_fallback_is_bounded_when_many_decisions_are_supported(self):
        a,b=self.price_rows();c=purchase("C",[("Socks",300)])
        c["items"][0]["category"]="clothing"
        f=facts([a,b,c],None)
        self.assertGreater(len(f["candidates"]),3)
        self.assertLessEqual(len(f["cards"]),3)
