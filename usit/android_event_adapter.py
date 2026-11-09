"""Adapt privacy-minimised Android instrumentation JSONL into USIT runtime evidence.

Input is a structured export produced by an Android test/instrumentation adapter.
This module is offline: it never invokes ADB, reads a device, or executes the app.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

from .safeio import read_bounded_no_follow
from .test_report_adapters import strict_json_loads
from typing import Any

MAX_INPUT_BYTES = 50 * 1024 * 1024
MAX_RECORDS = 500_000
PREFIX = "USIT_ANDROID_EVENT "
ANDROID_TYPES = {
    "screen_opened": "APP_STATE",
    "ui_action": "UI_ACTION",
    "permission_result": "PERMISSION_RESULT",
    "auth_context": "AUTH_CONTEXT",
    "http_request": "HTTP_REQUEST",
    "http_response": "HTTP_RESPONSE",
    "backend_span": "BACKEND_SPAN",
    "database_change": "DATABASE_CHANGE",
}
FIELDS = {
    "event_id", "timestamp", "event", "correlation_id", "parent_event_id",
    "business_function_id", "screen_id", "control_id", "action", "result",
    "duration_ms", "http_method", "route_template", "status_code",
    "db_operation", "table_or_collection", "permission", "granted",
    "app_state", "actor_ref",
}


def _read(path: str | Path) -> bytes:
    return read_bounded_no_follow(Path(path).expanduser(), MAX_INPUT_BYTES)


def adapt_android_jsonl(
    raw: bytes,
    graph: dict[str, Any],
    graph_sha256: str,
    session_id: str,
    captured_at: str,
) -> dict[str, Any]:
    if not isinstance(raw, bytes):
        raise ValueError("Android event input must be bytes")
    if not isinstance(graph, dict) or graph.get("schema_version") != "usit-graph-v1":
        raise ValueError("business graph must use usit-graph-v1")
    understanding = graph.get("business_understanding")
    if not isinstance(understanding, dict) or not isinstance(understanding.get("business_function_views"), list):
        raise ValueError("business graph is missing business_function_views")
    if not isinstance(session_id, str) or not session_id.strip() or len(session_id) > 128:
        raise ValueError("session_id must be a non-empty string of at most 128 characters")
    try:
        parsed = datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise ValueError("captured_at must be an ISO-8601 timestamp with timezone") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("captured_at must include a timezone offset")
    if not isinstance(graph_sha256, str) or len(graph_sha256) != 64 or any(c not in "0123456789abcdef" for c in graph_sha256):
        raise ValueError("graph_sha256 must be a lowercase SHA-256 digest")
    known = {
        row.get("business_function_id")
        for row in understanding.get("business_function_views", [])
        if isinstance(row, dict) and isinstance(row.get("business_function_id"), str)
    }
    events = []
    seen_ids = set()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("Android event input must be UTF-8") from exc
    for line_number, line in enumerate(text.splitlines(), 1):
        if not line.startswith(PREFIX):
            continue
        payload = line[len(PREFIX):]
        try:
            record = strict_json_loads(payload)
        except (json.JSONDecodeError, ValueError) as exc:
            detail = exc.msg if isinstance(exc, json.JSONDecodeError) else str(exc)
            raise ValueError(f"malformed USIT Android event at line {line_number}: {detail}") from exc
        if not isinstance(record, dict):
            raise ValueError(f"USIT Android event at line {line_number} must be an object")
        unknown = set(record) - FIELDS
        if unknown:
            raise ValueError(f"USIT Android event at line {line_number} has unsupported fields: {', '.join(sorted(unknown)[:8])}")
        event_name = record.get("event")
        if not isinstance(event_name, str) or event_name not in ANDROID_TYPES:
            raise ValueError(f"USIT Android event at line {line_number} has unsupported event type")
        event_id = record.get("event_id")
        if not isinstance(event_id, str) or not event_id.strip() or event_id in seen_ids:
            raise ValueError(f"USIT Android event at line {line_number} has missing or duplicate event_id")
        seen_ids.add(event_id)
        timestamp = record.get("timestamp")
        if not isinstance(timestamp, str):
            raise ValueError(f"USIT Android event at line {line_number} is missing timestamp")
        try:
            event_time = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"USIT Android event at line {line_number} has invalid timestamp") from exc
        if event_time.tzinfo is None or event_time.utcoffset() is None:
            raise ValueError(f"USIT Android event at line {line_number} timestamp requires timezone")
        function_id = record.get("business_function_id")
        if function_id is not None and (
            not isinstance(function_id, str) or not function_id or function_id not in known
        ):
            raise ValueError(f"USIT Android event at line {line_number} references unknown business_function_id")
        mapped = {
            "event_id": event_id,
            "timestamp": timestamp,
            "event_type": ANDROID_TYPES[event_name],
            "correlation_id": record.get("correlation_id"),
            "parent_event_id": record.get("parent_event_id"),
        }
        if function_id is not None:
            mapped["business_function_id"] = function_id
        field_map = {
            "screen_id": "component",
            "control_id": "component",
            "action": "operation",
            "result": "result",
            "duration_ms": "duration_ms",
            "http_method": "http_method",
            "route_template": "route_template",
            "status_code": "status_code",
            "db_operation": "db_operation",
            "table_or_collection": "table_or_collection",
            "permission": "permission",
            "granted": "granted",
            "app_state": "app_state",
            "actor_ref": "actor_ref",
        }
        for source, target in field_map.items():
            if source in record:
                if target == "component" and "component" in mapped:
                    mapped[target] = mapped[target] + "/" + str(record[source])
                else:
                    mapped[target] = record[source]
        events.append(mapped)
        if len(events) > MAX_RECORDS:
            raise ValueError(f"Android event count exceeds limit ({MAX_RECORDS})")
    if not events:
        raise ValueError("no prefixed USIT Android event records were found")
    return {
        "schema_version": "usit-runtime-evidence-v1",
        "source_graph_sha256": graph_sha256,
        "session": {"session_id": session_id, "platform": "ANDROID", "captured_at": captured_at},
        "events": events,
    }


def adapt_android_jsonl_file(
    input_path: str | Path,
    graph_path: str | Path,
    session_id: str,
    captured_at: str,
) -> dict[str, Any]:
    raw = _read(input_path)
    graph_raw = read_bounded_no_follow(Path(graph_path).expanduser(), MAX_INPUT_BYTES)
    try:
        graph = strict_json_loads(graph_raw)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"invalid business graph JSON: {exc}") from exc
    result = adapt_android_jsonl(raw, graph, hashlib.sha256(graph_raw).hexdigest(), session_id, captured_at)
    return result
