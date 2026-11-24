"""Read-only local connectivity test. Never prints the database URL or credentials."""
import argparse,json,socket,sys
from datetime import datetime,timezone
from pathlib import Path
from time import perf_counter
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,default=Path('evaluation-results/database-connectivity.json'));a=p.parse_args()
    from app.core.config import get_settings
    from sqlalchemy.engine import make_url
    s=get_settings();url=make_url(s.database_url.get_secret_value());port=url.port or 5432
    result={'schema_version':1,'measured_at':datetime.now(timezone.utc).isoformat(),'dns':'not_run','tcp':[],'select_one':'not_run','note':'No credentials, hostnames, SQL parameters or exception messages are stored. TCP reachability alone does not establish authentication or database health.'}
    try:
        addresses=socket.getaddrinfo(url.host,port,type=socket.SOCK_STREAM);result['dns']='resolved'
        seen=set()
        for family,kind,protocol,_,address in addresses:
            if address in seen:continue
            seen.add(address);start=perf_counter()
            try:
                with socket.socket(family,kind,protocol) as conn:conn.settimeout(3);conn.connect(address)
                status='reachable'
            except OSError:status='unreachable_or_timed_out'
            result['tcp'].append({'address_number':len(seen),'status':status,'ms':round((perf_counter()-start)*1000,2)})
            if len(seen)>=4:break
        if any(row['status']=='reachable' for row in result['tcp']):
            from app.db.session import make_db_engine
            from sqlalchemy import text
            engine=make_db_engine(s.database_url,pool_size=0);start=perf_counter()
            try:
                with engine.connect() as conn:ok=conn.scalar(text('SELECT 1'))==1
                result['select_one']='passed' if ok else 'unexpected_result'
            except Exception as exc:result['select_one']='failed';result['error_class']=type(exc).__name__
            finally:result['connection_and_query_ms']=round((perf_counter()-start)*1000,2);engine.dispose()
    except OSError:result['dns']='failed'
    a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))
    return 0 if result['select_one']=='passed' else 1
if __name__=='__main__':raise SystemExit(main())
