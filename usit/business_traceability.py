"""Build an explicit, review-gated requirements-to-runtime-evidence traceability matrix.

No feature-to-endpoint relation is inferred from names, modules, paths, or proximity.
Only an exact, reviewer-approved mapping manifest can connect a requirement feature
to an endpoint-centred business-function view.
"""
from __future__ import annotations
from collections import Counter
from typing import Any


def build_business_traceability(
    semantics: dict[str, Any],
    graph: dict[str, Any],
    mapping_manifest: dict[str, Any],
    test_plan: dict[str, Any] | None = None,
    evidence_matrix: dict[str, Any] | None = None,
    input_hashes: dict[str, str] | None = None,
) -> dict[str, Any]:
    if not isinstance(semantics, dict) or not isinstance(semantics.get("features", []), list):
        raise ValueError("semantics.features must be a list")
    if not isinstance(graph, dict) or graph.get("schema_version") != "usit-graph-v1":
        raise ValueError("graph must use usit-graph-v1")
    understanding = graph.get("business_understanding")
    if not isinstance(understanding, dict):
        raise ValueError("graph.business_understanding is required")
    if not isinstance(mapping_manifest, dict) or mapping_manifest.get("schema_version") != "usit-feature-function-mapping-v1":
        raise ValueError("mapping manifest must use usit-feature-function-mapping-v1")
    graph_hash = (input_hashes or {}).get("business_graph_sha256")
    manifest_graph_hash = mapping_manifest.get("source_graph_sha256")
    if not isinstance(graph_hash, str) or len(graph_hash) != 64:
        raise ValueError("exact source graph SHA-256 is required")
    if manifest_graph_hash != graph_hash:
        raise ValueError("mapping manifest source_graph_sha256 does not match the supplied graph bytes")
    if test_plan is not None and (not isinstance(test_plan, dict) or test_plan.get("schema_version") != "usit-business-test-plan-v1"):
        raise ValueError("test plan must use usit-business-test-plan-v1")
    if evidence_matrix is not None and (not isinstance(evidence_matrix, dict) or evidence_matrix.get("schema_version") != "usit-business-evidence-verification-v1"):
        raise ValueError("evidence matrix must use usit-business-evidence-verification-v1")
    if evidence_matrix is not None and evidence_matrix.get("business_graph_sha256") not in {None, graph_hash}:
        raise ValueError("evidence matrix business_graph_sha256 does not match the supplied graph bytes")

    features = [item for item in semantics["features"] if isinstance(item, dict) and isinstance(item.get("name"), str)]
    feature_index: dict[tuple[str, str, int | None], dict[str, Any]] = {}
    for feature in features:
        source = feature.get("source")
        line = feature.get("line")
        if line is not None and (not isinstance(line, int) or isinstance(line, bool) or line < 1):
            line = None
        key = (feature["name"], source if isinstance(source, str) else "", line)
        if key in feature_index:
            raise ValueError(f"ambiguous duplicate requirement feature identity: {key}")
        feature_index[key] = feature

    views = understanding.get("business_function_views", [])
    if not isinstance(views, list):
        raise ValueError("business_function_views must be a list")
    functions: dict[str, dict[str, Any]] = {}
    for index, view in enumerate(views):
        if not isinstance(view, dict) or not isinstance(view.get("business_function_id"), str):
            raise ValueError(f"business_function_views[{index}] has invalid business_function_id")
        fid = view["business_function_id"]
        if fid in functions:
            raise ValueError(f"duplicate business_function_id: {fid}")
        functions[fid] = view

    mappings = mapping_manifest.get("mappings", [])
    if not isinstance(mappings, list):
        raise ValueError("mapping manifest mappings must be a list")
    mapping_by_feature: dict[tuple[str, str, int | None], dict[str, Any]] = {}
    for index, mapping in enumerate(mappings):
        if not isinstance(mapping, dict):
            raise ValueError(f"mappings[{index}] must be an object")
        name, source, line = mapping.get("feature_name"), mapping.get("source_path"), mapping.get("source_line")
        if not isinstance(name, str) or not name or not isinstance(source, str) or not source:
            raise ValueError(f"mappings[{index}] requires exact feature_name and source_path")
        if line is not None and (not isinstance(line, int) or isinstance(line, bool) or line < 1):
            raise ValueError(f"mappings[{index}].source_line must be a positive integer or null")
        key = (name, source, line)
        if key not in feature_index:
            raise ValueError(f"mapping references unknown feature identity: {key}")
        if key in mapping_by_feature:
            raise ValueError(f"duplicate mapping decision for feature identity: {key}")
        decision = mapping.get("decision")
        if decision not in {"APPROVED_LINK", "REJECTED_LINK", "NEEDS_REVIEW"}:
            raise ValueError(f"mappings[{index}] has invalid decision")
        for required in ("reviewed_by", "reviewed_at", "rationale"):
            if not isinstance(mapping.get(required), str) or not mapping[required].strip():
                raise ValueError(f"mappings[{index}] requires non-empty {required}")
        if decision == "APPROVED_LINK" and (not isinstance(mapping.get("business_function_id"), str) or not mapping["business_function_id"]):
            raise ValueError(f"mappings[{index}] approved link requires business_function_id")
        mapping_by_feature[key] = mapping

    candidates = test_plan.get("candidates", []) if test_plan else []
    if not isinstance(candidates, list):
        raise ValueError("test_plan.candidates must be a list")
    candidates_by_function: dict[str, list[dict[str, Any]]] = {}
    for candidate in candidates:
        if isinstance(candidate, dict) and isinstance(candidate.get("business_function_id"), str):
            candidates_by_function.setdefault(candidate["business_function_id"], []).append(candidate)

    rows = evidence_matrix.get("business_function_evidence", []) if evidence_matrix else []
    if not isinstance(rows, list):
        raise ValueError("evidence_matrix.business_function_evidence must be a list")
    runtime_by_function: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("business_function_id"), str):
            raise ValueError("evidence matrix contains a malformed business_function_evidence row")
        fid = row["business_function_id"]
        if fid in runtime_by_function:
            raise ValueError(f"duplicate runtime evidence row for business_function_id: {fid}")
        runtime_by_function[fid] = row

    result_rows = []
    status_counts = Counter()
    for key in sorted(feature_index):
        feature = feature_index[key]
        mapping = mapping_by_feature.get(key)
        if mapping is None:
            decision = "NOT_MAPPED"
            fid = None
            linkage = "NOT_LINKED_NO_REVIEWED_MAPPING"
        else:
            decision = mapping["decision"]
            fid = mapping.get("business_function_id") if decision == "APPROVED_LINK" else None
            linkage = {
                "APPROVED_LINK": "REVIEWED_LINK_PENDING_TARGET_VALIDATION",
                "REJECTED_LINK": "REVIEWED_NO_LINK",
                "NEEDS_REVIEW": "REVIEW_REQUIRED",
            }[decision]
        view = functions.get(fid) if fid else None
        if decision == "APPROVED_LINK" and view is None:
            linkage = "MAPPING_TARGET_NOT_FOUND_IN_GRAPH"
        candidate_items = candidates_by_function.get(fid, []) if view else []
        runtime = runtime_by_function.get(fid) if view else None
        stages = {
            "requirement_to_business_function": linkage,
            "business_function_to_frontend_entrypoint": "LINKED" if view and view.get("frontend_entrypoint_count", len(view.get("frontend_entrypoints", []))) else "NOT_LINKED_OR_UNKNOWN",
            "business_function_to_api_endpoint": "LINKED" if view and view.get("endpoint_id") else "NOT_LINKED_OR_UNKNOWN",
            "api_to_backend_handler": (view.get("stage_status", {}).get("handler", "UNKNOWN") if view else "NOT_AVAILABLE"),
            "backend_call_chain": (view.get("stage_status", {}).get("calls", "UNKNOWN") if view else "NOT_AVAILABLE"),
            "backend_to_database": (view.get("stage_status", {}).get("database", "UNKNOWN") if view else "NOT_AVAILABLE"),
            "authorization_evidence": (view.get("stage_status", {}).get("authorization", "UNKNOWN") if view else "NOT_AVAILABLE"),
            "identity_resource_evidence": (view.get("stage_status", {}).get("identity_resource", "UNKNOWN") if view else "NOT_AVAILABLE"),
            "test_design": "CANDIDATE_LINKED" if candidate_items else ("NO_LINKED_TEST_CANDIDATE" if view else "NOT_AVAILABLE"),
            "test_execution": runtime.get("test_execution_status", "NOT_MEASURED") if runtime else "NOT_MEASURED",
            "code_coverage": runtime.get("code_coverage_status", "NOT_MEASURED") if runtime else "NOT_MEASURED",
        }
        row_status = "TRACEABLE_WITH_RUNTIME_EVIDENCE" if view and runtime and stages["test_execution"] in {"PASSED", "FAILED"} else (
            "TRACEABLE_TO_STATIC_FUNCTION_VIEW" if view else linkage)
        status_counts[row_status] += 1
        refs = []
        if isinstance(feature.get("source"), str):
            refs.append({"kind": "requirement", "path": feature["source"], "line": feature.get("line")})
        if view:
            for entry in view.get("frontend_entrypoints", []):
                if isinstance(entry, dict):
                    refs.append({"kind": "frontend_event", "path": entry.get("source_file"), "line": entry.get("event_line")})
                    refs.append({"kind": "frontend_api_call", "path": entry.get("api_call_file"), "line": entry.get("api_call_line")})
            for kind, field in (("backend_route", "source"), ("backend_handler", "handler")):
                value = view.get(field)
                if isinstance(value, dict):
                    refs.append({"kind": kind, "path": value.get("path"), "line": value.get("line")})
            for item in view.get("call_chain", []):
                if isinstance(item, dict):
                    loc = item.get("call_site") if isinstance(item.get("call_site"), dict) else {}
                    refs.append({"kind": "backend_call", "path": loc.get("path"), "line": loc.get("line")})
            for item in view.get("database_accesses", []):
                if isinstance(item, dict):
                    refs.append({"kind": "database_access", "path": item.get("path"), "line": item.get("line")})
            auth = view.get("authorization")
            auth_items = auth.get("evidence", []) if isinstance(auth, dict) else []
            for item in auth_items:
                if isinstance(item, dict):
                    refs.append({"kind": "authorization", "path": item.get("path"), "line": item.get("line")})
            for item in view.get("identity_resource_flow", []):
                if isinstance(item, dict):
                    refs.append({"kind": "identity_resource", "path": item.get("path"), "line": item.get("line")})
        refs = [item for item in refs if isinstance(item.get("path"), str)]
        result_rows.append({
            "feature_name": feature["name"], "feature_source": feature.get("source"), "feature_line": feature.get("line"),
            "mapping_decision": decision, "business_function_id": fid if view else None,
            "traceability_status": row_status, "stage_status": stages,
            "endpoint": {"method": view.get("method"), "path": view.get("endpoint")} if view else None,
            "test_candidate_ids": sorted(str(item.get("test_id")) for item in candidate_items if item.get("test_id") is not None),
            "runtime_evidence": runtime,
            "evidence_refs": refs,
            "review": {key: mapping.get(key) for key in ("reviewed_by", "reviewed_at", "rationale")} if mapping else None,
            "confidence": "STATIC_INFERENCE" if view else "UNKNOWN",
            "verification_status": "NOT_VERIFIED",
        })
    return {
        "schema_version": "usit-business-traceability-matrix-v1",
        "model": "ExplicitReviewGatedBusinessTraceability",
        "assessment_mode": "EXPLICIT_LINKS_ONLY_NO_NAME_INFERENCE",
        "input_hashes": dict(sorted((input_hashes or {}).items())),
        "source_graph_sha256": graph_hash,
        "feature_count": len(feature_index),
        "business_function_view_count": len(functions),
        "reviewed_mapping_count": len(mapping_by_feature),
        "traceability_status_counts": dict(sorted(status_counts.items())),
        "rows": result_rows,
        "unmapped_feature_count": sum(1 for row in result_rows if row["mapping_decision"] == "NOT_MAPPED"),
        "unlinked_runtime_function_ids": sorted(set(runtime_by_function) - set(functions)),
        "unlinked_test_design_function_ids": sorted(set(candidates_by_function) - set(functions)),
        "limitations": [
            "只有 mapping manifest 中逐项审阅并批准的精确 feature source/name → business_function_id 才建立需求追溯；不按名称、模块、目录或相邻代码猜测。",
            "静态 UI → API → endpoint → handler/call/DB/authorization/identity 关联不证明运行时页面可达、代码执行或业务正确。",
            "测试候选表示测试设计，不表示执行；运行时状态仅来自导入的测试证据。",
            "代码覆盖和权限/身份阶段状态仅表示输入工件中的证据状态，不证明安全性或业务正确性。",
            "映射 manifest 绑定精确 graph SHA-256；图谱变化后必须重新审阅或重新生成映射，旧映射不会自动迁移。",
        ],
    }
