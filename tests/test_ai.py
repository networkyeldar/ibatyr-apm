import importlib
import json
from pathlib import Path
import sys
import time

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'shell'))
import ai_routes
from ai_security import password_hash, write_config
from ai_evidence import safe_sql, build_evidence
from frontend_routes import register_frontend

PASSWORD = 'test-password-only-2026'
SALT = '00112233445566778899aabbccddeeff'
HASH = password_hash(PASSWORD, SALT)
TRACE = {'trace_id':'trace-1', 'services':['DemoApp'], 'span_count':3, 'database_span_count':2, 'error_span_count':2, 'has_http_entry_in_trace':True,
 'entries':[{'segment_id':'segment-1','span_id':0,'service':'DemoApp','operation':'/api/workflow/work/get_form_for_result','http_status':'200','entry_has_error':False,'duration_ms':1052255,'covered_by_children_ms':183512,'not_covered_by_children_ms':868743,'longest_uncovered_intervals':[{'start':'2026-09-30T12:43:41.463+05:00','end':'2026-09-30T12:54:38.457+05:00','duration_ms':656994}]}],
 'spans':[{'segment_id':'segment-1','span_id':54,'service':'DemoApp','instance':'private-host','operation':'Mysql/JDBC/execute','type':'Exit','layer':'Database','duration_ms':90861,'start':'2026-09-30T12:42:10.380+05:00','peer':'192.0.2.20:3306','is_database':True,'database':'synergy','sql':"DELETE FROM registry_docs_filters WHERE documentId = 'secret-doc-id'",'has_error':True,'error_events':[{'kind':'MySQLTransactionRollbackException','message':'Lock wait timeout exceeded; private@example.com'}]},
 {'segment_id':'segment-1','span_id':113,'service':'DemoApp','instance':'private-host','operation':'Mysql/JDBC/execute','type':'Exit','layer':'Database','duration_ms':90869,'start':'2026-09-30T12:54:38.457+05:00','peer':'192.0.2.20:3306','is_database':True,'database':'synergy','sql':'DELETE FROM registry_docs_filters WHERE documentId = ?','has_error':True,'error_events':[{'kind':'MySQLTransactionRollbackException','message':'Lock wait timeout exceeded'}]},
 {'segment_id':'segment-1','span_id':0,'service':'DemoApp','type':'Entry','layer':'Http','operation':'/api/workflow/work/get_form_for_result','duration_ms':1052255,'start':'2026-09-30T12:38:37.407+05:00','is_database':False,'has_error':False,'error_events':[]}], 'warnings':['Полнота трассировки не подтверждена.']}


def make_config(path):
    profile={'base_url':'https://example.test/v1','model':'test-model','api_key':'NEVER-RETURN-THIS-KEY','auth_mode':'bearer','token_parameter':'max_tokens','max_tokens':2048}
    write_config(path,{'username':'devadmin','password_salt':SALT,'password_hash':HASH,'providers':{'external':profile,'local':{**profile,'base_url':'http://127.0.0.1:8000/v1','model':'RedHatAI/gemma-4-26B-A4B-it-NVFP4'}}})


async def fake_oap(query, variables=None):
    if 'listServices' in query:
        return {'getTimeInfo':{'timezone':'+0500'},'listServices':[{'id':'service=1','name':'DemoApp'}]}
    start=1790753400000
    def metric(vals, p=None):
        return {'type':'TIME_SERIES_VALUES','error':None,'results':[{'metric':{'labels':[{'key':'p','value':p}] if p else []},'values':[{'id':str(start+i*60000),'value':str(v)} for i,v in enumerate(vals)]}]}
    return {'latency':metric([100,200,0]),'traffic':metric([10,30,0]),'success':metric([10000,9000,0]),'percentiles':metric([300,600,0],'95')}


def make_app(config, licensed=True):
    app=FastAPI()
    @app.get('/api/ai/services')
    async def services():return {'services':[{'id':'service=1','name':'DemoApp'}]}
    @app.get('/api/ai/traces')
    async def traces(page:int=1):
        return {'records':[{'segment_id':'segment-1','trace_ids':['trace-1'],'endpoints':['/api/workflow/work/get_form_for_result'],'start':'2026-09-30T12:38:37.407+05:00','duration_ms':1052255,'has_error':True}] if page==1 else [],'period':{'start':'2026-09-30T12:30:00+05:00','end_exclusive':'2026-09-30T13:36:00+05:00'},'pagination':{'may_have_more':page==1},'warnings':['Выборка, не все вызовы.']}
    @app.get('/api/ai/traces/{trace_id}')
    async def details(trace_id:str):return TRACE
    register_frontend(app)
    ai_routes.register_ai_features(app,fake_oap,config)
    if licensed:
        from license_fixture import provision
        _, doc = provision(app.state.licensing)
        app.state.licensing.activate(doc)
    return app


@pytest.fixture
def client(tmp_path):
    config=tmp_path/'config.json';make_config(config)
    with TestClient(make_app(config)) as client:
        yield client,config


def login(client):
    response=client.post('/api/ai/auth/login',json={'username':'devadmin','password':PASSWORD})
    assert response.status_code==200
    return {'X-CSRF-Token':response.json()['csrf_token']}


def test_auth_csrf_and_logout(client):
    c,_=client
    assert c.get('/api/ai/services').status_code==401
    assert c.get('/ai/').status_code==200
    assert c.get('/ai/assets/app.js').status_code==200
    assert c.post('/api/ai/auth/login',headers={'origin':'https://evil.test'},json={'username':'devadmin','password':PASSWORD}).status_code==403
    h=login(c)
    assert c.get('/api/ai/services').status_code==200
    assert 'httponly' in c.cookies.jar._cookies['testserver.local']['/']['swai_session']._rest.keys().__str__().lower()
    assert c.post('/api/ai/llm/providers/local/test').status_code==403
    assert c.post('/api/ai/auth/logout',headers=h).status_code==200
    assert c.get('/api/ai/services').status_code==401


def test_provider_keys_and_address_change(client):
    c,path=client;h=login(c)
    response=c.get('/api/ai/llm/providers')
    assert 'NEVER-RETURN' not in response.text
    assert response.json()['providers'][0]['has_key']
    body={'base_url':'https://example.test/v1','model':'new-model'}
    assert c.put('/api/ai/llm/providers/external',json=body,headers=h).status_code==200
    assert json.loads(path.read_text())['providers']['external']['api_key']=='NEVER-RETURN-THIS-KEY'
    body['base_url']='https://different.test/v1'
    assert c.put('/api/ai/llm/providers/external',json=body,headers=h).status_code==422
    body['api_key']='replacement-key'
    assert c.put('/api/ai/llm/providers/external',json=body,headers=h).status_code==200
    body['base_url']='http://example.test/v1'
    assert c.put('/api/ai/llm/providers/external',json=body,headers=h).status_code==422
    body['base_url']='http://127.0.0.1:8000/v1';body['auth_mode']='none'
    assert c.put('/api/ai/llm/providers/local',json=body,headers=h).status_code==200
    assert (path.stat().st_mode&0o777)==0o600


@pytest.mark.parametrize('sql', ["SELECT * FROM t WHERE secret='abc' AND x=123",'SELECT * FROM t WHERE token=0xABCD',"SELECT * FROM t WHERE x='O\\'Reilly' /* sensitive comment */",'SELECT * FROM t WHERE email="user@example.com"'])
def test_sql_redaction(sql):
    value=safe_sql(sql)
    assert value and '?' in value
    assert all(secret not in value for secret in ('abc','123','ABCD','Reilly','sensitive','user@example'))


def test_packet_minimization():
    packet,links=build_evidence(TRACE,'Разбери задержку')
    text=json.dumps(packet)
    for secret in ('DemoApp','secret-doc-id','private@example','100.72','private-host','/api/workflow'):
        assert secret not in text
    assert 'db_lock_wait_timeout' in text
    assert links['E001']['span_id']==0
    assert safe_sql('XA START 0xABC,0xDEF,0x20005') is None


def test_ai_snapshot_flow(client,monkeypatch):
    c,_=client;h=login(c);calls=[]
    async def fake(profile,messages,test=False):
        calls.append((profile,messages))
        return json.dumps({'summary':'Наблюдаются ошибки ожидания блокировки.','findings':[{'title':'SQL timeout','interpretation':'Проверить блокирующую транзакцию','evidence_ids':['E002']}],'hypotheses':['Причина непокрытого времени неизвестна.'],'next_checks':['Сопоставить журналы приложения.']}),{'total_tokens':90}
    monkeypatch.setattr(ai_routes,'completion',fake)
    preview=c.post('/api/ai/llm/preview',headers=h,json={'trace_id':'trace-1','provider':'local'})
    assert preview.status_code==200
    assert not calls
    assert 'secret-doc-id' not in json.dumps(preview.json()['packet'])
    body={'snapshot_id':preview.json()['snapshot_id']}
    result=c.post('/api/ai/llm/analyze',headers=h,json=body)
    assert result.status_code==200,result.text
    assert len(calls)==1 and result.json()['provider']=='local'
    assert c.post('/api/ai/llm/analyze',headers=h,json=body).status_code==409
    assert 'NEVER-RETURN' not in result.text


def test_config_change_invalidates_preview(client):
    c,_=client;h=login(c)
    p=c.post('/api/ai/llm/preview',headers=h,json={'trace_id':'trace-1','provider':'external'}).json()
    c.put('/api/ai/llm/providers/external',headers=h,json={'base_url':'https://example.test/v1','model':'changed'})
    assert c.post('/api/ai/llm/analyze',headers=h,json={'snapshot_id':p['snapshot_id']}).status_code==409


def test_unknown_evidence_rejected():
    text=json.dumps({'summary':'test','findings':[{'title':'x','interpretation':'y','evidence_ids':['E999']}],'hypotheses':[],'next_checks':[]})
    with pytest.raises(HTTPException):ai_routes.parse_analysis(text,{'E001':{}})


def test_dashboard_period_and_gaps(client):
    c,_=client;login(c)
    response=c.get('/api/ai/dashboard',params={'start':'2026-09-30T12:30:00+05:00','end':'2026-09-30T12:33:00+05:00','service_id':'service=1'})
    assert response.status_code==200,response.text
    data=response.json()
    assert len(data['points'])==3 and data['points'][2]['calls_per_minute'] is None
    assert data['kpis']['estimated_mean_latency_ms']==175
    assert data['kpis']['estimated_error_rate_percent']==7.5
    assert data['coverage']['minutes_with_positive_traffic']==2


def test_auth_throttling(client):
    c,_=client
    for _ in range(5):assert c.post('/api/ai/auth/login',json={'username':'devadmin','password':'wrong'}).status_code==401
    assert c.post('/api/ai/auth/login',json={'username':'devadmin','password':'wrong'}).status_code==429


def test_completion_auth_and_wire_protocol(monkeypatch):
    import asyncio
    calls=[]
    def handler(request):
        calls.append(request)
        return httpx.Response(200,json={'choices':[{'message':{'content':'OK'},'finish_reason':'stop'}],'usage':{'total_tokens':2,'secret':'not-returned'}})
    original=httpx.AsyncClient
    monkeypatch.setattr(ai_routes.httpx,'AsyncClient',lambda **kw:original(transport=httpx.MockTransport(handler),**kw))
    profile={'base_url':'https://example.test/v1','model':'test-model','api_key':'a-secret','auth_mode':'bearer','token_parameter':'max_completion_tokens','max_tokens':4096}
    text,usage=asyncio.run(ai_routes.completion(profile,[{'role':'user','content':'OK'}],test=True))
    assert str(calls[0].url)=='https://example.test/v1/chat/completions'
    assert calls[0].headers['authorization']=='Bearer a-secret'
    assert json.loads(calls[0].content)['max_completion_tokens']==128
    assert text=='OK' and usage=={'total_tokens':2}


def test_snapshot_cannot_be_used_by_another_session(client):
    c,_=client;h=login(c)
    p=c.post('/api/ai/llm/preview',headers=h,json={'trace_id':'trace-1','provider':'local'}).json()
    h=login(c)
    assert c.post('/api/ai/llm/analyze',headers=h,json={'snapshot_id':p['snapshot_id']}).status_code==404
