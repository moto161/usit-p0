from __future__ import annotations
import hashlib
import json
import unittest
from usit.business_evidence_verifier import verify_business_function_evidence


class BusinessEvidenceVerifierTests(unittest.TestCase):
    def setUp(self):
        self.graph={"schema_version":"usit-graph-v1","business_understanding":{
            "business_function_views":[
                {"business_function_id":"bf-upload","endpoint_id":"ep-upload","basis":"ENDPOINT_CENTRED_STATIC_SLICE","verification_status":"NOT_VERIFIED"},
                {"business_function_id":"bf-delete","endpoint_id":"ep-delete","basis":"ENDPOINT_CENTRED_STATIC_SLICE","verification_status":"NOT_VERIFIED"},
                {"business_function_id":"bf-share","endpoint_id":"ep-share","basis":"ENDPOINT_CENTRED_STATIC_SLICE","verification_status":"NOT_VERIFIED"}]}}
        self.graph_bytes=json.dumps(self.graph,ensure_ascii=False,indent=2).encode()
        self.graph_hash=hashlib.sha256(self.graph_bytes).hexdigest()

    def test_cross_checks_test_and_coverage_without_claiming_business_correctness(self):
        tests={"schema_version":"usit-test-execution-results-v1",
            "source_hashes":{"test_report_sha256":"1"*64,"mapping_json_sha256":"2"*64,"business_graph_sha256":self.graph_hash},
            "business_function_results":[
                {"business_function_id":"bf-upload","execution_status":"PASSED","mapped_test_case_count":3,"failed":0,"skipped":0},
                {"business_function_id":"bf-delete","execution_status":"FAILED","mapped_test_case_count":2,"failed":1,"skipped":0}]}
        coverage={"schema_version":"usit-code-coverage-results-v1",
            "source_hashes":{"coverage_report_sha256":"3"*64,"mapping_json_sha256":"4"*64,"business_graph_sha256":self.graph_hash},
            "business_function_coverage":[
                {"business_function_id":"bf-upload","code_coverage_status":"COVERED","line_coverage_percent":100.0},
                {"business_function_id":"bf-delete","code_coverage_status":"PARTIAL","line_coverage_percent":50.0}]}
        result=verify_business_function_evidence(self.graph,tests,coverage,self.graph_hash)
        by_id={x["business_function_id"]:x for x in result["business_function_evidence"]}
        self.assertEqual(by_id["bf-upload"]["verification_conclusion"],"TEST_PASS_AND_CODE_LINES_COVERED")
        self.assertEqual(
            by_id["bf-upload"]["code_coverage_detail"]["source_hashes"]["coverage_report_sha256"],
            "3"*64,
        )
        self.assertEqual(by_id["bf-delete"]["verification_conclusion"],"TEST_FAILURE_OBSERVED")
        self.assertEqual(by_id["bf-share"]["verification_conclusion"],"NO_RUNTIME_TEST_EVIDENCE")
        self.assertTrue(all(x["business_correctness"]=="NOT_PROVEN" for x in by_id.values()))
        self.assertEqual(result["evidence_conflict_count"],0)

    def test_graph_hash_mismatch_is_global_conflict(self):
        tests={"schema_version":"usit-test-execution-results-v1",
            "source_hashes":{"test_report_sha256":"1"*64,"mapping_json_sha256":"2"*64,"business_graph_sha256":"0"*64},"business_function_results":[]}
        result=verify_business_function_evidence(self.graph,tests,None,self.graph_hash)
        self.assertEqual(result["evidence_conflict_count"],1)
        self.assertTrue(all(x["evidence_conflict"] for x in result["business_function_evidence"]))
        self.assertTrue(all(x["verification_conclusion"]=="EVIDENCE_CONFLICT" for x in result["business_function_evidence"]))

    def test_unknown_function_and_duplicate_records_are_rejected_or_reported(self):
        tests={"schema_version":"usit-test-execution-results-v1",
            "source_hashes":{"test_report_sha256":"1"*64,"mapping_json_sha256":"2"*64,"business_graph_sha256":self.graph_hash},
            "business_function_results":[{"business_function_id":"not-in-graph","execution_status":"PASSED"}]}
        result=verify_business_function_evidence(self.graph,tests,None,self.graph_hash)
        self.assertEqual(result["evidence_conflicts"][0]["kind"],"UNKNOWN_BUSINESS_FUNCTION")
        duplicate={"schema_version":"usit-test-execution-results-v1",
            "source_hashes":{"test_report_sha256":"1"*64,"mapping_json_sha256":"2"*64,"business_graph_sha256":self.graph_hash},
            "business_function_results":[{"business_function_id":"bf-upload"},{"business_function_id":"bf-upload"}]}
        with self.assertRaisesRegex(ValueError,"duplicate business_function_id"):
            verify_business_function_evidence(self.graph,duplicate,None,self.graph_hash)

    def test_invalid_schema_and_status_are_not_silently_accepted(self):
        with self.assertRaisesRegex(ValueError,"usit-graph-v1"):
            verify_business_function_evidence({"schema_version":"wrong"})
        tests={"schema_version":"usit-test-execution-results-v1",
            "source_hashes":{"test_report_sha256":"1"*64,"mapping_json_sha256":"2"*64,"business_graph_sha256":self.graph_hash},
            "business_function_results":[{"business_function_id":"bf-upload","execution_status":"MAGIC_PASS"}]}
        result=verify_business_function_evidence(self.graph,tests,None,self.graph_hash)
        self.assertEqual(result["evidence_conflicts"][0]["kind"],"INVALID_TEST_STATUS")

    def test_incomplete_mapped_report_cannot_remain_passed_in_evidence_matrix(self):
        tests = {
            "schema_version": "usit-test-execution-results-v1",
            "source_hashes": {"test_report_sha256": "1"*64, "mapping_json_sha256": "2"*64, "business_graph_sha256": self.graph_hash},
            "business_function_results": [{
                "business_function_id": "bf-upload",
                "execution_status": "PASSED",
                "mapped_test_case_count": 1,
                "failed": 0,
                "skipped": 0,
                "expected_mapped_test_case_count": 2,
                "reported_mapped_test_case_count": 1,
                "mapped_case_completeness": "INCOMPLETE",
                "mapped_cases_missing_from_report": ["tests.PhotoTests::test_upload_permissions"],
                "mapped_test_cases": [{
                    "test_case_id": "tests.PhotoTests::test_upload",
                    "status": "PASSED",
                }],
            }],
        }
        result = verify_business_function_evidence(
            self.graph, tests, business_graph_sha256=self.graph_hash
        )
        upload = next(x for x in result["business_function_evidence"]
                      if x["business_function_id"] == "bf-upload")
        self.assertEqual(upload["reported_test_execution_status"], "PASSED")
        self.assertEqual(upload["test_execution_status"], "UNKNOWN")
        self.assertEqual(upload["verification_conclusion"], "EVIDENCE_CONFLICT")
        self.assertTrue(upload["evidence_conflict"])
        self.assertEqual(
            upload["test_execution_detail"]["mapped_cases_missing_from_report"],
            ["tests.PhotoTests::test_upload_permissions"],
        )
        self.assertIn(
            "TEST_PASS_WITH_INCOMPLETE_MAPPED_CASES",
            {item["kind"] for item in result["evidence_conflicts"]},
        )

    def test_case_level_failure_overrides_forged_pass_summary(self):
        tests = {
            "schema_version": "usit-test-execution-results-v1",
            "source_hashes": {"test_report_sha256": "1"*64, "mapping_json_sha256": "2"*64, "business_graph_sha256": self.graph_hash},
            "business_function_results": [{
                "business_function_id": "bf-upload",
                "execution_status": "PASSED",
                "expected_mapped_test_case_count": 1,
                "reported_mapped_test_case_count": 1,
                "mapped_case_completeness": "COMPLETE",
                "mapped_test_case_count": 1,
                "passed": 1,
                "failed": 0,
                "skipped": 0,
                "mapped_cases_missing_from_report": [],
                "mapped_test_cases": [{
                    "test_case_id": "tests.PhotoTests::test_upload",
                    "status": "FAILED",
                }],
            }],
        }
        result = verify_business_function_evidence(
            self.graph, tests, business_graph_sha256=self.graph_hash
        )
        upload = next(x for x in result["business_function_evidence"]
                      if x["business_function_id"] == "bf-upload")
        kinds = {item["kind"] for item in result["evidence_conflicts"]}
        self.assertEqual(upload["test_execution_status"], "FAILED")
        self.assertTrue(upload["evidence_conflict"])
        self.assertIn("TEST_CASE_SUMMARY_MISMATCH", kinds)
        self.assertIn("TEST_EXECUTION_STATUS_MISMATCH", kinds)

    def test_cli_writes_evidence_matrix_and_returns_conflict_exit_code(self):
        import tempfile
        from contextlib import redirect_stdout
        from io import StringIO
        from pathlib import Path
        from usit.cli import main
        with tempfile.TemporaryDirectory() as td:
            base=Path(td)
            graph=base/"graph.json"; tests=base/"tests.json"; out=base/"evidence.json"
            graph.write_bytes(self.graph_bytes)
            tests.write_text(json.dumps({"schema_version":"usit-test-execution-results-v1",
                "source_hashes":{"test_report_sha256":"1"*64,"mapping_json_sha256":"2"*64,"business_graph_sha256":self.graph_hash},
                "business_function_results":[]}),encoding="utf-8")
            output=StringIO()
            with redirect_stdout(output):
                code=main(["verify-evidence","--graph",str(graph),"--test-results",str(tests),"--out",str(out)])
            self.assertEqual(code,0)
            saved=json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(saved["business_function_count"],3)
            bad=base/"bad.json"
            tests.write_text(json.dumps({"schema_version":"usit-test-execution-results-v1",
                "source_hashes":{"test_report_sha256":"1"*64,"mapping_json_sha256":"2"*64,"business_graph_sha256":"0"*64},
                "business_function_results":[]}),encoding="utf-8")
            with redirect_stdout(StringIO()):
                code=main(["verify-evidence","--graph",str(graph),"--test-results",str(tests),"--out",str(bad)])
            self.assertEqual(code,1)
            self.assertEqual(json.loads(bad.read_text(encoding="utf-8"))["evidence_conflict_count"],1)

    def test_coverage_provenance_is_preserved_and_invalid_measurements_conflict(self):
        tests = {
            "schema_version": "usit-test-execution-results-v1",
            "source_hashes": {
                "test_report_sha256": "1"*64,
                "mapping_json_sha256": "2"*64,
                "business_graph_sha256": self.graph_hash,
            },
            "business_function_results": [],
        }
        coverage = {
            "schema_version": "usit-code-coverage-results-v1",
            "source_hashes": {
                "coverage_report_sha256": "3"*64,
                "mapping_json_sha256": "4"*64,
                "business_graph_sha256": self.graph_hash,
            },
            "business_function_coverage": [{
                "business_function_id": "bf-upload",
                "code_coverage_status": "COVERED",
                "line_coverage_percent": 120,
                "executable_line_count": 5,
                "covered_line_count": 5,
                "uncovered_line_count": 0,
                "mapping_ranges": [{
                    "source_file": "src/upload.py",
                    "line_start": 10,
                    "line_end": 14,
                }],
                "coverage_source_files_missing": [],
            }],
        }
        result = verify_business_function_evidence(
            self.graph, tests, coverage, self.graph_hash
        )
        upload = next(x for x in result["business_function_evidence"]
                      if x["business_function_id"] == "bf-upload")
        self.assertEqual(
            upload["code_coverage_detail"]["source_hashes"]["mapping_json_sha256"],
            "4"*64,
        )
        self.assertEqual(
            upload["code_coverage_detail"]["mapping_ranges"][0]["source_file"],
            "src/upload.py",
        )
        self.assertTrue(upload["evidence_conflict"])
        self.assertIn(
            "INVALID_COVERAGE_PERCENT",
            {item["kind"] for item in result["evidence_conflicts"]},
        )

    def test_missing_coverage_sources_can_remain_unknown_without_false_status_conflict(self):
        coverage = {
            "schema_version": "usit-code-coverage-results-v1",
            "source_hashes": {
                "coverage_report_sha256": "3"*64,
                "mapping_json_sha256": "4"*64,
                "business_graph_sha256": self.graph_hash,
            },
            "business_function_coverage": [{
                "business_function_id": "bf-upload",
                "code_coverage_status": "UNKNOWN",
                "line_coverage_percent": 80.0,
                "executable_line_count": 10,
                "covered_line_count": 8,
                "uncovered_line_count": 2,
                "mapping_ranges": [{"source_file": "src/upload.py", "line_start": 1, "line_end": 10}],
                "coverage_source_files_missing": ["src/other.py"],
            }],
        }
        result = verify_business_function_evidence(
            self.graph, coverage_results=coverage, business_graph_sha256=self.graph_hash
        )
        kinds = {item["kind"] for item in result["evidence_conflicts"]}
        self.assertNotIn("COVERAGE_STATUS_COUNT_MISMATCH", kinds)
        self.assertNotIn("COVERAGE_STATUS_WITH_MISSING_SOURCE_FILES", kinds)
        upload = next(item for item in result["business_function_evidence"]
                      if item["business_function_id"] == "bf-upload")
        self.assertEqual(upload["code_coverage_detail"]["verification_status"], "UNKNOWN")

    def test_coverage_counts_percent_status_and_nonfinite_values_are_cross_checked(self):
        import copy
        base = {
            "schema_version": "usit-code-coverage-results-v1",
            "source_hashes": {
                "coverage_report_sha256": "3"*64,
                "mapping_json_sha256": "4"*64,
                "business_graph_sha256": self.graph_hash,
            },
            "business_function_coverage": [{
                "business_function_id": "bf-upload",
                "code_coverage_status": "PARTIAL",
                "line_coverage_percent": 80.0,
                "executable_line_count": 10,
                "covered_line_count": 8,
                "uncovered_line_count": 2,
                "mapping_ranges": [{"source_file": "src/upload.py", "line_start": 1, "line_end": 10}],
                "coverage_source_files_missing": [],
            }],
        }

        valid = verify_business_function_evidence(
            self.graph, coverage_results=base, business_graph_sha256=self.graph_hash
        )
        self.assertEqual(valid["evidence_conflict_count"], 0)

        cases = [
            ("INCOMPLETE_COVERAGE_MEASUREMENTS", {"covered_line_count": 8}),
            ("INCONSISTENT_COVERAGE_COUNTS", {"uncovered_line_count": 1}),
            ("COVERAGE_PERCENT_COUNT_MISMATCH", {"line_coverage_percent": 75.0}),
            ("COVERAGE_STATUS_COUNT_MISMATCH", {"code_coverage_status": "COVERED"}),
            ("INVALID_COVERAGE_PERCENT", {"line_coverage_percent": float("nan")}),
            ("COVERAGE_STATUS_WITH_MISSING_SOURCE_FILES", {
                "coverage_source_files_missing": ["src/missing.py"],
            }),
        ]
        for expected_kind, changes in cases:
            with self.subTest(expected_kind=expected_kind):
                coverage = copy.deepcopy(base)
                coverage["business_function_coverage"][0].update(changes)
                result = verify_business_function_evidence(
                    self.graph, coverage_results=coverage, business_graph_sha256=self.graph_hash
                )
                self.assertIn(
                    expected_kind,
                    {item["kind"] for item in result["evidence_conflicts"]},
                )
                upload = next(
                    item for item in result["business_function_evidence"]
                    if item["business_function_id"] == "bf-upload"
                )
                self.assertTrue(upload["evidence_conflict"])
                # Even malformed non-finite input must not make the emitted artifact invalid JSON.
                json.dumps(result, allow_nan=False)

    def test_source_hashes_must_be_complete_sha256_digests(self):
        malformed = {
            "schema_version": "usit-test-execution-results-v1",
            "source_hashes": {
                "test_report_sha256": "not-a-hash",
                "mapping_json_sha256": "2"*64,
                "business_graph_sha256": self.graph_hash,
            },
            "business_function_results": [],
        }
        with self.assertRaisesRegex(ValueError, "test_report_sha256"):
            verify_business_function_evidence(self.graph, malformed, business_graph_sha256=self.graph_hash)

    def test_missing_inputs_remain_not_measured(self):
        result=verify_business_function_evidence(self.graph)
        self.assertTrue(all(x["test_execution_status"]=="NOT_MEASURED" for x in result["business_function_evidence"]))
        self.assertTrue(all(x["code_coverage_status"]=="NOT_MEASURED" for x in result["business_function_evidence"]))


if __name__=="__main__":
    unittest.main()
