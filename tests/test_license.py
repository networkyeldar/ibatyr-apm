import json
import time
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from test_ai import make_config, make_app, login
from license_manager import LicenseManager
from license_fixture import provision, document


def test_missing_license_disables_only_ai(tmp_path, monkeypatch):
    conf=tmp_path/'config.json';make_config(conf)
    app=make_app(conf, licensed=False)
    async def never(*a,**kw):raise AssertionError('LLM must not be called')
    monkeypatch.setattr('ai_routes.completion',never)
    with TestClient(app) as c:
        assert c.get('/api/ai/license').status_code==401
        h=login(c)
        assert c.get('/api/ai/license').json()['status']=='not_configured'
        assert c.get('/api/ai/services').status_code==200
        assert c.get('/api/ai/traces/trace-1').status_code==200
        assert c.post('/api/ai/llm/preview',json={'trace_id':'trace-1','provider':'external'},headers=h).status_code==403
        assert c.post('/api/ai/llm/analyze',json={'snapshot_id':'anything'},headers=h).status_code==403
        assert c.get('/api/ai/license/request').json()['installation_id']


def test_activation_csrf_tamper_and_binding(tmp_path):
    conf=tmp_path/'config.json';make_config(conf);app=make_app(conf,False)
    manager=app.state.licensing;key,doc=provision(manager)
    with TestClient(app) as c:
        h=login(c)
        assert c.post('/api/ai/license/activate',json={'document':doc}).status_code==403
        assert c.post('/api/ai/license/activate',json={'document':doc},headers=h).status_code==200
        original=manager.license_path.read_bytes()
        bad=json.loads(doc);bad['payload']['expires_at']+=86400
        assert c.post('/api/ai/license/activate',json={'document':json.dumps(bad)},headers=h).status_code==422
        wrong=document(manager,key,installation_id=str(uuid4()))
        assert c.post('/api/ai/license/activate',json={'document':wrong},headers=h).status_code==422
        assert manager.license_path.read_bytes()==original
        data=c.get('/api/ai/license').json()
        assert data['ai_enabled'] and data['license']['edition']=='trial'
        assert manager.license_path.stat().st_mode & 0o777 == 0o600


def test_expiry_and_renewal_between_preview_and_send(tmp_path,monkeypatch):
    conf=tmp_path/'config.json';make_config(conf);app=make_app(conf,False)
    m=app.state.licensing;key,doc=provision(m);m.activate(doc)
    with TestClient(app) as c:
        h=login(c);r=c.post('/api/ai/llm/preview',json={'trace_id':'trace-1','provider':'external'},headers=h)
        assert r.status_code==200
        snapshot=r.json()['snapshot_id']
        now=int(time.time())
        m.license_path.write_text(document(m,key,issued_at=now-86400,not_before=now-86400,expires_at=now-1))
        assert c.get('/api/ai/license').json()['status']=='expired'
        assert c.post('/api/ai/llm/analyze',json={'snapshot_id':snapshot},headers=h).status_code==403
        assert c.get('/api/ai/traces/trace-1').status_code==200
        m.activate(document(m,key,edition='professional',expires_at=now+365*86400))
        assert c.post('/api/ai/llm/analyze',json={'snapshot_id':snapshot},headers=h).status_code==409


def test_reload_does_not_reset_trial(tmp_path):
    m=LicenseManager(tmp_path);key,doc=provision(m);m.activate(doc)
    expires=m.public_status()['license']['expires_at']
    n=LicenseManager(tmp_path);n.activate(doc)
    assert n.public_status()['license']['expires_at']==expires
    assert n.state['installation_id']==m.state['installation_id']


def test_grace_and_future_and_trial_validation(tmp_path):
    m=LicenseManager(tmp_path);key,_=provision(m);now=int(time.time())
    grace=document(m,key,edition='professional',issued_at=now-86400,not_before=now-86400,expires_at=now-60,grace_days=1)
    m.activate(grace)
    assert m.public_status()['status']=='grace' and m.public_status()['ai_enabled']
    for doc in [document(m,key,not_before=now+100,expires_at=now+1000),document(m,key,grace_days=1),document(m,key,expires_at=now+40*86400)]:
        with pytest.raises(HTTPException):m.activate(doc)


def test_clock_rollback_and_state_corruption(tmp_path):
    m=LicenseManager(tmp_path);key,doc=provision(m);m.activate(doc)
    m.state['last_seen']=time.time()+10000
    assert m.public_status()['status']=='clock_error'
    with pytest.raises(HTTPException):m.require_ai()
    m.state_path.write_text('broken')
    n=LicenseManager(tmp_path)
    assert n.public_status()['status']=='state_error'
    assert n.state_path.read_text()=='broken'


def test_wrong_key_empty_features_and_corruption(tmp_path):
    m=LicenseManager(tmp_path);key,doc=provision(m);m.activate(doc)
    m.activate(document(m,key,features=[]))
    assert not m.public_status()['ai_enabled']
    with pytest.raises(HTTPException):m.require_ai()
    provision(m)
    assert m.public_status()['status']=='invalid'
    m.license_path.write_text('{}')
    assert m.public_status()['status']=='invalid'
