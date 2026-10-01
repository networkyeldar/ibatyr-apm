from datetime import timedelta
import asyncio

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest

import jvm_routes
from test_dashboard_windows import START, TZ
from datetime import datetime


def client_for(*, fail=False, missing=False, no_instances=False, slow=False):
    calls = []
    async def oap(query, variables=None):
        if slow:
            await asyncio.sleep(.1)
        if 'listServices' in query:
            return {'getTimeInfo': {'timezone': '+0500'}, 'listServices': [{'id': 'service', 'name': 'App'}]}
        d = variables['duration']
        begin = datetime.strptime(d['start'], '%Y-%m-%d %H%M').replace(tzinfo=TZ)
        end = datetime.strptime(d['end'], '%Y-%m-%d %H%M').replace(tzinfo=TZ)+timedelta(minutes=1)
        assert end-begin <= timedelta(minutes=480)
        if 'getServiceInstances' in query:
            return {'getServiceInstances': [] if no_instances else [{'id': 'instance', 'name': 'JVM1'}]}
        assert variables['entity'] == {'serviceName': 'App', 'serviceInstanceName': 'JVM1', 'normal': True}
        calls.append((begin, end))
        if fail and begin > START:
            raise HTTPException(502, 'upstream failed')
        rows={}
        for name in jvm_routes.METRICS:
            points=[]
            for i in range(int((end-begin).total_seconds()/60)):
                at=begin+timedelta(minutes=i)
                index=int((at-START).total_seconds()/60)
                if index == 481:  # No samples for any metric.
                    continue
                value={'cpu_percent':18,'heap_bytes':20734686094,'heap_max_bytes':32212254720,
                       'threads_live':2883,'threads_blocked':69,'young_gc_ms':1280}.get(name,0)
                if index == 482:
                    value=0  # OAP's empty zero buckets must not look healthy.
                if name == 'heap_max_bytes' and index == 483:
                    value=-1  # Undefined memory limit.
                points.append({'id':str(int(at.timestamp()*1000)), 'value':str(value)})
            # Bad boundary point must not leak into a missing bucket in the next chunk.
            points.append({'id':str(int(end.timestamp()*1000)), 'value':'999999'})
            rows[name]={'type':'TIME_SERIES_VALUES','error':None,'results':[{'values':points}]}
        if missing:
            rows['metaspace_bytes']={'type':'UNKNOWN','error':'unavailable'}
        return rows
    app=FastAPI();jvm_routes.register_jvm(app,oap)
    return TestClient(app), calls


def query(client, **kwargs):
    params={'start':START.isoformat(),'end':(START+timedelta(days=1)).isoformat(),'service_id':'service'}
    params.update(kwargs)
    return client.get('/api/ai/jvm',params=params)


def test_day_instance_scoping_units_zero_and_missing_buckets():
    client,calls=client_for()
    r=query(client);assert r.status_code==200,r.text
    data=r.json();assert len(calls)==3
    assert data['instance']['id']=='instance'
    assert len(data['instances'])==1
    assert len(data['points'])==1440
    p=data['points'][0]
    assert p['cpu_percent']==18
    assert p['heap_max_gib']==30
    assert p['heap_percent']==pytest.approx(64.369)
    assert p['young_gc_ms']==1280 and p['old_gc_ms']==0
    assert p['threads_blocked']==69
    for i in (481,482):
        assert data['points'][i]['cpu_percent'] is None
        assert data['points'][i]['observation_status']=='unknown'
    assert data['points'][483]['heap_percent'] is None
    assert data['coverage']['minutes_with_jvm_evidence']==1438


@pytest.mark.parametrize('options', [{'no_instances':True},{}])
def test_missing_or_expired_instance_never_silently_switches(options):
    client,calls=client_for(**options)
    r=query(client,instance_id='old-instance');assert r.status_code==200
    assert r.json()['instance'] is None and r.json()['points']==[]
    assert not calls


def test_partial_metric_error_is_explicit_gap():
    client,_=client_for(missing=True)
    data=query(client).json()
    assert data['points'][0]['metaspace_gib'] is None
    assert any('metaspace_bytes:' in w for w in data['warnings'])


def test_failed_window_is_not_partial_success():
    client,_=client_for(fail=True)
    r=query(client);assert r.status_code==502,r.text
    assert 'points' not in r.json()


def test_deadline(monkeypatch):
    monkeypatch.setattr(jvm_routes,'TIMEOUT_SECONDS',.001)
    client,_=client_for(slow=True)
    assert query(client).status_code==504


@pytest.mark.parametrize('changes',[
    {'start':'2026-09-30T12:00:00'},
    {'end':(START+timedelta(days=2)).isoformat()},
    {'start':(START+timedelta(seconds=1)).isoformat()},
    {'end':START.isoformat()},
])
def test_invalid_period(changes):
    client,_=client_for()
    assert query(client,**changes).status_code==422
