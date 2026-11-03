from __future__ import annotations

import json
import unittest

from usit.test_report_adapters import (
    map_line_coverage_to_functions,
    parse_coverage_py_json,
    parse_jacoco_xml,
    parse_pytest_json,
)


class TestReportAdapters(unittest.TestCase):
    def test_pytest_json_normalizes_results_and_rejects_duplicates(self):
        data = json.dumps({"tests": [
            {"nodeid": "tests/test_a.py::test_ok", "outcome": "passed"},
            {"nodeid": "tests/test_a.py::test_skip", "outcome": "skipped"},
            {"nodeid": "tests/test_a.py::test_bad", "outcome": "failed"},
        ]}).encode()
        result = parse_pytest_json(data)
        self.assertEqual([x["status"] for x in result], ["FAILED", "PASSED", "SKIPPED"])
        duplicate = json.dumps({"tests": [
            {"nodeid": "x", "outcome": "passed"}, {"nodeid": "x", "outcome": "passed"}
        ]}).encode()
        with self.assertRaisesRegex(ValueError, "duplicate pytest"):
            parse_pytest_json(duplicate)

    def test_coverage_py_json_keeps_executed_and_missing_lines(self):
        data = json.dumps({"files": {"src/photo.py": {
            "executed_lines": [2, 4], "missing_lines": [3]
        }}}).encode()
        result = parse_coverage_py_json(data)
        self.assertEqual(result["src/photo.py"]["executable_lines"], [2, 3, 4])
        self.assertEqual(result["src/photo.py"]["executed_lines"], [2, 4])

    def test_jacoco_xml_extracts_package_relative_line_counters(self):
        xml = b"""<report name="demo"><package name="com/example"><sourcefile name="Photo.java">
          <line nr="10" mi="0" ci="2"/><line nr="11" mi="1" ci="0"/>
        </sourcefile></package></report>"""
        result = parse_jacoco_xml(xml)
        self.assertEqual(result["com/example/Photo.java"]["executed_lines"], [10])
        self.assertEqual(result["com/example/Photo.java"]["missing_lines"], [11])

    def test_function_coverage_requires_explicit_line_mapping(self):
        coverage = {"src/photo.py": {"executable_lines": [10, 11, 12],
            "executed_lines": [10, 12], "missing_lines": [11]}}
        mapping = {"schema_version": "usit-coverage-mapping-v1", "mappings": [
            {"business_function_id": "bf-upload", "source_file": "src/photo.py",
             "line_start": 10, "line_end": 12}
        ]}
        graph = {"business_understanding": {"business_function_views": [
            {"business_function_id": "bf-upload"}, {"business_function_id": "bf-delete"}
        ]}}
        result = map_line_coverage_to_functions(coverage, mapping, graph)
        by_id = {x["business_function_id"]: x for x in result}
        self.assertEqual(by_id["bf-upload"]["code_coverage_status"], "PARTIAL")
        self.assertEqual(by_id["bf-upload"]["line_coverage_percent"], 66.67)
        self.assertEqual(by_id["bf-delete"]["code_coverage_status"], "UNKNOWN")
        self.assertEqual(by_id["bf-delete"]["verification_status"], "NOT_MEASURED")

    def test_coverage_mapping_rejects_path_escape_and_unknown_function(self):
        graph = {"business_understanding": {"business_function_views": [
            {"business_function_id": "bf-upload"}
        ]}}
        with self.assertRaisesRegex(ValueError, "normalized relative path"):
            map_line_coverage_to_functions({}, {"schema_version":"usit-coverage-mapping-v1",
                "mappings":[{"business_function_id":"bf-upload","source_file":"../secret.py",
                "line_start":1,"line_end":2}]}, graph)
        with self.assertRaisesRegex(ValueError, "normalized relative path"):
            map_line_coverage_to_functions({}, {"schema_version":"usit-coverage-mapping-v1",
                "mappings":[{"business_function_id":"bf-upload","source_file":"C:outside.py",
                "line_start":1,"line_end":2}]}, graph)
        with self.assertRaisesRegex(ValueError, "unknown business_function_id"):
            map_line_coverage_to_functions({}, {"schema_version":"usit-coverage-mapping-v1",
                "mappings":[{"business_function_id":"missing","source_file":"src/a.py",
                "line_start":1,"line_end":2}]}, graph)


if __name__ == "__main__":
    unittest.main()
