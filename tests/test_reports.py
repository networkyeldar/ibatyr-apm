from io import BytesIO
import json
from datetime import datetime,timedelta,timezone

from fastapi import FastAPI,HTTPException
from fastapi.testclient import TestClient
from pypdf import PdfReader
import pytest
import ai_routes
from ai_security import write_config
from frontend_routes import register_frontend
from jvm_routes import METRICS
from test_ai import make_config,login,TRACE
from license_fixture import provision

START=datetime(2026,9,30,12,0,tzinfo=timezone(timedelta(hours=5)))


@pytest.fixture
def reports_client(tmp_path,monkeypatch):
    calls=[]
    async def oap(query,variables=None):
        if 'listServices' in query:return {'getTimeInfo':{'timezone':'+0500'},'listServices':[{'id':'service1','name':'PrivateName'}]}
        if 'getTimeInfo' in query:return {'getTimeInfo':{'timezone':'+0500'}}
        if 'getServiceInstances' in query:return {'getServiceInstances':[{'id':'jvm1','name':'private-host'}]}
        if 'getAlarm' in query:return {'getAlarm':{'msgs':[]}}
        d=variables['duration'];begin=datetime.strptime(d['start'],'%Y-%m-%d %H%M').replace(tzinfo=START.tzinfo)
        end=datetime.strptime(d['end'],'%Y-%m-%d %H%M').replace(tzinfo=START.tzinfo)
        def metric(value,p=None):
            points=[{'id':str(int((begin+timedelta(minutes=i)).timestamp()*1000)),'value':str(value)} for i in range(int((end-begin).total_seconds()/60)+1)]
            return {'type':'TIME_SERIES_VALUES','error':None,'results':[{'metric':{'labels':[{'key':'p','value':p}] if p else []},'values':points}]}
        if 'query JVM' in query:
            return {key:metric({'cpu_percent':18,'heap_bytes':20*1024**3,'heap_max_bytes':30*1024**3,'threads_live':2900,'threads_blocked':69}.get(key,0)) for key in METRICS}
        return {'latency':metric(500),'traffic':metric(1000),'success':metric(9900),'percentiles':metric(1500,'95')}
    async def complete(profile,messages,test=False):
        packet=json.loads(messages[-1]['content']);calls.append(packet)
        return json.dumps({'summary':'Наблюдаются задержки; причина требует проверки.','impact':'Влияние ограничено выбранным периодом.',
            'findings':[{'title':'Подтверждённое наблюдение','interpretation':'Сопоставить метрики и интервалы ожидания. <script>alert(1)</script>','evidence_ids':['E001']}],
            'hypotheses':['Причина блокировок требует thread dump.'],'next_checks':['P1: сопоставить trace и время GC; проверить совпадение.'],
            'limitations':['Полнота трассировки неизвестна.'],'conclusion':'Измерения подтверждены, причина не установлена.'},ensure_ascii=False),{'total_tokens':500}
    monkeypatch.setattr(ai_routes,'completion',complete)
    path=tmp_path/'config.json';make_config(path);app=FastAPI()
    @app.get('/api/ai/traces/{trace_id}')
    async def trace(trace_id:str):return TRACE
    register_frontend(app);ai_routes.register_ai_features(app,oap,path)
    _,doc=provision(app.state.licensing);app.state.licensing.activate(doc)
    with TestClient(app) as c:yield c,calls


def trace_body():return {'trace_id':'trace-1','provider':'external','request_id':'unique-request-1'}


def test_direct_trace_one_call_idempotency_auth_pdf_owner(reports_client,tmp_path):
    c,calls=reports_client
    assert c.post('/api/ai/llm/trace-analysis',json=trace_body()).status_code==401
    h=login(c)
    assert c.post('/api/ai/llm/trace-analysis',json=trace_body()).status_code==403
    r=c.post('/api/ai/llm/trace-analysis',headers=h,json=trace_body());assert r.status_code==200,r.text
    data=r.json();assert len(calls)==1 and data['report_id']
    text=json.dumps(calls[0]);assert 'secret-doc-id' not in text and 'private-host' not in text
    assert any(row['kind']=='sql_group' for row in calls[0]['evidence'])
    assert c.post('/api/ai/llm/trace-analysis',headers=h,json=trace_body()).json()['report_id']==data['report_id']
    assert len(calls)==1
    assert c.post('/api/ai/llm/trace-analysis',headers=h,json={**trace_body(),'question':'new'}).status_code==409
    pdf=c.get('/api/ai/reports/'+data['report_id']+'.pdf');assert pdf.status_code==200
    assert pdf.content.startswith(b'%PDF') and pdf.headers['content-type']=='application/pdf'
    reader=PdfReader(BytesIO(pdf.content));extracted='\n'.join(page.extract_text() for page in reader.pages)
    assert 'Подтверждённое наблюдение' in extracted and 'E001' in extracted
    assert '<script>alert(1)</script>' in extracted
    login(c)
    assert c.get('/api/ai/reports/'+data['report_id']+'.pdf').status_code==404


def test_overview_all_metrics_period_pdf_and_private_names(reports_client):
    c,calls=reports_client;h=login(c)
    body={'start':START.isoformat(),'end':(START+timedelta(days=1)).isoformat(),'service_id':'service1','instance_id':'jvm1','provider':'local','request_id':'overview-123'}
    r=c.post('/api/ai/llm/overview-analysis',headers=h,json=body);assert r.status_code==200,r.text
    data=r.json();assert data['metadata']['instance']['id']=='jvm1'
    packet=calls[0];assert packet['period']['start']==START.isoformat()
    text=json.dumps(packet);assert 'PrivateName' not in text and 'private-host' not in text
    metrics={row['metric'] for row in packet['evidence'] if row['kind']=='metric_summary'}
    assert {'p95_ms','cpu_percent','threads_blocked','normal_gc_ms','classes_loaded'}<=metrics
    assert any(row['kind']=='aligned_minute' for row in packet['evidence'])
    pdf=c.get('/api/ai/reports/'+data['report_id']+'.pdf');assert pdf.status_code==200
    assert len(PdfReader(BytesIO(pdf.content)).pages)>3
    from pathlib import Path
    Path('/tmp/ibatyr-overview-report.pdf').write_bytes(pdf.content)


def test_overview_invalid_period_no_llm(reports_client):
    c,calls=reports_client;h=login(c)
    body={'start':START.isoformat(),'end':(START+timedelta(days=2)).isoformat(),'service_id':'service1','provider':'local','request_id':'invalid-request'}
    r=c.post('/api/ai/llm/overview-analysis',headers=h,json=body);assert r.status_code==422,r.text
    assert not calls


def test_direct_requires_license(reports_client):
    c,calls=reports_client;h=login(c)
    c.app.state.licensing.require_ai=lambda:(_ for _ in ()).throw(HTTPException(403,'license expired'))
    assert c.post('/api/ai/llm/trace-analysis',headers=h,json=trace_body()).status_code==403
    assert not calls
