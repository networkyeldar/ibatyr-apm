"""Read-only reachability and schema check. No introspection bypass or credentials printed."""
import argparse,json,sys
from urllib.request import Request,urlopen
p=argparse.ArgumentParser();p.add_argument('--oap',default='http://127.0.0.1:12800/graphql');p.add_argument('--shell',default='http://127.0.0.1:8010');a=p.parse_args()
def query(q,variables=None):
    req=Request(a.oap,data=json.dumps({'query':q,'variables':variables or {}}).encode(),headers={'Content-Type':'application/json'})
    with urlopen(req,timeout=10) as r:data=json.load(r)
    if data.get('errors'):raise RuntimeError('GraphQL errors: '+json.dumps(data['errors'],ensure_ascii=False))
    return data['data']
try:
    data=query('query { version getTimeInfo { timezone currentTimestamp } listServices(layer:"GENERAL"){id name} }')
    print('OAP version:',data['version'],'timezone:',data['getTimeInfo']['timezone'],'GENERAL services:',len(data['listServices']))
    required={'TraceQueryCondition':{'serviceId','queryDuration','minTraceDuration','traceState','queryOrder','paging'},'Pagination':{'pageNum','pageSize'},'Span':{'segmentId','spanId','startTime','endTime','tags','logs'}}
    for name,fields in required.items():
        v=query('query($n:String!){__type(name:$n){fields{name} inputFields{name}}}',{'n':name})['__type'] or {}
        got={f['name'] for f in (v.get('fields') or [])+(v.get('inputFields') or [])}
        if fields-got:raise RuntimeError(name+' missing fields: '+str(fields-got))
        print(name+': OK')
    with urlopen(a.shell.rstrip('/')+'/ready',timeout=10) as r:ready=json.load(r)
    if ready.get('status')!='ok':raise RuntimeError('Shell not ready')
    print('Shell readiness: OK. Login, metrics, one trace and AI must be checked in browser.')
except Exception as e:print('CHECK FAILED:',str(e));sys.exit(1)
