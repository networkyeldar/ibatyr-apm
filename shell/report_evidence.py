"""Bounded, numeric evidence for an overview. Raw names/logs stay on this server."""
import math

SERVICE_SERIES = {
    'mean_latency_ms': ('Средняя задержка', 'мс'), 'p95_ms': ('Минутный P95', 'мс'),
    'calls_per_minute': ('Вызовы', 'выз/мин'), 'error_rate_percent': ('Ошибки', '%'),
}
JVM_SERIES = {
    'cpu_percent': ('CPU JVM', '%'), 'heap_gib': ('Heap', 'GiB'),
    'heap_max_gib': ('Heap max', 'GiB'), 'heap_percent': ('Heap used/max', '%'),
    'nonheap_gib': ('Non-Heap', 'GiB'), 'metaspace_gib': ('Metaspace', 'GiB'),
    'threads_live': ('Потоки Live', 'потоков'), 'threads_blocked': ('Потоки Blocked', 'потоков'),
    'threads_runnable': ('Потоки Runnable', 'потоков'), 'threads_waiting': ('Потоки Waiting', 'потоков'),
    'threads_timed_waiting': ('Потоки Timed waiting', 'потоков'),
    'young_gc_ms': ('Время Young GC за минуту', 'мс'), 'old_gc_ms': ('Время Old GC за минуту', 'мс'),
    'normal_gc_ms': ('Время Normal GC за минуту', 'мс'),
    'young_gc_count': ('Young GC', 'сборок/мин'), 'old_gc_count': ('Old GC', 'сборок/мин'),
    'normal_gc_count': ('Normal GC', 'сборок/мин'), 'classes_loaded': ('Загруженные классы', 'классов'),
}


def numeric(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def overview_evidence(dashboard, jvm, alarms, question):
    rows, links, charts = [], {}, []
    def add(row, reference):
        identity = f'E{len(rows)+1:03d}'
        rows.append({'id': identity, **row});links[identity] = reference
    add({'kind':'coverage','scope':'selected_service_and_one_selected_jvm',
         'service': dashboard['coverage'], 'jvm': jvm['coverage'],
         'available_instances':len(jvm['instances']), 'jvm_selected':bool(jvm['instance']),
         'service_kpis':dashboard['kpis']}, {'title':'Покрытие периода и показатели сервиса'})
    for name, dataset, columns in [('service_1',dashboard,SERVICE_SERIES),('jvm_1',jvm,JVM_SERIES)]:
        points=dataset['points']
        for key,(title,unit) in columns.items():
            samples=[p for p in points if numeric(p.get(key))]
            values=[p[key] for p in samples]
            peak=max(samples,key=lambda p:p[key]) if samples else None
            row={'kind':'metric_summary','entity':name,'metric':key,'unit':unit,
                 'samples':len(samples),'requested_minutes':dashboard['coverage']['requested_minutes'],
                 'min':min(values) if values else None,'max':max(values) if values else None,
                 'mean_of_minute_values':sum(values)/len(values) if values else None,
                 'first':{'time':samples[0]['time'],'value':values[0]} if samples else None,
                 'last':{'time':samples[-1]['time'],'value':values[-1]} if samples else None,
                 'peak':{'time':peak['time'],'value':peak[key]} if peak else None,
                 'top_minutes':[{'time':p['time'],'value':p[key]} for p in sorted(samples,key=lambda p:p[key],reverse=True)[:3]]}
            add(row, {'title':title,'metric':key,'unit':unit})
            charts.append({'key':key,'title':title,'unit':unit,'points':[{'time':p['time'],'value':p.get(key)} for p in points]})
    # Time-aligned evidence: selected extreme minutes, never a fabricated causal correlation.
    jvm_by_time={p['time']:p for p in jvm['points']}
    candidates=set()
    for dataset,keys in [(dashboard,['mean_latency_ms','p95_ms','error_rate_percent','calls_per_minute']),
                         (jvm,['cpu_percent','heap_percent','threads_blocked','young_gc_ms','old_gc_ms'])]:
        for key in keys:
            candidates.update(p['time'] for p in sorted(
                [p for p in dataset['points'] if numeric(p.get(key))],key=lambda p:p[key],reverse=True)[:2])
    service_by_time={p['time']:p for p in dashboard['points']}
    for stamp in sorted(candidates)[:20]:
        a,b=service_by_time.get(stamp,{}),jvm_by_time.get(stamp,{})
        add({'kind':'aligned_minute','time':stamp,
             'service':{k:a.get(k) for k in SERVICE_SERIES},
             'jvm':{k:b.get(k) for k in ['cpu_percent','heap_percent','threads_blocked','threads_live','young_gc_ms','old_gc_ms','normal_gc_ms']}},
            {'title':'Сопоставление метрик '+stamp,'time':stamp})
    # Alarms are explicitly all-service sample context; do not attach them to the chosen service.
    if alarms is not None:
        from collections import Counter
        add({'kind':'alarm_sample','scope':'all_services_first_page_only',
             'count':len(alarms['records']), 'by_severity':dict(Counter(a['severity'] for a in alarms['records'])),
             'may_have_more':alarms['pagination']['may_have_more'],
             'selected_service_events':sum(a['scope']=='Service' and a['entity_id']==dashboard['service']['id'] for a in alarms['records'])},
            {'title':'Выборка алертов по всем сервисам; только первая страница'})
    warnings=list(dashboard['warnings'])+list(jvm['warnings'])+[
        'Анализ использует минутные агрегаты одного сервиса и одной JVM; несколько экземпляров не объединяются.',
        'Среднее минутных P95 не является P95 за период. Среднее минутных значений не всегда взвешено числом запросов.',
        'Совпадение пиков по времени не доказывает причинную связь. Без spans и данных БД нельзя назвать SQL-причину.',
        'Алерты: первая страница до 50 событий по всем сервисам, не полный список и не состояние открытых инцидентов.',
    ]
    if alarms is None:warnings.append('Алерты недоступны и не включены в анализ.')
    if not any(numeric(p.get('calls_per_minute')) for p in dashboard['points']) and not any(numeric(p.get('cpu_percent')) for p in jvm['points']):
        from fastapi import HTTPException
        raise HTTPException(422,'В выбранном периоде нет доступных метрик сервиса/JVM для анализа.')
    by_key={c['key']:c for c in charts}
    bundles=[('Задержки API',['mean_latency_ms','p95_ms']),('Трафик',['calls_per_minute']),('Ошибки',['error_rate_percent']),
             ('CPU JVM',['cpu_percent']),('Heap',['heap_gib','heap_max_gib']),('Non-Heap и Metaspace',['nonheap_gib','metaspace_gib']),
             ('Состояния потоков',['threads_live','threads_runnable','threads_waiting','threads_timed_waiting']),
             ('Заблокированные потоки',['threads_blocked']),('Время GC',['young_gc_ms','old_gc_ms','normal_gc_ms']),
             ('Число сборок GC',['young_gc_count','old_gc_count','normal_gc_count']),('Загруженные классы',['classes_loaded'])]
    charts=[{'title':title,'unit':by_key[keys[0]]['unit'],'datasets':[by_key[k] for k in keys]} for title,keys in bundles]
    return {'question':question,'scope':'dashboard_overview','period':dashboard['period'],
            'limitations':warnings,'evidence':rows},links,charts
