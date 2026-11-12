"""Related-question execution/context checks. Synthetic snapshots and temporary DBs only."""
from datetime import date
import unittest
from unittest.mock import Mock, patch

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.orm import Session

import test_planner as fixture
from test_analysis_ranking import plan, purchase
from app.schemas.analysis import AnalysisInput, ConversationContext, ContextPeriod, RelatedQuestion
from app.ai.analysis_context import checked_context, next_context
from app.ai.analysis_followups import resolve
from app.ai.analysis_related import (child_plan, execute_reference, execute_related, resolve_related)
with patch('app.core.config.get_settings', return_value=fixture.SETTINGS):
    from app.ai import analysis_assistant as assistant


PERIOD = (date(2026, 11, 1), date(2026, 11, 10))


def compound(scope='period', **overrides):
    child = RelatedQuestion(operation='items', scope=scope, top_n=1)
    return plan(operation='highest_category', sort_direction='highest', related=child, **overrides)


def request(question, context=None, dates=PERIOD):
    return AnalysisInput(question=question, start_date=dates[0], end_date=dates[1], conversation_context=context)


def data(tie=False):
    first = purchase('A', [('Milk', 300), ('Bread', 100)])
    second = purchase('B', [('Soap', 600 if not tie else 400)])
    second['items'][0]['category'] = 'household'
    return [first, second]


def follow(question, memory, dates=PERIOD):
    body = request(question, memory, dates)
    raw = plan(operation='clarify', sort_direction='highest')
    routed = resolve(question, raw, memory, body)
    return resolve_related(question, routed, memory, body, assistant.valid_plan)


class RelatedTests(unittest.TestCase):
    def test_global_ranking_is_labeled_and_not_silently_category_filtered(self):
        p = compound(); p.related.top_n = 2
        result = execute_related(p, data())
        self.assertEqual(result['status'], 'answered')
        self.assertIn('Household', result['answer'])
        self.assertIn('across the requested period', result['answer'])
        self.assertIn('Milk', result['answer'])
        self.assertEqual({r.kind for r in result['evidence']}, {'summary', 'purchase'})
        self.assertEqual(result['summaries'][0].total_cents, 1000)

    def test_category_ranking_uses_exact_winning_category(self):
        result = execute_related(compound('highest_category'), data())
        self.assertIn('highest category/categories: Household', result['answer'])
        self.assertNotIn('Milk', result['answer'])
        self.assertEqual({r.item_description for r in result['evidence'] if r.kind=='purchase'}, {'Soap'})

    def test_category_ties_include_all_tied_categories(self):
        result = execute_related(compound('highest_category'), data(tie=True))
        self.assertIn('tied for highest spending', result['answer'])
        self.assertIn('Groceries and Household', result['answer'])
        self.assertIn('Soap', result['answer'])
        self.assertEqual({r.category for r in result['evidence'] if r.kind=='summary'}, {'groceries','household'})

    def test_ranked_item_ties_are_not_arbitrarily_dropped(self):
        rows = [purchase('A', [('Milk', 200), ('Bread', 200), ('Rice', 100)])]
        result = execute_related(compound(), rows)
        self.assertEqual({r.item_description for r in result['evidence'] if r.kind=='purchase'}, {'Milk','Bread'})

    def test_scope_question_keeps_first_answer_and_resolves_choice(self):
        p=compound('clarify'); result=execute_related(p,data())
        self.assertEqual(result['status'],'clarification_required')
        self.assertIn('Household',result['answer'])
        memory=next_context(None,p,request('Category and those items?'),result)
        self.assertEqual(memory.pending.reason,'scope')
        for choice,expected in [('overall','period'),('in that category','highest_category')]:
            resolved=follow(choice,memory)
            self.assertEqual(resolved.related.scope,expected)
            self.assertTrue(assistant.valid_plan(resolved))

    def test_german_scopes_and_labels(self):
        p=compound('clarify',language='de'); memory=next_context(None,p,request('Kategorie und diese Artikel?'),execute_related(p,data()))
        resolved=follow('in dieser Kategorie',memory)
        self.assertEqual(resolved.related.scope,'highest_category')
        self.assertIn('Haushalt',execute_related(resolved,data())['answer'])

    def test_empty_and_negative_history_do_not_invent_a_positive_category(self):
        self.assertEqual(execute_related(compound(),[])['status'],'no_data')
        row=purchase('A',[('Return',-200)])
        result=execute_related(compound(),[row])
        self.assertIn('Groceries',result['answer'])
        self.assertNotIn('Household',result['answer'])

    def test_adjustment_only_category_is_not_presented_as_item_spending(self):
        row=purchase('A',[('Milk',100)])
        row['adjustments']=[{'category':'other','amount_cents':500,'kind':'deposit','label':'Deposit'}]
        row['total_cents']=600
        result=execute_related(compound('highest_category'),[row])
        self.assertEqual(result['status'],'answered')
        self.assertIn('purchase-level adjustments',result['answer'])
        self.assertFalse(any(r.kind=='purchase' for r in result['evidence']))

    def test_partial_unsupported_keeps_supported_answer_and_specific_reason(self):
        for reason,phrase in [('offers','Compare offers'),('prediction','predict'),('write','does not change'),('causal','why prices'),('quantity_ranking','incompatible quantities'),('unrelated','outside'),('limit','at most two')]:
            p=compound();p.related=RelatedQuestion(operation='unsupported',unsupported_reason=reason)
            self.assertTrue(assistant.valid_plan(p))
            result=execute_related(p,data())
            self.assertEqual(result['status'],'answered')
            self.assertIn('Household',result['answer']);self.assertIn(phrase,result['answer'])

    def test_child_cannot_add_identity_dates_or_a_third_question(self):
        for extra in [{'household_id':'forged'}, {'start_date':'2026-09-01'}, {'related':{'operation':'budget'}}, {'operation':'delete'}]:
            with self.assertRaises(ValidationError):RelatedQuestion.model_validate({'operation':'items',**extra})

    def test_invalid_operation_scope_combinations_are_rejected(self):
        invalid=[compound()]
        invalid[0].operation='summary';invalid[0].related.scope='highest_category'
        p=compound();p.related=RelatedQuestion(operation='budget',product_query='Milk');invalid.append(p)
        p=compound();p.related=RelatedQuestion(operation='unsupported');invalid.append(p)
        p=compound();p.operation='compare';p.comparison_start_date=date(2026,10,1);p.comparison_end_date=date(2026,10,10);invalid.append(p)
        p=compound();p.reference_scope='ranked_items';invalid.append(p)
        for p in invalid:self.assertFalse(assistant.valid_plan(p))

    def test_followup_clarifies_category_vs_ranked_items_and_keeps_dates(self):
        p=compound();memory=next_context(None,p,request('Category and top items'),execute_related(p,data()))
        question='Which purchases contributed to that spending?'
        clarification=follow(question,memory)
        self.assertEqual((clarification.operation,clarification.clarification_reason),('clarify','scope'))
        pending=next_context(memory,clarification,request(question,memory),{'status':'clarification_required'})
        resolved=follow('ranked items',pending)
        self.assertEqual(resolved.reference_scope,'ranked_items')
        self.assertEqual((resolved.start_date,resolved.end_date),PERIOD)
        self.assertIn('Soap',execute_reference(resolved,p,data())['answer'])

    def test_ambiguous_followup_after_generic_pair_offers_first_or_second(self):
        p=compound();p.operation='summary'
        memory=next_context(None,p,request('Total and items'),execute_related(p,data()))
        clarification=follow('Show those purchases',memory)
        pending=next_context(memory,clarification,request('Show those purchases',memory),{'status':'clarification_required'})
        resolved=follow('second result',pending)
        self.assertEqual(resolved.reference_scope,'second_result')
        self.assertEqual({r.item_description for r in execute_reference(resolved,p,data())['evidence']},{'Soap'})

    def test_merchant_request_is_retained_after_choosing_record_scope(self):
        p=compound();memory=next_context(None,p,request('Category and items'),execute_related(p,data()))
        clarification=follow('Which shop was that?',memory)
        pending=next_context(memory,clarification,request('Which shop was that?',memory),{'status':'clarification_required'})
        resolved=follow('ranked items',pending)
        self.assertEqual(resolved.detail,'merchant')
        result=execute_reference(resolved,p,data())
        self.assertIn('shopped at B',result['answer'])

    def test_polite_and_paraphrased_record_references_do_not_guess(self):
        p=compound();memory=next_context(None,p,request('Category and items'),execute_related(p,data()))
        for q in ['Please show those purchases','Show me those purchases please','Which of those records contributed?']:
            raw=plan(operation='records',sort_direction='highest')
            result=resolve_related(q,raw,memory,request(q,memory),assistant.valid_plan)
            self.assertEqual(result.operation,'clarify')

    def test_explicit_month_on_ambiguous_reference_is_retained(self):
        p=compound();memory=next_context(None,p,request('Category and items'),execute_related(p,data()))
        raw=plan(operation='records',sort_direction='highest',start_date=date(2026,9,1),end_date=date(2026,9,30))
        q='Show those purchases for September 2026'
        result=resolve_related(q,raw,memory,request(q,memory),assistant.valid_plan)
        self.assertEqual((result.start_date,result.end_date),(date(2026,9,1),date(2026,9,30)))
        pending=next_context(memory,result,request(q,memory),{'status':'clarification_required'})
        resolved=follow('ranked items',pending)
        self.assertEqual((resolved.start_date,resolved.end_date),(date(2026,9,1),date(2026,9,30)))

    def test_page_change_and_explicit_month_do_not_reuse_old_dates(self):
        p=compound();memory=next_context(None,p,request('Category and items'),execute_related(p,data()))
        sept=(date(2026,9,1),date(2026,9,30))
        q=follow('Show purchases behind the ranked items',memory,sept)
        self.assertEqual((q.start_date,q.end_date),sept)
        q=resolve('Now show 2026 November spendings.',plan(operation='summary',sort_direction='highest',start_date=sept[0],end_date=sept[1]),memory,request('Now show 2026 November spendings.',memory,sept))
        self.assertEqual((q.start_date,q.end_date),(date(2026,11,1),date(2026,11,30)))

    def test_reference_rankings_recompute_with_full_data_not_truncated_refs(self):
        p=compound();rows=[purchase('A',[('Milk',100)]) for _ in range(25)]
        result=execute_related(p,rows)
        self.assertTrue(result['evidence_truncated']);self.assertIn('25,00',result['answer'])
        scope=p.model_copy(update={'operation':'records','related':None,'reference_scope':'ranked_items','detail':'date'})
        detail=execute_reference(scope,p,rows)
        self.assertEqual(detail['status'],'clarification_required')
        self.assertTrue(detail['evidence_truncated'])
        self.assertEqual(len(detail['evidence']),10)

    def test_canonical_identity_survives_different_receipt_wordings(self):
        rows=[purchase('A',[('Milk',100)]),purchase('B',[('Vollmilch',200)]),purchase('C',[('Bread',250)])]
        for row in rows[:2]:row['items'][0]['product_id']='same-canonical-product'
        p=compound();scope=p.model_copy(update={'operation':'records','related':None,'reference_scope':'ranked_items'})
        result=execute_reference(scope,p,rows)
        self.assertEqual({r.item_description for r in result['evidence']},{'Milk','Vollmilch'})

    def test_old_single_question_context_remains_compatible(self):
        p=plan(operation='summary',sort_direction='highest')
        old=ConversationContext(last_plan=p,selected_period=ContextPeriod(start_date=PERIOD[0],end_date=PERIOD[1]))
        self.assertEqual(follow('Show those purchases',old).operation,'records')
        self.assertIsNone(follow('Show those purchases',old).reference_scope)

    def test_context_filter_strings_are_checked_in_both_related_copies(self):
        for field in ['last_plan','compound_plan']:
            p=compound();p.related.product_query='person@example.com'
            with self.assertRaises(HTTPException):checked_context(ConversationContext(**{field:p}))

    def test_missing_budget_does_not_hide_the_spending_result(self):
        p=compound();p.operation='summary';p.related=RelatedQuestion(operation='budget')
        reader=Mock(return_value=dict(status='no_data',answer='No budget is set for this period.',evidence=[],summaries=[],evidence_truncated=False))
        result=execute_related(p,data(),reader)
        self.assertEqual(result['status'],'answered');self.assertIn('10,00',result['answer']);self.assertIn('No budget',result['answer'])
        self.assertEqual(reader.call_args.args[0].start_date,PERIOD[0])

    def test_two_readonly_receipt_details_share_the_same_purchase(self):
        p=compound();p.operation='records';p.detail='merchant';p.related=RelatedQuestion(operation='records',detail='date')
        self.assertTrue(assistant.valid_plan(p))
        result=execute_related(p,[purchase('Example Shop',[('Milk',100)])])
        self.assertEqual(result['status'],'answered')
        self.assertIn('Example Shop',result['answer']);self.assertIn('05.11.2026',result['answer'])
        self.assertEqual(len(result['evidence']),1)

    def test_same_period_different_store_summaries_keep_both_evidence_sets(self):
        p=compound();p.operation='summary';p.merchant_query='A';p.related=RelatedQuestion(operation='summary',merchant_query='B')
        result=execute_related(p,data())
        self.assertEqual({r.total_cents for r in result['evidence']},{400,600})
        self.assertEqual(len({r.evidence_id for r in result['evidence']}),2)
        self.assertEqual({s.total_cents for s in result['summaries']},{400,600})

    def test_first_part_ambiguity_is_not_reported_as_complete(self):
        p=compound();p.operation='records';p.detail='merchant';p.related=RelatedQuestion(operation='summary')
        result=execute_related(p,data())
        self.assertEqual(result['status'],'clarification_required');self.assertIn('10,00',result['answer'])

    def test_two_quantities_keep_units_separate_in_their_answers(self):
        p=compound();p.operation='quantity';p.product_query='Milk';p.related=RelatedQuestion(operation='quantity',product_query='Bread')
        row=purchase('A',[('Milk',100),('Bread',200)])
        row['items'][1].update(quantity='0.5',quantity_unit='kg',unit_price_cents=400)
        result=execute_related(p,[row])
        self.assertEqual(result['status'],'answered')
        self.assertIn('“Milk” total 1 pack',result['answer']);self.assertIn('“Bread” total 0,5 kg',result['answer'])


class RelatedWorkflowTests(unittest.TestCase):
    setUp=fixture.PlannerTests.setUp
    tearDown=fixture.PlannerTests.tearDown
    purchase=fixture.PlannerTests.purchase

    def ask(self, question, p, memory=None, user=None):
        model=Mock();model.invoke.return_value={'parsed':p,'parsing_error':None,'raw':None}
        dates=(p.start_date or PERIOD[0],p.end_date or PERIOD[1])
        with patch.object(assistant,'get_settings',return_value=fixture.SETTINGS),patch.object(assistant,'routing_model',return_value=model),Session(self.engine) as db:
            result=assistant.ask_bonwise(db,self.household,user or self.user,request(question,memory,dates))
        return result,model

    def base_plan(self):
        from datetime import timedelta
        return compound(start_date=self.day-timedelta(days=30),end_date=self.day)

    def test_reported_phrase_uses_one_model_call_and_one_snapshot(self):
        p=self.base_plan()
        with patch.object(assistant,'load_snapshot',wraps=assistant.load_snapshot) as snapshot:
            result,model=self.ask('Which category had the highest spending and what are the items i spent more?',p)
        self.assertEqual((result.status,result.model_calls),('answered',1))
        self.assertEqual(snapshot.call_count,1);self.assertEqual(model.invoke.call_count,1)
        self.assertIn('Groceries',result.answer);self.assertIn('Whole milk',result.answer)
        self.assertEqual({e.kind for e in result.evidence},{'summary','purchase'})
        self.assertEqual(result.analysis_period.start_date,p.start_date)

    def test_related_traces_contain_counts_but_no_questions_or_records(self):
        from app.ai.analysis_tracing import AnalysisTrace
        p=self.base_plan();trace=AnalysisTrace(assistant.PROMPT_VERSION,'offline-test-model')
        with patch.object(assistant,'AnalysisTrace',return_value=trace):
            result,_=self.ask('Highest category and top item?',p)
        self.assertEqual(trace.payload[0]['extra']['metadata']['subquestion_count'],2)
        for row in trace.payload:
            self.assertEqual(row['inputs'],{});self.assertEqual(row['outputs'],{})
        self.assertNotIn('Whole milk',str(trace.payload));self.assertNotIn('Highest category and top item?',str(trace.payload))

    def test_http_contract_round_trips_compound_context_and_rejects_third_step(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.api.analysis import router
        from app.core.auth import get_current_user, CurrentUser
        from app.db.session import get_db
        app=FastAPI();app.include_router(router)
        app.dependency_overrides[get_current_user]=lambda:CurrentUser(self.user)
        def database():
            with Session(self.engine) as db:yield db
        app.dependency_overrides[get_db]=database
        p=self.base_plan();model=Mock();model.invoke.return_value={'parsed':p,'parsing_error':None,'raw':None}
        with patch.object(assistant,'get_settings',return_value=fixture.SETTINGS),patch.object(assistant,'routing_model',return_value=model),TestClient(app) as client:
            path=f'/households/{self.household}/analysis/ask'
            body=request('Highest category and top item?',dates=(p.start_date,p.end_date)).model_dump(mode='json')
            reply=client.post(path,json=body)
            self.assertEqual(reply.status_code,200);self.assertEqual(reply.headers['cache-control'],'no-store')
            saved=ConversationContext.model_validate(reply.json()['conversation_context'])
            self.assertIsNotNone(saved.compound_plan.related)
            body['conversation_context']=saved.model_dump(mode='json')
            body['conversation_context']['compound_plan']['related']['related']={'operation':'budget'}
            self.assertEqual(client.post(path,json=body).status_code,422)

    def test_compound_record_clarification_then_ranked_items_and_date(self):
        p=self.base_plan();first,_=self.ask('Highest category and top item?',p)
        raw=plan(operation='clarify',start_date=p.start_date,end_date=p.end_date)
        clarification,_=self.ask('Which purchases contributed to that spending?',raw,first.conversation_context)
        self.assertEqual(clarification.status,'clarification_required');self.assertIn('highest category',clarification.answer)
        details,_=self.ask('ranked items',raw,clarification.conversation_context)
        self.assertEqual(details.status,'answered')
        self.assertEqual({e.item_description for e in details.evidence},{'Whole milk 1L'})
        dates,_=self.ask('What date was that purchase?',raw,details.conversation_context)
        self.assertEqual(dates.status,'clarification_required');self.assertIn('Several purchases',dates.answer)

    def test_unauthorized_household_is_denied_before_provider(self):
        model=Mock()
        with patch.object(assistant,'get_settings',return_value=fixture.SETTINGS),patch.object(assistant,'routing_model',return_value=model),Session(self.engine) as db,self.assertRaises(HTTPException) as caught:
            assistant.ask_bonwise(db,self.household,self.other_user,request('Highest category and items'))
        self.assertEqual(caught.exception.status_code,404);model.assert_not_called()

    def test_nested_model_pii_is_blocked_before_snapshot(self):
        p=self.base_plan();p.related.product_query='person@example.com'
        with patch.object(assistant,'load_snapshot') as snapshot,self.assertRaises(HTTPException) as caught:
            self.ask('Highest category and items',p)
        self.assertEqual(caught.exception.status_code,422);snapshot.assert_not_called()

    def test_client_compound_context_pii_is_blocked_before_provider(self):
        p=self.base_plan();p.related.merchant_query='person@example.com'
        model=Mock()
        with patch.object(assistant,'get_settings',return_value=fixture.SETTINGS),patch.object(assistant,'routing_model',return_value=model),Session(self.engine) as db,self.assertRaises(HTTPException):
            assistant.ask_bonwise(db,self.household,self.user,request('Show those purchases',ConversationContext(compound_plan=p)))
        model.assert_not_called()

    def test_semantically_invalid_compound_context_is_blocked_before_provider(self):
        p=self.base_plan();p.operation='summary';p.related.scope='highest_category'
        model=Mock()
        with patch.object(assistant,'get_settings',return_value=fixture.SETTINGS),patch.object(assistant,'routing_model',return_value=model),Session(self.engine) as db,self.assertRaises(HTTPException) as caught:
            assistant.ask_bonwise(db,self.household,self.user,request('Show those purchases',ConversationContext(compound_plan=p)))
        self.assertEqual(caught.exception.status_code,422);model.assert_not_called()

    def test_invalid_model_plan_preserves_previous_valid_context(self):
        good=self.base_plan();first,_=self.ask('Highest category and top item?',good)
        bad=good.model_copy(deep=True);bad.operation='summary';bad.related.scope='highest_category'
        result,_=self.ask('Ambiguous request',bad,first.conversation_context)
        self.assertEqual(result.conversation_context,first.conversation_context)

    def test_spending_and_budget_share_dates_with_two_reads_at_most(self):
        p=self.base_plan();p.operation='summary';p.related=RelatedQuestion(operation='budget')
        with patch.object(assistant,'load_snapshot',wraps=assistant.load_snapshot) as snapshot,patch('app.services.analysis_budget.for_period',return_value={'status':'not_set','budget':None}) as budget:
            result,_=self.ask('Spending and budget for this period?',p)
        self.assertEqual(snapshot.call_count,1);self.assertEqual(budget.call_count,1)
        self.assertEqual(budget.call_args.args[-2:],(p.start_date,p.end_date))
        self.assertEqual(result.status,'answered');self.assertIn('No budget',result.answer)

    def test_partial_unsupported_does_not_trigger_an_extra_read(self):
        p=self.base_plan();p.related=RelatedQuestion(operation='unsupported',unsupported_reason='write')
        with patch.object(assistant,'load_snapshot',wraps=assistant.load_snapshot) as snapshot:
            result,_=self.ask('Highest category and change my budget?',p)
        self.assertEqual(snapshot.call_count,1);self.assertIn('does not change',result.answer)

    def test_invalid_nested_plan_never_executes(self):
        p=self.base_plan();p.related.scope='highest_category';p.operation='summary'
        with patch.object(assistant,'load_snapshot') as snapshot:
            result,_=self.ask('Ambiguous scoped question',p)
        self.assertEqual(result.status,'clarification_required');snapshot.assert_not_called()

    def test_references_never_use_client_context_as_evidence(self):
        p=self.base_plan();p.related.product_query='Imaginary item'
        memory=ConversationContext(compound_plan=p,last_plan=p)
        q=p.model_copy(update={'operation':'records','related':None,'reference_scope':'ranked_items'})
        result,_=self.ask('Show purchases behind the ranked items',q,memory)
        self.assertEqual(result.status,'no_data');self.assertEqual(result.evidence,[])


if __name__=='__main__':unittest.main()
