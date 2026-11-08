"""Conservative cross-validation of runtime test and coverage evidence."""
from __future__ import annotations
import hashlib
import json
import math
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
        coverage_source_verified = False
        if cov is not None:
            percent = cov.get("line_coverage_percent")
            percent_valid = percent is None or (
                isinstance(percent, (int, float)) and not isinstance(percent, bool)
                and not (isinstance(percent, float) and not math.isfinite(percent))
                and 0 <= percent <= 100
            )
            if not percent_valid:
                conflicts.append({
                    "kind": "INVALID_COVERAGE_PERCENT",
                    "business_function_id": function_id,
                    "value": repr(percent) if isinstance(percent, float) and not math.isfinite(percent) else percent,
                })
            counts = {}
            counts_valid = True
            for count_field in ("executable_line_count", "covered_line_count", "uncovered_line_count"):
                count_value = cov.get(count_field)
                if count_value is not None and (
                    not isinstance(count_value, int) or isinstance(count_value, bool) or count_value < 0
                ):
                    conflicts.append({
                        "kind": "INVALID_COVERAGE_COUNT",
                        "business_function_id": function_id,
                        "field": count_field,
                    })
                    counts_valid = False
                else:
                    counts[count_field] = count_value
            executable = counts.get("executable_line_count")
            covered = counts.get("covered_line_count")
            uncovered = counts.get("uncovered_line_count")
            if counts_valid and all(value is not None for value in (executable, covered, uncovered)):
                if covered > executable or uncovered > executable or covered + uncovered != executable:
                    conflicts.append({
                        "kind": "INCONSISTENT_COVERAGE_COUNTS",
                        "business_function_id": function_id,
                        "executable_line_count": executable,
                        "covered_line_count": covered,
                        "uncovered_line_count": uncovered,
                    })
                if percent_valid and percent is not None and executable > 0:
                    expected_percent = round(100 * covered / executable, 2)
                    if not math.isclose(float(percent), expected_percent, rel_tol=0, abs_tol=0.01):
                        conflicts.append({
                            "kind": "COVERAGE_PERCENT_COUNT_MISMATCH",
                            "business_function_id": function_id,
                            "reported_percent": percent,
                            "derived_percent": expected_percent,
                        })
                if percent_valid and percent is not None and not cov.get("coverage_source_files_missing"):
                    expected_status = "COVERED" if covered == executable else ("PARTIAL" if covered > 0 else "NOT_COVERED")
                    if cs != expected_status:
                        conflicts.append({
                            "kind": "COVERAGE_STATUS_COUNT_MISMATCH",
                            "business_function_id": function_id,
                            "reported_status": cs,
                            "derived_status": expected_status,
                        })
            elif percent_valid and percent is not None and any(
                value is not None for value in counts.values()
            ):
                conflicts.append({
                    "kind": "INCOMPLETE_COVERAGE_MEASUREMENTS",
                    "business_function_id": function_id,
                })
            missing_files = cov.get("coverage_source_files_missing", [])
            if not isinstance(missing_files, list) or any(
                not isinstance(path, str) or not path.strip() for path in missing_files
            ):
                conflicts.append({
                    "kind": "INVALID_COVERAGE_MISSING_SOURCE_FILES",
                    "business_function_id": function_id,
                })
            elif missing_files and cs != "UNKNOWN":
                conflicts.append({
                    "kind": "COVERAGE_STATUS_WITH_MISSING_SOURCE_FILES",
                    "business_function_id": function_id,
                    "reported_status": cs,
                })
            source_evidence = cov.get("source_binding_evidence")
            source_binding_valid = isinstance(source_evidence, list) and bool(source_evidence)
            if source_binding_valid:
                for source_item in source_evidence:
                    if not isinstance(source_item, dict):
                        source_binding_valid = False
                        break
                    status = source_item.get("source_binding_status")
                    expected_hash = source_item.get("expected_source_file_sha256")
                    actual_hash = source_item.get("actual_source_file_sha256")
                    if (not isinstance(source_item.get("source_file"), str)
                            or not source_item["source_file"].strip()
                            or status not in {"SOURCE_BYTES_MATCHED", "SOURCE_BINDING_NOT_CHECKED",
                                              "SOURCE_FILE_MISSING", "SOURCE_HASH_MISMATCH",
                                              "SOURCE_ENCODING_UNSUPPORTED", "SOURCE_LINE_RANGE_OUT_OF_BOUNDS"}):
                        source_binding_valid = False
                        break
                    for digest in (expected_hash, actual_hash):
                        if digest is not None and (
                            not isinstance(digest, str) or len(digest) != 64
                            or any(ch not in "0123456789abcdef" for ch in digest)
                        ):
                            source_binding_valid = False
                            break
                    if not source_binding_valid:
                        break
                    if status == "SOURCE_BYTES_MATCHED" and (
                        not expected_hash or expected_hash != actual_hash
                        or not isinstance(source_item.get("source_line_count"), int)
                        or isinstance(source_item.get("source_line_count"), bool)
                        or source_item["source_line_count"] < 1
                        or not isinstance(source_item.get("line_start"), int)
                        or isinstance(source_item.get("line_start"), bool)
                        or not isinstance(source_item.get("line_end"), int)
                        or isinstance(source_item.get("line_end"), bool)
                        or source_item["line_start"] < 1
                        or source_item["line_end"] < source_item["line_start"]
                        or source_item["line_end"] > source_item["source_line_count"]
                    ):
                        source_binding_valid = False
                        break
            if not source_binding_valid:
                if cs != "UNKNOWN" or percent is not None:
                    conflicts.append({
                        "kind": "INVALID_OR_MISSING_COVERAGE_SOURCE_BINDING",
                        "business_function_id": function_id,
                    })
                cs = "UNKNOWN"
                percent = None
            elif all(item["source_binding_status"] == "SOURCE_BYTES_MATCHED" for item in source_evidence):
                coverage_source_verified = True
            else:
                if cs != "UNKNOWN" or percent is not None:
                    conflicts.append({
                        "kind": "COVERAGE_WITH_UNVERIFIED_SOURCE_BINDING",
                        "business_function_id": function_id,
                    })
                cs = "UNKNOWN"
                percent = None
            ranges = cov.get("mapping_ranges", [])
            if not isinstance(ranges, list) or any(
                not isinstance(item, dict)
                or not isinstance(item.get("source_file"), str)
                or not item["source_file"].strip()
                or not isinstance(item.get("line_start"), int)
                or isinstance(item.get("line_start"), bool)
                or not isinstance(item.get("line_end"), int)
                or isinstance(item.get("line_end"), bool)
                or item["line_start"] < 1 or item["line_end"] < item["line_start"]
                for item in ranges
            ):
                conflicts.append({
                    "kind": "INVALID_COVERAGE_MAPPING_EVIDENCE",
                    "business_function_id": function_id,
                })
            elif source_binding_valid:
                mapped_ranges = sorted(
                    (item["source_file"], item["line_start"], item["line_end"])
                    for item in ranges
                )
                bound_ranges = sorted(
                    (item["source_file"], item["line_start"], item["line_end"])
                    for item in source_evidence
                    if all(isinstance(item.get(field), int) and not isinstance(item.get(field), bool)
                           for field in ("line_start", "line_end"))
                )
                if mapped_ranges != bound_ranges:
                    conflicts.append({
                        "kind": "COVERAGE_SOURCE_BINDING_RANGE_MISMATCH",
                        "business_function_id": function_id,
                    })
                    cs = "UNKNOWN"
                    percent = None
                    coverage_source_verified = False
        if ts not in {"PASSED","FAILED","UNKNOWN","NOT_EXECUTED","NOT_MEASURED"}:
            conflicts.append({"kind":"INVALID_TEST_STATUS","business_function_id":function_id,"status":ts})
        if cs not in {"COVERED","PARTIAL","NOT_COVERED","UNKNOWN","NOT_MEASURED"}:
            conflicts.append({"kind":"INVALID_COVERAGE_STATUS","business_function_id":function_id,"status":cs})

        test_case_evidence = []
        test_source_binding_evidence = []
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
                        raw_binding = item.get("test_source_binding_evidence")
                        binding_status = item.get("test_source_binding_status", "TEST_SOURCE_BINDING_NOT_PROVIDED")
                        allowed_binding_statuses = {
                            "TEST_SOURCE_BINDING_NOT_PROVIDED", "TEST_SOURCE_BINDING_NOT_CHECKED",
                            "TEST_SOURCE_BINDING_INVALID", "TEST_SOURCE_FILE_MISSING",
                            "TEST_SOURCE_HASH_MISMATCH", "TEST_SOURCE_ENCODING_UNSUPPORTED",
                            "TEST_SOURCE_LINE_OUT_OF_BOUNDS", "TEST_SOURCE_BYTES_MATCHED",
                        }
                        if binding_status not in allowed_binding_statuses:
                            conflicts.append({
                                "kind": "INVALID_TEST_SOURCE_BINDING_STATUS",
                                "business_function_id": function_id,
                                "test_case_id": item["test_case_id"],
                            })
                            binding_status = "TEST_SOURCE_BINDING_INVALID"
                        if raw_binding is not None and (
                            not isinstance(raw_binding, dict)
                            or raw_binding.get("status") != binding_status
                        ):
                            conflicts.append({
                                "kind": "TEST_SOURCE_BINDING_EVIDENCE_STATUS_MISMATCH",
                                "business_function_id": function_id,
                                "test_case_id": item["test_case_id"],
                            })
                            binding_status = "TEST_SOURCE_BINDING_INVALID"
                        if binding_status == "TEST_SOURCE_BYTES_MATCHED":
                            expected_digest = raw_binding.get("expected_sha256") if isinstance(raw_binding, dict) else None
                            actual_digest = raw_binding.get("actual_sha256") if isinstance(raw_binding, dict) else None
                            source_line = raw_binding.get("test_source_line") if isinstance(raw_binding, dict) else None
                            source_line_count = raw_binding.get("source_line_count") if isinstance(raw_binding, dict) else None
                            source_file = raw_binding.get("test_source_file") if isinstance(raw_binding, dict) else None
                            declared_file = raw_binding.get("declared_test_source_file") if isinstance(raw_binding, dict) else None
                            declared_line = raw_binding.get("declared_test_source_line") if isinstance(raw_binding, dict) else None
                            declared_digest = raw_binding.get("declared_test_source_file_sha256") if isinstance(raw_binding, dict) else None
                            valid_digest = lambda value: (
                                isinstance(value, str) and len(value) == 64
                                and all(char in "0123456789abcdef" for char in value)
                            )
                            normalized_declared_file = (
                                declared_file.replace(chr(92), "/") if isinstance(declared_file, str) else None
                            )
                            if (not valid_digest(expected_digest) or not valid_digest(actual_digest)
                                    or expected_digest != actual_digest
                                    or not valid_digest(declared_digest) or declared_digest != expected_digest
                                    or not isinstance(source_file, str) or not source_file
                                    or normalized_declared_file != source_file
                                    or not isinstance(source_line, int) or isinstance(source_line, bool) or source_line < 1
                                    or not isinstance(declared_line, int) or isinstance(declared_line, bool)
                                    or declared_line != source_line
                                    or not isinstance(source_line_count, int) or isinstance(source_line_count, bool)
                                    or source_line_count < source_line):
                                conflicts.append({
                                    "kind": "INVALID_MATCHED_TEST_SOURCE_BINDING_EVIDENCE",
                                    "business_function_id": function_id,
                                    "test_case_id": item["test_case_id"],
                                })
                                binding_status = "TEST_SOURCE_BINDING_INVALID"
                        raw_symbol = item.get("test_source_symbol_evidence")
                        symbol_status = item.get("test_source_symbol_status", "TEST_SOURCE_SYMBOL_NOT_PROVIDED")
                        allowed_symbol_statuses = {
                            "TEST_SOURCE_SYMBOL_NOT_PROVIDED", "TEST_SOURCE_SYMBOL_INVALID",
                            "TEST_SOURCE_SYMBOL_NOT_SUPPORTED", "TEST_SOURCE_SYMBOL_NOT_CHECKED",
                            "TEST_SOURCE_SYMBOL_MISMATCH", "TEST_SOURCE_SYMBOL_DECLARATION_MATCHED",
                        }
                        if symbol_status not in allowed_symbol_statuses:
                            conflicts.append({
                                "kind": "INVALID_TEST_SOURCE_SYMBOL_STATUS",
                                "business_function_id": function_id,
                                "test_case_id": item["test_case_id"],
                            })
                            symbol_status = "TEST_SOURCE_SYMBOL_INVALID"
                        if raw_symbol is not None and (
                            not isinstance(raw_symbol, dict)
                            or raw_symbol.get("status") != symbol_status
                        ):
                            conflicts.append({
                                "kind": "TEST_SOURCE_SYMBOL_EVIDENCE_STATUS_MISMATCH",
                                "business_function_id": function_id,
                                "test_case_id": item["test_case_id"],
                            })
                            symbol_status = "TEST_SOURCE_SYMBOL_INVALID"
                        if symbol_status == "TEST_SOURCE_SYMBOL_DECLARATION_MATCHED":
                            symbol_name = raw_symbol.get("qualified_symbol") if isinstance(raw_symbol, dict) else None
                            declared_symbol = raw_symbol.get("declared_test_source_symbol") if isinstance(raw_symbol, dict) else None
                            symbol_start = raw_symbol.get("symbol_start_line") if isinstance(raw_symbol, dict) else None
                            symbol_end = raw_symbol.get("symbol_end_line") if isinstance(raw_symbol, dict) else None
                            symbol_line = raw_symbol.get("test_source_line") if isinstance(raw_symbol, dict) else None
                            bound_line = raw_binding.get("test_source_line") if isinstance(raw_binding, dict) else None
                            if (not isinstance(symbol_name, str) or not symbol_name
                                    or symbol_name != declared_symbol
                                    or binding_status != "TEST_SOURCE_BYTES_MATCHED"
                                    or not isinstance(symbol_start, int) or isinstance(symbol_start, bool)
                                    or not isinstance(symbol_end, int) or isinstance(symbol_end, bool)
                                    or not isinstance(symbol_line, int) or isinstance(symbol_line, bool)
                                    or symbol_start < 1 or symbol_end < symbol_start
                                    or symbol_line != bound_line
                                    or not symbol_start <= symbol_line <= symbol_end):
                                conflicts.append({
                                    "kind": "INVALID_MATCHED_TEST_SOURCE_SYMBOL_EVIDENCE",
                                    "business_function_id": function_id,
                                    "test_case_id": item["test_case_id"],
                                })
                                symbol_status = "TEST_SOURCE_SYMBOL_INVALID"
                        raw_snapshot = item.get("test_source_snapshot_binding_evidence")
                        snapshot_status = item.get(
                            "test_source_snapshot_binding_status", "TEST_SOURCE_SNAPSHOT_NOT_CHECKED"
                        )
                        allowed_snapshot_statuses = {
                            "TEST_SOURCE_SNAPSHOT_NOT_CHECKED",
                            "TEST_SOURCE_SNAPSHOT_LOCATOR_NOT_PROVIDED",
                            "TEST_SOURCE_SNAPSHOT_LOCATOR_INVALID",
                            "TEST_SOURCE_NOT_IN_EXECUTION_SNAPSHOT",
                            "TEST_SOURCE_SNAPSHOT_HASH_MISMATCH",
                            "TEST_SOURCE_SNAPSHOT_ENTRY_MATCHED",
                        }
                        if snapshot_status not in allowed_snapshot_statuses:
                            conflicts.append({
                                "kind": "INVALID_TEST_SOURCE_SNAPSHOT_BINDING_STATUS",
                                "business_function_id": function_id,
                                "test_case_id": item["test_case_id"],
                            })
                            snapshot_status = "TEST_SOURCE_SNAPSHOT_LOCATOR_INVALID"
                        if raw_snapshot is not None and (
                            not isinstance(raw_snapshot, dict)
                            or raw_snapshot.get("status") != snapshot_status
                        ):
                            conflicts.append({
                                "kind": "TEST_SOURCE_SNAPSHOT_BINDING_EVIDENCE_STATUS_MISMATCH",
                                "business_function_id": function_id,
                                "test_case_id": item["test_case_id"],
                            })
                            snapshot_status = "TEST_SOURCE_SNAPSHOT_LOCATOR_INVALID"
                        if snapshot_status == "TEST_SOURCE_SNAPSHOT_HASH_MISMATCH":
                            conflicts.append({
                                "kind": "TEST_SOURCE_SNAPSHOT_BINDING_MISMATCH",
                                "business_function_id": function_id,
                                "test_case_id": item["test_case_id"],
                            })

                        raw_report_locator = item.get("test_source_report_locator_evidence")
                        report_locator_status = item.get(
                            "test_source_report_locator_status", "RUNNER_SOURCE_LOCATOR_NOT_PROVIDED"
                        )
                        allowed_report_locator_statuses = {
                            "RUNNER_SOURCE_LOCATOR_NOT_PROVIDED",
                            "RUNNER_SOURCE_LOCATOR_DUPLICATE_PROPERTY",
                            "RUNNER_SOURCE_LOCATOR_INVALID_PROPERTY",
                            "RUNNER_SOURCE_LOCATOR_INCOMPLETE",
                            "RUNNER_SOURCE_LOCATOR_INVALID_PATH",
                            "RUNNER_SOURCE_LOCATOR_INVALID_LINE",
                            "RUNNER_SOURCE_LOCATOR_INVALID_VALUE",
                            "RUNNER_SOURCE_LOCATOR_DECLARED_UNAUTHENTICATED",
                            "REPORT_SOURCE_LOCATOR_MAPPING_INCOMPLETE",
                            "REPORT_SOURCE_LOCATOR_MISMATCH",
                            "REPORT_SOURCE_LOCATOR_MATCHED_UNAUTHENTICATED",
                        }
                        if report_locator_status not in allowed_report_locator_statuses:
                            conflicts.append({
                                "kind": "INVALID_TEST_SOURCE_REPORT_LOCATOR_STATUS",
                                "business_function_id": function_id,
                                "test_case_id": item["test_case_id"],
                            })
                            report_locator_status = "RUNNER_SOURCE_LOCATOR_INVALID_PROPERTY"
                        if raw_report_locator is not None and (
                            not isinstance(raw_report_locator, dict)
                            or raw_report_locator.get("status") != report_locator_status
                        ):
                            conflicts.append({
                                "kind": "TEST_SOURCE_REPORT_LOCATOR_EVIDENCE_STATUS_MISMATCH",
                                "business_function_id": function_id,
                                "test_case_id": item["test_case_id"],
                            })
                            report_locator_status = "RUNNER_SOURCE_LOCATOR_INVALID_PROPERTY"
                        if report_locator_status == "REPORT_SOURCE_LOCATOR_MISMATCH":
                            conflicts.append({
                                "kind": "TEST_SOURCE_REPORT_LOCATOR_MISMATCH",
                                "business_function_id": function_id,
                                "test_case_id": item["test_case_id"],
                            })
                        test_source_binding_evidence.append({
                            "test_case_id": item["test_case_id"],
                            "status": binding_status,
                            "evidence": raw_binding if isinstance(raw_binding, dict) else None,
                        })
                        test_case_evidence.append({
                            "test_case_id": item["test_case_id"],
                            "status": item["status"],
                            "classname": item.get("classname"),
                            "name": item.get("name"),
                            "test_source_binding_status": binding_status,
                            "test_source_symbol_status": symbol_status,
                            "test_source_symbol_evidence": raw_symbol if isinstance(raw_symbol, dict) else None,
                            "test_source_snapshot_binding_status": snapshot_status,
                            "test_source_snapshot_binding_evidence": raw_snapshot if isinstance(raw_snapshot, dict) else None,
                            "test_source_report_locator_status": report_locator_status,
                            "test_source_report_locator_evidence": raw_report_locator if isinstance(raw_report_locator, dict) else None,
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
        binding_states = [item["status"] for item in test_source_binding_evidence]
        if not binding_states:
            test_source_binding_status = "NOT_PROVIDED"
        elif all(status == "TEST_SOURCE_BYTES_MATCHED" for status in binding_states):
            test_source_binding_status = "ALL_SOURCE_BYTES_MATCHED_AT_IMPORT"
        elif any(status == "TEST_SOURCE_BYTES_MATCHED" for status in binding_states):
            test_source_binding_status = "PARTIALLY_SOURCE_BYTES_MATCHED_AT_IMPORT"
        else:
            test_source_binding_status = "SOURCE_BINDING_NOT_VERIFIED"
        snapshot_states = [item.get("test_source_snapshot_binding_status") for item in test_case_evidence]
        if not snapshot_states:
            test_source_snapshot_binding_status = "NOT_PROVIDED"
        elif "TEST_SOURCE_SNAPSHOT_HASH_MISMATCH" in snapshot_states:
            test_source_snapshot_binding_status = "EVIDENCE_CONFLICT"
        elif all(status == "TEST_SOURCE_SNAPSHOT_ENTRY_MATCHED" for status in snapshot_states):
            test_source_snapshot_binding_status = "ALL_SNAPSHOT_ENTRIES_MATCHED_UNAUTHENTICATED"
        elif all(status == "TEST_SOURCE_SNAPSHOT_LOCATOR_NOT_PROVIDED" for status in snapshot_states):
            test_source_snapshot_binding_status = "NOT_PROVIDED"
        else:
            test_source_snapshot_binding_status = "SOURCE_SNAPSHOT_NOT_VERIFIED"
        report_locator_states = [item.get("test_source_report_locator_status") for item in test_case_evidence]
        if not report_locator_states:
            test_source_report_locator_status = "NOT_PROVIDED"
        elif "REPORT_SOURCE_LOCATOR_MISMATCH" in report_locator_states:
            test_source_report_locator_status = "EVIDENCE_CONFLICT"
        elif all(status == "REPORT_SOURCE_LOCATOR_MATCHED_UNAUTHENTICATED" for status in report_locator_states):
            test_source_report_locator_status = "ALL_REPORT_LOCATORS_MATCHED_UNAUTHENTICATED"
        elif all(status == "RUNNER_SOURCE_LOCATOR_NOT_PROVIDED" for status in report_locator_states):
            test_source_report_locator_status = "NOT_PROVIDED"
        else:
            test_source_report_locator_status = "REPORT_LOCATOR_NOT_VERIFIED"
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
            "test_source_binding_status": test_source_binding_status,
            "test_source_binding_evidence": test_source_binding_evidence,
            "test_source_snapshot_binding_status": test_source_snapshot_binding_status,
            "test_source_report_locator_status": test_source_report_locator_status,
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
                "test_source_binding_evidence": test_source_binding_evidence,
                "test_source_binding_status": test_source_binding_status,
                "test_source_snapshot_binding_status": test_source_snapshot_binding_status,
                "test_source_report_locator_status": test_source_report_locator_status,
            } if test else None,
            "code_coverage_status":cs,
            "line_coverage_percent": (percent if cov and percent_valid else None),
            "code_coverage_detail": {
                "source_hashes": dict(coverage_results.get("source_hashes", {})) if cov else {},
                "coverage_basis": cov.get("coverage_basis"),
                "verification_status": (cov.get("verification_status", cs) if coverage_source_verified else "SOURCE_BINDING_NOT_VERIFIED"),
                "executable_line_count": cov.get("executable_line_count"),
                "covered_line_count": cov.get("covered_line_count"),
                "uncovered_line_count": cov.get("uncovered_line_count"),
                "mapping_ranges": cov.get("mapping_ranges", []),
                "coverage_source_files_missing": cov.get("coverage_source_files_missing", []),
            } if cov else None,
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
            "测试与覆盖率报告、映射文件和业务图的 SHA-256 来源哈希会保留；覆盖率行范围与缺失源文件会随功能证据输出。",
            "覆盖率百分比、行计数和映射范围会执行结构校验；无效值产生证据冲突，不得作为可信覆盖结论。",
            "未提供的测试或覆盖率证据标记 NOT_MEASURED，不推断为失败或零覆盖。",
        ],
    }
