import asyncio
import json

import httpx
import pytest
from fastapi import HTTPException

import ai_routes
import diagnose_llm
from test_ai_response_format import PROFILE, MESSAGES, transport


@pytest.mark.parametrize('kind,marker', [
    (httpx.ConnectTimeout, 'connect_timeout'), (httpx.ReadTimeout, 'read_timeout'),
    (httpx.WriteTimeout, 'write_timeout'), (httpx.PoolTimeout, 'pool_timeout'),
])
def test_timeout_stage_is_distinguished_without_secret_or_retry(monkeypatch, kind, marker):
    calls = []
    def handler(request):
        calls.append(request)
        raise kind('secret-provider-text', request=request)
    transport(monkeypatch, handler)
    with pytest.raises(HTTPException) as err:
        asyncio.run(ai_routes.completion(PROFILE, MESSAGES, test=True))
    assert err.value.status_code == 504
    assert marker in err.value.detail and 'secret-provider-text' not in err.value.detail
    assert len(calls) == 1


def test_diagnostics_use_saved_auth_and_exact_model_without_echoing_body(capsys):
    calls = []
    profile = {**PROFILE, 'base_url': 'http://local.test:8000/v1', 'model': 'served-alias', 'token_parameter': 'max_completion_tokens'}
    def handler(req):
        calls.append(req)
        assert req.headers['authorization'] == 'Bearer test-secret'
        if req.url.path == '/health': return httpx.Response(200, text='')
        if req.url.path == '/v1/models': return httpx.Response(200, json={'data': [{'id': 'served-alias'}]})
        body = json.loads(req.content)
        assert body['model'] == 'served-alias' and body['max_completion_tokens'] == 128
        assert body['messages'] == [{'role': 'user', 'content': 'Reply with OK.'}]
        return httpx.Response(400, text='test-secret private-provider-body')
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        diagnose_llm.diagnose(profile, client, chat=True)
    output = capsys.readouterr().out
    assert 'HTTP 400' in output and 'ДА' in output
    assert 'test-secret' not in output and 'private-provider-body' not in output
    assert len(calls) == 3
