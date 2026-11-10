"""Paired catalogue/exact-memory baseline versus added semantic correction RAG.

Default validates synthetic labels only: zero provider calls. Live uses existing
household history read-only; labels are never supplied to retrieval or the LLM.
"""
import argparse
import json
import sys
from pathlib import Path
from time import perf_counter
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter
from app.schemas.product_matching import ProductSuggestionInput


class Case(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    case_id: str = Field(pattern=r"^case_[0-9]{2}$")
    input: ProductSuggestionInput
    expected_product_codes: list[str] = Field(max_length=10)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases',type=Path,default=Path(__file__).resolve().parents[1]/'data/evaluations/correction_rag_cases.json')
    parser.add_argument('--live',action='store_true')
    parser.add_argument('--household-id',type=UUID)
    parser.add_argument('--user-id',type=UUID)
    parser.add_argument('--limit',type=int,default=5)
    args=parser.parse_args()
    try:
        cases=TypeAdapter(list[Case]).validate_json(args.cases.read_text())
    except Exception:
        raise SystemExit("Could not validate the labelled case file. Use the supplied synthetic case schema.") from None
    if not cases or len(cases)>100 or len({c.case_id for c in cases})!=len(cases):parser.error('Provide 1–100 unique labelled cases.')
    if not 1<=args.limit<=20:parser.error('Use --limit between 1 and 20.')
    if not args.live:
        print(json.dumps(dict(live=False,labelled_cases=len(cases),provider_calls=0,quality_measured=False),indent=2));return
    if not args.household_id or not args.user_id:parser.error('Live evaluation requires household and member IDs.')
    from sqlalchemy import select
    from app.db.session import SessionLocal
    from app.db.models import Product
    from app.services.purchases import require_member
    from app.ai.product_matching import suggest_products
    from app.ai.analysis_tracing import AnalysisTrace
    from app.core.config import get_settings
    with SessionLocal() as db,db.begin():
        require_member(db,args.household_id,args.user_id)
        codes={str(p.id):(p.attributes or {}).get('code') for p in db.scalars(select(Product)).all()}
    selected=cases[:args.limit]
    expected={code for case in selected for code in case.expected_product_codes}
    if not expected<=set(codes.values()):parser.error('Some labelled catalogue products are not imported. Import the supplied catalogues or review the case file.')
    report={}
    for mode,enabled in [('baseline',False),('correction_rag',True)]:
        totals=dict(cases=len(selected),positive_cases=0,negative_cases=0,hit1=0,hit3=0,correct_abstentions=0,suggested=0,correct_suggestions=0,semantic_evidence_cases=0,errors=0,latency_ms=0)
        for index,case in enumerate(selected,1):
            totals['positive_cases' if case.expected_product_codes else 'negative_cases']+=1
            start=perf_counter()
            try:
                with SessionLocal() as db:result=suggest_products(db,args.household_id,args.user_id,case.input,use_corrections=enabled)
                returned=[codes.get(str(c.product_id)) for c in result.candidates]
                if case.expected_product_codes:
                    totals['hit1']+=bool(returned and returned[0] in case.expected_product_codes)
                    totals['hit3']+=any(code in case.expected_product_codes for code in returned[:3])
                else:totals['correct_abstentions']+=result.status=='no_match'
                totals['semantic_evidence_cases']+=any(c.match_origin=='correction_rag' for c in result.candidates)
                if result.status=='suggested':
                    totals['suggested']+=1
                    totals['correct_suggestions']+=codes.get(str(result.suggested_product_id)) in case.expected_product_codes
            except Exception:
                totals['errors']+=1
            totals['latency_ms']+=round((perf_counter()-start)*1000)
            print(f'{mode}: {index}/{len(selected)} completed',file=sys.stderr,flush=True)
        totals['hit_at_1']=totals['hit1']/totals['positive_cases'] if totals['positive_cases'] else None
        totals['hit_at_3']=totals['hit3']/totals['positive_cases'] if totals['positive_cases'] else None
        totals['suggestion_precision']=totals['correct_suggestions']/totals['suggested'] if totals['suggested'] else None
        totals['mean_latency_ms']=round(totals['latency_ms']/totals['cases'])
        report[mode]=totals
    trace=AnalysisTrace('correction-evaluation-v1',get_settings().llm_model,root_name='correction_evaluation');trace.finish()
    trace.payload[0]['extra']['metadata']['metrics']={f'{mode}_{key}':value for mode,metrics in report.items() for key,value in metrics.items() if type(value) in (int,float)}
    trace.send()
    print(json.dumps(dict(live=True,quality_measured=True,seeded_by_this_script=False,
        note='Small synthetic benchmark. Both modes retain exact SQL memory. Zero semantic evidence cases means the correction route was not exercised; do not claim a RAG improvement.',results=report),indent=2))

if __name__=='__main__':main()
