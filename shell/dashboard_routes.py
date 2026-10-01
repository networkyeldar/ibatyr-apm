import asyncio
from datetime import datetime, timedelta
import math

from fastapi import HTTPException, Query

MAX_WINDOW_MINUTES = 480
DASHBOARD_TIMEOUT_SECONDS = 35

METRICS = """
query Dashboard($entity: Entity!, $duration: Duration!) {
  latency: execExpression(expression:"service_resp_time",entity:$entity,duration:$duration){...Result}
  traffic: execExpression(expression:"service_cpm",entity:$entity,duration:$duration){...Result}
  success: execExpression(expression:"service_sla",entity:$entity,duration:$duration){...Result}
  percentiles: execExpression(expression:"service_percentile",entity:$entity,duration:$duration){...Result}
}
fragment Result on ExpressionResult {
  type error results { metric { labels { key value } } values { id value } }
}
"""


def number(value):
    try:
        n = float(value)
        return n if math.isfinite(n) and n >= 0 else None
    except (TypeError, ValueError):
        return None


def values(result, percentile=None):
    if not result or result.get("error") or result.get("type") != "TIME_SERIES_VALUES":
        return {}
    rows = result.get("results") or []
    for row in rows:
        labels = {i["key"]: i["value"] for i in (row.get("metric") or {}).get("labels") or []}
        if percentile is not None and labels.get("p") != percentile:
            continue
        output = {}
        for p in row.get("values") or []:
            try:
                output[int(p["id"])] = number(p.get("value"))
            except (TypeError, ValueError, KeyError):
                continue
        return output
    return {}



async def fetch_metric_windows(query_oap, service_name, start, end, deadline):
    """Merge disjoint minute windows below OAP's observed 500-minute limit."""
    windows = []
    cursor = start
    while cursor < end:
        until = min(cursor + timedelta(minutes=MAX_WINDOW_MINUTES), end)
        windows.append((cursor, until))
        cursor = until

    async def fetch_window(begin, until):
        return await query_oap(METRICS, {
            "entity": {"serviceName": service_name, "normal": True},
            "duration": {
                "start": begin.strftime("%Y-%m-%d %H%M"),
                "end": (until - timedelta(minutes=1)).strftime("%Y-%m-%d %H%M"),
                "step": "MINUTE",
            },
        })

    # The endpoint accepts at most 24 hours: at most three parallel requests.
    tasks = [asyncio.create_task(fetch_window(*window)) for window in windows]
    try:
        batches = await asyncio.wait_for(
            asyncio.gather(*tasks),
            timeout=max(0, deadline - asyncio.get_running_loop().time()),
        )
    except TimeoutError:
        raise HTTPException(504, "OAP не успел вернуть метрики за весь период. Повторите запрос или сократите интервал.")
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    merged = {name: {} for name in ("latency", "traffic", "success", "percentiles")}
    warnings = []
    for (begin, until), data in zip(windows, batches):
        if not isinstance(data, dict):
            raise HTTPException(502, "OAP вернул неверный ответ для части периода")
        first_ms, last_ms = int(begin.timestamp()) * 1000, int(until.timestamp()) * 1000
        for name in merged:
            result = data.get(name)
            if not isinstance(result, dict) or result.get("error") or result.get("type") != "TIME_SERIES_VALUES":
                warnings.append(f"Метрика {name} недоступна: {begin.isoformat()} → {until.isoformat()} (конец не включён)")
                continue
            samples = values(result, "95" if name == "percentiles" else None)
            # Only retain samples belonging to this window; never count a boundary twice.
            merged[name].update({key: value for key, value in samples.items() if first_ms <= key < last_ms})
    return merged, warnings, len(windows)


def register_dashboard(app, query_oap):
    @app.get("/api/ai/dashboard")
    async def dashboard(start: datetime, end: datetime, service_id: str = Query(..., min_length=1)):
        if start.utcoffset() is None or end.utcoffset() is None:
            raise HTTPException(422, "Укажите часовой пояс")
        if not timedelta(0) < end-start <= timedelta(days=1):
            raise HTTPException(422, "Допустимый интервал — до 24 часов")
        if start.second or end.second or start.microsecond or end.microsecond:
            raise HTTPException(422, "Метрики запрашиваются по полным минутам")
        deadline = asyncio.get_running_loop().time() + DASHBOARD_TIMEOUT_SECONDS
        try:
            metadata = await asyncio.wait_for(
                query_oap('query { getTimeInfo { timezone } listServices(layer:"GENERAL") { id name } }'),
                timeout=DASHBOARD_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            raise HTTPException(504, "OAP не успел вернуть метаданные")
        service = next((s for s in metadata.get("listServices", []) if s["id"] == service_id), None)
        if not service:
            raise HTTPException(404, "Сервис не найден в GENERAL")
        try:
            tz_text = metadata["getTimeInfo"]["timezone"]
            tz = datetime.strptime(tz_text, "%z").tzinfo
        except (KeyError, ValueError, TypeError):
            raise HTTPException(502, "OAP не вернул корректный часовой пояс")
        start, end = start.astimezone(tz), end.astimezone(tz)
        merged, metric_warnings, window_count = await fetch_metric_windows(
            query_oap, service["name"], start, end, deadline
        )
        latency, traffic, success = [merged[k] for k in ("latency", "traffic", "success")]
        p95 = merged["percentiles"]
        points, cursor = [], start
        while cursor < end:
            key = int(cursor.timestamp()) * 1000
            cpm = traffic.get(key)
            observed = cpm is not None and cpm > 0
            sla = success.get(key)
            points.append({"time": cursor.isoformat(), "calls_per_minute": cpm if observed else None, "mean_latency_ms": latency.get(key) if observed else None, "p95_ms": p95.get(key) if observed else None, "error_rate_percent": round(100-sla/100, 4) if observed and sla is not None and sla <= 10000 else None, "traffic_status": "positive" if observed else "zero_or_missing"})
            cursor += timedelta(minutes=1)
        known = [p for p in points if p["traffic_status"] == "positive"]
        timed = [p for p in known if p["mean_latency_ms"] is not None]
        rated = [p for p in known if p["error_rate_percent"] is not None]
        weight = sum(p["calls_per_minute"] for p in timed)
        rate_weight = sum(p["calls_per_minute"] for p in rated)
        percentiles = [p["p95_ms"] for p in known if p["p95_ms"] is not None]
        warnings = ["Нули без isEmptyValue не подтверждают наличие наблюдений; такие минуты показаны как пропуски.", "Средняя задержка и доля ошибок оценены с весами CPM по доступным минутам; это не точные агрегаты исходных запросов.", "Максимум минутного P95 не является P95 за весь период.", "Текущая и недавние минуты могут содержать неполные данные."]
        warnings.extend(metric_warnings)
        return {"query_plan": {"step": "MINUTE", "metric_requests": window_count, "max_window_minutes": MAX_WINDOW_MINUTES}, "service": service, "period": {"start": start.isoformat(), "end_exclusive": end.isoformat(), "timezone": tz_text}, "coverage": {"requested_minutes": len(points), "minutes_with_positive_traffic": len(known)}, "kpis": {"estimated_calls": sum(p["calls_per_minute"] for p in known) if known else None, "estimated_mean_latency_ms": round(sum(p["mean_latency_ms"]*p["calls_per_minute"] for p in timed)/weight, 2) if weight else None, "max_minute_p95_ms": max(percentiles) if percentiles else None, "estimated_error_rate_percent": round(sum(p["error_rate_percent"]*p["calls_per_minute"] for p in rated)/rate_weight, 3) if rate_weight else None}, "points": points, "warnings": warnings}
