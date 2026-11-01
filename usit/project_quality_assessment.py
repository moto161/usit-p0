"""Evidence-led project quality assessment; deliberately avoids a single quality score."""
from __future__ import annotations
from collections import Counter
from typing import Any


def assess_project_quality(
    semantics: dict[str, Any],
    business_graph: dict[str, Any],
    test_plan: dict[str, Any] | None = None,
    evidence_matrix: dict[str, Any] | None = None,
    gap_backlog: dict[str, Any] | None = None,
    input_hashes: dict[str, str] | None = None,
) -> dict[str, Any]:
    if not isinstance(semantics, dict) or not isinstance(semantics.get("features", []), list):
        raise ValueError("business semantics must contain a features list")
    if not isinstance(business_graph, dict) or business_graph.get("schema_version") != "usit-graph-v1":
        raise ValueError("business graph must use usit-graph-v1")
    understanding = business_graph.get("business_understanding")
    if not isinstance(understanding, dict):
        raise ValueError("business graph is missing business_understanding")
    views = understanding.get("business_function_views")
    if not isinstance(views, list):
        raise ValueError("business graph is missing business_function_views")
    if test_plan is not None and (not isinstance(test_plan, dict) or test_plan.get("schema_version") != "usit-business-test-plan-v1"):
        raise ValueError("test plan must use usit-business-test-plan-v1")
    if evidence_matrix is not None and (not isinstance(evidence_matrix, dict) or evidence_matrix.get("schema_version") != "usit-business-evidence-verification-v1"):
        raise ValueError("evidence matrix must use usit-business-evidence-verification-v1")
    if gap_backlog is not None and (not isinstance(gap_backlog, dict) or gap_backlog.get("schema_version") != "usit-verification-gap-backlog-v1"):
        raise ValueError("gap backlog must use usit-verification-gap-backlog-v1")

    functions: dict[str, dict[str, Any]] = {}
    for index, view in enumerate(views):
        if not isinstance(view, dict) or not isinstance(view.get("business_function_id"), str) or not view["business_function_id"]:
            raise ValueError(f"business_function_views[{index}] has invalid business_function_id")
        fid = view["business_function_id"]
        if fid in functions:
            raise ValueError(f"duplicate business_function_id: {fid}")
        functions[fid] = view

    features = [item for item in semantics.get("features", []) if isinstance(item, dict)]
    documented_links = understanding.get("documented_feature_linkage", [])
    if not isinstance(documented_links, list):
        raise ValueError("documented_feature_linkage must be a list")
    reviewed_names = {
        item.get("feature_name") for item in documented_links
        if isinstance(item, dict) and item.get("linkage_status") == "EXPLICIT_REVIEWED_LINK"
        and isinstance(item.get("feature_name"), str)
    }
    feature_names = [item.get("name") for item in features if isinstance(item.get("name"), str)]
    reviewed_feature_count = sum(1 for name in feature_names if name in reviewed_names)
    feature_count = len(feature_names)

    candidates = test_plan.get("candidates", []) if test_plan else []
    if not isinstance(candidates, list):
        raise ValueError("test_plan.candidates must be a list")
    design_ids = set()
    for item in candidates:
        if isinstance(item, dict) and isinstance(item.get("business_function_id"), str):
            design_ids.add(item["business_function_id"])
    unknown_design_ids = sorted(design_ids - set(functions))
    linked_design_ids = design_ids & set(functions)

    rows = evidence_matrix.get("business_function_evidence", []) if evidence_matrix else []
    if not isinstance(rows, list):
        raise ValueError("evidence_matrix.business_function_evidence must be a list")
    runtime: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or not isinstance(row.get("business_function_id"), str):
            raise ValueError(f"evidence_matrix row {index} has invalid business_function_id")
        fid = row["business_function_id"]
        if fid in runtime:
            raise ValueError(f"duplicate evidence row for business_function_id: {fid}")
        runtime[fid] = row
    unknown_runtime_ids = sorted(set(runtime) - set(functions))

    status_counts = Counter()
    coverage_counts = Counter()
    conflict_ids = []
    for fid in sorted(functions):
        row = runtime.get(fid)
        status = row.get("test_execution_status", "NOT_MEASURED") if row else "NOT_MEASURED"
        coverage = row.get("code_coverage_status", "NOT_MEASURED") if row else "NOT_MEASURED"
        if status not in {"PASSED", "FAILED", "UNKNOWN", "NOT_EXECUTED", "NOT_MEASURED"}:
            status = "INVALID"
        if coverage not in {"COVERED", "PARTIAL", "NOT_COVERED", "UNKNOWN", "NOT_MEASURED"}:
            coverage = "INVALID"
        status_counts[status] += 1
        coverage_counts[coverage] += 1
        if row and row.get("evidence_conflict"):
            conflict_ids.append(fid)

    endpoint_alignment = semantics.get("endpoint_contract_alignment", [])
    if not isinstance(endpoint_alignment, list):
        raise ValueError("endpoint_contract_alignment must be a list")
    alignment_counts = Counter(
        item.get("status", "UNKNOWN") for item in endpoint_alignment if isinstance(item, dict)
    )
    gap_items = gap_backlog.get("gaps", []) if gap_backlog else []
    if not isinstance(gap_items, list):
        raise ValueError("gap_backlog.gaps must be a list")
    gap_priority_counts = Counter(
        item.get("priority", "UNKNOWN") for item in gap_items if isinstance(item, dict)
    )
    gap_kind_counts = Counter(
        item.get("gap_kind", "UNKNOWN") for item in gap_items if isinstance(item, dict)
    )

    def ratio(numerator: int, denominator: int) -> dict[str, Any]:
        return {
            "numerator": numerator,
            "denominator": denominator,
            "percent": round(numerator * 100.0 / denominator, 2) if denominator else None,
            "status": "MEASURED" if denominator else "NOT_APPLICABLE_OR_NOT_FOUND",
        }

    dimensions = {
        "documented_feature_traceability": {
            **ratio(reviewed_feature_count, feature_count),
            "measured_meaning": "Features with an explicit reviewed feature-to-business-function linkage; not proof of implementation.",
        },
        "business_function_test_design": {
            **ratio(len(linked_design_ids), len(functions)),
            "measured_meaning": "Business functions with at least one test-plan candidate explicitly linked by business_function_id.",
        },
        "business_function_test_execution": {
            **ratio(status_counts["PASSED"] + status_counts["FAILED"], len(functions)),
            "measured_meaning": "Functions with imported PASSED or FAILED runtime evidence; not a pass rate.",
        },
        "business_function_test_pass": {
            **ratio(status_counts["PASSED"], status_counts["PASSED"] + status_counts["FAILED"]),
            "measured_meaning": "Passed functions among functions with a determinate PASSED/FAILED status; not business correctness.",
        },
        "business_function_code_coverage_measured": {
            **ratio(coverage_counts["COVERED"] + coverage_counts["PARTIAL"] + coverage_counts["NOT_COVERED"], len(functions)),
            "measured_meaning": "Functions with an explicit coverage status; line coverage does not prove branch or assertion quality.",
        },
    }

    return {
        "schema_version": "usit-project-quality-assessment-v1",
        "model": "EvidenceLedProjectQualityAssessment",
        "assessment_mode": "MULTIDIMENSIONAL_NO_AGGREGATE_SCORE",
        "input_hashes": dict(sorted((input_hashes or {}).items())),
        "inventory": {
            "documented_feature_count": feature_count,
            "business_function_view_count": len(functions),
            "endpoint_contract_alignment_count": len(endpoint_alignment),
            "test_design_candidate_count": len(candidates),
            "runtime_evidence_row_count": len(rows),
            "verification_gap_count": len(gap_items),
        },
        "dimensions": dimensions,
        "business_function_test_execution_status_counts": dict(sorted(status_counts.items())),
        "business_function_code_coverage_status_counts": dict(sorted(coverage_counts.items())),
        "endpoint_contract_alignment_status_counts": dict(sorted(alignment_counts.items())),
        "verification_gap_priority_counts": dict(sorted(gap_priority_counts.items())),
        "verification_gap_kind_counts": dict(sorted(gap_kind_counts.items())),
        "evidence_integrity": {
            "business_function_evidence_conflict_ids": conflict_ids,
            "unknown_test_design_business_function_ids": unknown_design_ids,
            "unknown_runtime_evidence_business_function_ids": unknown_runtime_ids,
            "status": "CONFLICTS_OR_UNLINKED_RECORDS_PRESENT" if conflict_ids or unknown_design_ids or unknown_runtime_ids else "NO_DETECTED_LINKAGE_CONFLICTS",
        },
        "limitations": [
            "本报告不计算单一质量总分，避免把未知、静态推断和运行时证据混为一谈。",
            "业务功能视图当前主要来自 endpoint-centred static slice，不代表项目全部用户功能。",
            "显式需求追溯关联不证明需求完整、功能实现或验收标准正确。",
            "测试设计候选不等于测试已执行；导入测试通过不证明业务正确。",
            "代码覆盖状态不证明分支覆盖、断言有效性、权限正确性或端到端流程可用。",
            "未知和未测量保持独立状态，不按零分或失败处理。",
            "本报告仅汇总输入工件；所有结论受输入范围、映射质量和证据新鲜度限制。",
        ],
    }
