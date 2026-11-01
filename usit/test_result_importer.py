"""Import real JUnit XML results and link them only through an explicit mapping.

No test commands are executed here. JUnit outcomes are execution evidence;
business-function linkage is supplied by a reviewed mapping file, never guessed.
"""
from __future__ import annotations

import hashlib
import json
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .test_report_adapters import parse_pytest_json

SCHEMA_VERSION = "usit-test-execution-results-v1"
MAX_REPORT_BYTES = 20 * 1024 * 1024
MAX_TESTCASES = 200_000


def _read_regular(path: str | Path, limit: int) -> bytes:
    source = Path(path).expanduser()
    if source.is_symlink():
        raise ValueError("input must not be a symbolic link")
    resolved = source.resolve(strict=True)
    if not resolved.is_file():
        raise ValueError("input must be a regular file")
    data = resolved.read_bytes()
    if len(data) > limit:
        raise ValueError(f"input exceeds size limit ({limit} bytes)")
    return data


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _case_id(classname: str, name: str) -> str:
    return f"{classname}::{name}" if classname else name


def _parse_junit(data: bytes) -> list[dict[str, Any]]:
    # ElementTree does not resolve external entities, and explicitly rejecting
    # declarations keeps the accepted report format deliberately conservative.
    upper = data[: min(len(data), 1_000_000)].upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise ValueError("JUnit XML with DTD/entity declarations is not accepted")
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise ValueError(f"invalid JUnit XML: {exc}") from exc
    if root.tag not in {"testsuite", "testsuites"}:
        raise ValueError("JUnit XML root must be testsuite or testsuites")
    cases = []
    for element in root.iter("testcase"):
        classname = (element.attrib.get("classname") or "").strip()
        name = (element.attrib.get("name") or "").strip()
        if not name:
            raise ValueError("JUnit testcase is missing a name")
        outcomes = [child.tag for child in list(element)
                    if child.tag in {"failure", "error", "skipped"}]
        status = "FAILED" if any(x in outcomes for x in ("failure", "error")) else (
            "SKIPPED" if "skipped" in outcomes else "PASSED"
        )
        cases.append({
            "test_case_id": _case_id(classname, name),
            "classname": classname or None,
            "name": name,
            "status": status,
            "failure_count": outcomes.count("failure"),
            "error_count": outcomes.count("error"),
            "skipped_count": outcomes.count("skipped"),
        })
        if len(cases) > MAX_TESTCASES:
            raise ValueError(f"JUnit testcase limit exceeded ({MAX_TESTCASES})")
    if not cases:
        raise ValueError("JUnit report contains no testcase elements")
    counts = Counter(item["test_case_id"] for item in cases)
    duplicates = sorted(key for key, count in counts.items() if count > 1)
    if duplicates:
        raise ValueError("duplicate JUnit testcase IDs are ambiguous: " + ", ".join(duplicates[:10]))
    return sorted(cases, key=lambda item: item["test_case_id"])


def ingest_junit_results(
    report_path: str | Path,
    mapping_path: str | Path,
    business_graph_path: str | Path,
    report_format: str = "junit-xml",
) -> dict[str, Any]:
    """Parse JUnit and compute function status using explicit testcase mappings.

    Mapping JSON:
      {"schema_version":"usit-test-result-mapping-v1",
       "mappings":[{"test_case_id":"pkg.Test::test_upload",
                    "business_function_id":"bf-..."}]}
    """
    report_bytes = _read_regular(report_path, MAX_REPORT_BYTES)
    mapping_bytes = _read_regular(mapping_path, 2 * 1024 * 1024)
    graph_bytes = _read_regular(business_graph_path, 50 * 1024 * 1024)
    try:
        mapping_doc = json.loads(mapping_bytes)
        graph_doc = json.loads(graph_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid JSON input: {exc}") from exc
    if not isinstance(mapping_doc, dict) or mapping_doc.get("schema_version") != "usit-test-result-mapping-v1":
        raise ValueError("mapping schema_version must be usit-test-result-mapping-v1")
    mappings = mapping_doc.get("mappings")
    if not isinstance(mappings, list):
        raise ValueError("mapping.mappings must be a list")
    if not isinstance(graph_doc, dict) or graph_doc.get("schema_version") != "usit-business-knowledge-graph-v1":
        # Current graph envelope keeps the original graph schema; validate the
        # required business-understanding projection instead of assuming a new
        # top-level schema version.
        if not isinstance(graph_doc, dict) or not isinstance(graph_doc.get("business_understanding"), dict):
            raise ValueError("business graph must contain business_understanding")
    understanding = graph_doc.get("business_understanding")
    if not isinstance(understanding, dict):
        raise ValueError("business graph must contain business_understanding")
    views = understanding.get("business_function_views")
    if not isinstance(views, list):
        raise ValueError("business graph is missing business_function_views")
    known_functions = {}
    for view in views:
        if not isinstance(view, dict):
            continue
        function_id = view.get("business_function_id")
        if isinstance(function_id, str) and function_id:
            if function_id in known_functions:
                raise ValueError(f"duplicate business_function_id in graph: {function_id}")
            known_functions[function_id] = view
    if not known_functions:
        raise ValueError("business graph contains no business function views")

    explicit_map: dict[str, str] = {}
    for index, item in enumerate(mappings):
        if not isinstance(item, dict):
            raise ValueError(f"mapping entry {index} must be an object")
        case_id = item.get("test_case_id")
        function_id = item.get("business_function_id")
        if not isinstance(case_id, str) or not case_id.strip():
            raise ValueError(f"mapping entry {index} has invalid test_case_id")
        if not isinstance(function_id, str) or function_id not in known_functions:
            raise ValueError(f"mapping entry {index} references unknown business_function_id")
        case_id = case_id.strip()
        if case_id in explicit_map:
            raise ValueError(f"duplicate mapping for test_case_id: {case_id}")
        explicit_map[case_id] = function_id

    if report_format == "junit-xml":
        cases = _parse_junit(report_bytes)
    elif report_format == "pytest-json":
        cases = parse_pytest_json(report_bytes)
    else:
        raise ValueError("report_format must be junit-xml or pytest-json")
    actual_ids = {item["test_case_id"] for item in cases}
    mapped_cases: dict[str, list[dict[str, Any]]] = defaultdict(list)
    unmapped = []
    for case in cases:
        function_id = explicit_map.get(case["test_case_id"])
        if function_id is None:
            unmapped.append({**case, "linkage_status": "UNMAPPED"})
        else:
            mapped_cases[function_id].append(case)

    function_results = []
    for function_id, view in sorted(known_functions.items()):
        linked = mapped_cases.get(function_id, [])
        counts = Counter(item["status"] for item in linked)
        expected_not_seen = sorted(case_id for case_id, target in explicit_map.items()
                                   if target == function_id and case_id not in actual_ids)
        expected_mapped_count = sum(1 for target in explicit_map.values() if target == function_id)
        if not linked:
            execution_status = "NOT_EXECUTED"
            execution_status_reason = "NO_MAPPED_TEST_CASE_REPORTED"
        elif counts["FAILED"]:
            # An observed failure remains a failure even when other expected
            # cases are absent; missing cases are still retained separately.
            execution_status = "FAILED"
            execution_status_reason = "MAPPED_TEST_FAILURE_OBSERVED"
        elif expected_not_seen:
            # A partial report cannot certify the whole mapped function as PASS.
            execution_status = "UNKNOWN"
            execution_status_reason = "MAPPED_TEST_CASES_MISSING_FROM_REPORT"
        elif counts["SKIPPED"]:
            execution_status = "UNKNOWN"
            execution_status_reason = "MAPPED_TEST_CASE_SKIPPED"
        else:
            execution_status = "PASSED"
            execution_status_reason = "ALL_MAPPED_TEST_CASES_PASSED"
        function_results.append({
            "business_function_id": function_id,
            "endpoint_id": view.get("endpoint_id"),
            "execution_status": execution_status,
            "execution_status_reason": execution_status_reason,
            "expected_mapped_test_case_count": expected_mapped_count,
            "reported_mapped_test_case_count": len(linked),
            "mapped_case_completeness": "INCOMPLETE" if expected_not_seen else (
                "NO_EXPECTED_MAPPED_CASES" if expected_mapped_count == 0 else "COMPLETE"
            ),
            "mapped_test_case_count": len(linked),
            "passed": counts["PASSED"],
            "failed": counts["FAILED"],
            "skipped": counts["SKIPPED"],
            "mapped_test_cases": linked,
            "mapped_cases_missing_from_report": expected_not_seen,
            "coverage_basis": "EXPLICIT_TESTCASE_TO_FUNCTION_MAPPING",
            "code_coverage": "NOT_MEASURED",
            "verification_status": "RUNTIME_TEST_RESULT_IMPORTED" if linked else "NOT_EXECUTED",
        })

    status_counts = Counter(item["execution_status"] for item in function_results)
    return {
        "schema_version": SCHEMA_VERSION,
        "model": "TestResultImporter",
        "source_format": "JUNIT_XML" if report_format == "junit-xml" else "PYTEST_JSON",
        "source_hashes": {
            "test_report_sha256": _sha256(report_bytes),
            "mapping_json_sha256": _sha256(mapping_bytes),
            "business_graph_sha256": _sha256(graph_bytes),
        },
        "test_case_count": len(cases),
        "test_case_status_counts": dict(sorted(Counter(x["status"] for x in cases).items())),
        "unmapped_test_case_count": len(unmapped),
        "unmapped_test_cases": unmapped,
        "business_function_count": len(function_results),
        "business_function_status_counts": dict(sorted(status_counts.items())),
        "business_function_results": function_results,
        "coverage_limitations": [
            "功能关联完全依赖显式 mapping；不使用名称相似度或 endpoint 猜测。",
            "JUnit 测试通过不等于业务需求完整正确，也不等于代码覆盖率。",
            "没有映射测试结果的功能标记 NOT_EXECUTED；被跳过的功能标记 UNKNOWN。",
            "映射中任一预期 testcase 未出现在报告中时，即使已报告用例全部通过，功能状态也只能是 UNKNOWN；观察到的失败仍优先报告 FAILED。",
            "JUnit XML 可接收 Android instrumentation 等兼容报告；pytest 支持 pytest-json-report JSON。",
            "代码覆盖率由独立 coverage importer 读取，并要求显式源码文件/行范围映射。",
        ],
    }
