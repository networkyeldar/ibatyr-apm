from datetime import datetime, timezone
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from alert_routes import register_alerts, normalize_alarm
from elastic_install import configuration, validate, role

BASE = int(datetime(2026, 10, 1, tzinfo=timezone.utc).timestamp()*1000)
def alarm(stamp=BASE, level='CRITICAL', message='Database lock timeout'):
    return {'id':'service-id','scope':'Service','startTime':stamp,'message':message,'tags':[] if level is None else [{'key':'level','value':level}]}


def test_unknown_is_not_critical_and_severity_not_inferred():
    a=normalize_alarm(alarm(level=None,message='CRITICAL failure'),timezone.utc)
    assert a['severity']=='UNKNOWN' and a['lifecycle']=='unknown'
    assert normalize_alarm(alarm(level='critical'),timezone.utc)['severity']=='CRITICAL'


def test_alarm_exclusive_end_pagination_filter_and_dedup():
    calls=[]
    async def oap(q,v=None):
        if 'getTimeInfo' in q:return {'getTimeInfo':{'timezone':'+0500'}}
        calls.append(v)
        return {'getAlarm':{'msgs':[alarm(),alarm(),alarm(BASE+60000),alarm(level=None),alarm(stamp='bad')]}}
    app=FastAPI();register_alerts(app,oap)
    with TestClient(app) as c:
        r=c.get('/api/ai/alerts',params={'start':'2026-10-01T05:00:00+05:00','end':'2026-10-01T05:01:00+05:00','page_size':5,'page':2})
    assert r.status_code==200
    d=r.json();assert len(d['records'])==1
    assert d['pagination']['may_have_more'] and d['pagination']['invalid']==1
    assert d['pagination']['outside_window_removed']==1
    assert calls[0]['tags']==[{'key':'level','value':'CRITICAL'}]
    assert calls[0]['paging']['pageNum']==2
    assert calls[0]['duration']['end']=='2026-10-01 0500'


@pytest.mark.parametrize('start,end', [('2026-10-01T00:00:00','2026-10-01T01:00:00'),('2026-10-01T00:00:00Z','2026-10-03T00:00:00Z')])
def test_bad_dates_do_not_call_oap(start,end):
    async def oap(*args):raise AssertionError('must validate first')
    app=FastAPI();register_alerts(app,oap)
    with TestClient(app) as c:assert c.get('/api/ai/alerts',params={'start':start,'end':end}).status_code==422


def test_oap_null_is_unavailable_not_empty():
    async def oap(q,v=None):return {'getTimeInfo':{'timezone':'+0000'}} if 'getTimeInfo' in q else {'getAlarm':None}
    app=FastAPI();register_alerts(app,oap)
    with TestClient(app) as c:assert c.get('/api/ai/alerts',params={'start':'2026-10-01T00:00:00Z','end':'2026-10-01T01:00:00Z'}).status_code==502


def test_elastic_local_tls_and_namespace_role():
    text=configuration()
    assert 'network.host: 127.0.0.1' in text and 'xpack.security.enabled: true' in text
    assert text.count('enabled: true')==3 and 'verification_mode: full' in text
    assert 'path.data: /var/lib/elasticsearch' in text
    assert role()['indices'][0]['names']==['ibatyr_*']
    assert 'superuser' not in str(role())
    validate('8.19.22',2)
    for version,heap in [('9.0.0',2),('8.19.22;echo x',2),('8.19.22',0)]:
        with pytest.raises(RuntimeError):validate(version,heap)


def test_elasticsearch_refuses_existing_data_before_mutations(tmp_path, monkeypatch):
    import elastic_install
    from types import SimpleNamespace
    es=tmp_path/'elasticsearch';es.mkdir()
    monkeypatch.setattr(elastic_install,'ES',es)
    monkeypatch.setattr(elastic_install,'run',lambda *a,**kw:pytest.fail('must not run a command'))
    with pytest.raises(RuntimeError,match='уже существуют'):
        elastic_install.install(SimpleNamespace(version='8.19.22',heap_gb=2),lambda *a:pytest.fail('must not probe'))


def test_alert_endpoint_requires_existing_session(tmp_path):
    from test_ai import make_app,make_config
    cfg=tmp_path/'config.json';make_config(cfg)
    with TestClient(make_app(cfg,licensed=False)) as c:
        assert c.get('/api/ai/alerts',params={'start':'2026-10-01T00:00:00Z','end':'2026-10-01T01:00:00Z'}).status_code==401
