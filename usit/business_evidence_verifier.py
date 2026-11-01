"""Cross-check imported runtime test and code-coverage evidence per business function.

This module does not promote static inference or line coverage into proof of
business correctness. It validates artifact identity, schema and function IDs,
then reports a conservative evidence matrix.
"""
from __future__ import annotations
import hashlib
from collections import Counter
from typing import Any

GRAPH_SCHEMA = "usit-graph-v1"
TEST_SCHEMA = "usit-test-execution-results-v1"
COVERAGE_SCHEMA = "usit-code-coverage-results-v1"


def _index(rows: Any, key: str, label: str) -> dict[str, dict[str, Any]]:
    if not isinstance(rows, list):
        raise ValueError(f"{label} must be a list")
    result: dict[str, dict[str, Any]] = {}
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f"{label}[{i}] must be an object")
        identity = row.get(key)
        if not isinstance(identity, str) or not identity:
            raise ValueError(f"{label}[{i}] missing {key}")
        if identity in result:
            raise ValueError(f"duplicate {key} in {label}: {identity}")
        result[identity] = row
    return result


def verify_business_function_evidence(
    business_graph: dict[str, Any],
    test_results: dict[str, Any] | None = None,
    coverage_results: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not isinstance(business_graph, dict) or business_graph.get("schema_version") != GRAPH_SCHEMA:
        raise ValueError("business graph must use usit-graph-v1")
    understanding = business_graph.get("business_understanding")
    if not isinstance(understanding, dict):
        raise ValueError("business graph is missing business_understanding")
    views = _index(understanding.get("business_function_views"), "business_function_id",
                  "business_function_views")
    if not views:
        raise ValueError("business graph contains no business function views")

    graph_hash = hashlib.sha256(
        __import__("json").dumps(business_graph, ensure_ascii=False, sort_keys=True,
                                 separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    # Importers hash the exact input bytes, so callers may supply the original
    # graph hash explicitly. Without it, the verifier compares normalized JSON.
    conflicts: list[dict[str, Any]] = []

    test_by_id: dict[str, dict[str, Any]] = {}
    if test_results is not None:
        if not isinstance(test_results, dict) or test_results.get("schema_version") != TEST_SCHEMA:
            raise ValueError(f"test results must use {TEST_SCHEMA}")
        hashes = test_results.get("source_hashes")
        if not isinstance(hashes, dict) or not isinstance(hashes.get("business_graph_sha256"), str):
            raise ValueError("test results missing business_graph_sha256")
        if hashes["business_graph_sha256"] != graph_hash:
            conflicts.append({"kind": "GRAPH_HASH_MISMATCH", "source": "test_results",
                              "expected": graph_hash, "actual": hashes["business_graph_sha256"]})
        test_by_id = _index(test_results.get("business_function_results"),
                            "business_function_id", "business_function_results")

    coverage_by_id: dict[str, dict[str, Any]] = {}
    if coverage_results is not None:
        if not isinstance(coverage_results, dict) or coverage_results.get("schema_version") != COVERAGE_SCHEMA:
            raise ValueError(f"coverage results must use {COVERAGE_SCHEMA}")
        hashes = coverage_results.get("source_hashes")
        if not isinstance(hashes, dict) or not isinstance(hashes.get("business_graph_sha256"), str):
            raise ValueError("coverage results missing business_graph_sha256")
        if hashes["business_graph_sha256"] != graph_hash:
            conflicts.append({"kind": "GRAPH_HASH_MISMATCH", "source": "coverage_results",
                              "expected": graph_hash, "actual": hashes["business_graph_sha256"]})
        coverage_by_id = _index(coverage_results.get("business_function_coverage"),
                                "business_function_id", "business_function_coverage")

    known = set(views)
    for source, records in (("test_results", test_by_id), ("coverage_results", coverage_by_id)):
        for function_id in sorted(set(records) - known):
            conflicts.append({"kind": "UNKNOWN_BUSINESS_FUNCTION", "source": source,
                              "business_function_id": function_id})

    matrix = []
    for function_id, view in sorted(views.items()):
        test = test_by_id.get(function_id)
        cov = coverage_by_id.get(function_id)
        test_status = test.get("execution_status", "NOT_MEASURED") if test else "NOT_MEASURED"
        coverage_status = cov.get("code_coverage_status", "NOT_MEASURED") if cov else "NOT_MEASURED"
        valid_test = test_status in {"PASSED", "FAILED", "UNKNOWN", "NOT_EXECUTED", "NOT_MEASURED"}
        valid_cov = coverage_status in {"COVERED", "PARTIAL", "NOT_COVERED", "UNKNOWN", "NOT_MEASURED"}
        if not valid_test:
            conflicts.append({"kind": "INVALID_TEST_STATUS", "business_function_id": function_id,
                              "status": test_status})
        if not valid_cov:
            conflicts.append({"kind": "INVALID_COVERAGE_STATUS", "business_function_id": function_id,
                              "status": coverage_status})
        if test_status == "FAILED":
            conclusion = "TEST_FAILURE_OBSERVED"
        elif test_status == "PASSED" and coverage_status == "COVERED":
            conclusion = "TEST_PASS_AND_CODE_LINES_COVERED"
        elif test_status == "PASSED" and coverage_status in {"PARTIAL", "NOT_COVERED"}:
            conclusion = "TEST_PASS_WITH_COVERAGE_GAP"
        elif test_status == "PASSED":
            conclusion = "TEST_PASS_COVERAGE_NOT_MEASURED"
        elif test_status in {"UNKNOWN"}:
            conclusion = "TEST_EVIDENCE_INCONCLUSIVE"
        else:
            conclusion = "NO_RUNTIME_TEST_EVIDENCE"
        matrix.append({
            "business_function_id": function_id,
            "endpoint_id": view.get("endpoint_id"),
            "basis": view.get("basis", "UNKNOWN"),
            "static_verification_status": view.get("verification_status", "UNKNOWN"),
            "test_execution_status": test_status,
            "test_case_count": test.get("mapped_test_case_count", 0) if test else 0,
            "failed_test_count": test.get("failed", 0) if test else 0,
            "skipped_test_count": test.get("skipped", 0) if test else 0,
            "code_coverage_status": coverage_status,
            "line_coverage_percent": cov.get("line_coverage_percent") if cov else None,
            "verification_conclusion": conclusion,
            "business_correctness": "NOT_PROVEN",
            "evidence_conflict": False,
        })

    conflict_ids = {x.get("business_function_id") for x in conflicts if x.get("business_function_id")}
    if conflicts:
        for row in matrix:
            if row["business_function_id"] in conflict_ids or any(
                x.get("kind") == "GRAPH_HASH_MISMATCH" for x in conflicts
            ):
                row["evidence_conflict"] = True
                row["verification_conclusion"] = "EVIDENCE_CONFLICT"
                row["business_correctness"] = "NOT_PROVEN"

    conclusions = Counter(row["verification_conclusion"] for row in matrix)
    return {
        "schema_version": "usit-business-evidence-verification-v1",
        "model": "BusinessFunctionEvidenceVerifier",
        "business_graph_sha256_normalized": graph_hash,
        "business_function_count": len(matrix),
        "verification_conclusion_counts": dict(sorted(conclusions.items())),
        "evidence_conflict_count": len(conflicts),
        "evidence_conflicts": conflicts,
        "business_function_evidence": matrix,
        "global_limitations": [
            "PASS 表示导入报告记录测试通过，不证明测试充分、需求完整或业务行为正确。",
            "代码行覆盖率不证明断言有效、分支覆盖完整、权限正确或端到端用户流程可用。",
            "静态业务功能视图仍是 STATIC_INFERENCE；本报告不会把它升级为 VERIFIED_FACT。",
            "测试结果与覆盖率必须引用同一份业务图；图哈希不一致时标记证据冲突。",
            "未提供的测试或覆盖率证据标记 NOT_MEASURED，不推断为失败或零覆盖。",
        ],
    }
