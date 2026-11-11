"""Test-plan generation C1: reserved test_case_ids, the generated
test-case mapping, candidate attribution through import/verify, the
per-test-case G4 gate, and the reserved-but-not-executed gap.

The happy path runs the real pipeline (planner -> mapping -> real
importer -> verifier -> traceability); the unit tests pin the rules
at each layer.
"""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from usit.business_evidence_verifier import verify_business_function_evidence
from usit.business_test_planner import (
    assign_reserved_test_case_ids,
    build_test_case_mapping,
    plan_business_tests,
)
from usit.business_traceability import build_business_traceability
from usit.test_result_importer import ingest_junit_results
from usit.verification_gap_analyzer import build_verification_gap_backlog


# ---------------------------------------------------------------- helpers

def _reserved(test_id, function_id):
    return (
        "tests/test_usit_generated.py::test_"
        + test_id.replace("-", "_") + "_" + function_id.replace("-", "_")
    )


def _view(function_id):
    return {
        "business_function_id": function_id, "endpoint_id": "ep-" + function_id,
        "method": "GET", "endpoint": "/api/photos",
        "frontend_entrypoints": [{"source_file": "web/Photos.tsx", "event_line": 4}],
        "database_accesses": [{"operation": "SELECT", "tables": ["photos"]}],
        "source": {"path": "server/routes.js", "line": 8},
        "handler": {"path": "server/photos.js", "line": 22},
        "call_chain": [{"call_site": {"path": "server/photos.js", "line": 25}}],
        "authorization": {"evidence": [{"path": "server/auth.js", "line": 3}]},
        "identity_resource_flow": [{"path": "server/db.js", "line": 9}],
        "stage_status": {"handler": "RESOLVED", "calls": "RESOLVED_STATIC_CHAIN",
                         "database": "LINKED", "authorization": "EVIDENCE_FOUND",
                         "identity_resource": "EVIDENCE_FOUND"},
    }


def _graph(function_ids):
    graph = {"schema_version": "usit-graph-v1", "business_understanding": {
        "business_function_views": [_view(fid) for fid in function_ids]}}
    graph_hash = hashlib.sha256(
        json.dumps(graph, ensure_ascii=False, indent=2).encode()).hexdigest()
    return graph, graph_hash


def _candidate(test_id, function_id, confirmed=False):
    candidate = {"test_id": test_id, "business_function_id": function_id,
                 "test_case_id": _reserved(test_id, function_id),
                 "category": "api_contract_verification",
                 "purpose": "合成候选", "steps": ["打开照片"],
                 "expected_result": "照片正常展示。"}
    if confirmed:
        candidate["expected_result_confirmation"] = {
            "reviewed_by": "业务负责人",
            "reviewed_at": "2026-11-11T10:00:00+08:00",
            "rationale": "验收标准已批准，预期结果已确认。"}
    return candidate


def _bound_case(test_case_id, candidate_test_id, status):
    """A mapped case with a fully verified source binding, as the real
    importer produces for a test whose source file was supplied: the
    verifier only reports PASSED when at least one case is bound to
    exact source bytes."""
    digest = "a" * 64
    case = {
        "test_case_id": test_case_id, "status": status,
        "test_source_binding_status": "TEST_SOURCE_BYTES_MATCHED",
        "test_source_binding_evidence": {
            "status": "TEST_SOURCE_BYTES_MATCHED",
            "expected_sha256": digest, "actual_sha256": digest,
            "declared_test_source_file_sha256": digest,
            "test_source_file": "tests/test_usit_generated.py",
            "declared_test_source_file": "tests/test_usit_generated.py",
            "test_source_line": 3, "declared_test_source_line": 3,
            "source_line_count": 10,
        },
    }
    if candidate_test_id is not None:
        case["candidate_test_id"] = candidate_test_id
    return case


def _tests_doc(graph_hash, manifest, cases_by_function):
    provenance = {"execution_id": "run-c1", "pytest_exit_code": 0,
                  "pytest_exit_status": "EXIT_OK",
                  "source_manifest_file_hashes": manifest}
    results = []
    for fid, cases in cases_by_function.items():
        results.append({
            "business_function_id": fid,
            "execution_status": "FAILED" if any(c["status"] == "FAILED" for c in cases)
            else ("PASSED" if cases else "NOT_EXECUTED"),
            "expected_mapped_test_case_count": len(cases),
            "reported_mapped_test_case_count": len(cases),
            "mapped_case_completeness": "COMPLETE" if cases else "NO_EXPECTED_MAPPED_CASES",
            "mapped_cases_missing_from_report": [],
            "mapped_test_case_count": len(cases),
            "failed": sum(1 for c in cases if c["status"] == "FAILED"),
            "skipped": sum(1 for c in cases if c["status"] == "SKIPPED"),
            "mapped_test_cases": cases,
        })
    return {"schema_version": "usit-test-execution-results-v1",
            "source_hashes": {"test_report_sha256": "1" * 64,
                              "mapping_json_sha256": "2" * 64,
                              "business_graph_sha256": graph_hash},
            "execution_provenance": provenance,
            "business_function_results": results}


def _coverage_doc(graph_hash, manifest, function_ids):
    rows = []
    for fid in function_ids:
        source_file = f"src/{fid}.py"
        digest = manifest[source_file]
        rows.append({"business_function_id": fid, "code_coverage_status": "COVERED",
                     "line_coverage_percent": 100.0,
                     "source_binding_evidence": [{
                         "source_file": source_file,
                         "source_binding_status": "SOURCE_BYTES_MATCHED",
                         "expected_source_file_sha256": digest,
                         "actual_source_file_sha256": digest,
                         "source_line_count": 5, "line_start": 1, "line_end": 5}],
                     "mapping_ranges": [{"source_file": source_file,
                                         "line_start": 1, "line_end": 5}]})
    return {"schema_version": "usit-code-coverage-results-v1", "execution_id": "run-c1",
            "source_hashes": {"coverage_report_sha256": "3" * 64,
                              "mapping_json_sha256": "4" * 64,
                              "business_graph_sha256": graph_hash,
                              "source_files": manifest},
            "business_function_coverage": rows}


def _manifest(function_ids):
    return {f"src/{fid}.py": hashlib.sha256(fid.encode()).hexdigest()
            for fid in function_ids}


def _semantics_and_mapping(graph_hash, names):
    semantics = {"schema_version": "usit-business-semantics-v1", "features": [
        {"name": name, "source": "docs/requirements.md", "line": 10 + index}
        for index, name in enumerate(names.values())]}
    mapping = {"schema_version": "usit-feature-function-mapping-v1",
               "source_graph_sha256": graph_hash, "mappings": [
                   {"feature_name": name, "source_path": "docs/requirements.md",
                    "source_line": 10 + index, "decision": "APPROVED_LINK",
                    "business_function_id": fid, "reviewed_by": "reviewer-1",
                    "reviewed_at": "2026-11-01T12:00:00+08:00",
                    "rationale": "合成演示映射。"}
                   for index, (fid, name) in enumerate(names.items())]}
    return semantics, mapping


def _trace(function_ids, names, plan_candidates, evidence, graph, graph_hash):
    semantics, mapping = _semantics_and_mapping(graph_hash, names)
    plan = {"schema_version": "usit-business-test-plan-v1",
            "candidates": plan_candidates}
    return build_business_traceability(
        semantics, graph, mapping, plan, evidence,
        {"business_graph_sha256": graph_hash})


# ---------------------------------------------------------------- unit: IDs

class ReservedTestCaseIdTests(unittest.TestCase):
    def test_reserved_id_is_deterministic_and_pytest_shaped(self):
        first = assign_reserved_test_case_ids([
            {"test_id": "bt-api_contract_verification-0123456789abcdef0123",
             "business_function_id": "bf-photo-list"}])
        second = assign_reserved_test_case_ids([
            {"test_id": "bt-api_contract_verification-0123456789abcdef0123",
             "business_function_id": "bf-photo-list"}])
        self.assertEqual(first, 1)
        self.assertEqual(second, 1)
        expected = ("tests/test_usit_generated.py::test_"
                    "bt_api_contract_verification_0123456789abcdef0123_bf_photo_list")
        self.assertEqual(_reserved(
            "bt-api_contract_verification-0123456789abcdef0123", "bf-photo-list"),
            expected)

    def test_same_candidate_two_functions_get_different_ids(self):
        candidates = [
            {"test_id": "bt-api_contract_verification-0123456789abcdef0123",
             "business_function_id": "bf-a"},
            {"test_id": "bt-api_contract_verification-0123456789abcdef0123",
             "business_function_id": "bf-b"},
        ]
        self.assertEqual(assign_reserved_test_case_ids(candidates), 2)
        self.assertNotEqual(candidates[0]["test_case_id"], candidates[1]["test_case_id"])

    def test_candidate_without_function_gets_no_reserved_id(self):
        candidates = [{"test_id": "bt-feature_behavior-0123456789abcdef0123"}]
        self.assertEqual(assign_reserved_test_case_ids(candidates), 0)
        self.assertNotIn("test_case_id", candidates[0])

    def test_build_mapping_requires_graph_hash_and_rejects_duplicates(self):
        plan = {"candidates": [
            {"test_id": "bt-a", "business_function_id": "bf-a",
             "test_case_id": _reserved("bt-a", "bf-a")},
            {"test_id": "bt-b", "business_function_id": "bf-b",
             "test_case_id": _reserved("bt-b", "bf-b")},
            {"test_id": "bt-c"},  # unlinked: skipped
        ]}
        mapping = build_test_case_mapping(plan, None, "a" * 64)
        self.assertEqual(mapping["schema_version"], "usit-test-result-mapping-v1")
        self.assertEqual(mapping["source_graph_sha256"], "a" * 64)
        self.assertEqual(len(mapping["mappings"]), 2)
        entry = mapping["mappings"][0]
        self.assertEqual(entry["candidate_test_id"], "bt-a")
        self.assertTrue(entry["usit_reserved"])
        self.assertNotIn("test_source_file", entry)
        with self.assertRaises(ValueError):
            build_test_case_mapping(plan, None, "not-a-digest")
        duplicate = {"candidates": [
            {"test_id": "bt-a", "business_function_id": "bf-a",
             "test_case_id": _reserved("bt-a", "bf-a")},
            {"test_id": "bt-a", "business_function_id": "bf-a",
             "test_case_id": _reserved("bt-a", "bf-a")},
        ]}
        with self.assertRaises(ValueError):
            build_test_case_mapping(duplicate, None, "a" * 64)


# ---------------------------------------------------------------- importer

class ImporterCarryThroughTests(unittest.TestCase):
    def _run(self, base, report, mapping_entries, graph_doc):
        report_path = base / "report.json"
        mapping_path = base / "mapping.json"
        graph_path = base / "graph.json"
        report_path.write_text(json.dumps(report), encoding="utf-8")
        graph_bytes = json.dumps(graph_doc).encode("utf-8")
        graph_path.write_bytes(graph_bytes)
        mapping_path.write_text(json.dumps({
            "schema_version": "usit-test-result-mapping-v1",
            "source_graph_sha256": hashlib.sha256(graph_bytes).hexdigest(),
            "mappings": mapping_entries}), encoding="utf-8")
        return ingest_junit_results(report_path, mapping_path, graph_path,
                                    report_format="pytest-json")

    def test_pytest_json_import_carries_candidate_test_id(self):
        reserved = _reserved("bt-api_contract_verification-0123456789abcdef0123",
                             "bf-photo-list")
        graph_doc = {"schema_version": "usit-graph-v1", "business_understanding": {
            "business_function_views": [
                {"business_function_id": "bf-photo-list", "endpoint_id": "ep-1"}]}}
        with tempfile.TemporaryDirectory() as td:
            result = self._run(
                Path(td),
                {"tests": [{"nodeid": reserved, "outcome": "passed",
                            "duration": 0.1}], "duration": 0.2},
                [{"test_case_id": reserved, "business_function_id": "bf-photo-list",
                  "candidate_test_id": "bt-api_contract_verification-0123456789abcdef0123",
                  "usit_reserved": True}],
                graph_doc)
            function = result["business_function_results"][0]
            self.assertEqual(function["execution_status"], "PASSED")
            case = function["mapped_test_cases"][0]
            self.assertEqual(case["test_case_id"], reserved)
            self.assertEqual(
                case["candidate_test_id"],
                "bt-api_contract_verification-0123456789abcdef0123")

    def test_invalid_candidate_test_id_is_rejected(self):
        reserved = _reserved("bt-a", "bf-a")
        graph_doc = {"schema_version": "usit-graph-v1", "business_understanding": {
            "business_function_views": [
                {"business_function_id": "bf-a", "endpoint_id": "ep-1"}]}}
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(ValueError):
                self._run(
                    Path(td),
                    {"tests": [{"nodeid": reserved, "outcome": "passed"}]},
                    [{"test_case_id": reserved, "business_function_id": "bf-a",
                      "candidate_test_id": 42}],
                    graph_doc)


# ---------------------------------------------------------------- verifier

class VerifierCarryThroughTests(unittest.TestCase):
    def test_verifier_carries_candidate_test_id_into_evidence(self):
        function_ids = ("bf-c1-a",)
        graph, graph_hash = _graph(function_ids)
        manifest = _manifest(function_ids)
        reserved = _reserved("bt-api_contract_verification-0123456789abcdef0123",
                             "bf-c1-a")
        tests = _tests_doc(graph_hash, manifest, {
            "bf-c1-a": [_bound_case(
                reserved, "bt-api_contract_verification-0123456789abcdef0123",
                "PASSED")]})
        evidence = verify_business_function_evidence(
            graph, tests, _coverage_doc(graph_hash, manifest, function_ids),
            graph_hash)
        row = evidence["business_function_evidence"][0]
        detail = row["test_execution_detail"]
        self.assertEqual(detail["mapped_test_cases"][0]["candidate_test_id"],
                         "bt-api_contract_verification-0123456789abcdef0123")
        self.assertEqual(row["test_execution_status"], "PASSED")


# ---------------------------------------------------------------- traceability gate

class PerCaseGateTests(unittest.TestCase):
    def _row(self, function_id, candidates, cases):
        function_ids = (function_id,)
        graph, graph_hash = _graph(function_ids)
        manifest = _manifest(function_ids)
        tests = _tests_doc(graph_hash, manifest, {function_id: cases})
        evidence = verify_business_function_evidence(
            graph, tests, _coverage_doc(graph_hash, manifest, function_ids),
            graph_hash)
        names = {function_id: "功能"}
        result = _trace(function_ids, names, candidates, evidence, graph, graph_hash)
        return result["rows"][0]

    def test_confirmed_candidate_pass_supports_row_per_test_case(self):
        candidate = _candidate("bt-confirmed", "bf-c1-a", confirmed=True)
        row = self._row("bf-c1-a", [candidate], [_bound_case(
            candidate["test_case_id"], "bt-confirmed", "PASSED")])
        self.assertEqual(row["expected_result_confirmation_status"], "CONFIRMED")
        self.assertEqual(row["expected_result_confirmation_scope"], "PER_TEST_CASE")
        self.assertEqual(row["stage_status"]["test_execution"], "PASSED")

    def test_passed_case_of_unconfirmed_candidate_does_not_ride_on_other_confirmation(self):
        # The discriminating case: another candidate of the same
        # function IS confirmed, but the PASSED case belongs to the
        # unconfirmed one. The per-function gate would say CONFIRMED;
        # the per-test-case gate must say UNCONFIRMED.
        unconfirmed = _candidate("bt-unconfirmed", "bf-c1-a", confirmed=False)
        confirmed = _candidate("bt-confirmed-other", "bf-c1-a", confirmed=True)
        row = self._row("bf-c1-a", [unconfirmed, confirmed], [_bound_case(
            unconfirmed["test_case_id"], "bt-unconfirmed", "PASSED")])
        self.assertEqual(row["expected_result_confirmation_status"], "UNCONFIRMED")
        self.assertEqual(row["expected_result_confirmation_scope"], "PER_TEST_CASE")
        self.assertEqual(row["stage_status"]["test_execution"], "UNKNOWN")
        self.assertEqual(row["traceability_status"],
                         "TRACEABLE_WITH_INCONCLUSIVE_TEST_EVIDENCE")

    def test_hand_written_mapping_falls_back_to_per_function_gate(self):
        candidate = _candidate("bt-confirmed", "bf-c1-a", confirmed=True)
        row = self._row("bf-c1-a", [candidate], [_bound_case(
            "tests.PhotoTests::test_upload", None, "PASSED")])
        self.assertEqual(row["expected_result_confirmation_status"], "CONFIRMED")
        self.assertEqual(row["expected_result_confirmation_scope"], "PER_FUNCTION")
        self.assertEqual(row["stage_status"]["test_execution"], "PASSED")

    def test_failed_case_stays_failed_regardless_of_confirmation(self):
        candidate = _candidate("bt-confirmed", "bf-c1-a", confirmed=True)
        row = self._row("bf-c1-a", [candidate], [_bound_case(
            candidate["test_case_id"], "bt-confirmed", "FAILED")])
        self.assertEqual(row["expected_result_confirmation_scope"], "PER_TEST_CASE")
        self.assertEqual(row["stage_status"]["test_execution"], "FAILED")


# ---------------------------------------------------------------- gap

class ReservedNotExecutedGapTests(unittest.TestCase):
    def _backlog(self, function_id, candidates, cases):
        function_ids = (function_id,)
        graph, graph_hash = _graph(function_ids)
        manifest = _manifest(function_ids)
        tests = _tests_doc(graph_hash, manifest, {function_id: cases})
        evidence = verify_business_function_evidence(
            graph, tests, _coverage_doc(graph_hash, manifest, function_ids),
            graph_hash)
        names = {function_id: "功能"}
        result = _trace(function_ids, names, candidates, evidence, graph, graph_hash)
        semantics, _ = _semantics_and_mapping(graph_hash, names)
        plan = {"schema_version": "usit-business-test-plan-v1",
                "candidates": candidates}
        return build_verification_gap_backlog(
            semantics, graph, plan, evidence,
            {"business_graph_sha256": graph_hash}, result)

    def test_reserved_candidate_never_run_is_a_p1_gap(self):
        ran = _candidate("bt-ran", "bf-c1-a", confirmed=True)
        idle = _candidate("bt-idle", "bf-c1-a", confirmed=True)
        backlog = self._backlog("bf-c1-a", [ran, idle], [_bound_case(
            ran["test_case_id"], "bt-ran", "PASSED")])
        gaps = [g for g in backlog["gaps"]
                if g["gap_kind"] == "RESERVED_TEST_CASE_NOT_EXECUTED"]
        self.assertEqual(len(gaps), 1)
        self.assertEqual(gaps[0]["priority"], "P1")
        self.assertFalse(gaps[0]["defect_confirmed"])
        self.assertEqual(gaps[0]["target"], "bf-c1-a")
        self.assertIn("bt-idle", gaps[0]["reason"])

    def test_no_gap_when_function_has_no_execution_evidence(self):
        idle = _candidate("bt-idle", "bf-c1-a", confirmed=True)
        backlog = self._backlog("bf-c1-a", [idle], [])
        kinds = {g["gap_kind"] for g in backlog["gaps"]}
        self.assertNotIn("RESERVED_TEST_CASE_NOT_EXECUTED", kinds)
        # The matrix row exists with NOT_EXECUTED, so the analyzer
        # reports the inconclusive state instead of the per-candidate
        # gap; the per-candidate gap only fires when some case ran.
        self.assertIn("INCONCLUSIVE_TEST_EVIDENCE", kinds)


# ---------------------------------------------------------------- end to end

class TestPlanGenerationEndToEndTests(unittest.TestCase):
    def _pipeline(self, confirmed):
        semantics = {"endpoint_contract_alignment": [{
            "status": "EXACT_MATCH", "method": "GET", "path": "/api/photos",
            "document_evidence": {"source": "docs/api.md", "line": 7},
            "code_evidence": [{"source": "server/routes/photos.js", "line": 18}],
        }]}
        graph = {"business_understanding": {
            "business_function_views": [_view("bf-photo-list")],
            "endpoint_execution_paths": [{
                "endpoint_id": "ep-bf-photo-list", "method": "GET",
                "endpoint": "/api/photos",
                "source": {"path": "server/routes/photos.js", "line": 18},
                "handler": {"path": "server/photos.js", "line": 30,
                            "symbol_id": "sym-handler"},
                "call_chain": [{"call_site": {"path": "server/photos.js", "line": 34},
                                "target_id": "sym-db"}],
                "database_accesses": [{"database_access_id": "db-1",
                                       "path": "server/db.js", "line": 9}],
                "authorization": {"evidence": [{"authorization_check_id": "auth-1",
                                                "kind": "route_middleware",
                                                "path": "server/routes/photos.js",
                                                "line": 17}]},
                "identity_resource_flow": [{"identity_evidence_id": "identity-1",
                                            "path": "server/db.js", "line": 9}],
            }]}}
        plan = plan_business_tests(semantics, graph)
        candidate = next(x for x in plan["candidates"]
                         if x["category"] == "api_contract_verification")
        self.assertIn("test_case_id", candidate)
        self.assertEqual(candidate["business_function_id"], "bf-photo-list")
        graph = {"schema_version": "usit-graph-v1", **graph}
        graph_bytes = json.dumps(
            graph, ensure_ascii=False, indent=2).encode("utf-8")
        graph_hash = hashlib.sha256(graph_bytes).hexdigest()
        mapping = build_test_case_mapping(plan, None, graph_hash)
        self.assertEqual(len(mapping["mappings"]), 1)
        self.assertEqual(mapping["mappings"][0]["test_case_id"],
                         candidate["test_case_id"])
        # The author writes the test body under the reserved ID and
        # fills in the source locator in the generated mapping; the
        # importer then binds the executed case to exact source bytes.
        test_function = candidate["test_case_id"].split("::", 1)[1]
        test_source = (
            "# Test written by the author under the planner-reserved ID.\n"
            "import pytest\n\n\n"
            f"def {test_function}():\n    pass\n")
        test_digest = hashlib.sha256(test_source.encode("utf-8")).hexdigest()
        mapping["mappings"][0].update({
            "test_source_file": "tests/test_usit_generated.py",
            "test_source_line": 5,
            "test_source_file_sha256": test_digest})
        # The reserved ID is executed (pytest JSON report) and imported.
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            (base / "tests").mkdir()
            (base / "tests" / "test_usit_generated.py").write_text(
                test_source, encoding="utf-8")
            report = base / "report.json"
            mapping_path = base / "mapping.json"
            graph_path = base / "graph.json"
            report.write_text(json.dumps({
                "tests": [{"nodeid": candidate["test_case_id"],
                           "outcome": "passed", "duration": 0.1}],
                "duration": 0.2}), encoding="utf-8")
            graph_path.write_bytes(graph_bytes)
            mapping_path.write_text(json.dumps(mapping), encoding="utf-8")
            imported = ingest_junit_results(report, mapping_path, graph_path,
                                            report_format="pytest-json",
                                            project_root=base)
        function = imported["business_function_results"][0]
        self.assertEqual(function["execution_status"], "PASSED")
        self.assertEqual(function["mapped_test_cases"][0]["candidate_test_id"],
                         candidate["test_id"])
        manifest = _manifest(("bf-photo-list",))
        tests_doc = {"schema_version": "usit-test-execution-results-v1",
                     "source_hashes": {"test_report_sha256": "1" * 64,
                                       "mapping_json_sha256": "2" * 64,
                                       "business_graph_sha256": graph_hash},
                     "execution_provenance": {
                         "execution_id": "run-c1", "pytest_exit_code": 0,
                         "pytest_exit_status": "EXIT_OK",
                         "source_manifest_file_hashes": manifest},
                     "business_function_results": imported["business_function_results"]}
        evidence = verify_business_function_evidence(
            graph, tests_doc, _coverage_doc(graph_hash, manifest, ("bf-photo-list",)),
            graph_hash)
        if confirmed:
            candidate["expected_result_confirmation"] = {
                "reviewed_by": "业务负责人",
                "reviewed_at": "2026-11-11T10:00:00+08:00",
                "rationale": "验收标准已批准，预期结果已确认。"}
        names = {"bf-photo-list": "查看照片"}
        result = _trace(("bf-photo-list",), names, [candidate], evidence,
                        graph, graph_hash)
        return candidate, result

    def test_reserved_id_executed_with_confirmed_oracle_closes_row(self):
        candidate, result = self._pipeline(confirmed=True)
        row = result["rows"][0]
        self.assertEqual(row["expected_result_confirmation_status"], "CONFIRMED")
        self.assertEqual(row["expected_result_confirmation_scope"], "PER_TEST_CASE")
        self.assertEqual(row["stage_status"]["test_execution"], "PASSED")

    def test_reserved_id_executed_without_confirmation_stays_unknown(self):
        candidate, result = self._pipeline(confirmed=False)
        row = result["rows"][0]
        self.assertEqual(row["expected_result_confirmation_status"], "UNCONFIRMED")
        self.assertEqual(row["expected_result_confirmation_scope"], "PER_TEST_CASE")
        self.assertEqual(row["stage_status"]["test_execution"], "UNKNOWN")
        self.assertEqual(row["traceability_status"],
                         "TRACEABLE_WITH_INCONCLUSIVE_TEST_EVIDENCE")


if __name__ == "__main__":
    unittest.main()
