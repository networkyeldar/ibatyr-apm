"""Standalone iBatyr shell entry point; configuration comes from the environment."""
import os
from urllib.parse import urlsplit
import httpx
from fastapi import FastAPI, HTTPException
from frontend_routes import register_frontend
from trace_api import register_traces
from ai_routes import register_ai_features

OAP_URL = os.environ.get('IBATYR_OAP_URL', 'http://127.0.0.1:12800/graphql')
u = urlsplit(OAP_URL)
if u.scheme not in ('http', 'https') or not u.hostname or u.username or u.password or u.fragment:
    raise RuntimeError('IBATYR_OAP_URL must be an HTTP(S) GraphQL endpoint without credentials')

async def query_oap(query, variables=None):
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30, connect=5), trust_env=False) as client:
            async with client.stream('POST', OAP_URL, json={'query':query,'variables':variables or {}}) as r:
                r.raise_for_status()
                body=bytearray()
                async for part in r.aiter_bytes():
                    body.extend(part)
                    if len(body)>32*1024*1024:
                        raise HTTPException(502,'Ответ OAP слишком большой; сократите запрос')
        import json
        payload=json.loads(body)
        if payload.get('errors') or not isinstance(payload.get('data'),dict):
            raise HTTPException(502,'OAP отклонил GraphQL-запрос. Проверьте совместимость схемы и журнал OAP.')
        return payload['data']
    except (httpx.HTTPError, ValueError):
        raise HTTPException(502,'Не удалось получить данные OAP; проверьте IBATYR_OAP_URL и состояние сервера')

app=FastAPI(title='iBatyr APM',docs_url=None,redoc_url=None,openapi_url=None)
@app.get('/health')
async def health():return {'status':'ok','product':'iBatyr APM','version':'0.4.0-rc1'}
@app.get('/ready')
async def ready():
    await query_oap('query { __typename }')
    return {'status':'ok','oap':'reachable'}
register_traces(app,query_oap)
register_frontend(app)
register_ai_features(app,query_oap)
app.version='0.4.0-rc1'
