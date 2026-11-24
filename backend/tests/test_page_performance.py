"""Query counts, independent ledger reads and bounded opt-in connection pools."""
import unittest
from unittest.mock import patch
from sqlalchemy import event
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool, QueuePool
from fastapi import HTTPException
from pydantic import SecretStr
import test_planner as fixture
from app.services import purchases, shopping_overview
from app.db.session import make_db_engine


class PagePerformanceTests(unittest.TestCase):
    setUp = fixture.PlannerTests.setUp
    tearDown = fixture.PlannerTests.tearDown
    purchase = fixture.PlannerTests.purchase

    def test_page_query_count_and_detail_equivalence(self):
        with Session(self.engine) as db, db.begin():
            self.purchase(db, self.day, [(self.milk, 'Milk', '2', 100)])
            self.purchase(db, self.day, [(self.milk, 'Foreign', '1', 999)], self.other_household)
        queries = []
        def count(conn, cursor, statement, params, context, many):
            if statement.lstrip().upper().startswith('SELECT'):
                queries.append(1)
        event.listen(self.engine, 'before_cursor_execute', count)
        try:
            with Session(self.engine) as db:
                result = purchases.list_purchases(db, self.household, self.user, 5, 0)
            self.assertEqual(len(queries), 4)
        finally:
            event.remove(self.engine, 'before_cursor_execute', count)
        self.assertEqual(result['total'], 5)
        self.assertEqual(len(result['purchases']), 5)
        for row in result['purchases']:
            self.assertEqual(row['household_id'], self.household)
            with Session(self.engine) as db:
                from app.db.models import Purchase
                self.assertEqual(row, purchases.purchase_view(db, db.get(Purchase, row['id'])))

    def test_empty_page_and_foreign_household(self):
        with Session(self.engine) as db:
            empty = purchases.list_purchases(db, self.household, self.user, 5, 100)
        self.assertEqual(empty['purchases'], [])
        self.assertEqual(empty['total'], 4)
        with Session(self.engine) as db, self.assertRaises(HTTPException) as caught:
            purchases.list_purchases(db, self.household, self.other_user, 5, 0)
        self.assertEqual(caught.exception.status_code, 404)

    def test_ledger_skips_offers_and_keeps_figures(self):
        with patch.object(shopping_overview.offers, 'recommendations_locked', return_value=None) as offers:
            with Session(self.engine) as db:
                combined = shopping_overview.overview(db, self.household, self.user)
            offers.assert_called_once()
            offers.reset_mock()
            with Session(self.engine) as db:
                ledger = shopping_overview.overview(db, self.household, self.user, include_offers=False)
            offers.assert_not_called()
        self.assertEqual(ledger['offers_status'], 'not_requested')
        self.assertIsNone(ledger['offers'])
        for key in ('budget', 'confirmed_spent_cents', 'purchase_count', 'spending_start', 'spending_end'):
            self.assertEqual(ledger[key], combined[key])

    def test_offer_failure_keeps_ledger(self):
        with patch.object(shopping_overview.offers, 'recommendations_locked', side_effect=HTTPException(503, 'unavailable')):
            with Session(self.engine) as db:
                result = shopping_overview.overview(db, self.household, self.user)
        self.assertEqual(result['offers_status'], 'unavailable')
        self.assertGreater(result['confirmed_spent_cents'], 0)

    def test_ledger_authorization_still_required(self):
        with patch.object(shopping_overview.offers, 'recommendations_locked') as offers:
            with Session(self.engine) as db, self.assertRaises(HTTPException):
                shopping_overview.overview(db, self.household, self.other_user, include_offers=False)
            offers.assert_not_called()


class PoolConfigurationTests(unittest.TestCase):
    url = SecretStr('postgresql://offline:offline@localhost/offline')

    def test_zero_keeps_nullpool(self):
        engine = make_db_engine(self.url, pool_size=0)
        self.assertIsInstance(engine.pool, NullPool)
        engine.dispose()

    def test_pool_is_bounded(self):
        engine = make_db_engine(self.url, pool_size=2)
        self.assertIsInstance(engine.pool, QueuePool)
        self.assertEqual(engine.pool.size(), 2)
        self.assertEqual(engine.pool._max_overflow, 0)
        self.assertTrue(engine.pool._pre_ping)
        engine.dispose()

    def test_invalid_pool_values(self):
        for value in (-1, 6, True, '2'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                make_db_engine(self.url, pool_size=value)


class OfferReadPerformanceTests(unittest.TestCase):
    """Real SQL fixtures: bounded reads, household isolation and unchanged price rules."""
    tearDown = fixture.PlannerTests.tearDown
    purchase = fixture.PlannerTests.purchase

    def setUp(self):
        from pathlib import Path
        from decimal import Decimal
        from sqlalchemy import insert
        from app.db.models import Product
        from app.services import offer_data
        fixture.PlannerTests.setUp(self)
        retail_path = str(Path(self.temp.name) / 'retail.db')
        @event.listens_for(self.engine, 'connect')
        def attach_retail(connection, record):
            connection.execute('ATTACH DATABASE ? AS bonwise_retail_demo', (retail_path,))
        self.engine.dispose()
        offer_data.metadata.create_all(self.engine)
        with Session(self.engine) as db, db.begin():
            for pid in [self.milk, self.bread]:
                product = db.get(Product, pid)
                product.brand = 'Demo'
                product.attributes = dict(catalogue='retail-demo-catalogue-v1', family='milk' if pid == self.milk else 'bread', variant_code='plain', organic=False)
            db.execute(insert(offer_data.stores), [dict(store_id='unused', chain='Demo', store_name='Demo')])
            self.row = dict(offer_id='milk-price', batch_id='batch', chain='Demo', product_name='Milk', brand='Demo', variant='plain', pack_unit='l', price_basis='per_pack', store_scope='national', country_code='DE', national_scope_basis='synthetic_national_fixture_v1', source='demo', is_organic=False, quantity_is_approximate=False, membership_required=False, pack_quantity=Decimal('1'), price_eur=Decimal('1.20'), deposit_eur_per_pack=Decimal('0'), valid_from=self.day, valid_until=self.day, multi_buy=None, store_ids=[])
            db.execute(insert(offer_data.offers), [self.row])
            db.execute(insert(offer_data.links), [dict(batch_id='batch', offer_id='milk-price', product_id=self.milk)])

    def read(self, function, *args):
        queries = []
        def count(conn, cursor, statement, params, context, many):
            if statement.lstrip().upper().startswith('SELECT'): queries.append(statement)
        event.listen(self.engine, 'before_cursor_execute', count)
        try:
            with Session(self.engine) as db: result = function(db, *args)
        finally: event.remove(self.engine, 'before_cursor_execute', count)
        return result, queries

    def test_options_four_reads_and_same_picker_counts(self):
        from app.services import offers
        result, queries = self.read(offers.options, self.household, self.user)
        self.assertEqual(len(queries), 4)
        milk = next(p for p in result['products'] if p['id'] == self.milk)
        self.assertEqual(milk['current_offer_count'], 1)
        self.assertEqual(milk['purchase_count'], 4)
        self.assertFalse(any('stores' in q for q in queries))
        with Session(self.engine) as db:
            self.assertEqual(result['products'], offers.product_options(offers.products(db), offers.offer_rows(db, self.day), offers.history(db, self.household, self.day)))

    def test_home_four_reads_and_only_selected_offer_rows(self):
        from app.services import offers
        result, queries = self.read(offers.recommendations, self.household, self.user)
        self.assertEqual(len(queries), 4)
        self.assertLessEqual(len(result['recommendations']), 3)
        milk = next(r for r in result['recommendations'] if r['product']['id'] == self.milk)
        self.assertEqual(milk['comparison']['offers'][0]['advertised_price_cents'], 120)
        self.assertEqual(milk['product']['current_offer_count'], 1)
        self.assertIn('product_id IN', next(q for q in queries if 'published_approved_offers' in q))

    def test_new_household_skips_catalogue_and_retail_queries(self):
        from app.services import offers
        result, queries = self.read(offers.recommendations, self.other_household, self.other_user)
        self.assertEqual(result['recommendations'], [])
        self.assertEqual(len(queries), 2)
        self.assertFalse(any('bonwise_retail_demo' in q for q in queries))

    def test_compare_three_reads_and_no_history_lookup(self):
        from app.services import offers
        from app.schemas.commerce import CompareInput
        result, queries = self.read(offers.compare, self.household, self.user, CompareInput(product_ids=[self.milk], include_alternatives=False))
        self.assertEqual(len(queries), 3)
        self.assertEqual(result['results'][0]['offers'][0]['unit_price_eur'], '1.2000')
        self.assertFalse(any('purchase_items' in q for q in queries))

    def test_all_three_endpoints_reject_foreign_household_first(self):
        from app.services import offers
        from app.schemas.commerce import CompareInput
        for function, args in [(offers.options, ()), (offers.recommendations, ()), (offers.compare, (CompareInput(product_ids=[self.milk]),))]:
            with self.subTest(function=function.__name__), self.assertRaises(HTTPException) as caught:
                self.read(function, self.household, self.other_user, *args)
            self.assertEqual(caught.exception.status_code, 404)

    def test_expired_local_and_membership_prices_keep_their_rules(self):
        from sqlalchemy import insert
        from datetime import timedelta
        from app.services import offers, offer_data
        from app.schemas.commerce import CompareInput
        rows = [dict(self.row, offer_id='expired', valid_from=self.day-timedelta(days=2), valid_until=self.day-timedelta(days=1), price_eur='0.01'), dict(self.row, offer_id='local', store_scope='local', price_eur='0.01'), dict(self.row, offer_id='member', membership_required=True, price_eur='0.50')]
        with Session(self.engine) as db, db.begin():
            db.execute(insert(offer_data.offers), rows)
            db.execute(insert(offer_data.links), [dict(batch_id='batch', offer_id=r['offer_id'], product_id=self.milk) for r in rows])
        result, _ = self.read(offers.compare, self.household, self.user, CompareInput(product_ids=[self.milk]))
        cards = result['results'][0]['offers']
        self.assertEqual({r['offer_id'] for r in cards}, {'milk-price', 'member'})
        self.assertTrue(next(r for r in cards if r['offer_id']=='milk-price')['is_best_unit_price'])
        self.assertFalse(next(r for r in cards if r['offer_id']=='member')['is_best_unit_price'])

    def test_alternative_matches_and_incompatible_products_stay_filtered(self):
        from sqlalchemy import insert
        from decimal import Decimal
        from uuid import uuid4
        from app.db.models import Product
        from app.services import offers, offer_data
        from app.schemas.commerce import CompareInput
        alt = uuid4()
        with Session(self.engine) as db, db.begin():
            db.add(Product(id=alt, canonical_name='Other milk', category='groceries', brand='Other', base_unit='l', pack_base_quantity=Decimal('1'), attributes=dict(catalogue='retail-demo-catalogue-v1', family='milk', variant_code='plain', organic=False)))
            db.flush()
            db.execute(insert(offer_data.offers), [dict(self.row, offer_id='alternative', price_eur='1.00')])
            db.execute(insert(offer_data.links), [dict(batch_id='batch', offer_id='alternative', product_id=alt)])
        enabled, _ = self.read(offers.compare, self.household, self.user, CompareInput(product_ids=[self.milk], include_alternatives=True))
        disabled, _ = self.read(offers.compare, self.household, self.user, CompareInput(product_ids=[self.milk], include_alternatives=False))
        self.assertEqual(len(enabled['results'][0]['offers']), 2)
        self.assertEqual(len(disabled['results'][0]['offers']), 1)

    def test_malformed_selected_bundle_still_fails_closed(self):
        from sqlalchemy import update
        from app.services import offers, offer_data
        from app.schemas.commerce import CompareInput
        with Session(self.engine) as db, db.begin():
            db.execute(update(offer_data.offers).values(price_basis='bundle_total', multi_buy={'required_packs': 2, 'bundle_price_eur': '0.01'}))
        with self.assertRaises(HTTPException) as caught:
            self.read(offers.compare, self.household, self.user, CompareInput(product_ids=[self.milk]))
        self.assertEqual(caught.exception.status_code, 500)
