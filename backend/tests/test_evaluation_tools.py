import importlib.util
from pathlib import Path
import unittest
ROOT=Path(__file__).resolve().parents[2]
def load(name):
 spec=importlib.util.spec_from_file_location(name,ROOT/'scripts'/f'{name}.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
suite=load('evaluate_ai_workflows');benchmark=load('compare_performance')
class EvaluationToolTests(unittest.TestCase):
 def test_wer_normalization_and_substitution(self):
  self.assertEqual(suite.word_errors('Milk, and BREAD!','milk and bread')['wer'],0)
  self.assertEqual(suite.word_errors('milk and bread','milk and rice')['word_errors'],1)
 def test_extraction_order_independent_numeric_score(self):
  expected=dict(merchant='Demo',purchased_on='2026-11-10',currency='EUR',reported_total_cents=300,items=[dict(original_text='Milk',quantity='1.0',unit_price_cents=100,line_total_cents=100),dict(original_text='Bread',quantity='1',unit_price_cents=200,line_total_cents=200)])
  got={**expected,'items':list(reversed(expected['items']))}
  self.assertTrue(suite.extraction_score({'extracted':got,'saved':False},expected)['passed'])
  got={**expected,'reported_total_cents':299};self.assertFalse(suite.extraction_score({'extracted':got},expected)['passed'])
 def test_errors_in_denominator_blocked_not_hidden(self):
  s=suite.summary([dict(status='passed',latency_ms=10),dict(status='error',latency_ms=30),dict(status='blocked')])
  self.assertEqual(s['pass_rate_including_errors'],.5);self.assertFalse(s['complete']);self.assertEqual(s['blocked'],1)
 def test_no_measured_cases_does_not_claim_zero_or_success(self):
  s=suite.summary([dict(status='blocked')]);self.assertIsNone(s['pass_rate_including_errors']);self.assertFalse(s['complete'])
 def test_exception_content_not_published(self):
  def fail():raise ValueError('secret-token-user-prompt')
  row=suite.run_case('x',fail);self.assertNotIn('secret-token',str(row));self.assertEqual(row['status'],'error')
 def test_comparison_rejects_failures_and_different_work(self):
  b={'authenticated':True,'scenarios':{'home_overview':{'observations':[dict(total_ms=100,success=True)]}}}
  a={**b,'home_overview_includes_offers':False}
  row=benchmark.compare(b,a)['scenarios'][0];self.assertFalse(row['comparable']);self.assertIsNone(row['median_reduction_percent'])
  a={**b,'scenarios':{'home_overview':{'observations':[dict(success=False)]}}}
  self.assertFalse(benchmark.compare(b,a)['scenarios'][0]['comparable'])

class LiveEvaluationWiringTests(unittest.TestCase):
 def test_reused_receipt_fixture_is_valid_encoded_image(self):
  from datetime import date
  from app.ai.image_extractor import prepare_image
  smoke=load('check_image_extraction')
  image=prepare_image(smoke.receipt('en',date(2026,11,10)))
  self.assertTrue(image.startswith(b'\xff\xd8'))
  self.assertEqual(suite.capture_cases()['receipt_extraction'][0]['expected']['reported_total_cents'],427)
 def test_numeric_feedback_and_no_private_payload(self):
  from unittest.mock import patch,MagicMock
  from uuid import uuid4
  report=dict(models={'language':'test'},source_sha256='abc',dataset_sha256='def',complete=True,workflows={'ask_bonwise':{'summary':{'pass_rate_including_errors':.5,'errors':1},'cases':[]}})
  with patch('app.ai.analysis_tracing.AnalysisTrace') as trace_class, patch('app.ai.analysis_tracing.trace_client') as client:
   trace=trace_class.return_value;trace.id=uuid4();trace.payload=[{'extra':{'metadata':{}}}];trace.send.return_value=True
   result=suite.publish_langsmith(report)
   self.assertTrue(result['trace_sent']);self.assertEqual(result['feedback_sent'],2)
   self.assertEqual(client.return_value.create_feedback.call_args_list[0].kwargs,{'score':.5})
   self.assertEqual(trace.payload[0]['extra']['metadata']['evaluation_scope'],'synthetic_components')
 def test_disabled_langsmith_keeps_local_results(self):
  from unittest.mock import patch
  report=dict(models={'language':'test'},source_sha256='abc',dataset_sha256='def',complete=False,workflows={})
  with patch('app.ai.analysis_tracing.AnalysisTrace') as trace_class:
   trace=trace_class.return_value;trace.payload=[{'extra':{'metadata':{}}}];trace.send.return_value=False
   self.assertFalse(suite.publish_langsmith(report)['trace_sent'])
 def test_evaluation_root_is_explicitly_allowed(self):
  from app.ai.analysis_tracing import AnalysisTrace
  self.assertEqual(AnalysisTrace('test','test',root_name='bonwise_evaluation').root_name,'bonwise_evaluation')
