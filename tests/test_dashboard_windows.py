import asyncio
from datetime import datetime, timedelta, timezone
import math

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import dashboard_routes

TZ = timezone(timedelta(hours=5))
START = datetime(2026, 9, 30, 12, 0, tzinfo=TZ)


def make_client(*, fail_window=False, missing_latency=False):
    calls = []

    async def oap(query, variables=None):
        if 'listServices' in query:
            return {'getTimeInfo': {'timezone': '+0500'}, 'listServices': [{'id': 'test', 'name': 'TestApp'}]}
        duration = variables['duration']
        begin = datetime.strptime(duration['start'], '%Y-%m-%d %H%M').replace(tzinfo=TZ)
        end = datetime.strptime(duration['end'], '%Y-%m-%d %H%M').replace(tzinfo=TZ) + timedelta(minutes=1)
        assert duration['step'] == 'MINUTE'
        assert end - begin <= timedelta(minutes=500), 'OAP 500-minute limit'
        calls.append((begin, end))
        if fail_window and begin > START:
            raise HTTPException(502, 'Fixture upstream failure')
        # Complete in reverse order to exercise ordering of the merge.
        await asyncio.sleep(.003 if begin == START else 0)
        indexes = range(int((begin - START).total_seconds() / 60), int((end - START).total_seconds() / 60))

        def metric(fn, percentile=None):
            samples = [{'id': str(int((START + timedelta(minutes=i)).timestamp()) * 1000), 'value': str(fn(i))}
                       for i in indexes if i != 481]
            # A spurious observation beyond this window must never fill the next window's gap.
            samples.append({'id': str(int(end.timestamp()) * 1000), 'value': '9999999'})
            return {'type': 'TIME_SERIES_VALUES', 'error': None, 'results': [
                {'metric': {'labels': [{'key': 'p', 'value': percentile}] if percentile else []}, 'values': samples}]}

        result = {'traffic': metric(lambda i: 1 + i % 3), 'latency': metric(lambda i: 100 + i),
                  'success': metric(lambda i: 10000 if i % 2 == 0 else 9000),
                  'percentiles': metric(lambda i: 200 + i, '95')}
        if missing_latency and begin > START:
            result['latency'] = {'type': 'UNKNOWN', 'error': 'unavailable', 'results': []}
        return result

    app = FastAPI()
    dashboard_routes.register_dashboard(app, oap)
    return TestClient(app), calls


@pytest.mark.parametrize('minutes', [60, 480, 481, 500, 501, 960, 1440])
def test_disjoint_minute_windows_preserve_kpis_and_gaps(minutes):
    client, calls = make_client()
    # Input is UTC; OAP expects local +05:00 buckets.
    end = START + timedelta(minutes=minutes)
    response = client.get('/api/ai/dashboard', params={
        'start': START.astimezone(timezone.utc).isoformat(), 'end': end.astimezone(timezone.utc).isoformat(), 'service_id': 'test'})
    assert response.status_code == 200, response.text
    data = response.json()
    assert len(calls) == math.ceil(minutes / 480)
    windows = sorted(calls)
    assert windows[0][0] == START and windows[-1][1] == end
    assert all(a[1] == b[0] for a, b in zip(windows, windows[1:]))
    assert len(data['points']) == minutes
    assert len({p['time'] for p in data['points']}) == minutes
    known = [i for i in range(minutes) if i != 481]
    weight = sum(1 + i % 3 for i in known)
    assert data['coverage']['minutes_with_positive_traffic'] == len(known)
    assert data['kpis']['estimated_calls'] == weight
    assert data['kpis']['estimated_mean_latency_ms'] == round(sum((100+i)*(1+i%3) for i in known)/weight, 2)
    assert data['kpis']['estimated_error_rate_percent'] == round(sum((0 if i%2==0 else 10)*(1+i%3) for i in known)/weight, 3)
    assert data['kpis']['max_minute_p95_ms'] == max(200+i for i in known)
    assert data['query_plan']['metric_requests'] == len(calls)
    if minutes > 481:
        assert data['points'][481]['calls_per_minute'] is None
        assert data['points'][481]['p95_ms'] is None


def test_failed_window_does_not_return_successful_partial_day():
    client, _ = make_client(fail_window=True)
    response = client.get('/api/ai/dashboard', params={
        'start': START.isoformat(), 'end': (START+timedelta(days=1)).isoformat(), 'service_id': 'test'})
    assert response.status_code == 502
    assert 'points' not in response.json()


def test_unavailable_metric_window_is_gap_with_explicit_warning():
    client, _ = make_client(missing_latency=True)
    response = client.get('/api/ai/dashboard', params={
        'start': START.isoformat(), 'end': (START+timedelta(days=1)).isoformat(), 'service_id': 'test'})
    assert response.status_code == 200
    data = response.json()
    assert data['points'][480]['mean_latency_ms'] is None
    assert data['points'][480]['calls_per_minute'] is not None
    assert any('latency' in w and '2026-09-30T20:00:00+05:00' in w for w in data['warnings'])


def test_deadline_returns_timeout_instead_of_empty_metrics(monkeypatch):
    monkeypatch.setattr(dashboard_routes, 'DASHBOARD_TIMEOUT_SECONDS', .001)
    client, _ = make_client()
    response = client.get('/api/ai/dashboard', params={
        'start': START.isoformat(), 'end': (START+timedelta(minutes=60)).isoformat(), 'service_id': 'test'})
    assert response.status_code == 504
