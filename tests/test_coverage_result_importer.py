from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from usit.coverage_result_importer import ingest_coverage_results


class CoverageImporterTests(unittest.TestCase):
    def test_coverage_py_json_imports_with_explicit_mapping_and_hashes(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            report = base / "coverage.json"
            mapping = base / "mapping.json"
            graph = base / "graph.json"
            report.write_text(json.dumps({"files":{"src/a.py":{
                "executed_lines":[4,5],"missing_lines":[6]}}}),encoding="utf-8")
            mapping.write_text(json.dumps({"schema_version":"usit-coverage-mapping-v1","mappings":[
                {"business_function_id":"bf-a","source_file":"src/a.py","line_start":4,"line_end":6}
            ]}),encoding="utf-8")
            graph.write_text(json.dumps({"business_understanding":{"business_function_views":[
                {"business_function_id":"bf-a"},{"business_function_id":"bf-b"}]}}),encoding="utf-8")
            result=ingest_coverage_results(report,mapping,graph,"coverage-py-json")
            by_id={x["business_function_id"]:x for x in result["business_function_coverage"]}
            self.assertEqual(by_id["bf-a"]["code_coverage_status"],"PARTIAL")
            self.assertEqual(by_id["bf-b"]["code_coverage_status"],"UNKNOWN")
            self.assertEqual(len(result["source_hashes"]["coverage_report_sha256"]),64)

    def test_jacoco_import_and_xml_entity_rejection(self):
        with tempfile.TemporaryDirectory() as td:
            base=Path(td)
            report=base/"jacoco.xml"
            mapping=base/"mapping.json"
            graph=base/"graph.json"
            report.write_text('<report><package name="demo"><sourcefile name="A.java"><line nr="7" ci="1" mi="0"/></sourcefile></package></report>',encoding="utf-8")
            mapping.write_text(json.dumps({"schema_version":"usit-coverage-mapping-v1","mappings":[
                {"business_function_id":"bf-a","source_file":"demo/A.java","line_start":7,"line_end":7}
            ]}),encoding="utf-8")
            graph.write_text(json.dumps({"business_understanding":{"business_function_views":[{"business_function_id":"bf-a"}]}}),encoding="utf-8")
            result=ingest_coverage_results(report,mapping,graph,"jacoco-xml")
            self.assertEqual(result["business_function_coverage"][0]["code_coverage_status"],"COVERED")
            report.write_text('<!DOCTYPE report [<!ENTITY x "x">]><report/>',encoding="utf-8")
            with self.assertRaisesRegex(ValueError,"DTD/entity"):
                ingest_coverage_results(report,mapping,graph,"jacoco-xml")


if __name__ == "__main__":
    unittest.main()
