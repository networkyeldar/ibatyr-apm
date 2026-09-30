from datetime import datetime, timedelta
import math

from fastapi import HTTPException, Query

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


def register_dashboard(app, query_oap):
    @app.get("/api/ai/dashboard")
    async def dashboard(start: datetime, end: datetime, service_id: str = Query(..., min_length=1)):
        if start.utcoffset() is None or end.utcoffset() is None:
            raise HTTPException(422, "Укажите часовой пояс")
        if not timedelta(0) < end-start <= timedelta(days=1):
            raise HTTPException(422, "Допустимый интервал — до 24 часов")
        if start.second or end.second or start.microsecond or end.microsecond:
            raise HTTPException(422, "Метрики запрашиваются по полным минутам")
        metadata = await query_oap('query { getTimeInfo { timezone } listServices(layer:"GENERAL") { id name } }')
        service = next((s for s in metadata.get("listServices", []) if s["id"] == service_id), None)
        if not service:
            raise HTTPException(404, "Сервис не найден в GENERAL")
        try:
            tz_text = metadata["getTimeInfo"]["timezone"]
            tz = datetime.strptime(tz_text, "%z").tzinfo
        except (KeyError, ValueError, TypeError):
            raise HTTPException(502, "OAP не вернул корректный часовой пояс")
        start, end = start.astimezone(tz), end.astimezone(tz)
        data = await query_oap(METRICS, {"entity": {"serviceName": service["name"], "normal": True}, "duration": {"start": start.strftime("%Y-%m-%d %H%M"), "end": (end-timedelta(minutes=1)).strftime("%Y-%m-%d %H%M"), "step": "MINUTE"}})
        latency, traffic, success = [values(data.get(k)) for k in ("latency", "traffic", "success")]
        p95 = values(data.get("percentiles"), "95")
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
        for name, result in data.items():
            if result.get("error") or result.get("type") != "TIME_SERIES_VALUES":
                warnings.append(f"Метрика {name} недоступна или имеет неподдерживаемый формат")
        return {"service": service, "period": {"start": start.isoformat(), "end_exclusive": end.isoformat(), "timezone": tz_text}, "coverage": {"requested_minutes": len(points), "minutes_with_positive_traffic": len(known)}, "kpis": {"estimated_calls": sum(p["calls_per_minute"] for p in known) if known else None, "estimated_mean_latency_ms": round(sum(p["mean_latency_ms"]*p["calls_per_minute"] for p in timed)/weight, 2) if weight else None, "max_minute_p95_ms": max(percentiles) if percentiles else None, "estimated_error_rate_percent": round(sum(p["error_rate_percent"]*p["calls_per_minute"] for p in rated)/rate_weight, 3) if rate_weight else None}, "points": points, "warnings": warnings}
