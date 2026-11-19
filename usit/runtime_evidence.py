"""Validate privacy-minimised runtime event evidence without executing the target app.

The importer accepts already captured events only. It does not connect to Android,
ADB, a device, a server, a database, or the network. Event linkage is based solely
on explicit IDs and correlation IDs; it never guesses business-function ownership.
"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime
from pathlib import Path

from .safeio import read_bounded_no_follow
from .test_report_adapters import strict_json_loads
from typing import Any

SCHEMA_VERSION = "usit-runtime-evidence-v1"
MAX_EVENT_BYTES = 50 * 1024 * 1024
MAX_EVENTS = 500_000
MAX_ID_LENGTH = 256
EVENT_TYPES = {
    "UI_ACTION", "APP_STATE", "PERMISSION_RESULT", "AUTH_CONTEXT",
    "HTTP_REQUEST", "HTTP_RESPONSE", "BACKEND_SPAN", "DATABASE_CHANGE",
}
SAFE_FIELDS = {
    "event_id", "timestamp", "event_type", "correlation_id", "parent_event_id",
    "business_function_id", "component", "operation", "result", "duration_ms",
    "http_method", "route_template", "status_code", "db_operation",
    "table_or_collection", "permission", "granted", "app_state", "actor_ref",
}
FORBIDDEN_KEY_PARTS = {
    "password", "passwd", "secret", "token", "authorization", "cookie",
    "credential", "private_key", "access_key", "refresh", "payload", "body",
    "email", "phone", "address", "real_name",
}
STAGE_BY_TYPE = {
    "UI_ACTION": "ui",
    "APP_STATE": "app_state",
    "PERMISSION_RESULT": "permission",
    "AUTH_CONTEXT": "identity_context",
    "HTTP_REQUEST": "http_request",
    "HTTP_RESPONSE": "http_response",
    "BACKEND_SPAN": "backend",
    "DATABASE_CHANGE": "database",
}


def _read_regular(path: str | Path, limit: int) -> bytes:
    return read_bounded_no_follow(Path(path).expanduser(), limit)


def _aware_timestamp(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be an ISO-8601 timestamp with timezone")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label} must be an ISO-8601 timestamp with timezone") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{label} must include a timezone offset")
    return parsed


def validate_runtime_evidence(
    document: dict[str, Any],
    business_graph: dict[str, Any],
    business_graph_sha256: str,
    runtime_evidence_sha256: str | None = None,
) -> dict[str, Any]:
    if not isinstance(document, dict) or document.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"runtime evidence must use {SCHEMA_VERSION}")
    # File-based validation is bounded before reading. Apply the same maximum
    # to callers that provide an already-decoded document directly.
    try:
        encoded_size = len(json.dumps(
            document, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8"))
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("runtime evidence document must be JSON-serializable") from exc
    if encoded_size > MAX_EVENT_BYTES:
        raise ValueError(f"runtime evidence exceeds size limit ({MAX_EVENT_BYTES} bytes)")
    extra_top = set(document) - {"schema_version", "source_graph_sha256", "session", "events", "correlation_function_declarations"}
    if extra_top:
        raise ValueError("runtime evidence contains unsupported top-level fields: " + ", ".join(sorted(extra_top)[:8]))
    if not isinstance(business_graph, dict) or business_graph.get("schema_version") != "usit-graph-v1":
        raise ValueError("business graph must use usit-graph-v1")
    understanding = business_graph.get("business_understanding")
    if not isinstance(understanding, dict) or not isinstance(understanding.get("business_function_views"), list):
        raise ValueError("business graph is missing business_function_views")
    known = set()
    for view in understanding["business_function_views"]:
        if isinstance(view, dict) and isinstance(view.get("business_function_id"), str):
            identity = view["business_function_id"]
            if identity in known:
                raise ValueError(f"duplicate business_function_id in graph: {identity}")
            known.add(identity)
    if not isinstance(business_graph_sha256, str) or len(business_graph_sha256) != 64 or any(c not in "0123456789abcdef" for c in business_graph_sha256):
        raise ValueError("business_graph_sha256 must be a lowercase SHA-256 digest")

    metadata = document.get("session")
    if not isinstance(metadata, dict):
        raise ValueError("session must be an object")
    extra_session = set(metadata) - {"session_id", "platform", "captured_at"}
    if extra_session:
        raise ValueError("session contains unsupported fields: " + ", ".join(sorted(extra_session)[:8]))
    session_id = metadata.get("session_id")
    platform = metadata.get("platform")
    if not isinstance(session_id, str) or not session_id.strip() or len(session_id) > 128:
        raise ValueError("session.session_id must be a non-empty string of at most 128 characters")
    if not isinstance(platform, str) or platform not in {"ANDROID", "IOS", "WEB", "DESKTOP", "BACKEND"}:
        raise ValueError("session.platform is invalid")
    _aware_timestamp(metadata.get("captured_at"), "session.captured_at")
    if runtime_evidence_sha256 is not None and (
        not isinstance(runtime_evidence_sha256, str)
        or len(runtime_evidence_sha256) != 64
        or any(c not in "0123456789abcdef" for c in runtime_evidence_sha256)
    ):
        raise ValueError("runtime_evidence_sha256 must be a lowercase SHA-256 digest")
    graph_hash = document.get("source_graph_sha256")
    conflicts = []
    if graph_hash != business_graph_sha256:
        conflicts.append({"kind": "GRAPH_HASH_MISMATCH", "expected": business_graph_sha256, "actual": graph_hash})

    # B2 G3(a): optional collector declarations of which business
    # functions each correlation covers. Without an independent
    # declaration there is nothing to check an event's claimed function
    # against -- the correlation summary's function list is derived from
    # the same events -- so the per-event check below only runs for
    # declared correlations. Old documents without this field validate
    # exactly as before.
    declarations = document.get("correlation_function_declarations")
    if declarations is not None:
        if not isinstance(declarations, dict):
            raise ValueError("correlation_function_declarations must be an object mapping correlation IDs to business function ID lists")
        parsed_declarations = {}
        for correlation_id, function_ids in declarations.items():
            if not isinstance(correlation_id, str) or not correlation_id.strip() or len(correlation_id) > MAX_ID_LENGTH:
                raise ValueError("correlation_function_declarations keys must be non-empty correlation IDs of at most 256 characters")
            if not isinstance(function_ids, list) or not function_ids or any(
                not isinstance(value, str) or not value or len(value) > MAX_ID_LENGTH for value in function_ids
            ):
                raise ValueError(f"correlation_function_declarations[{correlation_id}] must be a non-empty list of business function IDs")
            parsed_declarations[correlation_id] = sorted(set(function_ids))
        declarations = parsed_declarations
        for correlation_id, function_ids in declarations.items():
            for function_id in function_ids:
                if function_id not in known:
                    conflicts.append({"kind": "UNKNOWN_BUSINESS_FUNCTION", "source": "correlation_function_declarations",
                                      "correlation_id": correlation_id, "business_function_id": function_id})
    else:
        declarations = {}

    events = document.get("events")
    if not isinstance(events, list):
        raise ValueError("events must be a list")
    if len(events) > MAX_EVENTS:
        raise ValueError(f"runtime event limit exceeded ({MAX_EVENTS})")
    indexed: dict[str, dict[str, Any]] = {}
    parsed_times: dict[str, datetime] = {}
    correlations: dict[str, list[dict[str, Any]]] = {}
    for index, event in enumerate(events):
        if not isinstance(event, dict):
            raise ValueError(f"events[{index}] must be an object")
        unknown = set(event) - SAFE_FIELDS
        if unknown:
            unsafe = sorted(k for k in unknown if any(part in k.lower() for part in FORBIDDEN_KEY_PARTS))
            label = "potentially sensitive fields" if unsafe else "unsupported fields"
            raise ValueError(f"events[{index}] contains {label}: {', '.join(sorted(unknown)[:8])}")
        event_id = event.get("event_id")
        if not isinstance(event_id, str) or not event_id.strip() or len(event_id) > MAX_ID_LENGTH:
            raise ValueError(f"events[{index}].event_id must be a non-empty string of at most {MAX_ID_LENGTH} characters")
        if event_id in indexed:
            raise ValueError(f"duplicate runtime event_id: {event_id}")
        event_type = event.get("event_type")
        if not isinstance(event_type, str) or event_type not in EVENT_TYPES:
            raise ValueError(f"events[{index}].event_type is invalid")
        correlation_id = event.get("correlation_id")
        if not isinstance(correlation_id, str) or not correlation_id.strip() or len(correlation_id) > MAX_ID_LENGTH:
            raise ValueError(f"events[{index}].correlation_id must be a non-empty string of at most {MAX_ID_LENGTH} characters")
        parent_event_id = event.get("parent_event_id")
        if parent_event_id is not None and (
            not isinstance(parent_event_id, str)
            or not parent_event_id.strip()
            or len(parent_event_id) > MAX_ID_LENGTH
        ):
            raise ValueError(f"events[{index}].parent_event_id must be a non-empty string of at most {MAX_ID_LENGTH} characters when provided")
        parsed_times[event_id] = _aware_timestamp(event.get("timestamp"), f"events[{index}].timestamp")
        function_id = event.get("business_function_id")
        if function_id is not None and (not isinstance(function_id, str) or not function_id or len(function_id) > MAX_ID_LENGTH):
            raise ValueError(f"events[{index}].business_function_id must be a non-empty string of at most {MAX_ID_LENGTH} characters")
        if function_id and function_id not in known:
            conflicts.append({"kind": "UNKNOWN_BUSINESS_FUNCTION", "event_id": event_id, "business_function_id": function_id})
        # B2 G3(a): when the collector declared which functions this
        # correlation covers, each event's claimed function must fall
        # inside that declaration. A claim outside it is a collector
        # inconsistency, not proof of misattribution -- the declaration
        # and the field are both collector self-report.
        declared = declarations.get(correlation_id)
        if declared is not None and function_id and function_id not in declared:
            conflicts.append({"kind": "EVENT_FUNCTION_NOT_IN_CORRELATION_DECLARATION",
                              "event_id": event_id, "correlation_id": correlation_id,
                              "business_function_id": function_id, "declared_function_ids": declared})
        duration = event.get("duration_ms")
        if duration is not None and (
            isinstance(duration, bool) or not isinstance(duration, (int, float))
            or (isinstance(duration, float) and not math.isfinite(duration)) or duration < 0
        ):
            raise ValueError(f"events[{index}].duration_ms must be a non-negative number")
        status_code = event.get("status_code")
        if status_code is not None and (isinstance(status_code, bool) or not isinstance(status_code, int) or not 100 <= status_code <= 599):
            raise ValueError(f"events[{index}].status_code must be an HTTP status code")
        if "granted" in event and not isinstance(event["granted"], bool):
            raise ValueError(f"events[{index}].granted must be boolean")
        if "actor_ref" in event and (not isinstance(event["actor_ref"], str) or len(event["actor_ref"]) > 128):
            raise ValueError(f"events[{index}].actor_ref must be a pseudonymous reference of at most 128 characters")
        for field in ("component", "operation", "result", "http_method", "route_template", "db_operation", "table_or_collection", "permission", "app_state"):
            value = event.get(field)
            if value is not None and (not isinstance(value, str) or len(value) > 512):
                raise ValueError(f"events[{index}].{field} must be a string of at most 512 characters")
        copied = dict(event)
        indexed[event_id] = copied
        correlations.setdefault(correlation_id, []).append(copied)

    # B2 G3(a): a declaration naming a correlation no event belongs to
    # is a collector/document error worth flagging, not a silent no-op.
    for correlation_id, function_ids in sorted(declarations.items()):
        if correlation_id not in correlations:
            conflicts.append({"kind": "CORRELATION_DECLARATION_WITHOUT_EVENTS",
                              "correlation_id": correlation_id, "declared_function_ids": function_ids})

    parent_edges = []
    for event in events:
        parent_id = event.get("parent_event_id")
        if parent_id is not None:
            if not isinstance(parent_id, str) or parent_id not in indexed:
                conflicts.append({"kind": "MISSING_PARENT_EVENT", "event_id": event["event_id"], "parent_event_id": parent_id})
            elif indexed[parent_id].get("correlation_id") != event["correlation_id"]:
                conflicts.append({"kind": "CROSS_CORRELATION_PARENT", "event_id": event["event_id"], "parent_event_id": parent_id})
            else:
                parent_edges.append((parent_id, event["event_id"]))
                if parsed_times[parent_id] > parsed_times[event["event_id"]]:
                    conflicts.append({"kind": "PARENT_AFTER_CHILD_TIMESTAMP", "event_id": event["event_id"], "parent_event_id": parent_id})

    parent_by_child = {child: parent for parent, child in parent_edges}
    visited: set[str] = set()
    for start_id in sorted(indexed):
        path: list[str] = []
        position: dict[str, int] = {}
        current = start_id
        while current in parent_by_child and current not in visited:
            if current in position:
                conflicts.append({"kind": "PARENT_EVENT_CYCLE", "event_id": current})
                break
            position[current] = len(path)
            path.append(current)
            current = parent_by_child[current]
        visited.update(path)

    summaries = []
    for correlation_id, rows in sorted(correlations.items()):
        types = {row["event_type"] for row in rows}
        function_ids = sorted({row["business_function_id"] for row in rows if row.get("business_function_id")})
        # B2 G3(a): per-event attribution for this correlation, taken
        # from the events' own claimed function IDs. The attribution is
        # collector-declared, never independently verified: agreement
        # between declaration and events only proves the collector's
        # self-report is internally consistent.
        declared = declarations.get(correlation_id)
        attributed = sorted(
            [{"event_id": row["event_id"], "business_function_id": row["business_function_id"],
              "stage": STAGE_BY_TYPE[row["event_type"]]}
             for row in rows if row.get("business_function_id")],
            key=lambda item: item["event_id"])
        unattributed_event_count = len(rows) - len(attributed)
        if declared is None:
            attribution_status = "NO_DECLARATION"
        elif unattributed_event_count == 0:
            attribution_status = "PER_EVENT_COLLECTOR_DECLARED"
        else:
            attribution_status = "PARTIAL_COLLECTOR_DECLARED"
        summaries.append({
            "correlation_id": correlation_id,
            "event_count": len(rows),
            "observed_stages": sorted({STAGE_BY_TYPE[row["event_type"]] for row in rows}),
            "business_function_ids": function_ids,
            "declared_function_ids": declared,
            "event_function_attribution": attributed,
            "unattributed_event_count": unattributed_event_count,
            "attribution_status": attribution_status,
            "runtime_observation_status": "RUNTIME_OBSERVED",
            "has_ui_action": "UI_ACTION" in types,
            "has_http_exchange": "HTTP_REQUEST" in types and "HTTP_RESPONSE" in types,
            "has_backend_span": "BACKEND_SPAN" in types,
            "has_database_change": "DATABASE_CHANGE" in types,
            "correlation_status": "PARTIAL_OBSERVATION",
            "interpretation": "阶段共现不证明因果关系；必须检查 parent_event_id、时间戳及外部采集器的可信度。",
        })
        # Parent links, rather than mere co-occurrence, are required even to
        # describe a linked chain. This is still observation, never correctness.
        row_ids = {r["event_id"] for r in rows}
        edges = sorted((a, b) for a, b in parent_edges if a in row_ids and b in row_ids)
        linked = {x for edge in edges for x in edge}
        if len(types) >= 2 and linked:
            summaries[-1]["correlation_status"] = "PARENT_LINKED_EVENTS_OBSERVED"
        # Expose only explicit parent references. Timestamp ordering is useful
        # for review, but is deliberately not represented as a causal edge.
        ordered_rows = sorted(rows, key=lambda row: (parsed_times[row["event_id"]], row["event_id"]))
        summaries[-1]["observed_event_ids_by_timestamp"] = [row["event_id"] for row in ordered_rows]
        summaries[-1]["explicit_parent_edges"] = [
            {"parent_event_id": parent, "child_event_id": child} for parent, child in edges
        ]
        summaries[-1]["parent_linked_event_count"] = len(linked)
        summaries[-1]["unlinked_event_ids"] = sorted(row_ids - linked)
        summaries[-1]["root_event_ids"] = sorted(
            row["event_id"] for row in rows
            if row.get("parent_event_id") is None
        )
        child_ids_by_parent: dict[str, list[str]] = {}
        for parent_id, child_id in edges:
            child_ids_by_parent.setdefault(parent_id, []).append(child_id)
        parent_ids = {parent_id for parent_id, _ in edges}
        child_ids = {child_id for _, child_id in edges}
        roots = summaries[-1]["root_event_ids"]
        leaves = sorted(row_ids - parent_ids)
        branching = sorted(parent_id for parent_id, children in child_ids_by_parent.items() if len(children) > 1)
        isolated = sorted(row_ids - parent_ids - child_ids)
        cycle_event_ids = sorted({
            conflict["event_id"] for conflict in conflicts
            if conflict.get("kind") == "PARENT_EVENT_CYCLE" and conflict.get("event_id") in row_ids
        })
        if cycle_event_ids:
            topology_status = "CYCLE_DETECTED"
        elif branching:
            topology_status = "BRANCHED"
        elif not edges:
            topology_status = "NO_EXPLICIT_LINKS"
        elif len(roots) > 1:
            topology_status = "MULTI_ROOT"
        elif len(roots) == 1 and len(edges) == len(rows) - 1:
            topology_status = "LINEAR_CHAIN"
        else:
            topology_status = "PARTIAL_LINKAGE"
        summaries[-1]["leaf_event_ids"] = leaves
        summaries[-1]["branching_parent_event_ids"] = branching
        summaries[-1]["isolated_event_ids"] = isolated
        summaries[-1]["cycle_event_ids"] = cycle_event_ids
        summaries[-1]["parent_link_coverage_ratio"] = round(len(linked) / len(rows), 6) if rows else 0.0
        summaries[-1]["parent_topology_status"] = topology_status

    return {
        "schema_version": "usit-runtime-evidence-validation-v1",
        "assessment_status": "EVIDENCE_CONFLICT" if conflicts else "VALIDATED_EVENT_STRUCTURE",
        "session": {"session_id": session_id, "platform": platform, "captured_at": metadata["captured_at"]},
        "source_hashes": {
            "runtime_evidence_sha256": runtime_evidence_sha256,
            "business_graph_sha256": business_graph_sha256,
        },
        "event_count": len(events),
        "correlation_count": len(summaries),
        "business_function_ids_observed": sorted({row["business_function_id"] for row in events if row.get("business_function_id") and row["business_function_id"] in known}),
        "business_function_observation_count": len({row["business_function_id"] for row in events if row.get("business_function_id") and row["business_function_id"] in known}),
        "event_type_counts": {kind: sum(1 for row in events if row["event_type"] == kind) for kind in sorted(EVENT_TYPES)},
        "correlations": summaries,
        "evidence_conflicts": conflicts,
        "evidence_conflict_count": len(conflicts),
        "limitations": [
            "本工具只校验已经采集的事件结构，不负责采集事件，不连接设备、应用、服务器或数据库。",
            "RUNTIME_OBSERVED 仅表示输入中存在运行事件，不证明功能正确、需求满足或测试通过。",
            "业务功能关联只接受事件中显式提供且存在于当前图谱的 business_function_id；不按页面名、路由名或时间邻近推断。",
            "parent_event_id 可支持事件关联，但不证明分布式调用链因果真实；需要可信采集器和独立证据。",
            "仅接受白名单字段；禁止提交请求/响应正文、凭证、Cookie、个人身份信息或原始数据库值。",
            "逐事件功能归属取自采集端声明与事件自带字段，未经独立验证；声明与事件一致只证明自述无矛盾，不证明事件真实属于该功能。",
            "完整阶段链不是所有业务功能的固定要求；缺失阶段标记为未观察，不自动认定为缺陷。",
        ],
    }


def validate_runtime_evidence_files(event_path: str | Path, graph_path: str | Path) -> dict[str, Any]:
    event_bytes = _read_regular(event_path, MAX_EVENT_BYTES)
    graph_bytes = _read_regular(graph_path, 50 * 1024 * 1024)
    try:
        document = strict_json_loads(event_bytes)
        graph = strict_json_loads(graph_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"invalid JSON input: {exc}") from exc
    return validate_runtime_evidence(
        document, graph, hashlib.sha256(graph_bytes).hexdigest(),
        hashlib.sha256(event_bytes).hexdigest(),
    )
