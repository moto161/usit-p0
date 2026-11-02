from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from usit.coverage_result_importer import MAX_COVERAGE_BYTES, ingest_coverage_results
from usit.test_report_adapters import parse_jacoco_xml


def _write_graph_and_mapping(base: Path, function_ids: list[str], mappings: list[dict]) -> tuple[Path, Path]:
    graph = base / "graph.json"
    mapping = base / "mapping.json"
    graph.write_text(json.dumps({
        "schema_version": "usit-graph-v1",
        "business_understanding": {
            "business_function_views": [
                {"business_function_id": function_id} for function_id in function_ids
            ]
        },
    }), encoding="utf-8")
    mapping.write_text(json.dumps({
        "schema_version": "usit-coverage-mapping-v1",
        "source_graph_sha256": hashlib.sha256(graph.read_bytes()).hexdigest(),
        "mappings": mappings,
    }), encoding="utf-8")
    return mapping, graph


class CoverageImporterTests(unittest.TestCase):
    def test_coverage_py_json_imports_with_explicit_mapping_and_hashes(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            report = base / "coverage.json"
            report.write_text(json.dumps({"files": {"src/a.py": {
                "executed_lines": [4, 5], "missing_lines": [6]
            }}}), encoding="utf-8")
            mapping, graph = _write_graph_and_mapping(base, ["bf-a", "bf-b"], [
                {"business_function_id": "bf-a", "source_file": "src/a.py", "line_start": 4, "line_end": 6}
            ])
            result = ingest_coverage_results(report, mapping, graph, "coverage-py-json")
            by_id = {item["business_function_id"]: item for item in result["business_function_coverage"]}
            self.assertEqual(by_id["bf-a"]["code_coverage_status"], "PARTIAL")
            self.assertEqual(by_id["bf-b"]["code_coverage_status"], "UNKNOWN")
            self.assertEqual(len(result["source_hashes"]["coverage_report_sha256"]), 64)

    def test_jacoco_import_and_xml_entity_rejection(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            report = base / "jacoco.xml"
            report.write_text(
                '<report><package name="demo"><sourcefile name="A.java">'
                '<line nr="7" ci="1" mi="0"/></sourcefile></package></report>',
                encoding="utf-8",
            )
            mapping, graph = _write_graph_and_mapping(base, ["bf-a"], [
                {"business_function_id": "bf-a", "source_file": "demo/A.java", "line_start": 7, "line_end": 7}
            ])
            result = ingest_coverage_results(report, mapping, graph, "jacoco-xml")
            self.assertEqual(result["business_function_coverage"][0]["code_coverage_status"], "COVERED")
            report.write_text('<!DOCTYPE report [<!ENTITY x "x">]><report/>', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "DTD/entity"):
                ingest_coverage_results(report, mapping, graph, "jacoco-xml")

    def test_jacoco_rejects_declarations_after_long_prolog(self):
        data = b"<!--" + (b"x" * 1_000_001) + b"--><!DOCTYPE report [<!ENTITY x 'x'>]><report/>"
        with self.assertRaisesRegex(ValueError, "DTD/entity"):
            parse_jacoco_xml(data)

    def test_extremely_large_mapping_range_does_not_expand_memory(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            report = base / "coverage.json"
            report.write_text(json.dumps({"files": {"src/a.py": {
                "executed_lines": [7], "missing_lines": [8]
            }}}), encoding="utf-8")
            mapping, graph = _write_graph_and_mapping(base, ["bf-a"], [
                {"business_function_id": "bf-a", "source_file": "src/a.py",
                 "line_start": 1, "line_end": 10**12}
            ])
            result = ingest_coverage_results(report, mapping, graph, "coverage-py-json")
            self.assertEqual(result["business_function_coverage"][0]["code_coverage_status"], "PARTIAL")

    def test_coverage_report_size_limit_is_enforced_before_read(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            report = base / "coverage.json"
            with report.open("wb") as stream:
                stream.truncate(MAX_COVERAGE_BYTES + 1)
            mapping, graph = _write_graph_and_mapping(base, ["bf-a"], [])
            with self.assertRaisesRegex(ValueError, "exceeds size limit"):
                ingest_coverage_results(report, mapping, graph, "coverage-py-json")

    def test_coverage_report_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            target = base / "target.json"
            link = base / "link.json"
            target.write_text('{"files":{}}', encoding="utf-8")
            try:
                link.symlink_to(target)
            except OSError as exc:
                self.skipTest(f"platform does not permit symlink creation: {exc}")
            mapping, graph = _write_graph_and_mapping(base, ["bf-a"], [])
            with self.assertRaisesRegex(ValueError, "symbolic links or reparse points"):
                ingest_coverage_results(link, mapping, graph, "coverage-py-json")


if __name__ == "__main__":
    unittest.main()
