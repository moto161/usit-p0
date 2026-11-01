from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from usit.test_result_importer import ingest_junit_results


class PytestResultImporterTests(unittest.TestCase):
    def test_pytest_json_uses_exact_nodeid_mapping(self):
        with tempfile.TemporaryDirectory() as td:
            base=Path(td)
            report=base/"pytest.json"; mapping=base/"mapping.json"; graph=base/"graph.json"
            report.write_text(json.dumps({"tests":[
                {"nodeid":"tests/test_photo.py::test_upload","outcome":"passed"},
                {"nodeid":"tests/test_photo.py::test_delete","outcome":"failed"},
                {"nodeid":"tests/test_photo.py::test_share","outcome":"skipped"}]}),encoding="utf-8")
            mapping.write_text(json.dumps({"schema_version":"usit-test-result-mapping-v1","mappings":[
                {"test_case_id":"tests/test_photo.py::test_upload","business_function_id":"bf-upload"},
                {"test_case_id":"tests/test_photo.py::test_delete","business_function_id":"bf-delete"},
                {"test_case_id":"tests/test_photo.py::test_share","business_function_id":"bf-share"}]}),encoding="utf-8")
            graph.write_text(json.dumps({"business_understanding":{"business_function_views":[
                {"business_function_id":"bf-upload"},{"business_function_id":"bf-delete"},{"business_function_id":"bf-share"}]}}),encoding="utf-8")
            result=ingest_junit_results(report,mapping,graph,"pytest-json")
            self.assertEqual(result["source_format"],"PYTEST_JSON")
            self.assertEqual(result["test_case_status_counts"],{"FAILED":1,"PASSED":1,"SKIPPED":1})
            self.assertEqual({x["execution_status"] for x in result["business_function_results"]},
                             {"PASSED","FAILED","UNKNOWN"})

    def test_pytest_format_rejects_unknown_outcomes(self):
        with tempfile.TemporaryDirectory() as td:
            base=Path(td)
            report=base/"pytest.json"; mapping=base/"mapping.json"; graph=base/"graph.json"
            report.write_text(json.dumps({"tests":[{"nodeid":"x","outcome":"rerun"}]}),encoding="utf-8")
            mapping.write_text(json.dumps({"schema_version":"usit-test-result-mapping-v1","mappings":[]}),encoding="utf-8")
            graph.write_text(json.dumps({"business_understanding":{"business_function_views":[{"business_function_id":"bf"}]}}),encoding="utf-8")
            with self.assertRaisesRegex(ValueError,"unsupported outcome"):
                ingest_junit_results(report,mapping,graph,"pytest-json")


if __name__ == "__main__":
    unittest.main()
