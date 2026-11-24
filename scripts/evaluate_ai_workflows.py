"""Run bounded live component evaluations for all six AI workflows. No record writes.

Use --live explicitly; configured providers charge for calls. No model responses,
receipts, transcripts, household IDs or access tokens are saved in result reports.
"""
import argparse,base64,hashlib,json,re,subprocess,sys
from importlib.metadata import version,PackageNotFoundError
from collections import Counter
from datetime import date,datetime,timezone
from decimal import Decimal
from pathlib import Path
from statistics import median
from time import perf_counter
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
WORKFLOWS=['receipt_extraction','voice_capture','product_matching_rag','ask_bonwise','ai_insights','shopping_planner']

def capture_cases():
    """Reuse the existing synthetic image smoke fixtures; no generated asset files."""
    cases = {"receipt_extraction": [], "voice_capture": []}
    for language, milk, bread in [("en", "Milk", "Bread"), ("de", "Milch", "Brot")]:
        expected = dict(merchant="DEMO MARKT", purchased_on="2026-11-10", currency="EUR",
                        reported_total_cents=427, items=[
                            dict(original_text=milk, quantity="2", unit_price_cents=119, line_total_cents=238),
                            dict(original_text=bread, quantity="1", unit_price_cents=189, line_total_cents=189)])
        cases["receipt_extraction"].append(dict(id="receipt_"+language, language=language, expected=expected))
        transcript = (
            "On November tenth 2026 I bought two packs of milk at one euro nineteen each "
            "and one bread at one euro eighty-nine at Demo Markt. The total was four euros twenty-seven."
            if language == "en" else
            "Am zehnten November 2026 habe ich im Demo Markt zwei Packungen Milch zu je einem Euro neunzehn "
            "und ein Brot für einen Euro neunundachtzig gekauft. Insgesamt waren es vier Euro siebenundzwanzig."
        )
        cases["voice_capture"].append(dict(id="voice_"+language, file="voice_"+language+".wav",
                                           language=language, reference_transcript=transcript, expected=expected))
    return cases


def publish_langsmith(report):
    """Attach numeric evaluation feedback to one content-free run, not raw examples."""
    from app.ai.analysis_tracing import AnalysisTrace, trace_client
    trace = AnalysisTrace("component-evaluation-v2", report["models"]["language"], root_name="bonwise_evaluation")
    trace.finish()
    trace.payload[0]["extra"]["metadata"].update(
        evaluation_scope="synthetic_components", source_sha256=report["source_sha256"],
        dataset_sha256=report["dataset_sha256"], coverage_complete=report["complete"])
    if not trace.send():
        return dict(trace_sent=False, feedback_sent=0, note="LangSmith disabled or delivery failed; local results remain available.")
    scores = {}
    for workflow, result in report["workflows"].items():
        rate = result["summary"]["pass_rate_including_errors"]
        if rate is not None:
            scores[workflow+".pass_rate"] = rate
        scores[workflow+".errors"] = result["summary"]["errors"]
        if workflow == "product_matching_rag":
            for case in result["cases"]:
                for mode, metrics in case.get("metrics", {}).get("paired_metrics", {}).items():
                    for key in ["hit_at_1", "hit_at_3", "suggestion_precision", "semantic_evidence_cases"]:
                        value = metrics.get(key)
                        if type(value) in (int, float): scores["rag."+mode+"."+key] = value
    sent = 0
    try:
        client = trace_client()
        for key, score in scores.items():
            client.create_feedback(trace.id, key, score=score)
            sent += 1
    except Exception:
        return dict(trace_sent=True, run_id=str(trace.id), feedback_sent=sent, feedback_expected=len(scores), note="Some numeric feedback failed to upload; inspect local results.")
    return dict(trace_sent=True, run_id=str(trace.id), feedback_sent=sent,
                note="Component evaluation scores, not a hosted end-to-end experiment or general accuracy claim.")


def normalize(text):return re.findall(r'\w+',text.casefold(),flags=re.UNICODE)
def word_errors(reference,actual):
    a,b=normalize(reference),normalize(actual);prev=list(range(len(b)+1))
    for i,x in enumerate(a,1):
        curr=[i]
        for j,y in enumerate(b,1):curr.append(min(curr[-1]+1,prev[j]+1,prev[j-1]+(x!=y)))
        prev=curr
    return {'reference_words':len(a),'word_errors':prev[-1],'wer':prev[-1]/len(a) if a else None}

def extraction_score(output,expected):
    got=output['extracted']
    if hasattr(got,'model_dump'):got=got.model_dump(mode='json')
    checks={k:got.get(k)==expected[k] for k in ['merchant','purchased_on','currency','reported_total_cents']}
    def signature(item):
        try:q=str(Decimal(item['quantity']).normalize())
        except Exception:q=None
        return (q,item.get('unit_price_cents'),item.get('line_total_cents'))
    e=Counter(signature(i) for i in expected['items']);g=Counter(signature(i) for i in got['items']);hits=sum((e&g).values())
    checks['numeric_item_lines_exact']=e==g
    # Wording can differ legitimately; this deliberately strict secondary metric is separate.
    expected_names=Counter(' '.join(normalize(i['original_text'])) for i in expected['items'])
    actual_names=Counter(' '.join(normalize(i['original_text'])) for i in got['items'])
    return {'checks':checks,'passed':all(checks.values()),'matched_numeric_item_lines':hits,'expected_item_lines':sum(e.values()),'returned_item_lines':sum(g.values()),'strict_item_wording_exact':expected_names==actual_names,'review_required':output.get('saved') is False}

def fixture_purchase(index,lines,day=date(2026,11,5)):
    from uuid import UUID
    items=[dict(id=str(UUID(int=100+index*10+i)),product_id=None,description=name,quantity='1',quantity_unit='pack',unit_price_cents=cents,line_total_cents=cents,category=category) for i,(name,cents,category) in enumerate(lines)]
    return dict(id=str(UUID(int=index+1)),merchant='DEMO MARKET',purchased_on=day,total_cents=sum(i['line_total_cents'] for i in items),adjustments=[],items=items)

def run_case(case_id,fn):
    start=perf_counter()
    try:
        result=fn();return dict(case_id=case_id,status='passed' if result.get('passed') else 'failed',latency_ms=round((perf_counter()-start)*1000,2),metrics=result)
    except Exception as exc:
        # No exception message: it can contain prompts, identifiers or provider response data.
        return dict(case_id=case_id,status='error',latency_ms=round((perf_counter()-start)*1000,2),error_class=type(exc).__name__)

def summary(rows):
    executed=[r for r in rows if r['status']!='blocked'];passed=sum(r['status']=='passed' for r in rows)
    return dict(defined_cases=len(rows),executed_cases=len(executed),passed=passed,failed=sum(r['status']=='failed' for r in rows),errors=sum(r['status']=='error' for r in rows),blocked=sum(r['status']=='blocked' for r in rows),pass_rate_including_errors=passed/len(executed) if executed else None,complete=bool(rows) and len(executed)==len(rows),median_latency_ms=median(r['latency_ms'] for r in executed) if executed else None)

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--live',action='store_true');p.add_argument('--audio-dir',type=Path,default=ROOT/'private_audio/evaluation');p.add_argument('--limit',type=int,default=2);p.add_argument('--workflows',nargs='+',choices=WORKFLOWS,default=WORKFLOWS);p.add_argument('--household-id');p.add_argument('--user-id');p.add_argument('--output',type=Path,default=ROOT/'evaluation-results/live-ai-components.json');a=p.parse_args()
    if not 1<=a.limit<=25:p.error('Use --limit 1–25. Default 2 is a small smoke evaluation, not a general accuracy estimate.')
    data=capture_cases()
    if not a.live:
        print(json.dumps({'live':False,'workflow_count':len(a.workflows),'workflows':a.workflows,'provider_calls':0,'quality_measured':False,'missing_voice_assets':[c['id'] for c in data['voice_capture'] if not (a.audio_dir/c['file']).exists()],'note':'Fixtures validated only; --live runs charged provider calls. RAG needs existing authorized household/catalogue/correction context.'},indent=2));return
    from app.core.config import get_settings
    from langsmith import tracing_context
    settings=get_settings()
    source_hash=hashlib.sha256()
    for path in sorted((ROOT/'backend/app').rglob('*.py')):source_hash.update(str(path.relative_to(ROOT)).encode()+path.read_bytes())
    fixture_hash=hashlib.sha256(json.dumps(data,sort_keys=True).encode())
    paths=[ROOT/'data/evaluations/planner_text_edits.json',ROOT/'data/evaluations/correction_rag_cases.json']
    for path in sorted(paths):
        if path.is_file():fixture_hash.update(str(path.relative_to(ROOT)).encode()+path.read_bytes())
    for case in data['voice_capture']:
        audio = a.audio_dir/case['file']
        if audio.is_file():
            fixture_hash.update(case['id'].encode()+audio.read_bytes())
    report=dict(schema_version=1,live=True,measured_at=datetime.now(timezone.utc).isoformat(),python=sys.version.split()[0],source_sha256=source_hash.hexdigest(),dataset_sha256=fixture_hash.hexdigest(),models={'language':settings.llm_model,'vision':settings.vision_model,'transcription':settings.transcription_model,'embedding':settings.embedding_model},scope='Live provider component tests on synthetic fixtures. Not hosted end-to-end, registration, approval/reservation security, or natural-receipt accuracy.',notes=['Errors stay in the executed-case denominator. Missing prerequisites are blocked and make coverage incomplete.','No costs are estimated without current model rates and measured usage. Latency is client-observed component duration.','RAG exact-memory successes do not prove semantic retrieval quality; inspect actual retrieval evidence counts.','Small deterministic synthetic samples do not establish real-world quality.'],workflows={})
    report['package_versions']={}
    for package in ['openai','langchain-openai','langchain','langgraph','pinecone','sqlalchemy','fastapi','pydantic','pillow']:
        try:report['package_versions'][package]=version(package)
        except PackageNotFoundError:report['package_versions'][package]='not_installed'
    def save():
        report['complete']=all(w in report['workflows'] and report['workflows'][w]['summary']['complete'] for w in WORKFLOWS)
        a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(report,indent=2,default=str)+'\n')
    for workflow in a.workflows:
        rows=[]
        with tracing_context(enabled=False):
            if workflow=='receipt_extraction':
                from app.ai.image_extractor import extract_image
                from app.ai.purchase_extractor import PROMPT_VERSION as EXTRACTION_PROMPT_VERSION
                report.setdefault('prompt_versions',{})['receipt_extraction']=EXTRACTION_PROMPT_VERSION
                for case in data[workflow][:a.limit]:
                    def run(c=case):
                        from check_image_extraction import receipt
                        encoded_image = receipt(c['language'], date(2026, 11, 10))
                        return extraction_score(extract_image(encoded_image), c['expected'])
                    rows.append(run_case(case['id'],run))
            elif workflow=='voice_capture':
                from app.ai.transcriber import transcribe
                from app.ai.purchase_extractor import PROMPT_VERSION as EXTRACTION_PROMPT_VERSION
                report.setdefault('prompt_versions',{})['voice_capture']=dict(transcription='not_applicable',extraction=EXTRACTION_PROMPT_VERSION)
                from app.ai.purchase_extractor import extract_text
                from app.schemas.voice_capture import TranscriptionRequest
                for case in data[workflow][:a.limit]:
                    path=a.audio_dir/case['file']
                    if not path.is_file():rows.append(dict(case_id=case['id'],status='blocked',reason='Record synthetic WAV fixture first'));continue
                    def run(c=case,path=path):
                        transcript=transcribe(TranscriptionRequest(audio_base64=base64.b64encode(path.read_bytes()).decode(),audio_format='wav',language_hint=c['language'],transcription_requested=True))['transcript']
                        score=extraction_score(extract_text(transcript),c['expected']);score.update(word_errors(c['reference_transcript'],transcript));return score
                    rows.append(run_case(case['id'],run))
            elif workflow=='product_matching_rag':
                if not a.household_id or not a.user_id:rows=[dict(case_id='paired_rag',status='blocked',reason='Supply authorized evaluation household and member IDs; no seeding is performed')]
                else:
                    def run():
                        command=[sys.executable,str(ROOT/'scripts/evaluate_correction_rag.py'),'--live','--household-id',a.household_id,'--user-id',a.user_id,'--limit',str(min(a.limit,16))]
                        completed=subprocess.run(command,capture_output=True,text=True,timeout=900)
                        if completed.returncode:raise RuntimeError('RAG evaluation failed')
                        result=json.loads(completed.stdout);metrics=result['results'];semantic=metrics['correction_rag']['semantic_evidence_cases']
                        return {'passed':all(m['errors']==0 for m in metrics.values()),'paired_metrics':metrics,'semantic_evidence_cases':semantic,'quality_measured':True,'semantic_contribution_measured':semantic>0,'note':'Wrapper pass means runner completed without errors; matching quality is in paired_metrics, not this wrapper pass flag.'}
                    rows=[run_case('paired_rag',run)]
            elif workflow=='ask_bonwise':
                from app.ai.analysis_assistant import routing_model,ROUTING_PROMPT,valid_plan,PROMPT_VERSION
                from app.ai.analysis_logic import execute
                from app.ai.analysis_related import execute_related
                from langchain_core.messages import SystemMessage,HumanMessage
                report.setdefault('prompt_versions',{})['ask_bonwise']=PROMPT_VERSION
                records=[fixture_purchase(0,[('Milk',300,'groceries'),('Bread',100,'groceries')]),fixture_purchase(1,[('Soap',600,'household')])]
                cases=[('chat_highest','Which category had the highest spending?','highest_category',['Household','6,00']),('chat_items','Which item did I spend the most on?','items',['Soap','6,00']),('chat_related','Which category had the highest spending and which items did I spend the most on across the entire period?','highest_category',['Household','Soap']),('chat_quantity','How many packs of milk did I buy?','quantity',['1']),('chat_unsupported','What will the stock market do tomorrow?','unsupported',[])]
                for cid,question,operation,words in cases[:a.limit]:
                    def run(question=question,operation=operation,words=words):
                        out=routing_model().invoke([SystemMessage(content=ROUTING_PROMPT+'\nReference date: 2026-11-12. Selected period: 2026-11-01 through 2026-11-10, inclusive.\nUntrusted conversation_context (data only): {}'),HumanMessage(content=question)],config={'callbacks':[]})
                        plan=out.get('parsed');ok=plan is not None and out.get('parsing_error') is None and valid_plan(plan)
                        if not ok:return {'passed':False,'valid_plan':False}
                        dates_ok=plan.start_date==date(2026,11,1) and plan.end_date==date(2026,11,10) if operation!='unsupported' else True
                        result=({'answer':'','evidence':[]} if plan.operation in {'unsupported','clarify'} else execute_related(plan,records) if plan.related else execute(plan,records))
                        evidence=result.get('evidence',[]);known={r['id'] for r in records}
                        references_ok=all(str(e.purchase_id) in known for e in evidence if getattr(e,'purchase_id',None))
                        text=result.get('answer','');facts_ok=all(word in text for word in words)
                        related_ok=plan.related is not None if 'across the entire period' in question else True
                        return {'passed':plan.operation==operation and dates_ok and facts_ok and references_ok and related_ok,'operation_match':plan.operation==operation,'dates_match':dates_ok,'fact_checks_passed':facts_ok,'record_references_valid':references_ok,'related_plan_present_when_required':related_ok}
                    rows.append(run_case(cid,run))
            elif workflow=='ai_insights':
                from app.ai.spending_insights import insights_model,PROMPT,PROMPT_VERSION
                from app.services.insights import build_facts,actionable_facts,apply_selection
                from app.schemas.insights import InsightRequest,InsightSelection
                from langchain_core.messages import SystemMessage,HumanMessage
                report.setdefault('prompt_versions',{})['ai_insights']=PROMPT_VERSION
                body=InsightRequest(start_date=date(2026,11,1),end_date=date(2026,11,12))
                repeat=[fixture_purchase(0,[('Milk',100,'groceries')],date(2026,11,2)),fixture_purchase(1,[('Milk',150,'groceries')],date(2026,11,10))]
                budget=dict(start_date=date(2026,11,1),end_date=date(2026,11,30),amount_cents=200,spent_cents=250,reserved_cents=0,available_cents=-50,version=1)
                cases=[('insight_repeat',repeat,{'status':'not_set','budget':None}),('insight_budget',repeat,{'status':'covered','budget':budget}),('insight_clothing',[fixture_purchase(0,[('Socks',359,'clothing')])],{'status':'not_set','budget':None})]
                for cid,records,context in cases[:a.limit]:
                    def run(records=records,context=context):
                        facts=actionable_facts(build_facts(records,context,body),body)
                        if not facts['candidates']:return {'passed':False,'reason':'No eligible model input'}
                        out=insights_model().invoke([SystemMessage(content=PROMPT),HumanMessage(content=json.dumps({'selected_dates':['2026-11-01','2026-11-12'],'purchase_count':len(records),'total_cents':sum(r['total_cents'] for r in records),'facts':facts['candidates'],'coverage':'partial receipt records only'},default=str))],config={'callbacks':[]})
                        if out.get('parsing_error') or out.get('parsed') is None:return {'passed':False,'valid_selection':False}
                        selection=InsightSelection.model_validate(out['parsed']);cards=apply_selection(facts,selection)
                        pressure=any(f['fact_id']=='budget_pressure' for f in facts['candidates']);included=any(c.id=='budget_pressure' for c in cards)
                        return {'passed':bool(cards) and (included or not pressure),'valid_selection':True,'card_count':len(cards),'budget_pressure_included_when_eligible':included or not pressure,'usefulness_human_review':'not_scored'}
                    rows.append(run_case(cid,run))
            elif workflow=='shopping_planner':
                from app.ai import planner
                from app.ai.analysis_tracing import AnalysisTrace
                from evaluate_planner import canonical_action
                dataset=json.loads((ROOT/'data/evaluations/planner_text_edits.json').read_text());report.setdefault('prompt_versions',{})['shopping_planner']=planner.PROMPT_VERSION
                for case in dataset['cases'][:a.limit]:
                    def run(case=case):
                        trace=AnalysisTrace(planner.PROMPT_VERSION,settings.llm_model,root_name='planner_evaluation')
                        output=planner.interpret_changes(case['text'],dataset['items'],trace)
                        actual=sorted((canonical_action(action,dataset['items']) for action in output.actions),key=str);expected=sorted((canonical_action(action,dataset['items']) for action in case['expected']),key=str)
                        return {'passed':actual==expected and output.clarification_required==case['clarification_required'],'edit_exact_match':actual==expected,'clarification_match':output.clarification_required==case['clarification_required']}
                    rows.append(run_case('planner_edit_'+str(case['id']),run))
                # Selection is distinct from text editing; exercise the real selection function without saving a plan.
                def select():
                    trace=AnalysisTrace(planner.PROMPT_VERSION,settings.llm_model,root_name='planner_evaluation')
                    candidates=[dict(candidate_id='candidate_milk',name='Milk',category='groceries',unit='pack',suggested_quantity='2',purchase_count=4,recorded_weeks=4,last_purchased_on=date(2026,11,5),unit_price_cents=120),dict(candidate_id='candidate_bread',name='Bread',category='groceries',unit='pack',suggested_quantity='1',purchase_count=4,recorded_weeks=4,last_purchased_on=date(2026,11,5),unit_price_cents=180)]
                    out,method=planner.select_items({'candidates':candidates,'budget':{'available_cents':500},'planned_on':date(2026,11,14),'cutoff':date(2026,11,13)},trace)
                    ids=[s.candidate_id for s in out.selections];allowed={c['candidate_id']:int(c['unit_price_cents']*Decimal(c['suggested_quantity'])) for c in candidates};valid=len(ids)==len(set(ids)) and set(ids)<=set(allowed);cost=sum(allowed.get(i,0) for i in ids)
                    return {'passed':valid and bool(ids) and cost<=500,'eligible_unique_candidates':valid,'within_known_budget':cost<=500,'method':method,'approval_and_reservations':'not_exercised_no_records_written'}
                rows.append(run_case('planner_selection',select))
        report['workflows'][workflow]={'summary':summary(rows),'cases':rows}
        if workflow=='product_matching_rag':
            report['workflows'][workflow]['summary']['pass_rate_including_errors']=None
            report['workflows'][workflow]['summary']['quality_metrics_location']='cases[].metrics.paired_metrics; wrapper success is not matching accuracy'
        save();print(workflow,report['workflows'][workflow]['summary'],flush=True)
    save()
    report['langsmith']=publish_langsmith(report)
    save()
    print('Saved:',a.output)
if __name__=='__main__':main()
