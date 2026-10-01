"""Exercise actual Chat Completions wire format, including both report routes."""
import asyncio
import copy
import json

import httpx
import pytest
from fastapi import HTTPException

import ai_routes
from test_ai import login
from test_reports import reports_client, START
from datetime import timedelta

REAL_COMPLETION = ai_routes.completion
PROFILE = {'base_url': 'https://api.openai.com/v1', 'model': 'gpt-4o-mini-2024-07-18',
           'auth_mode': 'bearer', 'api_key': 'test-secret', 'max_tokens': 4096}
MESSAGES = [{'role': 'system', 'content': ai_routes.SYSTEM_PROMPT},
            {'role': 'user', 'content': json.dumps({'evidence': [{'id': 'E001'}, {'id': 'E002'}]})}]
VALID = {'summary': 'Проверенные измерения.', 'impact': 'Одна выборка.',
         'findings': [{'title': 'Задержка', 'interpretation': 'Причина требует проверки.', 'evidence_ids': ['E001']}],
         'hypotheses': [], 'next_checks': ['P1: проверить thread dump.'],
         'limitations': ['Полнота неизвестна.'], 'conclusion': 'Причина не установлена.'}


def transport(monkeypatch, handler):
    original = httpx.AsyncClient
    monkeypatch.setattr(ai_routes.httpx, 'AsyncClient',
                        lambda **kw: original(transport=httpx.MockTransport(handler), **kw))


def response(content=None, reason='stop', refusal=None):
    return httpx.Response(200, json={'choices': [{'finish_reason': reason, 'message': {
        'content': json.dumps(VALID, ensure_ascii=False) if content is None else content, 'refusal': refusal}}]})


@pytest.mark.parametrize('model', ['gpt-4o-mini', 'gpt-4o-mini-2024-07-18'])
def test_official_openai_schema_on_actual_wire(monkeypatch, model):
    calls = []
    def handler(req):
        body = json.loads(req.content);calls.append(body)
        fmt = body['response_format']
        assert fmt['type'] == 'json_schema' and fmt['json_schema']['strict'] is True
        schema = fmt['json_schema']['schema']
        assert set(schema['required']) == set(VALID)
        assert schema['additionalProperties'] is False
        finding = schema['properties']['findings']['items']
        assert finding['additionalProperties'] is False
        assert finding['properties']['evidence_ids']['items']['enum'] == ['E001', 'E002']
        assert body['model'] == model and body['max_tokens'] == 4096
        return response()
    transport(monkeypatch, handler)
    text, _ = asyncio.run(REAL_COMPLETION({**PROFILE, 'model': model}, MESSAGES))
    assert ai_routes.parse_analysis(text, {'E001': {}, 'E002': {}}) == VALID
    assert len(calls) == 1


@pytest.mark.parametrize('base,model,mode,expected', [
    ('http://127.0.0.1:8000/v1', 'local', 'auto', None),
    ('https://gateway.example/v1', PROFILE['model'], 'auto', None),
    ('https://api.openai.com/v1', 'unknown-model', 'auto', None),
    ('https://api.openai.com/v1', PROFILE['model'], 'prompt', None),
    ('http://127.0.0.1:8000/v1', 'local', 'json_object', 'json_object'),
    ('http://127.0.0.1:8000/v1', 'local', 'json_schema', 'json_schema'),
])
def test_explicit_and_compatible_modes(base, model, mode, expected):
    fmt = ai_routes.analysis_response_format({**PROFILE, 'base_url': base, 'model': model, 'response_format': mode}, MESSAGES)
    assert (fmt['type'] if fmt else None) == expected


@pytest.mark.parametrize('content,reason,refusal,expected', [
    ('', 'length', None, 'length'),
    ('{"summary":', 'length', None, 'length'),
    ('', 'stop', 'private provider text', 'refusal'),
    ('', 'content_filter', None, 'content_filter'),
    ('', 'stop', None, 'message.content'),
])
def test_incomplete_or_refused_not_retried(monkeypatch, content, reason, refusal, expected):
    calls=[]
    def handler(req):
        calls.append(req);return response(content, reason, refusal)
    transport(monkeypatch, handler)
    with pytest.raises(HTTPException) as err:
        asyncio.run(REAL_COMPLETION(PROFILE, MESSAGES))
    assert expected in err.value.detail
    assert 'private provider text' not in err.value.detail
    assert len(calls) == 1


@pytest.mark.parametrize('change,expected', [
    (lambda d: d.update(next_checks=[{'action': 'private-text'}]), 'next_checks[0]'),
    (lambda d: d.update(findings=[{'title':'x','interpretation':'y','evidence_ids':['E999-private-text']}]), 'unknown_evidence'),
    (lambda d: d.update(findings=[{'title':'x','interpretation':'y','evidence_ids':[]}]), 'нет ссылок'),
    (lambda d: d.update(summary=None), 'summary'),
    (lambda d: d.update(limitations='private-text'), 'limitations'),
])
def test_precise_safe_validation(change, expected):
    data = copy.deepcopy(VALID);change(data)
    with pytest.raises(HTTPException) as err:
        ai_routes.parse_analysis(json.dumps(data), {'E001': {}})
    assert expected in err.value.detail and 'private-text' not in err.value.detail


def test_json_syntax_and_fences():
    with pytest.raises(HTTPException, match='json_syntax'):
        ai_routes.parse_analysis('private-text {', {'E001': {}})
    assert ai_routes.parse_analysis('```json\n'+json.dumps(VALID)+'\n```', {'E001': {}}) == VALID


@pytest.mark.parametrize('scope', ['trace', 'overview'])
def test_real_completion_in_report_routes_and_pdf(reports_client, monkeypatch, scope):
    client, _ = reports_client;headers = login(client);calls=[]
    monkeypatch.setattr(ai_routes, 'completion', REAL_COMPLETION)
    saved = client.put('/api/ai/llm/providers/external', headers=headers,
                       json={**PROFILE, 'response_format': 'auto'})
    assert saved.status_code == 200 and saved.json()['response_format'] == 'auto'
    assert 'test-secret' not in saved.text
    def handler(req):
        body=json.loads(req.content);calls.append(body)
        actual_ids = sorted(row['id'] for row in json.loads(body['messages'][-1]['content'])['evidence'])
        enum=body['response_format']['json_schema']['schema']['properties']['findings']['items']['properties']['evidence_ids']['items']['enum']
        assert enum == actual_ids and 'E001' in enum
        return response()
    transport(monkeypatch, handler)
    body={'provider':'external','request_id':f'wire-{scope}-123'}
    if scope=='trace':body['trace_id']='trace-1'
    else:body.update(start=START.isoformat(),end=(START+timedelta(hours=1)).isoformat(),service_id='service1',instance_id='jvm1')
    result=client.post('/api/ai/llm/'+scope+'-analysis',headers=headers,json=body)
    assert result.status_code==200,result.text
    assert len(calls)==1 and result.json()['analysis']==VALID
    pdf=client.get('/api/ai/reports/'+result.json()['report_id']+'.pdf')
    assert pdf.status_code==200 and pdf.content.startswith(b'%PDF')
