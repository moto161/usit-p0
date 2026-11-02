from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from usit.test_result_importer import MAX_REPORT_BYTES, ingest_junit_results


class TestResultImporterTests(unittest.TestCase):
    def _inputs(self, base: Path, xml: str, mappings=None):
        report = base / "junit.xml"
        mapping = base / "mapping.json"
        graph = base / "business_graph.json"
        report.write_text(xml, encoding="utf-8")
        graph_doc = {
            "schema_version": "usit-graph-v1",
            "business_understanding": {"business_function_views": [
                {"business_function_id": "bf-upload", "endpoint_id": "ep-upload"},
                {"business_function_id": "bf-delete", "endpoint_id": "ep-delete"},
                {"business_function_id": "bf-share", "endpoint_id": "ep-share"},
            ]},
        }
        graph_bytes = json.dumps(graph_doc).encode("utf-8")
        graph.write_bytes(graph_bytes)
        mapping.write_text(json.dumps({
            "schema_version": "usit-test-result-mapping-v1",
            "source_graph_sha256": hashlib.sha256(graph_bytes).hexdigest(),
            "mappings": mappings if mappings is not None else [
                {"test_case_id": "tests.PhotoTests::test_upload", "business_function_id": "bf-upload"},
                {"test_case_id": "tests.PhotoTests::test_delete", "business_function_id": "bf-delete"},
            ],
        }), encoding="utf-8")
        return report, mapping, graph

    def test_imports_pass_fail_and_not_executed_with_explicit_mapping(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            report, mapping, graph = self._inputs(base, """<testsuites><testsuite>
              <testcase classname="tests.PhotoTests" name="test_upload"/>
              <testcase classname="tests.PhotoTests" name="test_delete"><failure message="bad"/></testcase>
              <testcase classname="tests.PhotoTests" name="test_other"/>
            </testsuite></testsuites>""")
            result = ingest_junit_results(report, mapping, graph)
            by_id = {x["business_function_id"]: x for x in result["business_function_results"]}
            self.assertEqual(by_id["bf-upload"]["execution_status"], "PASSED")
            self.assertEqual(by_id["bf-delete"]["execution_status"], "FAILED")
            self.assertEqual(by_id["bf-share"]["execution_status"], "NOT_EXECUTED")
            self.assertEqual(result["unmapped_test_case_count"], 1)
            self.assertEqual(by_id["bf-upload"]["code_coverage"], "NOT_MEASURED")
            self.assertEqual(result["schema_version"], "usit-test-execution-results-v1")

    def test_skipped_case_produces_unknown_not_pass(self):
        with tempfile.TemporaryDirectory() as td:
            report, mapping, graph = self._inputs(Path(td), """<testsuite>
              <testcase classname="tests.PhotoTests" name="test_upload"><skipped/></testcase>
            </testsuite>""")
            result = ingest_junit_results(report, mapping, graph)
            upload = next(x for x in result["business_function_results"] if x["business_function_id"] == "bf-upload")
            self.assertEqual(upload["execution_status"], "UNKNOWN")

    def test_partial_mapped_report_is_unknown_and_never_false_pass(self):
        from usit.business_evidence_verifier import verify_business_function_evidence
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            expected = [
                {"test_case_id": "tests.PhotoTests::test_upload", "business_function_id": "bf-upload"},
                {"test_case_id": "tests.PhotoTests::test_upload_permissions", "business_function_id": "bf-upload"},
            ]
            report, mapping, graph = self._inputs(
                base,
                """<testsuite>
                  <testcase classname="tests.PhotoTests" name="test_upload"/>
                </testsuite>""",
                expected,
            )
            result = ingest_junit_results(report, mapping, graph)
            upload = next(x for x in result["business_function_results"]
                          if x["business_function_id"] == "bf-upload")
            self.assertEqual(upload["execution_status"], "UNKNOWN")
            self.assertEqual(upload["execution_status_reason"], "MAPPED_TEST_CASES_MISSING_FROM_REPORT")
            self.assertEqual(upload["expected_mapped_test_case_count"], 2)
            self.assertEqual(upload["reported_mapped_test_case_count"], 1)
            self.assertEqual(upload["mapped_case_completeness"], "INCOMPLETE")
            self.assertEqual(
                upload["mapped_cases_missing_from_report"],
                ["tests.PhotoTests::test_upload_permissions"],
            )
            graph_doc = json.loads(graph.read_text(encoding="utf-8"))
            matrix = verify_business_function_evidence(
                graph_doc, result, business_graph_sha256=result["source_hashes"]["business_graph_sha256"]
            )
            verified_upload = next(x for x in matrix["business_function_evidence"]
                                   if x["business_function_id"] == "bf-upload")
            self.assertEqual(verified_upload["test_execution_status"], "UNKNOWN")
            self.assertEqual(verified_upload["verification_conclusion"], "TEST_EVIDENCE_INCONCLUSIVE")
            self.assertFalse(verified_upload["evidence_conflict"])

    def test_duplicate_case_ids_and_duplicate_mapping_are_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            xml = """<testsuite>
              <testcase classname="tests.PhotoTests" name="test_upload"/>
              <testcase classname="tests.PhotoTests" name="test_upload"/>
            </testsuite>"""
            report, mapping, graph = self._inputs(base, xml)
            with self.assertRaisesRegex(ValueError, "duplicate JUnit"):
                ingest_junit_results(report, mapping, graph)
            report.write_text('<testsuite><testcase name="test_upload"/></testsuite>', encoding="utf-8")
            dupes = [
                {"test_case_id": "test_upload", "business_function_id": "bf-upload"},
                {"test_case_id": "test_upload", "business_function_id": "bf-delete"},
            ]
            report, mapping, graph = self._inputs(base, '<testsuite><testcase name="test_upload"/></testsuite>', dupes)
            with self.assertRaisesRegex(ValueError, "duplicate mapping"):
                ingest_junit_results(report, mapping, graph)

    def test_rejects_unknown_function_and_xml_dtd(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            report, mapping, graph = self._inputs(base, '<testsuite><testcase name="test_upload"/></testsuite>', [
                {"test_case_id": "test_upload", "business_function_id": "missing"},
            ])
            with self.assertRaisesRegex(ValueError, "unknown business_function_id"):
                ingest_junit_results(report, mapping, graph)
            report.write_text('<!DOCTYPE x [<!ENTITY a "x">]><testsuite><testcase name="x"/></testsuite>', encoding="utf-8")
            mapping.write_text(json.dumps({
                "schema_version": "usit-test-result-mapping-v1",
                "source_graph_sha256": hashlib.sha256(graph.read_bytes()).hexdigest(),
                "mappings": [],
            }), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "DTD/entity"):
                ingest_junit_results(report, mapping, graph)

    def test_unknown_graph_schema_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            report, mapping, graph = self._inputs(
                Path(td), '<testsuite><testcase name="test_upload"/></testsuite>'
            )
            graph_doc = json.loads(graph.read_text(encoding="utf-8"))
            graph_doc["schema_version"] = "usit-business-knowledge-graph-v1"
            graph.write_text(json.dumps(graph_doc), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "schema_version must be usit-graph-v1"):
                ingest_junit_results(report, mapping, graph)

    def test_mapping_bound_to_old_graph_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            report, mapping, graph = self._inputs(
                Path(td), '<testsuite><testcase name="test_upload"/></testsuite>'
            )
            graph_doc = json.loads(graph.read_text(encoding="utf-8"))
            graph_doc["revision_marker"] = "changed-after-mapping"
            graph.write_text(json.dumps(graph_doc), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "source_graph_sha256"):
                ingest_junit_results(report, mapping, graph)

    def test_oversized_report_is_rejected_before_unbounded_read(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            report, mapping, graph = self._inputs(
                base, '<testsuite><testcase name="test_upload"/></testsuite>'
            )
            with report.open("wb") as stream:
                stream.truncate(MAX_REPORT_BYTES + 1)
            with self.assertRaisesRegex(ValueError, "size limit"):
                ingest_junit_results(report, mapping, graph)

    def test_rejects_dtd_after_large_xml_prolog_comment(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            report, mapping, graph = self._inputs(
                base, '<testsuite><testcase name="test_upload"/></testsuite>'
            )
            padded = (
                b'<?xml version="1.0"?>\n<!--'
                + (b'x' * 1_000_001)
                + b'-->\n<!DOCTYPE x [<!ENTITY a "x">]>'
                + b'<testsuite><testcase name="test_upload"/></testsuite>'
            )
            report.write_bytes(padded)
            with self.assertRaisesRegex(ValueError, "DTD/entity"):
                ingest_junit_results(report, mapping, graph)

    def test_missing_mapped_case_is_reported_as_not_seen(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            report, mapping, graph = self._inputs(base, '<testsuite><testcase classname="tests.PhotoTests" name="test_delete"/></testsuite>')
            result = ingest_junit_results(report, mapping, graph)
            upload = next(x for x in result["business_function_results"] if x["business_function_id"] == "bf-upload")
            self.assertEqual(upload["execution_status"], "UNKNOWN")
            self.assertEqual(
                upload["execution_status_reason"],
                "MAPPED_TEST_CASES_MISSING_FROM_REPORT",
            )
            self.assertEqual(upload["expected_mapped_test_case_count"], 1)
            self.assertEqual(upload["reported_mapped_test_case_count"], 0)
            self.assertEqual(upload["mapped_case_completeness"], "INCOMPLETE")
            self.assertEqual(upload["mapped_cases_missing_from_report"], ["tests.PhotoTests::test_upload"])
            from usit.business_evidence_verifier import verify_business_function_evidence
            graph_doc = json.loads(graph.read_text(encoding="utf-8"))
            matrix = verify_business_function_evidence(
                graph_doc, result,
                business_graph_sha256=result["source_hashes"]["business_graph_sha256"],
            )
            verified_upload = next(
                item for item in matrix["business_function_evidence"]
                if item["business_function_id"] == "bf-upload"
            )
            self.assertEqual(verified_upload["test_execution_status"], "UNKNOWN")
            self.assertEqual(
                verified_upload["verification_conclusion"],
                "TEST_EVIDENCE_INCONCLUSIVE",
            )


    def test_cli_writes_result_file_without_running_tests(self):
        from contextlib import redirect_stdout
        from io import StringIO
        from usit.cli import main
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            report, mapping, graph = self._inputs(base, """<testsuite>
              <testcase classname="tests.PhotoTests" name="test_upload"/>
            </testsuite>""")
            output = base / "out" / "results.json"
            stdout = StringIO()
            with redirect_stdout(stdout):
                code = main(["ingest-test-results", "--junit", str(report),
                             "--mapping", str(mapping), "--graph", str(graph),
                             "--out", str(output)])
            self.assertEqual(code, 0)
            self.assertTrue(output.is_file())
            summary = json.loads(stdout.getvalue())
            self.assertEqual(summary["test_case_count"], 1)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["business_function_status_counts"],
                             {"NOT_EXECUTED": 1, "PASSED": 1, "UNKNOWN": 1})

if __name__ == "__main__":
    unittest.main()
