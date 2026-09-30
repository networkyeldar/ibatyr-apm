import re
import json
import logging

import sqlglot
from sqlglot import exp

logging.getLogger("sqlglot").setLevel(logging.ERROR)


def safe_sql(sql):
    """Fail closed: unsupported syntax is not sent to any model."""
    if not sql or len(sql) > 16000:
        return None
    if not re.match(r"\s*(SELECT|INSERT|UPDATE|DELETE|WITH)\b", sql, re.I):
        return None
    try:
        statements = sqlglot.parse(sql, read="mysql")
        if len(statements) != 1 or statements[0] is None:
            return None
        tree = statements[0]
        if any(isinstance(n, (exp.Command, exp.Introducer)) for n in tree.walk()):
            return None
        if any(isinstance(n, exp.Identifier) and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", n.name) for n in tree.walk()):
            return None
        for node in list(tree.walk()):
            if isinstance(node, (exp.Literal, exp.HexString, exp.BitString, exp.ByteString)):
                node.replace(exp.Placeholder())
        text = tree.sql(dialect="mysql", comments=False)
        # No arbitrary quoted identifiers or residual literal strings.
        if "'" in text or '"' in text or re.search(r"https?://|@|\b\d{1,3}(?:\.\d{1,3}){3}\b", text):
            return None
        return text if len(text) <= 6000 else None
    except Exception:
        return None


def event_codes(span):
    codes = set()
    for event in span.get("error_events") or []:
        message = str(event.get("message") or "").lower()
        kind = str(event.get("kind") or "").lower()
        if "lock wait timeout" in message:
            codes.add("db_lock_wait_timeout")
        elif "communications" in kind or "communications link failure" in message:
            codes.add("db_communication_failure")
        elif "deadlock" in message:
            codes.add("db_deadlock_reported")
        else:
            codes.add("recorded_error")
    if span.get("has_error") and not codes:
        codes.add("span_marked_error")
    return sorted(codes)


def build_evidence(detail, question):
    spans = detail.get("spans") or []
    services = {name: f"service_{i+1}" for i, name in enumerate(sorted({s.get('service', '') for s in spans}))}
    rows, links = [], {}

    def add(value, reference):
        evidence_id = f"E{len(rows)+1:03d}"
        rows.append({"id": evidence_id, **value})
        links[evidence_id] = reference

    for entry in (detail.get("entries") or [])[:8]:
        add({"kind": "entry_timing", "service": services.get(entry.get("service")), "duration_ms": entry["duration_ms"], "http_status": entry.get("http_status"), "entry_has_error": entry.get("entry_has_error"), "observed_direct_children_ms": entry["covered_by_children_ms"], "uncovered_ms": entry["not_covered_by_children_ms"], "longest_uncovered_ms": [g["duration_ms"] for g in entry.get("longest_uncovered_intervals", [])[:5]]}, {"segment_id": entry["segment_id"], "span_id": entry["span_id"], "operation": entry.get("operation")})

    errors = [s for s in spans if s.get("has_error") or s.get("error_events")]
    ordered = sorted(spans, key=lambda s: s.get("duration_ms", 0), reverse=True)
    selected, seen = [], set()
    for span in errors[:20] + ordered[:30]:
        key = (span.get("segment_id"), span.get("span_id"))
        if key in seen:
            continue
        seen.add(key)
        selected.append(span)
        if len(selected) >= 40:
            break
    omitted_sql = 0
    for span in selected:
        sql = safe_sql(span.get("sql")) if span.get("is_database") else None
        if span.get("sql") and not sql:
            omitted_sql += 1
        add({"kind": "database_operation" if span.get("is_database") else "operation", "service": services.get(span.get("service")), "duration_ms": span.get("duration_ms"), "is_error": span.get("has_error"), "error_codes": event_codes(span), "sql_template": sql}, {"segment_id": span["segment_id"], "span_id": span["span_id"], "operation": span.get("operation")})

    packet = {
        "question": question,
        "scope": "one_stored_trace",
        "coverage": {"total_returned_spans": len(spans), "selected_spans": len(selected), "sql_templates_omitted": omitted_sql, "has_http_entry": detail.get("has_http_entry_in_trace"), "trace_completeness": "unknown"},
        "limitations": ["Spans may be missing. Nested durations overlap; do not sum them.", "Uncovered time is not a measurement of CPU and does not identify its cause.", "An HTTP 200 entry can contain database errors.", "A client JDBC duration is not necessarily server SQL execution time."],
        "evidence": rows,
    }
    for row in sorted(rows, key=lambda r: len(r.get("sql_template") or ""), reverse=True):
        if len(json.dumps(packet, ensure_ascii=False)) <= 52000:
            break
        if row.get("sql_template"):
            row["sql_template"] = None
            packet["coverage"]["sql_templates_omitted"] += 1
    return packet, links
