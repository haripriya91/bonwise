"""Offline correctness tests with SQL and simulated providers, never real receipts."""
import json
import tempfile
import unittest
from contextlib import ExitStack
from datetime import date
from types import SimpleNamespace as Obj
from unittest.mock import patch
from uuid import uuid4
from pydantic import SecretStr
from sqlalchemy import create_engine,event,insert,select,func,delete
from sqlalchemy.orm import Session
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles

SETTINGS=Obj(database_url=SecretStr('postgresql://offline:offline@localhost/offline'),llm_model='offline-model',embedding_model='offline-embedding',embedding_dimensions=3,correction_rag_enabled=True,langsmith_tracing=False)
with patch('app.core.config.get_settings',return_value=SETTINGS):
    from app.db.base import Base
    from app.db.models import Household,HouseholdMember,Product,Purchase,PurchaseDraft,PurchaseItem
    from app.db.corrections import ProductCorrection
    from app.services import correction_memory as memory,purchases
    from app.ai import correction_retrieval as retrieval,product_matching as matching
    from app.ai.analysis_tracing import AnalysisTrace
    from app.schemas.product_matching import ProductSuggestionInput
    from app.schemas.purchase import ConfirmDraft

@compiles(JSONB,'sqlite')
def sqlite_json(element,compiler,**kw):return 'JSON'

class CorrectionRagTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.engine=create_engine('sqlite:///'+self.temp.name+'/test.db')
        @event.listens_for(self.engine,'connect')
        def attach(connection,_):connection.execute('ATTACH DATABASE ? AS auth',(self.temp.name+'/auth.db',))
        Base.metadata.create_all(self.engine)
        self.hh,self.other,self.user,self.product,self.lactose=[uuid4() for _ in range(5)]
        self.stack=ExitStack()
        for mod in (memory,retrieval,matching):self.stack.enter_context(patch.object(mod,'get_settings',return_value=SETTINGS))
        self.stack.enter_context(patch('app.ai.analysis_tracing.get_settings',return_value=SETTINGS))
        self.trace=AnalysisTrace('offline','offline-model',root_name='product_suggestions')
        with Session(self.engine) as db,db.begin():
            db.execute(insert(Base.metadata.tables['auth.users']),{'id':self.user})
            db.add_all([Household(id=self.hh,name='Synthetic A'),Household(id=self.other,name='Synthetic B')]);db.flush()
            db.add(HouseholdMember(household_id=self.hh,user_id=self.user,role='owner'))
            for pid,code,variant in [(self.product,'whole','whole'),(self.lactose,'lactose','lactose_free')]:
                db.add(Product(id=pid,canonical_name='Whole milk 1L' if variant=='whole' else 'Lactose-free milk 1L',name_de='Vollmilch',name_en='Whole milk 1L',category='groceries',base_unit='l',pack_base_quantity=1,
                    attributes=dict(catalogue='receipt-catalogue-v1',code=code,family='milk',variant_code=variant,variant=variant,source='receipt_review')))
        self.index={};self.embeds=[]
        def embedding(**kw):self.embeds.append(kw);return Obj(data=[Obj(index=0,embedding=[1.,0.,0.])],usage=None)
        self.stack.enter_context(patch.object(retrieval,'embedding_client',return_value=Obj(embeddings=Obj(create=embedding))))
        def upsert(**kw):
            for row in kw['vectors']:self.index[(kw['namespace'],row['id'])]=row
            return Obj(upserted_count=len(kw['vectors']))
        def query(**kw):return Obj(matches=[Obj(id=vid,metadata=row['metadata'],score=.91) for (ns,vid),row in self.index.items() if ns==kw['namespace'] and all(row['metadata'].get(k)==v['$eq'] for k,v in kw['filter'].items())][:5])
        self.fake_index=Obj(upsert=upsert,query=query)
        self.stack.enter_context(patch.object(retrieval,'vector_index',return_value=self.fake_index))

    def tearDown(self):self.stack.close();self.engine.dispose();self.temp.cleanup()

    def example(self,wording='VOLLMLCH 3,5% 1L',product=None,household=None,source='image',merchant='Demo market',status='confirmed'):
        with Session(self.engine) as db,db.begin():
            draft=PurchaseDraft(household_id=household or self.hh,created_by=self.user,source_type=source,status=status,data={});db.add(draft);db.flush()
            purchase=Purchase(household_id=household or self.hh,draft_id=draft.id,merchant=merchant,purchased_on=date.today(),total_cents=100,adjustments=[]);db.add(purchase);db.flush()
            item=PurchaseItem(purchase_id=purchase.id,product_id=product or self.product,original_text=wording,category='groceries',quantity=1,quantity_unit='pack',unit_price_cents=100,line_total_cents=100);db.add(item);db.flush()
            memory.enqueue_purchase(db,household or self.hh,purchase.id)
            return purchase.id,item.id

    def index_pending(self):return memory.process_pending(self.engine,self.hh,self.user,limit=10)
    def retrieve(self,household=None,merchant='Demo market',unit='pack'):
        with Session(self.engine) as db:return retrieval.retrieve_examples(db,household or self.hh,'Vollm. 3.5 1 Liter','groceries',unit,merchant,self.trace)

    def facts(self):return matching.ProductFacts(type_compatible_codes=['whole'],family={'value':'milk','quote':'Vollm'},brand={'value':None,'quote':None},variant_code={'value':None,'quote':None},organic={'value':None,'quote':None})

    def test_queue_idempotent_and_contains_no_wording(self):
        purchase,_=self.example()
        with Session(self.engine) as db,db.begin():
            self.assertEqual(memory.enqueue_purchase(db,self.hh,purchase),0);self.assertEqual(db.scalar(select(func.count()).select_from(ProductCorrection)),1)
        self.assertNotIn('wording',ProductCorrection.__table__.columns)

    def test_only_confirmed_non_seed_links_queued(self):
        self.example(source='seed');self.example(status='pending')
        with Session(self.engine) as db:self.assertEqual(db.scalar(select(func.count()).select_from(ProductCorrection)),0)
        self.assertEqual(self.embeds,[])

    def test_private_wording_never_embedded(self):
        self.example(wording='Milk test@example.com');self.index_pending();self.assertEqual(self.embeds,[])

    def test_namespace_and_minimal_metadata(self):
        self.example();self.assertEqual(self.index_pending()['indexed'],1)
        examples,status=self.retrieve();self.assertEqual(status,'retrieved');self.assertEqual(len(examples),1)
        self.assertEqual(self.retrieve(household=self.other)[0],[])
        self.assertNotEqual(memory.namespace(self.hh),memory.namespace(self.other))
        for row in self.index.values():
            self.assertEqual(set(row['metadata']),{'kind','fingerprint','category','quantity_unit','merchant_key'});self.assertNotIn('Demo market',json.dumps(row))

    def test_unit_and_merchant_scope(self):
        self.example();self.index_pending();self.assertEqual(self.retrieve(merchant='Other market')[0],[]);self.assertEqual(self.retrieve(unit='kg')[0],[])

    def test_wrong_namespace_results_rejected_by_sql(self):
        self.example();self.index_pending()
        self.example(household=self.other)
        with Session(self.engine) as db,db.begin():
            row=db.scalar(select(ProductCorrection).where(ProductCorrection.household_id==self.other));row.status='indexed';row.indexed_fingerprint=row.fingerprint
        matches=[Obj(id=r['id'],metadata=r['metadata'],score=.95) for r in self.index.values()]
        with patch.object(self.fake_index,'query',return_value=Obj(matches=matches)):self.assertEqual(self.retrieve(household=self.other)[0],[])

    def test_changed_and_deleted_source_cannot_reuse_vectors(self):
        _,item=self.example();self.index_pending()
        with Session(self.engine) as db,db.begin():db.get(PurchaseItem,item).product_id=self.lactose
        self.assertEqual(self.retrieve()[0],[])
        with Session(self.engine) as db,db.begin():db.execute(delete(PurchaseItem).where(PurchaseItem.id==item))
        self.assertEqual(self.retrieve()[0],[])

    def test_conflicting_confirmations_require_review(self):
        self.example();self.index_pending();self.example(product=self.lactose)
        examples,status=self.retrieve();self.assertEqual(examples,[]);self.assertEqual(status,'conflict')

    def test_provider_failure_preserves_exact_memory(self):
        self.example();self.index_pending()
        with patch.object(retrieval,'vector_index',side_effect=RuntimeError('secret')):self.assertEqual(self.retrieve()[1],'unavailable')
        with Session(self.engine) as db:status,previous=matching.previous_choice(db,self.hh,'VOLLMLCH 3,5% 1L','groceries','pack','Demo market')
        self.assertEqual(status,'reused');self.assertEqual(previous['product_id'],self.product)

    def test_failed_job_retryable_without_loop(self):
        self.example()
        with patch.object(retrieval,'upsert_example',side_effect=RuntimeError('secret')) as provider:self.assertEqual(self.index_pending()['failed'],1);self.assertEqual(provider.call_count,1)
        with Session(self.engine) as db:
            row=db.scalar(select(ProductCorrection));self.assertEqual(row.status,'failed');self.assertEqual(row.attempts,1);self.assertEqual(row.last_error_code,'index_provider_unavailable')
        self.assertEqual(self.index_pending()['indexed'],1)

    def test_worker_requires_membership(self):
        self.example();self.assertEqual(memory.process_pending(self.engine,self.other,self.user)['indexed'],0);self.assertEqual(self.embeds,[])

    def test_model_receives_examples_not_prices_or_household_id(self):
        self.example();self.index_pending();examples,_=self.retrieve()
        with patch.object(matching,'client') as client:
            client.return_value.responses.parse.return_value=Obj(status='completed',usage=None,output_parsed=self.facts())
            matching.interpret('Vollm 1 Liter',[examples[0]['product']],self.trace,accepted_examples=[{'wording':examples[0]['wording'],'accepted_product_code':'whole'}])
            payload=json.loads(client.return_value.responses.parse.call_args.kwargs['input'])
        self.assertEqual(payload['accepted_examples'][0]['accepted_product_code'],'whole');self.assertNotIn('unit_price',json.dumps(payload));self.assertNotIn(str(self.hh),json.dumps(payload))

    def test_rag_recovers_product_missing_from_catalogue_top_k(self):
        self.example();self.index_pending()
        with patch.object(matching,'retrieve_candidates',return_value={'candidates':[]}),patch.object(matching,'interpret',return_value=self.facts()) as llm:
            with Session(self.engine) as db:result=matching.suggest_products(db,self.hh,self.user,ProductSuggestionInput(original_text='Vollm 3.5 1 Liter',merchant='Demo market',category='groceries'))
        self.assertEqual(result.memory_status,'retrieved');self.assertEqual(result.candidates[0].product_id,self.product);self.assertEqual(result.candidates[0].match_origin,'correction_rag');self.assertTrue(llm.call_args.kwargs['accepted_examples'])

    def test_lactose_free_rejects_ordinary_milk_even_if_model_omits_variant(self):
        self.example();self.index_pending();examples,_=self.retrieve();facts=self.facts();facts.family.quote='milk'
        result=matching.check_candidates('lactose-free milk 1L','groceries',facts,[examples[0]['product']]);self.assertEqual(result.status,'no_match')

    def test_confirmation_saves_queue_without_provider_calls(self):
        content=dict(merchant='Demo market',purchased_on=date.today().isoformat(),currency='EUR',items=[dict(product_id=str(self.product),original_text='Vollmilch 1L',quantity='1',quantity_unit='pack',unit_price_cents=100,category='groceries')],adjustments=[],reported_total_cents=100)
        with Session(self.engine) as db,db.begin():
            draft=PurchaseDraft(household_id=self.hh,created_by=self.user,source_type='text',status='pending',data={'content':content});db.add(draft);db.flush();draft_id=draft.id;revision=purchases.draft_revision(draft)
        with Session(self.engine) as db:result=purchases.confirm_draft(db,self.hh,self.user,draft_id,ConfirmDraft(revision=revision))
        with Session(self.engine) as db:self.assertEqual(db.get(PurchaseDraft,draft_id).status,'confirmed');self.assertEqual(db.scalar(select(func.count()).select_from(ProductCorrection)),1)
        self.assertEqual(self.embeds,[]);self.assertEqual(result['household_id'],self.hh)

    def test_traces_content_free(self):
        self.example();self.index_pending();self.retrieve();self.trace.finish();payload=json.dumps(self.trace.payload,default=str)
        for value in ['VOLLMLCH','Demo market',str(self.hh),str(self.product)]:self.assertNotIn(value,payload)
        self.assertIn('retrieve_correction_examples',payload)

    def test_exact_memory_does_not_call_semantic_retrieval(self):
        self.example()
        with patch.object(matching,'retrieve_examples') as vectors,patch.object(matching,'interpret') as llm:
            with Session(self.engine) as db:result=matching.suggest_products(db,self.hh,self.user,ProductSuggestionInput(original_text='VOLLMLCH 3,5% 1L',merchant='Demo market',category='groceries'))
        vectors.assert_not_called();llm.assert_not_called()
        self.assertEqual(result.candidates[0].match_origin,'exact_memory')

    def test_conflict_forces_review_even_if_catalogue_result_supported(self):
        self.example();self.index_pending();example=self.retrieve()[0][0]
        self.example(product=self.lactose)
        facts=self.facts();facts.family.quote='VOLLMLCH';facts.variant_code=matching.TextFact(value='whole',quote='VOLLMLCH')
        with patch.object(matching,'retrieve_candidates',return_value={'candidates':[example['product']]}),patch.object(matching,'interpret',return_value=facts):
            with Session(self.engine) as db:result=matching.suggest_products(db,self.hh,self.user,ProductSuggestionInput(original_text='VOLLMLCH 3,5% 1L',merchant='Demo market',category='groceries'))
        self.assertEqual(result.memory_status,'conflict');self.assertEqual(result.status,'review_required');self.assertIsNone(result.suggested_product_id)

    def test_explicit_package_size_and_unit_mismatch_rejected(self):
        self.example();self.index_pending();example=self.retrieve()[0][0]
        facts=self.facts()
        self.assertEqual(matching.check_candidates('Vollm 2 Liter','groceries',facts,[example['product']]).status,'no_match')
        self.assertEqual(matching.check_candidates('Vollm','groceries',facts,[example['product']],quantity_unit='kg').status,'no_match')

    def test_disabled_semantic_route_makes_no_vector_calls(self):
        disabled=Obj(**{**vars(SETTINGS),'correction_rag_enabled':False})
        with patch.object(retrieval,'get_settings',return_value=disabled):self.assertEqual(self.retrieve()[1],'disabled')
        self.assertEqual(self.embeds,[])

    def test_retry_limit_stops_exhausted_job(self):
        self.example()
        with Session(self.engine) as db,db.begin():db.scalar(select(ProductCorrection)).attempts=5
        self.assertEqual(self.index_pending()['indexed'],0);self.assertEqual(self.embeds,[])

    def test_reclaimed_lease_prevents_stale_worker_overwrite(self):
        self.example()
        def supersede(*args):
            with Session(self.engine) as db,db.begin():
                row=db.scalar(select(ProductCorrection));row.lease_until=None;row.fingerprint='f'*64
        with patch.object(retrieval,'upsert_example',side_effect=supersede):self.index_pending()
        with Session(self.engine) as db:self.assertNotEqual(db.scalar(select(ProductCorrection)).status,'indexed')

    def test_confirmation_route_starts_indexing_after_commit(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.api.purchases import router
        from app.core.auth import get_current_user
        from app.db.session import get_db
        content=dict(merchant='Demo market',purchased_on=date.today().isoformat(),currency='EUR',items=[dict(product_id=str(self.product),original_text='Vollmilch 1L',quantity='1',quantity_unit='pack',unit_price_cents=100,category='groceries')],adjustments=[],reported_total_cents=100)
        with Session(self.engine) as db,db.begin():
            draft=PurchaseDraft(household_id=self.hh,created_by=self.user,source_type='text',status='pending',data={'content':content});db.add(draft);db.flush();draft_id=draft.id;revision=purchases.draft_revision(draft)
        app=FastAPI();app.include_router(router)
        app.dependency_overrides[get_current_user]=lambda:Obj(id=self.user)
        def session():
            with Session(self.engine) as db:yield db
        app.dependency_overrides[get_db]=session
        committed=[]
        def after_response(bind,household,user,**kwargs):
            with Session(bind) as db:committed.append(db.scalar(select(func.count()).select_from(ProductCorrection)))
        with patch.object(memory,'process_pending',side_effect=after_response):
            result=TestClient(app).post(f'/households/{self.hh}/purchase-drafts/{draft_id}/confirm',json={'revision':revision})
        self.assertEqual(result.status_code,200);self.assertEqual(committed,[1])

    def test_no_indexed_examples_means_no_embedding_call(self):
        self.assertEqual(self.retrieve()[1],'not_found');self.assertEqual(self.embeds,[])

if __name__=='__main__':unittest.main()
