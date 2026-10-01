"""Read persisted OAP alarms; severity comes only from the explicit level tag."""
import hashlib
import json
from datetime import datetime, timezone
from typing import Literal

from fastapi import HTTPException, Query
from trace_api import duration_window

ALARMS = """
query Alarms($duration: Duration!, $paging: Pagination!, $tags: [AlarmTag]) {
  getAlarm(duration:$duration, paging:$paging, tags:$tags) {
    msgs { id startTime scope message tags { key value } }
  }
}
"""


def normalize_alarm(row, tz):
    stamp = int(row['startTime'])
    tags = {t['key']: t['value'] for t in row.get('tags', [])}
    level = tags.get('level', '').upper()
    severity = level if level in ('CRITICAL', 'HIGH', 'WARNING', 'INFO') else 'UNKNOWN'
    key = hashlib.sha256(json.dumps(row, sort_keys=True).encode()).hexdigest()[:24]
    return dict(key=key, entity_id=str(row['id']), scope=row.get('scope'),
                time=datetime.fromtimestamp(stamp / 1000, tz).isoformat(), time_ms=stamp,
                message=row['message'], severity=severity, level_raw=tags.get('level'),
                tags=tags, lifecycle='unknown')


def register_alerts(app, query_oap):
    @app.get('/api/ai/alerts')
    async def alerts(start: datetime, end: datetime,
                     severity: Literal['ALL', 'CRITICAL', 'HIGH', 'WARNING', 'INFO'] = 'CRITICAL',
                     page: int = Query(1, ge=1, le=100), page_size: int = Query(20, ge=1, le=50)):
        # Validate before making an OAP request, including naive dates and window length.
        duration_window(start, end, timezone.utc)
        meta = await query_oap('query { getTimeInfo { timezone } }')
        try:
            tz = datetime.strptime(meta['getTimeInfo']['timezone'], '%z').tzinfo
        except (KeyError, ValueError, TypeError):
            raise HTTPException(502, 'OAP не вернул часовой пояс')
        data = await query_oap(ALARMS, {'duration': duration_window(start, end, tz),
                                      'paging': {'pageNum': page, 'pageSize': page_size},
                                      'tags': [] if severity == 'ALL' else [{'key': 'level', 'value': severity}]})
        payload = data.get('getAlarm')
        if not isinstance(payload, dict) or not isinstance(payload.get('msgs'), list):
            raise HTTPException(502, 'OAP не вернул список алертов')
        rows = payload['msgs']
        output, seen, invalid, outside = [], set(), 0, 0
        for row in rows:
            try:
                alarm = normalize_alarm(row, tz)
            except (KeyError, ValueError, TypeError, OverflowError, OSError):
                invalid += 1
                continue
            if not start.timestamp()*1000 <= alarm['time_ms'] < end.timestamp()*1000:
                outside += 1
                continue
            if severity != 'ALL' and alarm['severity'] != severity:
                continue
            if alarm['key'] not in seen:
                output.append(alarm)
                seen.add(alarm['key'])
        rank = {'CRITICAL': 0, 'HIGH': 1, 'WARNING': 2, 'INFO': 3, 'UNKNOWN': 4}
        output.sort(key=lambda a: (rank[a['severity']], -a['time_ms']))
        return {'records': output, 'scope': 'all_services', 'severity': severity,
                'period': {'start': start.isoformat(), 'end_exclusive': end.isoformat()},
                'fetched_at': datetime.now(timezone.utc).isoformat(),
                'pagination': {'page': page, 'page_size': page_size, 'received': len(rows),
                               'returned': len(output), 'may_have_more': len(rows) >= page_size,
                               'total': None, 'invalid': invalid, 'outside_window_removed': outside},
                'warnings': ['Срабатывания за период по всем сервисам. Это не список открытых инцидентов.',
                             'Уровень берётся из тега level. Фильтр требует индексирования этого тега в OAP.',
                             'Сортировка по уровню и времени применяется внутри страницы; общего количества API не возвращает.']}
