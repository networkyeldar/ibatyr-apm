from datetime import datetime,timezone,timedelta
import io,tarfile,importlib.util
from pathlib import Path
import pytest
from fastapi import FastAPI,HTTPException
from fastapi.testclient import TestClient
from trace_api import normalize_trace,duration_window,register_traces
from artifacts import extract
TZ=timezone(timedelta(hours=5))

def span(i,a,b,segment='one',parent=0,**kw):
    return dict(segmentId=segment,spanId=i,parentSpanId=parent,startTime=a,endTime=b,serviceCode='Demo',type='Exit',layer='Database',tags=[],logs=[],**kw)

def test_overlap_clipping_and_cross_segment():
    root=span(0,10000,20000,parent=-1);root.update(type='Entry',layer='Http')
    raw=[root,span(1,9000,14000),span(2,12000,16000),span(3,18000,22000),span(4,16000,18000,segment='other')]
    data=normalize_trace('t',raw,TZ);entry=data['entries'][0]
    assert entry['covered_by_children_ms']==8000
    assert entry['not_covered_by_children_ms']==2000
    assert entry['direct_child_count']==3
    assert data['span_count']==5

def test_errors_sql_and_invalid_span():
    s=span(1,10000,20000);s.update(tags=[{'key':'db.statement','value':'SELECT ?'},{'key':'db.instance','value':'demo'}],isError=True,logs=[{'time':20000,'data':[{'key':'error.kind','value':'Timeout'},{'key':'message','value':'lock wait'}]}])
    d=normalize_trace('t',[s,s,span(2,40,20)],TZ)
    assert d['span_count']==1 and d['database_span_count']==1
    assert d['spans'][0]['sql']=='SELECT ?'
    assert d['spans'][0]['error_events'][0]['kind']=='Timeout'
    assert not d['has_http_entry_in_trace']

def test_window_seconds_exclusive_and_timezone():
    a=datetime.fromisoformat('2026-09-30T12:30:15+05:00');b=datetime.fromisoformat('2026-09-30T12:31:00+05:00')
    assert duration_window(a,b,TZ)=={'start':'2026-09-30 1230','end':'2026-09-30 1230','step':'MINUTE'}
    with pytest.raises(HTTPException):duration_window(a.replace(tzinfo=None),b,TZ)
    with pytest.raises(HTTPException):duration_window(b,a,TZ)


def test_pagination_preserves_oap_page_and_filters_boundary():
    base=datetime.fromisoformat('2026-09-30T12:30:00+05:00');ms=int(base.timestamp()*1000);calls=[]
    async def oap(q,v=None):
        if 'getTimeInfo' in q:return {'getTimeInfo':{'timezone':'+0500'}}
        calls.append(v)
        def row(t):return {'segmentId':str(t),'start':str(t),'duration':1200,'traceIds':['t'],'endpointNames':['/demo'],'isError':False}
        return {'queryBasicTraces':{'traces':[row(ms),row(ms+15000),row(ms+60000)]}}
    app=FastAPI();register_traces(app,oap)
    with TestClient(app) as c:
        r=c.get('/api/ai/traces',params={'start':'2026-09-30T12:30:15+05:00','end':'2026-09-30T12:31:00+05:00','service_id':'demo','page':2,'page_size':3})
    assert r.status_code==200
    d=r.json();assert len(d['records'])==1
    assert d['pagination']['outside_window_removed']==2 and d['pagination']['may_have_more']
    assert calls[0]['condition']['paging']['pageNum']==2

@pytest.mark.parametrize('name,kind',[('../outside',tarfile.REGTYPE),('/absolute',tarfile.REGTYPE),('link',tarfile.SYMTYPE)])
def test_archive_escape_rejected(tmp_path,name,kind):
    file=tmp_path/'bad.tar.gz'
    with tarfile.open(file,'w:gz') as t:
        member=tarfile.TarInfo(name);member.type=kind;member.linkname='/etc/passwd';t.addfile(member,io.BytesIO())
    with pytest.raises(RuntimeError):extract(file,tmp_path/'out')

def test_free_port_and_env_injection_rejected():
    import socket
    spec=importlib.util.spec_from_file_location('installer',Path(__file__).resolve().parents[1]/'install.py');mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    with socket.socket() as s:
        s.bind(('127.0.0.1',0));s.listen()
        with pytest.raises(RuntimeError):mod.free(s.getsockname()[1])
    with pytest.raises(RuntimeError):mod.envtext({'VALUE':'safe\nEVIL=1'})
