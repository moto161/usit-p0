"""Import real JUnit XML results and link them only through an explicit mapping.

No test commands are executed here. JUnit outcomes are execution evidence;
business-function linkage is supplied by a reviewed mapping file, never guessed.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import stat
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .test_report_adapters import parse_pytest_json
from .safeio import is_reparse, open_no_follow
from .test_execution_provenance import (
    MAX_MANIFEST_BYTES, MAX_RECORD_BYTES, validate_execution_provenance,
)

SCHEMA_VERSION = "usit-test-execution-results-v1"
MAX_REPORT_BYTES = 20 * 1024 * 1024
MAX_TESTCASES = 200_000


def _read_regular(path: str | Path, limit: int) -> bytes:
    source = Path(path).expanduser()
    absolute = source if source.is_absolute() else Path.cwd() / source

    # Match the coverage reader: reject links/reparse points in every named
    # component, not only the final file, before opening the descriptor.
    cursor = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        cursor = cursor / part
        info = cursor.lstat()
        if cursor.is_symlink() or is_reparse(info):
            raise ValueError("input path must not contain symbolic links or reparse points")

    resolved = absolute.resolve(strict=True)
    fd = open_no_follow(resolved)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or is_reparse(before):
            raise ValueError("input must be a regular file")
        if before.st_size > limit:
            raise ValueError(f"input exceeds size limit ({limit} bytes)")
        chunks: list[bytes] = []
        total = 0
        while total <= limit:
            chunk = os.read(fd, min(1024 * 1024, limit + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        if total > limit:
            raise ValueError(f"input exceeds size limit ({limit} bytes)")
        after = os.fstat(fd)
        identity_before = (
            int(before.st_dev), int(before.st_ino), int(before.st_size),
            int(before.st_mtime_ns), int(before.st_ctime_ns),
        )
        identity_after = (
            int(after.st_dev), int(after.st_ino), int(after.st_size),
            int(after.st_mtime_ns), int(after.st_ctime_ns),
        )
        if identity_before != identity_after:
            raise ValueError("input changed while being read")
        return b"".join(chunks)
    finally:
        os.close(fd)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _case_id(classname: str, name: str) -> str:
    return f"{classname}::{name}" if classname else name


def _junit_runner_source_locator(element: ET.Element) -> tuple[dict[str, Any] | None, str]:
    properties = element.find("properties")
    if properties is None:
        return None, "RUNNER_SOURCE_LOCATOR_NOT_PROVIDED"
    values: dict[str, str] = {}
    for prop in properties.findall("property"):
        name = prop.attrib.get("name")
        if not isinstance(name, str) or not name.startswith("usit_"):
            continue
        if name in values:
            return None, "RUNNER_SOURCE_LOCATOR_DUPLICATE_PROPERTY"
        value = prop.attrib.get("value")
        if not isinstance(value, str) or len(value) > 4096:
            return None, "RUNNER_SOURCE_LOCATOR_INVALID_PROPERTY"
        values[name] = value
    required = {"usit_source_file", "usit_source_line", "usit_source_symbol", "usit_nodeid"}
    if not values:
        return None, "RUNNER_SOURCE_LOCATOR_NOT_PROVIDED"
    if not required.issubset(values):
        return None, "RUNNER_SOURCE_LOCATOR_INCOMPLETE"
    relative = values["usit_source_file"]
    parts = relative.split("/")
    if (not relative or relative.startswith("/") or "\\" in relative or ":" in relative
            or any(part in {"", ".", ".."} for part in parts)):
        return None, "RUNNER_SOURCE_LOCATOR_INVALID_PATH"
    try:
        line = int(values["usit_source_line"])
    except ValueError:
        return None, "RUNNER_SOURCE_LOCATOR_INVALID_LINE"
    if line < 1 or not values["usit_source_symbol"].strip() or not values["usit_nodeid"].strip():
        return None, "RUNNER_SOURCE_LOCATOR_INVALID_VALUE"
    return {
        "test_source_file": relative,
        "test_source_line": line,
        "test_source_symbol": values["usit_source_symbol"],
        "pytest_nodeid": values["usit_nodeid"],
    }, "RUNNER_SOURCE_LOCATOR_DECLARED_UNAUTHENTICATED"


def _verify_report_source_locator(
    mapping_item: dict[str, Any], testcase: dict[str, Any],
) -> dict[str, Any]:
    locator = testcase.get("runner_source_locator")
    status = testcase.get("runner_source_locator_status", "RUNNER_SOURCE_LOCATOR_NOT_PROVIDED")
    if locator is None:
        return {"status": status}
    expected = {
        "test_source_file": mapping_item.get("test_source_file"),
        "test_source_line": mapping_item.get("test_source_line"),
        "test_source_symbol": mapping_item.get("test_source_symbol"),
        "pytest_nodeid": mapping_item.get("pytest_nodeid"),
    }
    if any(value is None for value in expected.values()):
        return {
            "status": "REPORT_SOURCE_LOCATOR_MAPPING_INCOMPLETE",
            "runner_locator": locator,
        }
    if expected != locator:
        return {
            "status": "REPORT_SOURCE_LOCATOR_MISMATCH",
            "runner_locator": locator,
            "mapping_locator": expected,
        }
    return {
        "status": "REPORT_SOURCE_LOCATOR_MATCHED_UNAUTHENTICATED",
        "runner_locator": locator,
    }


def _parse_junit(data: bytes) -> list[dict[str, Any]]:
    # ElementTree does not resolve external entities, and explicitly rejecting
    # declarations keeps the accepted report format deliberately conservative.
    # The report is already bounded to MAX_REPORT_BYTES; scan the entire
    # document so a declaration cannot be hidden behind a long XML prolog.
    upper = data.upper()
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
        runner_locator, runner_locator_status = _junit_runner_source_locator(element)
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
            "runner_source_locator": runner_locator,
            "runner_source_locator_status": runner_locator_status,
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


def _verify_test_source_binding(item: dict[str, Any], project_root: str | Path | None) -> dict[str, Any]:
    """Verify an explicit testcase-to-test-source locator against exact project bytes."""
    fields = ("test_source_file", "test_source_line", "test_source_file_sha256")
    present = [field in item and item.get(field) is not None for field in fields]
    if not any(present):
        return {"status": "TEST_SOURCE_BINDING_NOT_PROVIDED"}
    if not all(present):
        return {"status": "TEST_SOURCE_BINDING_INVALID", "reason": "INCOMPLETE_TEST_SOURCE_LOCATOR"}
    if project_root is None:
        return {"status": "TEST_SOURCE_BINDING_NOT_CHECKED", "reason": "PROJECT_ROOT_NOT_PROVIDED"}
    relative, line, digest = (item.get("test_source_file"), item.get("test_source_line"),
                              item.get("test_source_file_sha256"))
    if not isinstance(relative, str) or not relative.strip():
        return {"status": "TEST_SOURCE_BINDING_INVALID", "reason": "INVALID_TEST_SOURCE_FILE"}
    normalized = relative.replace(chr(92), "/")
    parts = normalized.split("/")
    if (normalized.startswith("/") or ":" in normalized
            or any(part in {"", ".", ".."} for part in parts)):
        return {"status": "TEST_SOURCE_BINDING_INVALID", "reason": "UNSAFE_TEST_SOURCE_PATH"}
    if isinstance(line, bool) or not isinstance(line, int) or line < 1:
        return {"status": "TEST_SOURCE_BINDING_INVALID", "reason": "INVALID_TEST_SOURCE_LINE"}
    if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        return {"status": "TEST_SOURCE_BINDING_INVALID", "reason": "INVALID_TEST_SOURCE_SHA256"}
    try:
        root = Path(project_root).expanduser()
        if not root.is_absolute():
            return {"status": "TEST_SOURCE_BINDING_INVALID", "reason": "PROJECT_ROOT_MUST_BE_ABSOLUTE"}
        absolute_root = Path(os.path.abspath(root))
        cursor = Path(absolute_root.anchor)
        for part in absolute_root.parts[1:]:
            cursor = cursor / part
            info = cursor.lstat()
            if cursor.is_symlink() or is_reparse(info):
                return {"status": "TEST_SOURCE_BINDING_INVALID", "reason": "PROJECT_ROOT_CONTAINS_LINK"}
        root = absolute_root.resolve(strict=True)
        if not root.is_dir():
            return {"status": "TEST_SOURCE_BINDING_INVALID", "reason": "PROJECT_ROOT_NOT_DIRECTORY"}
        candidate = root.joinpath(*parts)
        current = root
        for part in parts:
            current = current / part
            info = current.lstat()
            if current.is_symlink() or is_reparse(info):
                return {"status": "TEST_SOURCE_BINDING_INVALID", "reason": "TEST_SOURCE_PATH_CONTAINS_LINK"}
        resolved = candidate.resolve(strict=True)
        if not resolved.is_relative_to(root) or not resolved.is_file():
            return {"status": "TEST_SOURCE_FILE_MISSING"}
        raw = _read_regular(resolved, 50 * 1024 * 1024)
    except FileNotFoundError:
        return {"status": "TEST_SOURCE_FILE_MISSING"}
    except (OSError, RuntimeError, ValueError) as exc:
        return {"status": "TEST_SOURCE_BINDING_INVALID", "reason": type(exc).__name__}
    actual = _sha256(raw)
    if actual != digest:
        return {"status": "TEST_SOURCE_HASH_MISMATCH", "expected_sha256": digest,
                "actual_sha256": actual, "test_source_file": normalized, "test_source_line": line}
    try:
        source_text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return {"status": "TEST_SOURCE_ENCODING_UNSUPPORTED", "expected_sha256": digest,
                "actual_sha256": actual, "test_source_file": normalized, "test_source_line": line}
    line_count = len(source_text.splitlines())
    if line > line_count:
        return {"status": "TEST_SOURCE_LINE_OUT_OF_BOUNDS",
                "expected_sha256": digest, "actual_sha256": actual,
                "source_line_count": line_count, "test_source_file": normalized,
                "test_source_line": line}
    return {"status": "TEST_SOURCE_BYTES_MATCHED", "expected_sha256": digest, "actual_sha256": actual,
            "source_line_count": line_count, "test_source_file": normalized,
            "test_source_line": line, "_verified_source_text": source_text}


def _verify_python_test_symbol(item: dict[str, Any], source_text: str | None) -> dict[str, Any]:
    """Verify a declared Python test symbol exists at the declared source line.

    This verifies declaration/locator consistency only; it does not prove the
    report testcase was emitted by, or that the runner executed, this symbol.
    """
    symbol = item.get("test_source_symbol")
    if symbol is None:
        return {"status": "TEST_SOURCE_SYMBOL_NOT_PROVIDED"}
    if not isinstance(symbol, str) or not symbol.strip():
        return {"status": "TEST_SOURCE_SYMBOL_INVALID"}
    relative = item.get("test_source_file")
    if not isinstance(relative, str) or not relative.lower().endswith(".py"):
        return {"status": "TEST_SOURCE_SYMBOL_NOT_SUPPORTED", "reason": "NON_PYTHON_SOURCE"}
    if source_text is None:
        return {"status": "TEST_SOURCE_SYMBOL_NOT_CHECKED", "reason": "SOURCE_BYTES_NOT_VERIFIED"}
    parts = symbol.split(".")
    if any(not part.isidentifier() for part in parts):
        return {"status": "TEST_SOURCE_SYMBOL_INVALID", "reason": "INVALID_QUALIFIED_SYMBOL"}
    try:
        tree = ast.parse(source_text)
    except SyntaxError:
        return {"status": "TEST_SOURCE_SYMBOL_NOT_SUPPORTED", "reason": "PYTHON_PARSE_FAILED"}
    matches: list[ast.FunctionDef | ast.AsyncFunctionDef] = []

    def walk(nodes: list[ast.stmt], parents: tuple[str, ...] = ()) -> None:
        for node in nodes:
            if isinstance(node, ast.ClassDef):
                walk(node.body, parents + (node.name,))
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if parents + (node.name,) == tuple(parts):
                    matches.append(node)
    walk(tree.body)
    if len(matches) != 1:
        return {"status": "TEST_SOURCE_SYMBOL_MISMATCH", "reason": "SYMBOL_NOT_UNIQUE_OR_NOT_FOUND"}
    node = matches[0]
    start_line = min([node.lineno] + [decorator.lineno for decorator in node.decorator_list])
    end_line = node.end_lineno or node.lineno
    line = item.get("test_source_line")
    if isinstance(line, bool) or not isinstance(line, int) or not start_line <= line <= end_line:
        return {
            "status": "TEST_SOURCE_SYMBOL_MISMATCH",
            "reason": "DECLARED_LINE_OUTSIDE_SYMBOL",
            "symbol_start_line": start_line,
            "symbol_end_line": end_line,
        }
    return {
        "status": "TEST_SOURCE_SYMBOL_DECLARATION_MATCHED",
        "qualified_symbol": ".".join(parts),
        "symbol_start_line": start_line,
        "symbol_end_line": end_line,
        "test_source_line": line,
    }


def _verify_test_source_snapshot_binding(
    item: dict[str, Any], execution_provenance: dict[str, Any] | None,
) -> dict[str, Any]:
    """Compare an explicit mapping locator with the captured manifest entry.

    This is a consistency check against a manifest digest already validated by
    the provenance validator. It does not authenticate the runner or prove the
    test process loaded the declared source.
    """
    if execution_provenance is None:
        return {"status": "TEST_SOURCE_SNAPSHOT_NOT_CHECKED"}
    relative = item.get("test_source_file")
    digest = item.get("test_source_file_sha256")
    if relative is None and digest is None:
        return {"status": "TEST_SOURCE_SNAPSHOT_LOCATOR_NOT_PROVIDED"}
    if not isinstance(relative, str) or not relative or not isinstance(digest, str):
        return {"status": "TEST_SOURCE_SNAPSHOT_LOCATOR_INVALID"}
    manifest_hashes = execution_provenance.get("source_manifest_file_hashes", {})
    captured = manifest_hashes.get(relative)
    if captured is None:
        return {
            "status": "TEST_SOURCE_NOT_IN_EXECUTION_SNAPSHOT",
            "test_source_file": relative,
        }
    if captured != digest:
        return {
            "status": "TEST_SOURCE_SNAPSHOT_HASH_MISMATCH",
            "test_source_file": relative,
            "mapping_sha256": digest,
            "snapshot_sha256": captured,
        }
    return {
        "status": "TEST_SOURCE_SNAPSHOT_ENTRY_MATCHED",
        "test_source_file": relative,
        "sha256": captured,
    }


def ingest_junit_results(
    report_path: str | Path,
    mapping_path: str | Path,
    business_graph_path: str | Path,
    report_format: str = "junit-xml",
    project_root: str | Path | None = None,
    provenance_record_path: str | Path | None = None,
    source_manifest_path: str | Path | None = None,
) -> dict[str, Any]:
    """Parse JUnit and compute function status using explicit testcase mappings.

    Mapping JSON is bound to the exact graph bytes:
      {"schema_version":"usit-test-result-mapping-v1",
       "source_graph_sha256":"<sha256 of business graph file>",
       "mappings":[{"test_case_id":"pkg.Test::test_upload",
                    "business_function_id":"bf-...",
                    "test_source_file":"tests/test_upload.py",
                    "test_source_line":42,
                    "test_source_file_sha256":"<sha256 of exact test source bytes>"}]}
    """
    report_bytes = _read_regular(report_path, MAX_REPORT_BYTES)
    mapping_bytes = _read_regular(mapping_path, 2 * 1024 * 1024)
    graph_bytes = _read_regular(business_graph_path, 50 * 1024 * 1024)
    execution_provenance = None
    if (provenance_record_path is None) != (source_manifest_path is None):
        raise ValueError("provenance_record_path and source_manifest_path must be provided together")
    if provenance_record_path is not None and source_manifest_path is not None:
        provenance_bytes = _read_regular(provenance_record_path, MAX_RECORD_BYTES)
        source_manifest_bytes = _read_regular(source_manifest_path, MAX_MANIFEST_BYTES)
        execution_provenance = validate_execution_provenance(
            provenance_bytes, report_bytes, source_manifest_bytes
        )
        expected_format = {"junit-xml": "JUNIT_XML", "pytest-json": "PYTEST_JSON"}.get(report_format)
        try:
            provenance_doc = json.loads(provenance_bytes)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid execution provenance record: {exc}") from exc
        if expected_format is None or provenance_doc.get("report_format") != expected_format:
            raise ValueError("execution provenance report_format does not match importer report_format")
        execution_provenance["record_validation_status"] = "STRUCTURE_AND_DIGESTS_VALIDATED"
        execution_provenance["provenance_authenticity_status"] = "UNAUTHENTICATED"
        execution_provenance["execution_source_binding_status"] = "NOT_ESTABLISHED"
    try:
        mapping_doc = json.loads(mapping_bytes)
        graph_doc = json.loads(graph_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid JSON input: {exc}") from exc
    if not isinstance(graph_doc, dict) or graph_doc.get("schema_version") != "usit-graph-v1":
        raise ValueError("business graph schema_version must be usit-graph-v1")
    graph_sha256 = hashlib.sha256(graph_bytes).hexdigest()
    if not isinstance(mapping_doc, dict) or mapping_doc.get("schema_version") != "usit-test-result-mapping-v1":
        raise ValueError("mapping schema_version must be usit-test-result-mapping-v1")
    if mapping_doc.get("source_graph_sha256") != graph_sha256:
        raise ValueError("mapping source_graph_sha256 must match business graph bytes")
    mappings = mapping_doc.get("mappings")
    if not isinstance(mappings, list):
        raise ValueError("mapping.mappings must be a list")
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

    explicit_map: dict[str, dict[str, Any]] = {}
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
        explicit_map[case_id] = {**item, "business_function_id": function_id}

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
        mapping_item = explicit_map.get(case["test_case_id"])
        if mapping_item is None:
            unmapped.append({**case, "linkage_status": "UNMAPPED"})
        else:
            source_binding = _verify_test_source_binding(mapping_item, project_root)
            verified_source_text = source_binding.pop("_verified_source_text", None)
            symbol_binding = _verify_python_test_symbol(mapping_item, verified_source_text)
            symbol_binding.setdefault("declared_test_source_symbol", mapping_item.get("test_source_symbol"))
            snapshot_binding = _verify_test_source_snapshot_binding(mapping_item, execution_provenance)
            report_locator_binding = _verify_report_source_locator(mapping_item, case)
            source_binding.setdefault("declared_test_source_file", mapping_item.get("test_source_file"))
            source_binding.setdefault("declared_test_source_line", mapping_item.get("test_source_line"))
            source_binding.setdefault("declared_test_source_file_sha256", mapping_item.get("test_source_file_sha256"))
            mapped_cases[mapping_item["business_function_id"]].append({
                **case, "test_source_binding_evidence": source_binding,
                "test_source_binding_status": source_binding["status"],
                "test_source_snapshot_binding_evidence": snapshot_binding,
                "test_source_snapshot_binding_status": snapshot_binding["status"],
                "test_source_report_locator_evidence": report_locator_binding,
                "test_source_report_locator_status": report_locator_binding["status"],
                "test_source_symbol_evidence": symbol_binding,
                "test_source_symbol_status": symbol_binding["status"],
            })

    function_results = []
    for function_id, view in sorted(known_functions.items()):
        linked = mapped_cases.get(function_id, [])
        counts = Counter(item["status"] for item in linked)
        expected_not_seen = sorted(case_id for case_id, target in explicit_map.items()
                                   if target["business_function_id"] == function_id and case_id not in actual_ids)
        expected_mapped_count = sum(1 for target in explicit_map.values() if target["business_function_id"] == function_id)
        if counts["FAILED"]:
            # An observed failure remains a failure even when other expected
            # cases are absent; missing cases are still retained separately.
            execution_status = "FAILED"
            execution_status_reason = "MAPPED_TEST_FAILURE_OBSERVED"
        elif expected_not_seen:
            # Missing every expected case is still an incomplete report, not
            # proof that no test was executed. Preserve it as UNKNOWN.
            execution_status = "UNKNOWN"
            execution_status_reason = "MAPPED_TEST_CASES_MISSING_FROM_REPORT"
        elif not linked:
            # NOT_EXECUTED is reserved for functions with no expected mapping.
            execution_status = "NOT_EXECUTED"
            execution_status_reason = "NO_MAPPED_TEST_CASE_REPORTED"
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
        "execution_provenance": execution_provenance,
        "coverage_limitations": [
            "功能关联完全依赖显式 mapping；不使用名称相似度或 endpoint 猜测。",
            "JUnit 测试通过不等于业务需求完整正确，也不等于代码覆盖率。",
            "没有映射测试结果的功能标记 NOT_EXECUTED；被跳过的功能标记 UNKNOWN。",
            "映射中任一预期 testcase 未出现在报告中时，即使已报告用例全部通过，功能状态也只能是 UNKNOWN；观察到的失败仍优先报告 FAILED。",
            "JUnit XML 可接收 Android instrumentation 等兼容报告；pytest 支持 pytest-json-report JSON。",
            "代码覆盖率由独立 coverage importer 读取，并要求显式源码文件/行范围映射。",
            "测试源码绑定仅在 mapping 提供 test_source_file、test_source_line、test_source_file_sha256 且调用方提供 project_root 时核对；哈希匹配仅证明字节一致，不证明测试充分性或报告执行了该源码。",
            "未提供或未核验测试源码绑定时，JUnit 执行状态仍仅表示报告中的 testcase 结果，不代表测试源码位置已验证。",
            "执行来源记录与源码清单为可选导入输入；提供时仅验证记录结构及报告/清单摘要一致性，记录来源仍为 UNAUTHENTICATED，不能证明 runner 身份或测试进程实际加载的源码。",
            "映射中的测试源码路径与 SHA-256 可独立对照执行源码清单条目；即使条目匹配，也不证明 runner 真实性、测试进程实际加载该文件或测试符号身份。",
        ],
    }
