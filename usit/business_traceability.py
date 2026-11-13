"""Build an explicit, review-gated requirements-to-runtime-evidence traceability matrix.

No feature-to-endpoint relation is inferred from names, modules, paths, or proximity.
Only an exact, reviewer-approved mapping manifest can connect a requirement feature
to an endpoint-centred business-function view.
"""
from __future__ import annotations
from collections import Counter
from datetime import datetime
from typing import Any


# G6: parser-scope codes for the static stages, and the exact graph
# limitation text each code encodes. Rows carry only the codes; the texts
# live in business_graph.py. A test builds a real graph and asserts every
# mapped text appears verbatim in its limitations, so editing a limitation
# without updating this table fails the test instead of silently drifting.
# Limitations 3 and 5 in that list are general no-inference statements,
# not a parser scope for one stage, so no code maps to them.
STAGE_PARSER_SCOPE: dict[str, tuple[str, ...]] = {
    "business_function_to_frontend_entrypoint": ("JSX_TSX_ONLY",),
    "business_function_to_api_endpoint": ("CALL_GRAPH_DEPTH_8",),
    "api_to_backend_handler": ("HANDLER_UNIQUE_SYMBOL_ONLY",),
    "backend_call_chain": ("CALL_GRAPH_DEPTH_8",),
    "backend_to_database": ("SQL_LITERAL_EXPRESSION_ONLY",),
    "authorization_evidence": ("AUTHORIZATION_LIMITED_PATTERNS_ONLY",),
    "identity_resource_evidence": ("SAME_LINE_IDENTITY_ONLY",),
}

PARSER_SCOPE_LIMITATION_TEXT: dict[str, tuple[str, ...]] = {
    "JSX_TSX_ONLY": (
        "前端交互目前仅识别 JSX/TSX 的有限事件属性与唯一命名 handler；尚未连接前端 handler 到 API client 和后端 endpoint，也未覆盖 Vue、Angular、XML 或 Jetpack Compose UI 模型。",
    ),
    "CALL_GRAPH_DEPTH_8": (
        "调用链仅沿唯一 RESOLVED 的图谱符号边进行有界静态展开；循环会标记，深度/数量上限会截断，不证明运行时执行。",
    ),
    "HANDLER_UNIQUE_SYMBOL_ONLY": (
        "业务记录只与精确来源文件关联；命名路由 handler 可通过同文件唯一符号或 TypeScript Compiler API 的导入符号证据建立候选引用。",
        "跨文件 handler 仅在编译器符号解析唯一且目标符号存在于当前图谱时关联；动态、外部、歧义或缺少符号证据时保留原状态，不按名称猜测。",
    ),
    "SQL_LITERAL_EXPRESSION_ONLY": (
        "数据库访问只识别字面量表达式（隐式拼接、`+` 链、多行与模板字面量；动态插值以 `?` 占位，仅静态骨架参与解析）；变量/调用/`.format()`/`%`/ORM/跨行变量传播仍不解析；表/列解析仅为静态候选，不证明实际读写、事务提交或字段数据流。",
    ),
    "AUTHORIZATION_LIMITED_PATTERNS_ONLY": (
        "权限证据仅识别有限的 route middleware 名称、显式权限调用名和简单 role/owner 条件；函数权限调用只有在同文件同调用行的唯一 caller_symbol_id 存在时才归属函数。未检测到权限线索不证明缺少授权，检测到线索也不证明运行时执行或检查正确。",
    ),
    "SAME_LINE_IDENTITY_ONLY": (
        "身份/资源数据流仅识别有限同一源码行模式与同一 Python 函数归属；多行数据库调用按括号配平拼接后判定 sink（至多 10 行，记录落调用起始行）；同行附着之外，handler 级身份线索（身份来源、请求控制身份输入、属主比较）在函数归属已解析时附着到同函数的数据库访问（SAME_FUNCTION_SCOPE，候选关联）；sink 绑定仍只认同行；JS/TS 暂无函数归属。未做跨函数返回值传播、ORM 映射或控制流分析；同行/同函数模式不证明运行时身份可信或资源授权正确。",
    ),
}


def _validate_expected_result_confirmation(candidate: dict[str, Any]) -> None:
    """B2 G4: a confirmation record, when present, must name a reviewer,
    a parseable timestamp, and a rationale. Absent means unconfirmed;
    malformed means fail-closed. This checks the record's shape only --
    it cannot verify the reviewer is the business owner (no identity
    system; reviewed_by is a free string)."""
    confirmation = candidate.get("expected_result_confirmation")
    if confirmation is None:
        return
    label = f"candidate {candidate.get('test_id') or '?'}"
    if not isinstance(confirmation, dict):
        raise ValueError(f"{label} expected_result_confirmation must be an object")
    if not isinstance(confirmation.get("reviewed_by"), str) or not confirmation["reviewed_by"].strip():
        raise ValueError(f"{label} expected_result_confirmation requires a non-empty reviewed_by")
    try:
        datetime.fromisoformat(str(confirmation.get("reviewed_at")).replace("Z", "+00:00"))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError(f"{label} expected_result_confirmation reviewed_at must be an ISO-8601 timestamp") from exc
    if not isinstance(confirmation.get("rationale"), str) or not confirmation["rationale"].strip():
        raise ValueError(f"{label} expected_result_confirmation requires a non-empty rationale")


def build_business_traceability(
    semantics: dict[str, Any],
    graph: dict[str, Any],
    mapping_manifest: dict[str, Any],
    test_plan: dict[str, Any] | None = None,
    evidence_matrix: dict[str, Any] | None = None,
    input_hashes: dict[str, str] | None = None,
    runtime_evidence_validation: dict[str, Any] | None = None,
    security_test_plan: dict[str, Any] | None = None,
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
    if security_test_plan is not None and (
        not isinstance(security_test_plan, dict)
        or security_test_plan.get("schema_version") != "usit-security-test-candidates-v1"
    ):
        raise ValueError("security test plan must use usit-security-test-candidates-v1")
    if security_test_plan is not None and not isinstance(security_test_plan.get("candidates", []), list):
        raise ValueError("security test plan candidates must be a list")
    if security_test_plan is not None and security_test_plan.get("source_business_graph_sha256") != graph_hash:
        raise ValueError("security test plan source_business_graph_sha256 does not match the supplied graph bytes")
    if evidence_matrix is not None and (not isinstance(evidence_matrix, dict) or evidence_matrix.get("schema_version") != "usit-business-evidence-verification-v1"):
        raise ValueError("evidence matrix must use usit-business-evidence-verification-v1")
    if evidence_matrix is not None and evidence_matrix.get("business_graph_sha256") not in {None, graph_hash}:
        raise ValueError("evidence matrix business_graph_sha256 does not match the supplied graph bytes")
    if runtime_evidence_validation is not None:
        if not isinstance(runtime_evidence_validation, dict) or runtime_evidence_validation.get("schema_version") != "usit-runtime-evidence-validation-v1":
            raise ValueError("runtime evidence validation must use usit-runtime-evidence-validation-v1")
        runtime_hashes = runtime_evidence_validation.get("source_hashes")
        if not isinstance(runtime_hashes, dict) or runtime_hashes.get("business_graph_sha256") != graph_hash:
            raise ValueError("runtime evidence business_graph_sha256 does not match the supplied graph bytes")
        if not isinstance(runtime_evidence_validation.get("business_function_ids_observed"), list):
            raise ValueError("runtime evidence validation is missing business_function_ids_observed")
        if not isinstance(runtime_evidence_validation.get("correlations"), list):
            raise ValueError("runtime evidence validation is missing correlations")
        conflicts = runtime_evidence_validation.get("evidence_conflicts", [])
        if not isinstance(conflicts, list):
            raise ValueError("runtime evidence validation evidence_conflicts must be a list")
        for index, conflict in enumerate(conflicts):
            if not isinstance(conflict, dict) or not isinstance(conflict.get("kind"), str) or not conflict["kind"].strip():
                raise ValueError(f"runtime evidence validation evidence_conflicts[{index}] is malformed")
            for field in ("event_id", "business_function_id", "parent_event_id", "expected", "actual"):
                value = conflict.get(field)
                if value is not None and not isinstance(value, str):
                    raise ValueError(f"runtime evidence validation evidence_conflicts[{index}].{field} must be a string")

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
        try:
            datetime.fromisoformat(mapping["reviewed_at"].replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"mappings[{index}].reviewed_at must be an ISO-8601 timestamp") from exc
        if decision == "APPROVED_LINK" and (not isinstance(mapping.get("business_function_id"), str) or not mapping["business_function_id"]):
            raise ValueError(f"mappings[{index}] approved link requires business_function_id")
        mapping_by_feature[key] = mapping

    candidates = test_plan.get("candidates", []) if test_plan else []
    if not isinstance(candidates, list):
        raise ValueError("test_plan.candidates must be a list")
    candidates_by_function: dict[str, list[dict[str, Any]]] = {}
    for candidate in candidates:
        if isinstance(candidate, dict) and isinstance(candidate.get("business_function_id"), str):
            _validate_expected_result_confirmation(candidate)
            candidates_by_function.setdefault(candidate["business_function_id"], []).append(candidate)
    security_candidates = security_test_plan.get("candidates", []) if security_test_plan else []
    unlinked_security_candidate_ids: list[str] = []
    for candidate in security_candidates:
        if not isinstance(candidate, dict):
            raise ValueError("security test plan contains a malformed candidate")
        function_id = candidate.get("business_function_id")
        if isinstance(function_id, str) and function_id and function_id in functions:
            _validate_expected_result_confirmation(candidate)
            candidates_by_function.setdefault(function_id, []).append(candidate)
        elif candidate.get("test_id") is not None:
            # A non-empty but unknown function ID is not a valid linkage.
            # Preserve the candidate as unlinked instead of silently dropping it.
            unlinked_security_candidate_ids.append(str(candidate.get("test_id")))

    rows = evidence_matrix.get("business_function_evidence", []) if evidence_matrix else []
    if not isinstance(rows, list):
        raise ValueError("evidence_matrix.business_function_evidence must be a list")
    runtime_observed_ids: set[str] = set()
    runtime_unknown_function_ids: set[str] = set()
    runtime_evidence_conflicts = list(runtime_evidence_validation.get("evidence_conflicts", [])) if runtime_evidence_validation else []
    runtime_correlations_by_function: dict[str, list[dict[str, Any]]] = {}
    if runtime_evidence_validation:
        observed_ids = runtime_evidence_validation["business_function_ids_observed"]
        if any(not isinstance(value, str) or not value for value in observed_ids):
            raise ValueError("runtime evidence validation contains malformed business_function_ids_observed")
        if len(observed_ids) != len(set(observed_ids)):
            raise ValueError("runtime evidence validation contains duplicate business_function_ids_observed")
        unknown_observed_ids = set(observed_ids) - set(functions)
        if unknown_observed_ids:
            raise ValueError("runtime evidence validation business_function_ids_observed contains unknown graph function IDs")
        runtime_observed_ids = set(observed_ids)
        seen_correlation_ids: set[str] = set()
        for correlation in runtime_evidence_validation["correlations"]:
            if not isinstance(correlation, dict) or not isinstance(correlation.get("correlation_id"), str) or not correlation["correlation_id"]:
                raise ValueError("runtime evidence validation contains malformed correlation")
            correlation_id = correlation["correlation_id"]
            if correlation_id in seen_correlation_ids:
                raise ValueError("runtime evidence validation contains duplicate correlation_id")
            seen_correlation_ids.add(correlation_id)
            observed_ids = correlation.get("business_function_ids", [])
            if not isinstance(observed_ids, list) or any(not isinstance(value, str) or not value for value in observed_ids):
                raise ValueError("runtime evidence validation contains malformed business_function_ids")
            if len(observed_ids) != len(set(observed_ids)):
                raise ValueError("runtime evidence validation contains duplicate correlation business_function_ids")
            parent_edges = correlation.get("explicit_parent_edges", [])
            if not isinstance(parent_edges, list) or any(
                not isinstance(edge, dict)
                or not isinstance(edge.get("parent_event_id"), str)
                or not isinstance(edge.get("child_event_id"), str)
                for edge in parent_edges
            ):
                raise ValueError("runtime evidence validation contains malformed explicit_parent_edges")
            event_ids = correlation.get("observed_event_ids_by_timestamp", [])
            if not isinstance(event_ids, list) or any(not isinstance(value, str) for value in event_ids):
                raise ValueError("runtime evidence validation contains malformed observed_event_ids_by_timestamp")
            # B2 G3(a): optional per-event attribution, supplied by the
            # runtime evidence validator. Validate shape fail-closed;
            # absent fields mean a B1-era validation document.
            declared_ids = correlation.get("declared_function_ids")
            if declared_ids is not None:
                if (not isinstance(declared_ids, list)
                        or any(not isinstance(value, str) or not value for value in declared_ids)
                        or len(declared_ids) != len(set(declared_ids))):
                    raise ValueError("runtime evidence validation contains malformed declared_function_ids")
            attribution = correlation.get("event_function_attribution")
            if attribution is None:
                attribution = []
            if not isinstance(attribution, list) or any(
                not isinstance(item, dict)
                or not isinstance(item.get("event_id"), str) or not item["event_id"]
                or item["event_id"] not in event_ids
                or not isinstance(item.get("business_function_id"), str) or not item["business_function_id"]
                or item["business_function_id"] not in observed_ids
                or not isinstance(item.get("stage"), str) or not item["stage"]
                for item in attribution
            ):
                raise ValueError("runtime evidence validation contains malformed event_function_attribution")
            attribution_status = correlation.get("attribution_status", "NO_DECLARATION")
            if attribution_status not in {"PER_EVENT_COLLECTOR_DECLARED", "PARTIAL_COLLECTOR_DECLARED", "NO_DECLARATION"}:
                raise ValueError("runtime evidence validation contains malformed attribution_status")
            unattributed_event_count = correlation.get("unattributed_event_count", 0)
            if not isinstance(unattributed_event_count, int) or isinstance(unattributed_event_count, bool) or unattributed_event_count < 0:
                raise ValueError("runtime evidence validation contains malformed unattributed_event_count")
            multi_function_correlation = len(observed_ids) > 1
            for observed_id in observed_ids:
                # B2 G3(a): when the validator attributed individual
                # events to this function (collector-declared), only
                # those events' stages and IDs belong to this function.
                # Parent topology stays correlation-level, never re-derived.
                own_attributed = [item for item in attribution if item.get("business_function_id") == observed_id]
                own_event_ids = {item["event_id"] for item in own_attributed}
                has_attribution = attribution_status != "NO_DECLARATION" and bool(own_attributed)
                if multi_function_correlation and has_attribution:
                    attribution_status_value = "PER_EVENT_COLLECTOR_DECLARED"
                    entry_event_count = len(own_attributed)
                    entry_stages = sorted({item["stage"] for item in own_attributed})
                    entry_event_ids = [event_id for event_id in event_ids if event_id in own_event_ids]
                    entry_edges = [
                        {"parent_event_id": edge["parent_event_id"], "child_event_id": edge["child_event_id"]}
                        for edge in parent_edges
                        if edge["parent_event_id"] in own_event_ids and edge["child_event_id"] in own_event_ids
                    ]
                    entry_topology = correlation.get("parent_topology_status", "NOT_PROVIDED")
                    entry_ratio = correlation.get("parent_link_coverage_ratio")
                    entry_roots = correlation.get("root_event_ids", [])
                    entry_leaves = correlation.get("leaf_event_ids", [])
                    entry_branching = correlation.get("branching_parent_event_ids", [])
                    entry_isolated = correlation.get("isolated_event_ids", [])
                    entry_cycles = correlation.get("cycle_event_ids", [])
                    interpretation = (
                        "该 correlation 的逐事件归属来自采集端声明与事件自带字段（未经独立验证）；"
                        "本功能只归属其声明事件的阶段与事件，父子拓扑仍为 correlation 层级，不按功能重算。"
                    )
                elif multi_function_correlation:
                    attribution_status_value = "SHARED_CORRELATION_EVENT_OWNERSHIP_UNRESOLVED"
                    entry_event_count = None
                    entry_stages = []
                    entry_event_ids = []
                    entry_edges = []
                    entry_topology = "NOT_ATTRIBUTABLE_ACROSS_FUNCTIONS"
                    entry_ratio = None
                    entry_roots = []
                    entry_leaves = []
                    entry_branching = []
                    entry_isolated = []
                    entry_cycles = []
                    interpretation = "该 correlation 显式关联多个业务功能，但当前摘要没有逐事件功能归属；不将阶段、事件或父子拓扑归属到单个功能。"
                else:
                    attribution_status_value = "SINGLE_FUNCTION_CORRELATION"
                    entry_event_count = correlation.get("event_count")
                    entry_stages = correlation.get("observed_stages", [])
                    entry_event_ids = list(event_ids)
                    entry_edges = [
                        {"parent_event_id": edge["parent_event_id"], "child_event_id": edge["child_event_id"]}
                        for edge in parent_edges
                    ]
                    entry_topology = correlation.get("parent_topology_status", "NOT_PROVIDED")
                    entry_ratio = correlation.get("parent_link_coverage_ratio")
                    entry_roots = correlation.get("root_event_ids", [])
                    entry_leaves = correlation.get("leaf_event_ids", [])
                    entry_branching = correlation.get("branching_parent_event_ids", [])
                    entry_isolated = correlation.get("isolated_event_ids", [])
                    entry_cycles = correlation.get("cycle_event_ids", [])
                    interpretation = "仅复用同一 correlation 中显式 business_function_id；父子边来自采集端声明，时间排序不代表因果。"
                runtime_correlations_by_function.setdefault(observed_id, []).append({
                    "correlation_id": correlation_id,
                    # A shared correlation can contain events owned by different
                    # business functions. Without per-event attribution, never
                    # copy its full stages/topology onto each function.
                    "function_ids_in_correlation": list(observed_ids),
                    "function_attribution_status": attribution_status_value,
                    "attribution_status": attribution_status,
                    "declared_function_ids": list(declared_ids) if declared_ids is not None else None,
                    "event_count": entry_event_count,
                    "observed_stages": entry_stages,
                    "observed_event_ids_by_timestamp": entry_event_ids,
                    "explicit_parent_edges": entry_edges,
                    "parent_topology_status": entry_topology,
                    "parent_link_coverage_ratio": entry_ratio,
                    "root_event_ids": entry_roots,
                    "leaf_event_ids": entry_leaves,
                    "branching_parent_event_ids": entry_branching,
                    "isolated_event_ids": entry_isolated,
                    "cycle_event_ids": entry_cycles,
                    "interpretation": interpretation,
                })
        correlation_ids_all = set(runtime_correlations_by_function)
        correlation_observed_ids = correlation_ids_all & set(functions)
        runtime_unknown_function_ids = correlation_ids_all - set(functions)
        declared_unknown_ids = {
            item["business_function_id"] for item in runtime_evidence_conflicts
            if item.get("kind") == "UNKNOWN_BUSINESS_FUNCTION" and isinstance(item.get("business_function_id"), str)
        }
        if runtime_unknown_function_ids - declared_unknown_ids:
            raise ValueError("unknown correlation business_function_ids require UNKNOWN_BUSINESS_FUNCTION conflict details")
        if correlation_observed_ids != runtime_observed_ids:
            raise ValueError(
                "runtime evidence business_function_ids_observed does not match "
                "the known function IDs declared by correlations"
            )
    # B2: a conflict naming a business_function_id or a correlation_id
    # is attributable to that function/correlation, not to the whole
    # validation. Only conflicts attributable to neither (e.g.
    # GRAPH_HASH_MISMATCH) taint every row's runtime observation.
    runtime_global_conflict = bool(runtime_evidence_validation and runtime_evidence_validation.get("assessment_status") == "EVIDENCE_CONFLICT" and (not runtime_evidence_conflicts or any(not item.get("business_function_id") and not item.get("correlation_id") for item in runtime_evidence_conflicts)))
    runtime_conflict_function_ids = {item["business_function_id"] for item in runtime_evidence_conflicts if isinstance(item.get("business_function_id"), str)}
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
        test_evidence_conflict = bool(runtime and (
            runtime.get("evidence_conflict")
            or runtime.get("verification_conclusion") == "EVIDENCE_CONFLICT"
        ))
        test_execution_detail = runtime.get("test_execution_detail") if isinstance(runtime, dict) else None
        test_execution_detail = test_execution_detail if isinstance(test_execution_detail, dict) else {}
        case_completeness = test_execution_detail.get("mapped_case_completeness", "NOT_PROVIDED")
        runtime_status = runtime.get("test_execution_status", "NOT_MEASURED") if runtime else "NOT_MEASURED"
        coverage_status = runtime.get("code_coverage_status", "NOT_MEASURED") if runtime else "NOT_MEASURED"
        function_runtime_conflict = bool(fid and fid in runtime_conflict_function_ids)
        # B1 G3: a function inside a shared correlation has no per-event
        # ownership, so its observation is unresolved, never a clean
        # RUNTIME_OBSERVED -- even though its events exist.
        shared_correlation = bool(fid and any(
            item.get("function_attribution_status") == "SHARED_CORRELATION_EVENT_OWNERSHIP_UNRESOLVED"
            for item in runtime_correlations_by_function.get(fid, [])
        ))
        # B1 G2: a runtime/static mismatch found by the evidence
        # verifier downgrades this stage as well (the row status already
        # becomes EVIDENCE_CONFLICT through test_evidence_conflict).
        verifier_runtime_mismatch = bool(
            runtime and runtime.get("runtime_consistency_status") == "MISMATCH")
        runtime_observation_status = "EVIDENCE_CONFLICT" if runtime_global_conflict or function_runtime_conflict or verifier_runtime_mismatch else (
            "SHARED_CORRELATION_EVENT_OWNERSHIP_UNRESOLVED" if shared_correlation else (
            "RUNTIME_OBSERVED" if fid in runtime_observed_ids else ("NOT_OBSERVED" if runtime_evidence_validation else "NOT_MEASURED")))
        valid_runtime_statuses = {"PASSED", "FAILED", "UNKNOWN", "NOT_EXECUTED", "NOT_MEASURED"}
        valid_coverage_statuses = {"COVERED", "PARTIAL", "NOT_COVERED", "UNKNOWN", "NOT_MEASURED"}
        if runtime_status not in valid_runtime_statuses:
            runtime_status = "INVALID"
        if coverage_status not in valid_coverage_statuses:
            coverage_status = "INVALID"
        # B2 G4: the expected-result confirmation gate, per function
        # (not per test case: executed test_case_ids and candidate
        # test_ids live in different ID spaces with no link, so the gate
        # can only say whether this function has any confirmed oracle).
        # C1 refines it per test case when the executed cases carry a
        # candidate_test_id (reserved by the planner): a PASSED case
        # owned by a confirmed candidate supports the row; PASSED cases
        # owned only by unconfirmed candidates do not. Without any
        # candidate-linked case, the B2 per-function gate still applies.
        # A PASSED execution behind an unconfirmed oracle cannot support
        # the row: its test_execution stage becomes UNKNOWN. FAILED stays
        # FAILED -- a failure is observed regardless of oracle status.
        confirmed_candidate_ids = {
            str(item.get("test_id")) for item in candidate_items
            if isinstance(item, dict) and isinstance(item.get("expected_result_confirmation"), dict)
            and item.get("test_id") is not None
        }
        mapped_cases = test_execution_detail.get("mapped_test_cases", []) if isinstance(test_execution_detail, dict) else []
        linked_cases = [
            case for case in mapped_cases
            if isinstance(case, dict) and isinstance(case.get("candidate_test_id"), str)
        ]
        if not candidate_items:
            expected_result_confirmation_status = "NOT_APPLICABLE"
            expected_result_confirmation_scope = "NOT_APPLICABLE"
        elif linked_cases:
            expected_result_confirmation_scope = "PER_TEST_CASE"
            passed_linked = [case for case in linked_cases if case.get("status") == "PASSED"]
            if any(case["candidate_test_id"] in confirmed_candidate_ids for case in passed_linked):
                expected_result_confirmation_status = "CONFIRMED"
            else:
                expected_result_confirmation_status = "UNCONFIRMED"
        elif confirmed_candidate_ids:
            expected_result_confirmation_status = "CONFIRMED"
            expected_result_confirmation_scope = "PER_FUNCTION"
        else:
            expected_result_confirmation_status = "UNCONFIRMED"
            expected_result_confirmation_scope = "PER_FUNCTION"
        if expected_result_confirmation_status == "UNCONFIRMED" and runtime_status == "PASSED":
            runtime_status = "UNKNOWN"
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
            "test_execution": runtime_status,
            "test_source_binding": runtime.get("test_source_binding_status", "NOT_PROVIDED") if runtime else "NOT_MEASURED",
            "test_source_snapshot_binding": runtime.get("test_source_snapshot_binding_status", "NOT_PROVIDED") if runtime else "NOT_MEASURED",
            "test_source_report_locator": runtime.get("test_source_report_locator_status", "NOT_PROVIDED") if runtime else "NOT_MEASURED",
            "test_evidence_integrity": (
                "EVIDENCE_CONFLICT" if test_evidence_conflict else
                case_completeness if case_completeness in {
                    "COMPLETE", "INCOMPLETE", "NO_EXPECTED_MAPPED_CASES"
                } else "NOT_MEASURED"
            ),
            "code_coverage": coverage_status,
            "runtime_observation": runtime_observation_status,
        }
        # B1 G3: a shared-correlation function never rises to
        # TRACEABLE_WITH_RUNTIME_EVIDENCE, even when its tests pass --
        # the runtime observation cannot be attributed to it alone. It
        # caps at TRACEABLE_WITH_INCONCLUSIVE_TEST_EVIDENCE.
        row_status = "EVIDENCE_CONFLICT" if view and (runtime_global_conflict or function_runtime_conflict or test_evidence_conflict) else (
            "TRACEABLE_WITH_RUNTIME_EVIDENCE" if view and runtime and stages["test_execution"] in {"PASSED", "FAILED"} and not shared_correlation and expected_result_confirmation_status != "UNCONFIRMED" else (
            "TRACEABLE_WITH_INCONCLUSIVE_TEST_EVIDENCE" if view and runtime and (stages["test_execution"] == "UNKNOWN" or (shared_correlation and stages["test_execution"] in {"PASSED", "FAILED"})) else (
            "TRACEABLE_WITH_RUNTIME_OBSERVATION" if view and runtime_observation_status == "RUNTIME_OBSERVED" else (
            "TRACEABLE_TO_STATIC_FUNCTION_VIEW" if view else linkage))))
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
            # G6: the parser scope of each static stage, taken from the
            # central table above. Stage status values are unchanged;
            # this only annotates the aperture each status was reached in.
            "stage_parser_scope": {stage: list(codes) for stage, codes in STAGE_PARSER_SCOPE.items()},
            # B2 G4: whether any linked candidate's expected result was
            # confirmed (CONFIRMED / UNCONFIRMED / NOT_APPLICABLE).
            # C1: which granularity the gate used (PER_TEST_CASE when
            # executed cases carried a candidate_test_id, else
            # PER_FUNCTION).
            "expected_result_confirmation_status": expected_result_confirmation_status,
            "expected_result_confirmation_scope": expected_result_confirmation_scope,
            "endpoint": {"method": view.get("method"), "path": view.get("endpoint")} if view else None,
            "test_candidate_ids": sorted(str(item.get("test_id")) for item in candidate_items if item.get("test_id") is not None),
            "test_candidates": [
                {
                    "test_id": item.get("test_id"),
                    "test_case_id": item.get("test_case_id"),
                    "category": item.get("category"),
                    "target": item.get("target"),
                    "purpose": item.get("purpose"),
                    "design_status": item.get("design_status", "UNKNOWN"),
                    "execution_status": item.get("execution_status", "NOT_EXECUTED"),
                    "preconditions": item.get("preconditions", []),
                    "actions": item.get("actions", []),
                    "expected_result": item.get("expected_result"),
                    "expected_results_status": item.get("expected_results_status", "OWNER_CONFIRMATION_REQUIRED"),
                    "expected_result_confirmation": item.get("expected_result_confirmation"),
                    "evidence_requirements": item.get("evidence_requirements", []),
                    "evidence_refs": item.get("evidence_refs", []),
                    "limitations": item.get("limitations", []),
                    "basis": item.get("basis", "STATIC_INFERENCE"),
                    "call_chain_evidence": item.get("call_chain_evidence", []),
                    "authorization_evidence": item.get("authorization_evidence", {}),
                    "database_access_evidence": item.get("database_access_evidence", []),
                    "identity_dataflow_evidence": item.get("identity_dataflow_evidence", []),
                }
                for item in sorted(candidate_items, key=lambda value: str(value.get("test_id", "")))
            ],
            "runtime_evidence": runtime,
            "runtime_observation": {
                "status": runtime_observation_status,
                # B1 G2: pass through the verifier's per-function
                # runtime/static consistency result for this row.
                "consistency": runtime.get("runtime_consistency_status") if isinstance(runtime, dict) else None,
                "correlation_ids": sorted(
                    item["correlation_id"] for item in runtime_correlations_by_function.get(fid, [])
                ) if fid else [],
                "correlations": sorted(
                    runtime_correlations_by_function.get(fid, []),
                    key=lambda item: item["correlation_id"],
                ) if fid else [],
            } if runtime_evidence_validation else None,
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
        "runtime_evidence_conflicts": runtime_evidence_conflicts,
        "runtime_evidence_conflict_count": len(runtime_evidence_conflicts),
        "unknown_runtime_function_ids": sorted(runtime_unknown_function_ids),
        "rows": result_rows,
        "unmapped_feature_count": sum(1 for row in result_rows if row["mapping_decision"] == "NOT_MAPPED"),
        "unlinked_runtime_function_ids": sorted(set(runtime_by_function) - set(functions)),
        "unlinked_test_design_function_ids": sorted(set(candidates_by_function) - set(functions)),
        "unlinked_security_test_candidate_ids": sorted(unlinked_security_candidate_ids),
        "invalid_runtime_status_function_ids": sorted(fid for fid, row in runtime_by_function.items()
            if row.get("test_execution_status", "NOT_MEASURED") not in {"PASSED", "FAILED", "UNKNOWN", "NOT_EXECUTED", "NOT_MEASURED"}
            or row.get("code_coverage_status", "NOT_MEASURED") not in {"COVERED", "PARTIAL", "NOT_COVERED", "UNKNOWN", "NOT_MEASURED"}),
        "limitations": [
            "只有 mapping manifest 中逐项审阅并批准的精确 feature source/name → business_function_id 才建立需求追溯；不按名称、模块、目录或相邻代码猜测。",
            "静态 UI → API → endpoint → handler/call/DB/authorization/identity 关联不证明运行时页面可达、代码执行或业务正确。",
            "测试候选表示测试设计，不表示执行；测试执行状态仅来自测试报告导入。运行时事件观察单独记录为 RUNTIME_OBSERVED，不等于测试通过。",
            "运行时事件按显式 business_function_id 和 correlation_id 关联；无显式关联的事件不会映射到需求功能。",
            "代码覆盖和权限/身份阶段状态仅表示输入工件中的证据状态，不证明安全性或业务正确性。",
            "映射 manifest 绑定精确 graph SHA-256；图谱变化后必须重新审阅或重新生成映射，旧映射不会自动迁移。",
            "共享 correlation 的运行时观察不归属到单个功能；该功能的行状态不升为 TRACEABLE_WITH_RUNTIME_EVIDENCE，观察阶段记 SHARED_CORRELATION_EVENT_OWNERSHIP_UNRESOLVED。",
            "共享 correlation 有逐事件归属时，归属来自采集端声明与事件自带字段，未经独立验证；声明与事件一致只证明自述无矛盾，不证明事件真实属于该功能；父子拓扑仍为 correlation 层级。",
            "预期结果确认门禁在用例带 planner 预留 test_case_id 时按用例判定，否则按功能判定；无候选归属的用例不能证明通过的恰好是已确认的候选。",
            "逐用例确认门禁只覆盖用了 planner 预留 test_case_id 的用例；手写映射、无候选归属的用例仍走逐功能门禁；预留 ID 本身不证明用例真的测了该候选（用例体与 ID 均由作者填写，错填 ID 会导致错误归属）。",
            "预期结果确认记录（reviewed_by/reviewed_at/rationale）只校验存在与格式合法，不验证确认人确为业务负责人；业务负责人身份由流程保证。",
            "运行时一致性只比对事件显式字段与静态视图；UI 组件名与静态入口文件无可靠等值关系时记 NOT_CHECKED，不判冲突。",
            "reviewed_by/reviewed_at/rationale 是审阅者提供的审计元数据，不构成身份认证或电子签名。",
        ],
    }
