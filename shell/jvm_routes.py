"""Read-only JVM dashboard, minute windows for OAP 10.1."""
import asyncio
from datetime import datetime, timedelta

from fastapi import HTTPException, Query
from dashboard_routes import MAX_WINDOW_MINUTES, values

TIMEOUT_SECONDS = 35
# CPU is already percent in OAP 10.1; memory is bytes; GC time is ms per bucket.
METRICS = {
    'cpu_percent': 'instance_jvm_cpu',
    'heap_bytes': 'instance_jvm_memory_heap',
    'heap_max_bytes': 'instance_jvm_memory_heap_max',
    'nonheap_bytes': 'instance_jvm_memory_noheap',
    'metaspace_bytes': 'instance_jvm_memory_pool_metaspace',
    'threads_live': 'instance_jvm_thread_live_count',
    'threads_blocked': 'instance_jvm_thread_blocked_state_thread_count',
    'threads_runnable': 'instance_jvm_thread_runnable_state_thread_count',
    'threads_waiting': 'instance_jvm_thread_waiting_state_thread_count',
    'threads_timed_waiting': 'instance_jvm_thread_timed_waiting_state_thread_count',
    'young_gc_ms': 'instance_jvm_young_gc_time',
    'old_gc_ms': 'instance_jvm_old_gc_time',
    'normal_gc_ms': 'instance_jvm_normal_gc_time',
    'young_gc_count': 'instance_jvm_young_gc_count',
    'old_gc_count': 'instance_jvm_old_gc_count',
    'normal_gc_count': 'instance_jvm_normal_gc_count',
    'classes_loaded': 'instance_jvm_class_loaded_class_count',
}
JVM_QUERY = 'query JVM($entity:Entity!,$duration:Duration!){\n' + '\n'.join(
    f'{alias}:execExpression(expression:"{metric}",entity:$entity,duration:$duration){{type error results{{values{{id value}}}}}}'
    for alias, metric in METRICS.items()
) + '\n}'
INSTANCES_QUERY = '''query Instances($duration:Duration!,$serviceId:ID!){
  getServiceInstances(duration:$duration,serviceId:$serviceId){id name}
}'''
WARNINGS = [
    'CPU — средняя загрузка JVM в процентах, не всего хоста. OAP 10.1 округляет входные значения ниже 1% вверх до 1%.',
    'Память и потоки — минутные средние. GiB = 1024³ байт. Heap % — отношение минутных средних used/max.',
    'GC — суммарное зарегистрированное время и число сборок за минуту; это не длительность одной паузы и не P95 пауз.',
    'Без isEmptyValue ноль не отличим от отсутствия наблюдений. Минуты без положительного Heap и live threads показаны как пропуски; внутри остальных минут нули возвращены как есть.',
    'Отсутствие Young/Old/Normal GC или Metaspace может зависеть от сборщика и JVM. Ноль не подтверждает поддержку метрики.',
    'BLOCKED означает ожидание Java-монитора; это не доказательство SQL-блокировки или deadlock.',
]


def windows(start, end):
    while start < end:
        until = min(start + timedelta(minutes=MAX_WINDOW_MINUTES), end)
        yield start, until
        start = until


def duration(begin, until):
    return {'start': begin.strftime('%Y-%m-%d %H%M'),
            'end': (until-timedelta(minutes=1)).strftime('%Y-%m-%d %H%M'), 'step': 'MINUTE'}


async def collect(query_oap, start, end, service_id, instance_id):
    meta = await query_oap('query { getTimeInfo { timezone } listServices(layer:"GENERAL") {id name} }')
    service = next((s for s in meta.get('listServices', []) if s['id'] == service_id), None)
    if not service:
        raise HTTPException(404, 'Сервис не найден в GENERAL')
    try:
        tz_text = meta['getTimeInfo']['timezone']
        tz = datetime.strptime(tz_text, '%z').tzinfo
    except (KeyError, ValueError, TypeError):
        raise HTTPException(502, 'OAP не вернул часовой пояс')
    start, end = start.astimezone(tz), end.astimezone(tz)
    chunks = list(windows(start, end))
    # TaskGroup cancels sibling requests when any window fails or the deadline expires.
    async with asyncio.TaskGroup() as group:
        tasks = [group.create_task(query_oap(INSTANCES_QUERY, {
            'duration': duration(a, b), 'serviceId': service_id})) for a, b in chunks]
    instances = {}
    for task in tasks:
        for item in task.result().get('getServiceInstances') or []:
            instances[item['id']] = item
    instances = sorted(instances.values(), key=lambda i: (i['name'], i['id']))
    selected = next((i for i in instances if i['id'] == instance_id), None) if instance_id else next(iter(instances), None)
    result = {'service': service, 'instances': instances, 'instance': selected,
              'period': {'start': start.isoformat(), 'end_exclusive': end.isoformat(), 'timezone': tz_text},
              'points': [], 'latest': None, 'warnings': list(WARNINGS),
              'coverage': {'requested_minutes': int((end-start).total_seconds()/60), 'minutes_with_jvm_evidence': 0},
              'query_plan': {'step': 'MINUTE', 'max_window_minutes': MAX_WINDOW_MINUTES, 'metric_requests': 0}}
    if not selected:
        result['warnings'].append('Выбранный экземпляр не найден в этом периоде. Выберите экземпляр из списка.' if instance_id else 'Экземпляры за этот период не найдены.')
        return result
    async with asyncio.TaskGroup() as group:
        tasks = [group.create_task(query_oap(JVM_QUERY, {
            'entity': {'serviceName': service['name'], 'serviceInstanceName': selected['name'], 'normal': True},
            'duration': duration(a, b)})) for a, b in chunks]
    merged = {name: {} for name in METRICS}
    for (begin, until), task in zip(chunks, tasks):
        data = task.result()
        for name in METRICS:
            metric = data.get(name)
            if not isinstance(metric, dict) or metric.get('error') or metric.get('type') != 'TIME_SERIES_VALUES':
                result['warnings'].append(f'{name}: нет данных {begin.isoformat()} → {until.isoformat()}')
                continue
            merged[name].update({k: v for k, v in values(metric).items()
                                 if int(begin.timestamp()*1000) <= k < int(until.timestamp()*1000)})
    for i in range(result['coverage']['requested_minutes']):
        at = start + timedelta(minutes=i)
        key = int(at.timestamp()*1000)
        point = {name: merged[name].get(key) for name in METRICS}
        evidence = (point['heap_bytes'] or 0) > 0 or (point['threads_live'] or 0) > 0
        if not evidence:
            point = dict.fromkeys(METRICS)
        else:
            result['coverage']['minutes_with_jvm_evidence'] += 1
        for name in ('heap', 'heap_max', 'nonheap', 'metaspace'):
            value = point[name+'_bytes']
            point[name+'_gib'] = value/(1024**3) if value is not None else None
        used, maximum = point['heap_bytes'], point['heap_max_bytes']
        point['heap_percent'] = used/maximum*100 if used is not None and maximum and maximum > 0 else None
        point.update(time=at.isoformat(), observation_status='jvm_evidence' if evidence else 'unknown')
        result['points'].append(point)
    # Always report the actual last bucket, never silently substitute an older sample.
    result['latest'] = result['points'][-1]
    result['query_plan']['metric_requests'] = len(chunks)
    return result


def register_jvm(app, query_oap):
    @app.get('/api/ai/jvm')
    async def jvm(start: datetime, end: datetime, service_id: str = Query(..., min_length=1, max_length=1000),
                  instance_id: str | None = Query(default=None, max_length=2000)):
        if start.utcoffset() is None or end.utcoffset() is None:
            raise HTTPException(422, 'Укажите часовой пояс')
        if not timedelta(0) < end-start <= timedelta(days=1):
            raise HTTPException(422, 'Допустимый интервал — до 24 часов')
        if start.second or end.second or start.microsecond or end.microsecond:
            raise HTTPException(422, 'Выберите полные минуты')
        try:
            try:
                async with asyncio.timeout(TIMEOUT_SECONDS):
                    return await collect(query_oap, start, end, service_id, instance_id)
            except* HTTPException as errors:
                # Flatten TaskGroup failures to a useful HTTP response.
                raise errors.exceptions[0]
        except TimeoutError:
            raise HTTPException(504, 'OAP не успел вернуть JVM за весь период. Повторите запрос или сократите интервал.')
