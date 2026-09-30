"""Read-only OAP adapter. Dates select segment starts; full traces may cross the window."""
from datetime import datetime, timedelta
from fastapi import HTTPException, Query

LIST_QUERY='''query Segments($condition:TraceQueryCondition!){queryBasicTraces(condition:$condition){traces{segmentId endpointNames duration start isError traceIds}}}'''
DETAIL_QUERY='''query Detail($id:ID!){queryTrace(traceId:$id){spans{traceId segmentId spanId parentSpanId serviceCode serviceInstanceName startTime endTime endpointName type peer component isError layer tags{key value} logs{time data{key value}} refs{traceId parentSegmentId parentSpanId type}}}}'''

def iso(ms,tz):return datetime.fromtimestamp(int(ms)/1000,tz).isoformat(timespec='milliseconds')

def timezone_from(data):
    try:return datetime.strptime(data['getTimeInfo']['timezone'],'%z').tzinfo
    except (KeyError,TypeError,ValueError):raise HTTPException(502,'OAP не вернул часовой пояс')

def duration_window(start,end,tz):
    if start.utcoffset() is None or end.utcoffset() is None:
        raise HTTPException(422,'Укажите часовой пояс в start и end')
    if not timedelta(0)<end-start<=timedelta(days=1):
        raise HTTPException(422,'Допустимый интервал — от 1 микросекунды до 24 часов')
    a=start.astimezone(tz);b=end.astimezone(tz)
    return {'start':a.strftime('%Y-%m-%d %H%M'),'end':(b-timedelta(microseconds=1)).strftime('%Y-%m-%d %H%M'),'step':'MINUTE'}

def coverage(entry,spans,tz):
    a,b=entry['start_ms'],entry['end_ms']
    children=[s for s in spans if s['segment_id']==entry['segment_id'] and s['parent_span_id']==entry['span_id'] and s['span_id']!=entry['span_id']]
    intervals=sorted((max(a,s['start_ms']),min(b,s['end_ms'])) for s in children if max(a,s['start_ms'])<min(b,s['end_ms']))
    merged=[]
    for x,y in intervals:
        if merged and x<=merged[-1][1]:merged[-1][1]=max(merged[-1][1],y)
        else:merged.append([x,y])
    gaps=[];cursor=a
    for x,y in merged:
        if x>cursor:gaps.append((cursor,x))
        cursor=max(cursor,y)
    if cursor<b:gaps.append((cursor,b))
    covered=sum(y-x for x,y in merged)
    return {'segment_id':entry['segment_id'],'span_id':entry['span_id'],'service':entry['service'],'operation':entry['operation'],
        'http_status':entry['http_status'],'entry_has_error':entry['has_error'],'duration_ms':b-a,
        'coverage_scope':'same_segment_direct_children','direct_child_count':len(children),
        'covered_by_children_ms':covered,'not_covered_by_children_ms':b-a-covered,'uncovered_intervals_count':len(gaps),
        'longest_uncovered_intervals':[{'start':iso(x,tz),'end':iso(y,tz),'duration_ms':y-x} for x,y in sorted(gaps,key=lambda z:z[1]-z[0],reverse=True)[:5]]}

def normalize_trace(trace_id,raw,tz):
    spans=[];warnings=['Полнота трассировки не подтверждена.','Покрытие — объединение прямых дочерних spans в том же сегменте. Непокрытое время не равно CPU.','Операции БД могут включать команды транзакций. Отсутствие SQL не означает отсутствие обращения к БД.']
    seen=set()
    for r in raw:
        try:
            key=(r['segmentId'],int(r['spanId']))
            if key in seen:continue
            a,b=int(r['startTime']),int(r['endTime'])
            if b<a:raise ValueError()
            tags={t['key']:t.get('value') for t in r.get('tags') or []}
            errors=[]
            for log in r.get('logs') or []:
                fields={t['key']:t.get('value') for t in log.get('data') or []}
                if any(k in fields for k in ('error.kind','error','message','stack')):
                    errors.append({'time_ms':log.get('time'),'kind':fields.get('error.kind') or fields.get('event'),
                      'message':fields.get('message') or fields.get('error') or fields.get('stack')})
            spans.append({'trace_id':r.get('traceId',trace_id),'segment_id':key[0],'span_id':key[1],'parent_span_id':int(r['parentSpanId']),
                'refs':r.get('refs') or [],'service':r.get('serviceCode'),'instance':r.get('serviceInstanceName'),
                'operation':r.get('endpointName'),'type':r.get('type'),'layer':r.get('layer'),'component':r.get('component'),
                'peer':r.get('peer'),'start_ms':a,'end_ms':b,'start':iso(a,tz),'end':iso(b,tz),'duration_ms':b-a,
                'has_error':r.get('isError'),'is_database':r.get('layer')=='Database','database':tags.get('db.instance'),
                'sql':tags.get('db.statement'),'http_status':tags.get('http.status_code'),'http_method':tags.get('http.method'),
                'error_events':errors})
            seen.add(key)
        except (KeyError,ValueError,TypeError,OverflowError,OSError):warnings.append('Пропущен span с некорректными полями или временем.')
    spans.sort(key=lambda s:(s['start_ms'],s['segment_id'],s['span_id']))
    entries=[coverage(s,spans,tz) for s in spans if s['type']=='Entry' and s['layer']=='Http']
    return {'trace_id':trace_id,'timezone':datetime.now(tz).strftime('%z'),'services':sorted({s['service'] for s in spans if s['service']}),
        'span_count':len(spans),'database_span_count':sum(s['is_database'] for s in spans),'error_span_count':sum(s['has_error'] is True for s in spans),
        'has_http_entry_in_trace':bool(entries),'entries':entries,'spans':spans,'warnings':list(dict.fromkeys(warnings))}

def register_traces(app,query_oap):
    @app.get('/api/ai/services')
    async def services():
        data=await query_oap('query { listServices(layer:"GENERAL") { id name } }')
        return {'services':data.get('listServices') or []}

    @app.get('/api/ai/traces')
    async def traces(start:datetime,end:datetime,service_id:str=Query(...,min_length=1,max_length=512),min_duration_ms:int=Query(1000,ge=0,le=2147483647),errors_only:bool=False,page:int=Query(1,ge=1,le=1000),page_size:int=Query(20,ge=1,le=100)):
        tz=timezone_from(await query_oap('query { getTimeInfo { timezone } }'))
        duration=duration_window(start,end,tz)
        condition={'serviceId':service_id,'queryDuration':duration,'minTraceDuration':min_duration_ms,'traceState':'ERROR' if errors_only else 'ALL','queryOrder':'BY_DURATION','paging':{'pageNum':page,'pageSize':page_size}}
        data=await query_oap(LIST_QUERY,{'condition':condition})
        rows=(data.get('queryBasicTraces') or {}).get('traces') or []
        records=[];outside=0;malformed=0
        for r in rows:
            try:
                ms=int(r['start']);dt=datetime.fromtimestamp(ms/1000,tz)
                if not start<=dt<end:outside+=1;continue
                length=int(r['duration'])
                if length<0:raise ValueError()
                records.append({'segment_id':r['segmentId'],'trace_ids':r.get('traceIds') or [],'endpoints':r.get('endpointNames') or [],'start':dt.isoformat(),'duration_ms':length,'duration_seconds':length/1000,'has_error':r.get('isError')})
            except (ValueError,KeyError,TypeError,OverflowError,OSError):malformed+=1
        return {'service_id':service_id,'period':{'start':start.astimezone(tz).isoformat(),'end_exclusive':end.astimezone(tz).isoformat(),'timezone':datetime.now(tz).strftime('%z'),'filter_basis':'segment_start_time'},
            'filters':{'min_duration_ms':min_duration_ms,'errors_only':errors_only},'pagination':{'page':page,'page_size':page_size,'received_from_oap':len(rows),'returned':len(records),'outside_window_removed':outside,'malformed_removed':malformed,'total':None,'may_have_more':len(rows)==page_size},
            'records':records,'warnings':['Возвращены сохранённые сегменты, а не все вызовы.','has_error не равнозначен HTTP-статусу.','Фильтр времени применяется к началу сегмента; детали показывают всю доступную трассировку.']}

    @app.get('/api/ai/traces/{trace_id}')
    async def details(trace_id:str):
        if not 1<=len(trace_id)<=512:raise HTTPException(422,'Некорректный trace_id')
        tz=timezone_from(await query_oap('query { getTimeInfo { timezone } }'))
        data=await query_oap(DETAIL_QUERY,{'id':trace_id})
        raw=(data.get('queryTrace') or {}).get('spans') or []
        if not raw:raise HTTPException(404,'Сохранённая трассировка не найдена')
        return normalize_trace(trace_id,raw,tz)
