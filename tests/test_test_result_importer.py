from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from usit.test_result_importer import ingest_junit_results


class TestResultImporterTests(unittest.TestCase):
    def _inputs(self, base: Path, xml: str, mappings=None):
        report = base / "junit.xml"
        mapping = base / "mapping.json"
        graph = base / "business_graph.json"
        report.write_text(xml, encoding="utf-8")
        mapping.write_text(json.dumps({
            "schema_version": "usit-test-result-mapping-v1",
            "mappings": mappings if mappings is not None else [
                {"test_case_id": "tests.PhotoTests::test_upload", "business_function_id": "bf-upload"},
                {"test_case_id": "tests.PhotoTests::test_delete", "business_function_id": "bf-delete"},
            ],
        }), encoding="utf-8")
        graph.write_text(json.dumps({
            "schema_version": "usit-graph-v1",
            "business_understanding": {"business_function_views": [
                {"business_function_id": "bf-upload", "endpoint_id": "ep-upload"},
                {"business_function_id": "bf-delete", "endpoint_id": "ep-delete"},
                {"business_function_id": "bf-share", "endpoint_id": "ep-share"},
            ]},
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
            mapping.write_text(json.dumps({"schema_version": "usit-test-result-mapping-v1", "mappings": []}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "DTD/entity"):
                ingest_junit_results(report, mapping, graph)

    def test_missing_mapped_case_is_reported_as_not_seen(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            report, mapping, graph = self._inputs(base, '<testsuite><testcase classname="tests.PhotoTests" name="test_delete"/></testsuite>')
            result = ingest_junit_results(report, mapping, graph)
            upload = next(x for x in result["business_function_results"] if x["business_function_id"] == "bf-upload")
            self.assertEqual(upload["execution_status"], "NOT_EXECUTED")
            self.assertEqual(upload["mapped_cases_missing_from_report"], ["tests.PhotoTests::test_upload"])


if __name__ == "__main__":
    unittest.main()
