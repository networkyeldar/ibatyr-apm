"""One-click analysis and session-owned, immutable PDF reports."""
import asyncio
from datetime import datetime, timezone
import hashlib
import json
import secrets
import time
from typing import Literal

from fastapi import HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from ai_evidence import build_evidence
from report_evidence import overview_evidence


class DirectTrace(BaseModel):
    trace_id: str = Field(min_length=1,max_length=512)
    provider: Literal['external','local']
    request_id: str = Field(min_length=8,max_length=100)
    question: str = Field(default='Подробно объясни задержки API, SQL, ошибки и порядок проверок.',min_length=1,max_length=1500)


class Overview(BaseModel):
    start: datetime
    end: datetime
    service_id: str = Field(min_length=1,max_length=1000)
    instance_id: str | None = Field(default=None,max_length=2000)
    provider: Literal['external','local']
    request_id: str = Field(min_length=8,max_length=100)
    question: str = Field(default='Подробно проанализируй все метрики за период, совпадения пиков, проблемы и приоритетные проверки.',min_length=1,max_length=1500)


def register_reports(app, trace_endpoint, licensing, profile, complete, parse, prompt, active):
    endpoints={r.path:r.endpoint for r in app.routes if hasattr(r,'path') and hasattr(r,'endpoint')}
    reports,jobs={},{}
    app.state.analysis_reports=reports

    def purge():
        now=time.time()
        for mapping in (reports,jobs):
            for key in list(mapping):
                if mapping[key]['expires']<now:mapping.pop(key,None)

    async def collect(body):
        if isinstance(body,DirectTrace):
            detail=await trace_endpoint(trace_id=body.trace_id)
            packet,links=build_evidence(detail,body.question)
            return packet,links,[],{'scope':'trace','trace_id':body.trace_id,'title':'Подробный анализ трассировки'}
        # Let the established endpoints validate exact period, timezone and instance ownership.
        async with asyncio.TaskGroup() as group:
            dashboard=group.create_task(endpoints['/api/ai/dashboard'](start=body.start,end=body.end,service_id=body.service_id))
            jvm=group.create_task(endpoints['/api/ai/jvm'](start=body.start,end=body.end,service_id=body.service_id,instance_id=body.instance_id))
        alarms=None
        try:
            alarms=await asyncio.wait_for(endpoints['/api/ai/alerts'](start=body.start,end=body.end,severity='ALL',page=1,page_size=50),timeout=8)
        except (HTTPException,TimeoutError):
            pass
        d,j=dashboard.result(),jvm.result()
        packet,links,charts=overview_evidence(d,j,alarms,body.question)
        return packet,links,charts,{'scope':'overview','title':'Общий анализ производительности','service':d['service'],
                                  'instance':j['instance'],'period':d['period'],'available_instances':len(j['instances'])}

    async def execute(body,request):
        licensing.require_ai()
        p=profile(body.provider)
        purge()
        owner=request.state.ai_session['id']
        identity=(owner,body.request_id)
        digest=hashlib.sha256(body.model_dump_json().encode()).hexdigest()
        previous=jobs.get(identity)
        if previous:
            if previous['digest']!=digest:raise HTTPException(409,'request_id уже использован с другими параметрами')
            if previous.get('result'):return previous['result']
            raise HTTPException(409,'Этот запрос уже отправлен. Дождитесь результата; для нового анализа нужен новый request_id.')
        if len(active)>=2 or any(key[0]==owner and value.get('running') for key,value in jobs.items()):
            raise HTTPException(429,'Анализ уже выполняется. Дождитесь завершения.')
        if len(jobs)>=100:
            oldest=next((k for k,v in jobs.items() if not v.get('running')),None)
            if oldest:jobs.pop(oldest)
        jobs[identity]={'digest':digest,'expires':time.time()+3600,'running':True}
        token=secrets.token_urlsafe(24);active.add(token)
        try:
            started=time.monotonic()
            try:
                try:
                    async with asyncio.timeout(150):
                        packet,links,charts,metadata=await collect(body)
                        encoded=json.dumps(packet,ensure_ascii=False,allow_nan=False)
                        if len(encoded)>60000:raise HTTPException(413,'Контекст превышает ограничение. Сократите период или трассировку.')
                        licensing.require_ai()
                        text,usage=await complete(p,[{'role':'system','content':prompt},{'role':'user','content':encoded}])
                        analysis=parse(text,links)
                except* HTTPException as errors:
                    raise errors.exceptions[0]
            except TimeoutError:
                raise HTTPException(504,'Анализ не завершён за 150 секунд. Проверьте OAP/LLM или сократите период.')
            result={'report_id':token,'provider':body.provider,'model':p['model'],
                    'created_at':datetime.now(timezone.utc).isoformat(),'elapsed_seconds':round(time.monotonic()-started,2),
                    'usage':usage,'analysis':analysis,'evidence_links':links,'metadata':metadata,
                    'evidence':packet['evidence'],'limitations':packet.get('limitations',[]),
                    'notice':'Интерпретация модели требует проверки. Совпадение метрик не доказывает причину. PDF доступен в этой сессии один час.'}
            if len(reports)>=20:reports.pop(next(iter(reports)))
            reports[token]={'owner':owner,'expires':time.time()+3600,'result':result,'charts':charts}
            jobs[identity]['result']=result
            return result
        finally:
            active.discard(token)
            if identity in jobs:jobs[identity]['running']=False

    @app.post('/api/ai/llm/trace-analysis')
    async def trace_analysis(body:DirectTrace,request:Request):
        return await execute(body,request)

    @app.post('/api/ai/llm/overview-analysis')
    async def overview_analysis(body:Overview,request:Request):
        return await execute(body,request)

    @app.get('/api/ai/reports/{report_id}.pdf')
    async def report_pdf(report_id:str,request:Request):
        purge();report=reports.get(report_id)
        if not report or report['owner']!=request.state.ai_session['id']:
            raise HTTPException(404,'Отчёт не найден или истёк. Сформируйте анализ заново.')
        from report_pdf import render_report
        pdf=await asyncio.to_thread(render_report,report['result'],report['charts'])
        return Response(pdf,media_type='application/pdf',headers={
            'Content-Disposition':f'attachment; filename="ibatyr-apm-report-{report_id}.pdf"'})
