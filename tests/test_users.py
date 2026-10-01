import json
import os
import sqlite3

from fastapi.testclient import TestClient
import pytest
from test_ai import make_config, make_app, login, PASSWORD
from ai_security import Security


@pytest.fixture
def app_config(tmp_path):
    path=tmp_path/'config.json';make_config(path)
    return make_app(path),path


def sign_in(c, name, password):
    r=c.post('/api/ai/auth/login',json={'username':name,'password':password})
    assert r.status_code==200,r.text
    return {'X-CSRF-Token':r.json()['csrf_token']}


def create(c,h,name='operator',role='user'):
    r=c.post('/api/ai/admin/users',headers=h,json={'username':name,'password':'operator-password-2026','role':role})
    assert r.status_code==201,r.text
    return r.json()


def test_migration_persistence_and_no_password_leaks(app_config):
    app,path=app_config
    with TestClient(app) as c:
        h=login(c)
        me=c.get('/api/ai/auth/session').json()
        assert me['role']=='admin' and me['username']=='devadmin'
        u=create(c,h)
        assert u['role']=='user' and u['active']
        text=c.get('/api/ai/admin/users').text+c.get('/api/ai/admin/audit').text
        assert 'password' not in text and 'salt' not in text
        assert 'operator-password-2026' not in path.with_name('users.sqlite3').read_bytes().decode(errors='ignore')
        assert os.stat(path.with_name('users.sqlite3')).st_mode & 0o777==0o600
    # New process reuses DB; legacy config cannot reset migrated credentials.
    s=Security(path)
    assert len(s.users.list())==2
    assert s.users.get('operator',True)['id']==u['id']


def test_user_can_read_apm_but_cannot_administer(app_config):
    app,_=app_config
    with TestClient(app) as admin,TestClient(app) as user:
        ah=login(admin);u=create(admin,ah)
        uh=sign_in(user,'operator','operator-password-2026')
        assert user.get('/api/ai/services').status_code==200
        assert user.get('/api/ai/llm/providers').status_code==200
        assert user.get('/api/ai/license').status_code==200
        for path in ['/api/ai/admin/users','/api/ai/admin/audit','/api/ai/license/request','/api/ai/license/history']:
            assert user.get(path).status_code==403
        for method,path,body in [
            ('post','/api/ai/admin/users',{'username':'evil','password':PASSWORD,'role':'admin'}),
            ('patch','/api/ai/admin/users/'+u['id'],{'role':'admin'}),
            ('post','/api/ai/admin/users/'+u['id']+'/password',{'password':PASSWORD}),
            ('put','/api/ai/llm/providers/external',{}),
            ('post','/api/ai/llm/providers/external/test',{}),
            ('post','/api/ai/license/activate',{'document':'{}'}),
        ]:
            assert getattr(user,method)(path,headers=uh,json=body).status_code==403


def test_csrf_validation_unique_login_and_self_lockout(app_config):
    app,_=app_config
    with TestClient(app) as c:
        assert c.get('/api/ai/admin/users').status_code==401
        h=login(c);me=c.get('/api/ai/auth/session').json()['user_id']
        assert c.post('/api/ai/admin/users',json={'username':'x','password':PASSWORD}).status_code==403
        for changes in [{'active':False},{'role':'user'}]:
            assert c.patch('/api/ai/admin/users/'+me,headers=h,json=changes).status_code==409
        for name,pw,code in [('devadmin',PASSWORD,409),('DEVADMIN',PASSWORD,409),('new','short',422),('<script>',PASSWORD,422)]:
            r=c.post('/api/ai/admin/users',headers=h,json={'username':name,'password':pw})
            assert r.status_code==code,r.text
        assert c.get('/api/ai/admin/users').json()['users'][0]['active']


@pytest.mark.parametrize('mutation', ['disable','role','password'])
def test_revokes_existing_sessions_and_login(app_config,mutation):
    app,path=app_config
    with TestClient(app) as admin,TestClient(app) as user:
        ah=login(admin);u=create(admin,ah);sign_in(user,'operator','operator-password-2026')
        url='/api/ai/admin/users/'+u['id']
        if mutation=='password':
            r=admin.post(url+'/password',headers=ah,json={'password':'changed-password-2026'})
        else:r=admin.patch(url,headers=ah,json={'active':False} if mutation=='disable' else {'role':'admin'})
        assert r.status_code==200,r.text
        assert user.get('/api/ai/services').status_code==401
        if mutation=='password':
            assert user.post('/api/ai/auth/login',json={'username':'operator','password':'operator-password-2026'}).status_code==401
            sign_in(user,'operator','changed-password-2026')
        elif mutation=='disable':
            assert user.post('/api/ai/auth/login',json={'username':'operator','password':'operator-password-2026'}).status_code==401
            assert admin.patch(url,headers=ah,json={'active':True}).status_code==200
            sign_in(user,'operator','operator-password-2026')
        else:
            sign_in(user,'operator','operator-password-2026')
            assert user.get('/api/ai/admin/users').status_code==200
        events=admin.get('/api/ai/admin/audit').json()['events']
        assert events[0]['actor']=='devadmin' and events[0]['target']=='operator'
        assert len(Security(path).users.list())==2


def test_changed_admin_password_does_not_fall_back_to_legacy(app_config):
    app,path=app_config
    with TestClient(app) as c:
        h=login(c);uid=c.get('/api/ai/auth/session').json()['user_id']
        assert c.post('/api/ai/admin/users/'+uid+'/password',headers=h,json={'password':'new-admin-password'}).status_code==200
        assert c.get('/api/ai/services').status_code==401
    with TestClient(make_app(path)) as c:
        assert c.post('/api/ai/auth/login',json={'username':'devadmin','password':PASSWORD}).status_code==401
        sign_in(c,'devadmin','new-admin-password')
