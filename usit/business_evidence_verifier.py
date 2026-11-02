"""Conservative cross-validation of runtime test and coverage evidence."""
from __future__ import annotations
import hashlib
import json
from collections import Counter
from typing import Any

GRAPH_SCHEMA = "usit-graph-v1"
TEST_SCHEMA = "usit-test-execution-results-v1"
COVERAGE_SCHEMA = "usit-code-coverage-results-v1"


def _index(rows: Any, key: str, label: str) -> dict[str, dict[str, Any]]:
    if not isinstance(rows, list):
        raise ValueError(f"{label} must be a list")
    result = {}
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


def _validated_hashes(value: Any, required: tuple[str, ...], label: str) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} source_hashes must be an object")
    result = {}
    for field in required:
        digest = value.get(field)
        if (not isinstance(digest, str) or len(digest) != 64
                or any(char not in "0123456789abcdef" for char in digest)):
            raise ValueError(f"{label} source_hashes.{field} must be a lowercase SHA-256 hex digest")
        result[field] = digest
    return result


def verify_business_function_evidence(
    business_graph: dict[str, Any],
    test_results: dict[str, Any] | None = None,
    coverage_results: dict[str, Any] | None = None,
    business_graph_sha256: str | None = None,
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
    graph_hash = business_graph_sha256 or hashlib.sha256(
        json.dumps(business_graph, ensure_ascii=False, sort_keys=True,
                   separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    if (not isinstance(graph_hash, str) or len(graph_hash) != 64 or
            any(c not in "0123456789abcdef" for c in graph_hash)):
        raise ValueError("business_graph_sha256 must be a lowercase SHA-256 hex digest")

    conflicts = []
    test_by_id, coverage_by_id = {}, {}
    if test_results is not None:
        if not isinstance(test_results, dict) or test_results.get("schema_version") != TEST_SCHEMA:
            raise ValueError(f"test results must use {TEST_SCHEMA}")
        hashes = _validated_hashes(
            test_results.get("source_hashes"),
            ("test_report_sha256", "mapping_json_sha256", "business_graph_sha256"),
            "test results",
        )
        if hashes["business_graph_sha256"] != graph_hash:
            conflicts.append({"kind":"GRAPH_HASH_MISMATCH","source":"test_results",
                              "expected":graph_hash,"actual":hashes["business_graph_sha256"]})
        test_by_id = _index(test_results.get("business_function_results"),
                            "business_function_id","business_function_results")
    if coverage_results is not None:
        if not isinstance(coverage_results, dict) or coverage_results.get("schema_version") != COVERAGE_SCHEMA:
            raise ValueError(f"coverage results must use {COVERAGE_SCHEMA}")
        hashes = _validated_hashes(
            coverage_results.get("source_hashes"),
            ("coverage_report_sha256", "mapping_json_sha256", "business_graph_sha256"),
            "coverage results",
        )
        if hashes["business_graph_sha256"] != graph_hash:
            conflicts.append({"kind":"GRAPH_HASH_MISMATCH","source":"coverage_results",
                              "expected":graph_hash,"actual":hashes["business_graph_sha256"]})
        coverage_by_id = _index(coverage_results.get("business_function_coverage"),
                                "business_function_id","business_function_coverage")

    known = set(views)
    for source, records in (("test_results",test_by_id),("coverage_results",coverage_by_id)):
        for function_id in sorted(set(records)-known):
            conflicts.append({"kind":"UNKNOWN_BUSINESS_FUNCTION","source":source,
                              "business_function_id":function_id})

    matrix=[]
    for function_id, view in sorted(views.items()):
        test=test_by_id.get(function_id); cov=coverage_by_id.get(function_id)
        ts=test.get("execution_status","NOT_MEASURED") if test else "NOT_MEASURED"
        reported_test_status = ts
        cs=cov.get("code_coverage_status","NOT_MEASURED") if cov else "NOT_MEASURED"
        if ts not in {"PASSED","FAILED","UNKNOWN","NOT_EXECUTED","NOT_MEASURED"}:
            conflicts.append({"kind":"INVALID_TEST_STATUS","business_function_id":function_id,"status":ts})
        if cs not in {"COVERED","PARTIAL","NOT_COVERED","UNKNOWN","NOT_MEASURED"}:
            conflicts.append({"kind":"INVALID_COVERAGE_STATUS","business_function_id":function_id,"status":cs})

        test_case_evidence = []
        missing_test_case_ids = []
        completeness_status = "NOT_PROVIDED"
        if test is not None:
            raw_missing = test.get("mapped_cases_missing_from_report")
            if raw_missing is not None:
                if not isinstance(raw_missing, list) or any(
                    not isinstance(item, str) or not item.strip() for item in raw_missing
                ):
                    conflicts.append({
                        "kind": "INVALID_TEST_COMPLETENESS_METADATA",
                        "business_function_id": function_id,
                        "field": "mapped_cases_missing_from_report",
                    })
                else:
                    missing_test_case_ids = sorted(set(raw_missing))
            raw_cases = test.get("mapped_test_cases")
            if raw_cases is not None:
                if not isinstance(raw_cases, list):
                    conflicts.append({
                        "kind": "INVALID_TEST_CASE_EVIDENCE",
                        "business_function_id": function_id,
                    })
                else:
                    for index, item in enumerate(raw_cases):
                        if (not isinstance(item, dict)
                                or not isinstance(item.get("test_case_id"), str)
                                or not item["test_case_id"].strip()
                                or item.get("status") not in {"PASSED", "FAILED", "SKIPPED"}):
                            conflicts.append({
                                "kind": "INVALID_TEST_CASE_EVIDENCE",
                                "business_function_id": function_id,
                                "index": index,
                            })
                            continue
                        test_case_evidence.append({
                            "test_case_id": item["test_case_id"],
                            "status": item["status"],
                            "classname": item.get("classname"),
                            "name": item.get("name"),
                        })
            expected_count = test.get("expected_mapped_test_case_count")
            reported_count = test.get("reported_mapped_test_case_count")
            counts_valid = True
            for field_name, count in (
                ("expected_mapped_test_case_count", expected_count),
                ("reported_mapped_test_case_count", reported_count),
            ):
                if count is not None and (
                    not isinstance(count, int) or isinstance(count, bool) or count < 0
                ):
                    conflicts.append({
                        "kind": "INVALID_TEST_COMPLETENESS_METADATA",
                        "business_function_id": function_id,
                        "field": field_name,
                    })
                    counts_valid = False
            claimed_completeness = test.get("mapped_case_completeness")
            if claimed_completeness not in {None, "COMPLETE", "INCOMPLETE", "NO_EXPECTED_MAPPED_CASES"}:
                conflicts.append({
                    "kind": "INVALID_TEST_COMPLETENESS_METADATA",
                    "business_function_id": function_id,
                    "field": "mapped_case_completeness",
                    "status": claimed_completeness,
                })
            inferred_incomplete = bool(missing_test_case_ids) or claimed_completeness == "INCOMPLETE"
            if counts_valid and expected_count is not None and reported_count is not None:
                if reported_count > expected_count:
                    conflicts.append({
                        "kind": "INVALID_TEST_COMPLETENESS_METADATA",
                        "business_function_id": function_id,
                        "field": "reported_mapped_test_case_count",
                    })
                elif expected_count > reported_count:
                    inferred_incomplete = True
            if claimed_completeness == "COMPLETE" and inferred_incomplete:
                conflicts.append({
                    "kind": "CONFLICTING_TEST_COMPLETENESS_METADATA",
                    "business_function_id": function_id,
                })
            if ts == "PASSED" and inferred_incomplete:
                conflicts.append({
                    "kind": "TEST_PASS_WITH_INCOMPLETE_MAPPED_CASES",
                    "business_function_id": function_id,
                    "missing_test_case_ids": missing_test_case_ids,
                })
                ts = "UNKNOWN"

            # When the importer supplies testcase-level evidence, independently
            # derive the function status and cross-check its summary fields.
            # A hand-edited PASSED summary must never override observed failures
            # or incomplete testcase coverage.
            if isinstance(raw_cases, list):
                case_ids = [item["test_case_id"] for item in test_case_evidence]
                case_counts = Counter(item["status"] for item in test_case_evidence)
                if len(case_ids) != len(set(case_ids)):
                    conflicts.append({
                        "kind": "DUPLICATE_MAPPED_TEST_CASE_ID",
                        "business_function_id": function_id,
                    })
                observed_summaries = {
                    "mapped_test_case_count": len(test_case_evidence),
                    "passed": case_counts["PASSED"],
                    "failed": case_counts["FAILED"],
                    "skipped": case_counts["SKIPPED"],
                }
                for field_name, observed_value in observed_summaries.items():
                    supplied_value = test.get(field_name)
                    if supplied_value is not None and supplied_value != observed_value:
                        conflicts.append({
                            "kind": "TEST_CASE_SUMMARY_MISMATCH",
                            "business_function_id": function_id,
                            "field": field_name,
                            "supplied": supplied_value,
                            "observed": observed_value,
                        })
                if expected_count is None or reported_count is None:
                    derived_status = "UNKNOWN"
                elif case_counts["FAILED"]:
                    derived_status = "FAILED"
                elif expected_count == 0 and reported_count == 0 and not test_case_evidence:
                    derived_status = "NOT_EXECUTED"
                elif (expected_count > reported_count or inferred_incomplete
                      or case_counts["SKIPPED"] or reported_count != len(test_case_evidence)
                      or reported_count != expected_count):
                    derived_status = "UNKNOWN"
                elif case_counts["PASSED"] == len(test_case_evidence):
                    derived_status = "PASSED"
                else:
                    derived_status = "UNKNOWN"
                if ts != derived_status:
                    conflicts.append({
                        "kind": "TEST_EXECUTION_STATUS_MISMATCH",
                        "business_function_id": function_id,
                        "reported": ts,
                        "derived": derived_status,
                    })
                    ts = "FAILED" if derived_status == "FAILED" else "UNKNOWN"

            if inferred_incomplete:
                completeness_status = "INCOMPLETE"
            elif claimed_completeness in {"COMPLETE", "NO_EXPECTED_MAPPED_CASES"}:
                completeness_status = claimed_completeness
            elif raw_missing is not None:
                completeness_status = "COMPLETE"
        if ts=="FAILED": conclusion="TEST_FAILURE_OBSERVED"
        elif ts=="PASSED" and cs=="COVERED": conclusion="TEST_PASS_AND_CODE_LINES_COVERED"
        elif ts=="PASSED" and cs in {"PARTIAL","NOT_COVERED"}: conclusion="TEST_PASS_WITH_COVERAGE_GAP"
        elif ts=="PASSED": conclusion="TEST_PASS_COVERAGE_NOT_MEASURED"
        elif ts=="UNKNOWN": conclusion="TEST_EVIDENCE_INCONCLUSIVE"
        else: conclusion="NO_RUNTIME_TEST_EVIDENCE"
        matrix.append({
            "business_function_id":function_id,"endpoint_id":view.get("endpoint_id"),
            "basis":view.get("basis","UNKNOWN"),
            "static_verification_status":view.get("verification_status","UNKNOWN"),
            "test_execution_status":ts,
            "reported_test_execution_status":reported_test_status,
            "test_case_count":test.get("mapped_test_case_count",0) if test else 0,
            "failed_test_count":test.get("failed",0) if test else 0,
            "skipped_test_count":test.get("skipped",0) if test else 0,
            "test_execution_detail": {
                "source_hashes": dict(test.get("source_hashes", {})) if test else {},
                "execution_status_reason": test.get("execution_status_reason"),
                "expected_mapped_test_case_count": test.get("expected_mapped_test_case_count"),
                "reported_mapped_test_case_count": test.get("reported_mapped_test_case_count"),
                "mapped_case_completeness": completeness_status,
                "mapped_cases_missing_from_report": missing_test_case_ids,
                "mapped_test_cases": test_case_evidence,
            } if test else None,
            "code_coverage_status":cs,
            "line_coverage_percent":cov.get("line_coverage_percent") if cov else None,
            "verification_conclusion":conclusion,"business_correctness":"NOT_PROVEN",
            "evidence_conflict":False,
        })
    global_hash_conflict=any(x["kind"]=="GRAPH_HASH_MISMATCH" for x in conflicts)
    conflict_ids={x.get("business_function_id") for x in conflicts if x.get("business_function_id")}
    for row in matrix:
        if global_hash_conflict or row["business_function_id"] in conflict_ids:
            row["evidence_conflict"]=True
            row["verification_conclusion"]="EVIDENCE_CONFLICT"
    counts=Counter(x["verification_conclusion"] for x in matrix)
    return {
        "schema_version":"usit-business-evidence-verification-v1",
        "model":"BusinessFunctionEvidenceVerifier",
        "business_graph_sha256":graph_hash,
        "business_function_count":len(matrix),
        "verification_conclusion_counts":dict(sorted(counts.items())),
        "evidence_conflict_count":len(conflicts),
        "evidence_conflicts":conflicts,
        "business_function_evidence":matrix,
        "global_limitations":[
            "PASS 表示导入报告记录测试通过，不证明测试充分、需求完整或业务行为正确。",
            "代码行覆盖率不证明断言有效、分支覆盖完整、权限正确或端到端用户流程可用。",
            "静态业务功能视图仍是 STATIC_INFERENCE；本报告不会把它升级为 VERIFIED_FACT。",
            "测试结果与覆盖率必须引用同一份业务图；图哈希不一致时标记证据冲突。",
            "已映射 testcase 的报告完整性会被单独保留；缺失预期 testcase 的 PASSED 工件会被标记为证据冲突。",
            "未提供的测试或覆盖率证据标记 NOT_MEASURED，不推断为失败或零覆盖。",
        ],
    }
