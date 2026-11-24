"""Compare matching read-only benchmark scenarios without manufacturing speedups."""
import argparse,json
from pathlib import Path
from statistics import median

def compare(before,after):
    reasons=[]
    for key in ('frontend_origin','api_origin','authenticated'):
        if before.get(key)!=after.get(key):reasons.append(key+' differs')
    rows=[]
    for name,b in before['scenarios'].items():
        if name not in after['scenarios']:continue
        a=after['scenarios'][name]
        good_b=[r['total_ms'] for r in b['observations'] if r.get('success')]
        good_a=[r['total_ms'] for r in a['observations'] if r.get('success')]
        bm,am=(median(x) if x else None for x in (good_b,good_a))
        row=dict(scenario=name,before_success=len(good_b),before_total=len(b['observations']),after_success=len(good_a),after_total=len(a['observations']),before_median_ms=bm,after_median_ms=am)
        comparable=not reasons and bool(good_a) and bool(good_b) and len(good_a)==len(a['observations']) and len(good_b)==len(b['observations'])
        if name=='home_overview' and before.get('home_overview_includes_offers',True)!=after.get('home_overview_includes_offers',True):
            comparable=False;row['note']='Different work: ledger-only versus combined offers. Report user-visible separation, not an equivalent-endpoint speedup.'
        row['comparable']=comparable
        row['median_reduction_percent']=round(100*(bm-am)/bm,2) if comparable and bm else None
        rows.append(row)
    return dict(schema_version=1,warnings=reasons+['Confirm same household, dates, data revision, machine and connection separately; identifiers are intentionally absent from the benchmark.','Five-sample medians are observations, not reliable tail-latency estimates.'],scenarios=rows)

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('before',type=Path);p.add_argument('after',type=Path);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    result=compare(json.loads(a.before.read_text()),json.loads(a.after.read_text()));a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(result,indent=2)+'\n');print('Saved comparison:',a.output)
if __name__=='__main__':main()
