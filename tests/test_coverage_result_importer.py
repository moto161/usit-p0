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
    for item in mappings:
        source = base / "project" / item["source_file"]
        source.parent.mkdir(parents=True, exist_ok=True)
        if not source.exists():
            source.write_text(chr(10).join("source line" for _ in range(min(max(item["line_end"], 10), 10))) + chr(10), encoding="utf-8")
        item["source_file_sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
    mapping.write_text(json.dumps({
        "schema_version": "usit-coverage-mapping-v1",
        "source_graph_sha256": hashlib.sha256(graph.read_bytes()).hexdigest(),
        "mappings": mappings,
    }), encoding="utf-8")
    return mapping, graph


def _write_provenance(base: Path, execution_id: str, manifest: dict, key=None) -> tuple[Path, Path]:
    """Write a minimal valid provenance record + source manifest pair."""
    from usit.test_execution_provenance import SCHEMA_VERSION, canonical_json_bytes, sha256_hex

    manifest_bytes = canonical_json_bytes(manifest)
    record = {
        "schema_version": SCHEMA_VERSION,
        "execution_id": execution_id,
        "started_at": "2026-11-03T10:00:00Z",
        "completed_at": "2026-11-03T10:00:01Z",
        "runner": {"name": "pytest", "version": "8.3.5"},
        "report_format": "JUNIT_XML",
        "report_relative_path": "junit.xml",
        "report_sha256": "a" * 64,
        "source_snapshot": {
            "manifest_schema_version": manifest["schema_version"],
            "manifest_sha256": sha256_hex(manifest_bytes),
            "file_count": len(manifest["files"]),
            "capture_method": "GIT_ARCHIVE_HEAD_STAGED_COPY",
        },
        "working_tree_state": {"state": "CLEAN", "untracked_files": "INCLUDED"},
        "command_fingerprint": "b" * 64,
        "environment_fingerprint": "c" * 64,
    }
    if key is not None:
        from usit.evidence_signing import sign_document

        record["signature"] = sign_document(record, key)
    prov_path = base / "execution-provenance.json"
    manifest_path = base / "source-manifest.json"
    prov_path.write_text(json.dumps(record), encoding="utf-8")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return prov_path, manifest_path


class CoverageImporterTests(unittest.TestCase):
    def test_coverage_without_provenance_is_not_provided(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            report = base / "coverage.json"
            report.write_text(json.dumps({"files": {"src/a.py": {
                "executed_lines": [4, 5], "missing_lines": [6]
            }}}), encoding="utf-8")
            mapping, graph = _write_graph_and_mapping(base, ["bf-a"], [
                {"business_function_id": "bf-a", "source_file": "src/a.py", "line_start": 4, "line_end": 6}
            ])
            result = ingest_coverage_results(report, mapping, graph, "coverage-py-json", base / "project")
            self.assertEqual(result["execution_binding"], "NOT_PROVIDED")
            self.assertIsNone(result["execution_id"])
            self.assertEqual(result["provenance_signature_status"], "NOT_CHECKED")
            # The measurement still stands; only the execution binding is unclaimed.
            by_id = {item["business_function_id"]: item for item in result["business_function_coverage"]}
            self.assertEqual(by_id["bf-a"]["code_coverage_status"], "PARTIAL")

    def test_coverage_with_provenance_records_execution_id(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            report = base / "coverage.json"
            report.write_text(json.dumps({"files": {"src/a.py": {
                "executed_lines": [4, 5], "missing_lines": [6]
            }}}), encoding="utf-8")
            mapping, graph = _write_graph_and_mapping(base, ["bf-a"], [
                {"business_function_id": "bf-a", "source_file": "src/a.py", "line_start": 4, "line_end": 6}
            ])
            manifest = {"schema_version": "usit-manifest-v1", "files": [
                {"path": "src/a.py", "sha256": "a" * 64}
            ]}
            prov_path, manifest_path = _write_provenance(base, "run_20261111T000000Z_" + "a" * 32, manifest)
            result = ingest_coverage_results(
                report, mapping, graph, "coverage-py-json", base / "project",
                provenance_record_path=prov_path, source_manifest_path=manifest_path,
            )
            self.assertEqual(result["execution_binding"], "PROVIDED")
            self.assertEqual(result["execution_id"], "run_20261111T000000Z_" + "a" * 32)
            self.assertEqual(result["provenance_signature_status"], "NOT_CHECKED")

    def test_coverage_with_signed_provenance_reports_valid_signature(self):
        from usit.evidence_signing import generate_run_key

        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            report = base / "coverage.json"
            report.write_text(json.dumps({"files": {"src/a.py": {
                "executed_lines": [4, 5], "missing_lines": [6]
            }}}), encoding="utf-8")
            mapping, graph = _write_graph_and_mapping(base, ["bf-a"], [
                {"business_function_id": "bf-a", "source_file": "src/a.py", "line_start": 4, "line_end": 6}
            ])
            manifest = {"schema_version": "usit-manifest-v1", "files": [
                {"path": "src/a.py", "sha256": "a" * 64}
            ]}
            key = generate_run_key()
            prov_path, manifest_path = _write_provenance(
                base, "run_20261111T000000Z_" + "a" * 32, manifest, key=key
            )
            result = ingest_coverage_results(
                report, mapping, graph, "coverage-py-json", base / "project",
                provenance_record_path=prov_path, source_manifest_path=manifest_path,
                hmac_key=key,
            )
            self.assertEqual(result["execution_binding"], "PROVIDED")
            self.assertEqual(result["provenance_signature_status"], "VALID")

    def test_coverage_provenance_and_manifest_must_be_provided_together(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            report = base / "coverage.json"
            report.write_text(json.dumps({"files": {}}), encoding="utf-8")
            mapping, graph = _write_graph_and_mapping(base, ["bf-a"], [])
            manifest = {"schema_version": "usit-manifest-v1", "files": []}
            prov_path, manifest_path = _write_provenance(base, "run-x", manifest)
            with self.assertRaisesRegex(ValueError, "provided together"):
                ingest_coverage_results(
                    report, mapping, graph, "coverage-py-json",
                    provenance_record_path=prov_path,
                )
            with self.assertRaisesRegex(ValueError, "provided together"):
                ingest_coverage_results(
                    report, mapping, graph, "coverage-py-json",
                    source_manifest_path=manifest_path,
                )

    def test_coverage_provenance_manifest_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            report = base / "coverage.json"
            report.write_text(json.dumps({"files": {}}), encoding="utf-8")
            mapping, graph = _write_graph_and_mapping(base, ["bf-a"], [])
            manifest = {"schema_version": "usit-manifest-v1", "files": [
                {"path": "src/a.py", "sha256": "a" * 64}
            ]}
            prov_path, manifest_path = _write_provenance(base, "run-x", manifest)
            # Replace the manifest with different content after the record
            # was built, so the declared hash no longer matches.
            manifest["files"] = [{"path": "src/a.py", "sha256": "b" * 64}]
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "manifest_sha256"):
                ingest_coverage_results(
                    report, mapping, graph, "coverage-py-json",
                    provenance_record_path=prov_path, source_manifest_path=manifest_path,
                )

    def test_coverage_tampered_signed_provenance_is_rejected(self):
        from usit.evidence_signing import generate_run_key

        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            report = base / "coverage.json"
            report.write_text(json.dumps({"files": {}}), encoding="utf-8")
            mapping, graph = _write_graph_and_mapping(base, ["bf-a"], [])
            manifest = {"schema_version": "usit-manifest-v1", "files": []}
            key = generate_run_key()
            prov_path, manifest_path = _write_provenance(base, "run-x", manifest, key=key)
            record = json.loads(prov_path.read_text(encoding="utf-8"))
            record["execution_id"] = "run-forged"
            prov_path.write_text(json.dumps(record), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "signature is INVALID"):
                ingest_coverage_results(
                    report, mapping, graph, "coverage-py-json",
                    provenance_record_path=prov_path, source_manifest_path=manifest_path,
                    hmac_key=key,
                )

    def test_coverage_mapping_rejects_duplicate_business_function_ids_in_graph(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            report = base / "coverage.json"
            report.write_text(json.dumps({"files": {}}), encoding="utf-8")
            mapping, graph = _write_graph_and_mapping(base, ["bf-duplicate", "bf-duplicate"], [])
            with self.assertRaisesRegex(ValueError, "duplicate business_function_id in graph"):
                ingest_coverage_results(report, mapping, graph, "coverage-py-json")

    def test_coverage_mapping_rejects_non_string_business_function_ids(self):
        invalid_ids = [None, 7, [], {}, True, ""]
        for invalid_id in invalid_ids:
            with self.subTest(invalid_id=invalid_id):
                with tempfile.TemporaryDirectory() as td:
                    base = Path(td)
                    report = base / "coverage.json"
                    report.write_text(json.dumps({"files": {"src/a.py": {
                        "executed_lines": [1], "missing_lines": []
                    }} }), encoding="utf-8")
                    mapping, graph = _write_graph_and_mapping(base, ["bf-a"], [
                        {"business_function_id": "bf-a", "source_file": "src/a.py",
                         "line_start": 1, "line_end": 1}
                    ])
                    doc = json.loads(mapping.read_text(encoding="utf-8"))
                    doc["mappings"][0]["business_function_id"] = invalid_id
                    mapping.write_text(json.dumps(doc), encoding="utf-8")
                    with self.assertRaisesRegex(ValueError, "invalid business_function_id"):
                        ingest_coverage_results(report, mapping, graph, "coverage-py-json")

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
            result = ingest_coverage_results(report, mapping, graph, "coverage-py-json", base / "project")
            by_id = {item["business_function_id"]: item for item in result["business_function_coverage"]}
            self.assertEqual(by_id["bf-a"]["code_coverage_status"], "PARTIAL")
            self.assertEqual(by_id["bf-a"]["source_binding_evidence"][0]["source_binding_status"], "SOURCE_BYTES_MATCHED")
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
            result = ingest_coverage_results(report, mapping, graph, "jacoco-xml", base / "project")
            self.assertEqual(result["business_function_coverage"][0]["code_coverage_status"], "COVERED")
            self.assertEqual(result["business_function_coverage"][0]["source_binding_evidence"][0]["source_binding_status"], "SOURCE_BYTES_MATCHED")
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
            result = ingest_coverage_results(report, mapping, graph, "coverage-py-json", base / "project")
            self.assertEqual(result["business_function_coverage"][0]["code_coverage_status"], "UNKNOWN")
            self.assertEqual(result["business_function_coverage"][0]["source_binding_evidence"][0]["source_binding_status"],
                             "SOURCE_LINE_RANGE_OUT_OF_BOUNDS")

    def test_source_hash_mismatch_and_out_of_bounds_ranges_fail_closed(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            report = base / "coverage.json"
            report.write_text(json.dumps({"files": {"src/a.py": {"executed_lines": [1], "missing_lines": []}}}), encoding="utf-8")
            mappings = [{"business_function_id": "bf-a", "source_file": "src/a.py", "line_start": 1, "line_end": 20}]
            mapping, graph = _write_graph_and_mapping(base, ["bf-a"], mappings)
            source = base / "project" / "src" / "a.py"
            source.write_text("changed" + chr(10), encoding="utf-8")
            result = ingest_coverage_results(report, mapping, graph, "coverage-py-json", base / "project")
            row = result["business_function_coverage"][0]
            self.assertEqual(row["source_binding_evidence"][0]["source_binding_status"], "SOURCE_HASH_MISMATCH")
            self.assertEqual(row["code_coverage_status"], "UNKNOWN")
            mappings[0]["source_file_sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
            mapping.write_text(json.dumps({"schema_version":"usit-coverage-mapping-v1",
                "source_graph_sha256":hashlib.sha256(graph.read_bytes()).hexdigest(),"mappings":mappings}), encoding="utf-8")
            result = ingest_coverage_results(report, mapping, graph, "coverage-py-json", base / "project")
            self.assertEqual(result["business_function_coverage"][0]["source_binding_evidence"][0]["source_binding_status"],
                             "SOURCE_LINE_RANGE_OUT_OF_BOUNDS")
            self.assertEqual(result["business_function_coverage"][0]["code_coverage_status"], "UNKNOWN")

    def test_invalid_source_line_ranges_never_receive_matched_status(self):
        from usit.coverage_result_importer import _verify_source_bindings

        invalid_ranges = [
            (0, 1),
            (3, 2),
            (True, 2),
            (1, False),
            ("1", 2),
        ]
        for line_start, line_end in invalid_ranges:
            with self.subTest(line_start=line_start, line_end=line_end):
                with tempfile.TemporaryDirectory() as td:
                    base = Path(td)
                    report = base / "coverage.json"
                    report.write_text(json.dumps({"files": {"src/a.py": {
                        "executed_lines": [1], "missing_lines": []
                    }}}), encoding="utf-8")
                    mapping, graph = _write_graph_and_mapping(base, ["bf-a"], [
                        {"business_function_id": "bf-a", "source_file": "src/a.py",
                         "line_start": 1, "line_end": 2}
                    ])
                    doc = json.loads(mapping.read_text(encoding="utf-8"))
                    doc["mappings"][0]["line_start"] = line_start
                    doc["mappings"][0]["line_end"] = line_end
                    mapping.write_text(json.dumps(doc), encoding="utf-8")
                    evidence = _verify_source_bindings(doc, base / "project")["bf-a"][0]
                    self.assertEqual(
                        evidence["source_binding_status"],
                        "SOURCE_LINE_RANGE_OUT_OF_BOUNDS",
                    )
                    with self.assertRaisesRegex(ValueError, "invalid line range"):
                        ingest_coverage_results(
                            report, mapping, graph, "coverage-py-json", base / "project"
                        )

    def test_coverage_without_project_root_is_never_verified(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            report = base / "coverage.json"
            report.write_text(json.dumps({"files":{"src/a.py":{"executed_lines":[1],"missing_lines":[]}}}), encoding="utf-8")
            mapping, graph = _write_graph_and_mapping(base, ["bf-a"], [
                {"business_function_id":"bf-a","source_file":"src/a.py","line_start":1,"line_end":1}
            ])
            result = ingest_coverage_results(report, mapping, graph, "coverage-py-json")
            row = result["business_function_coverage"][0]
            self.assertEqual(row["code_coverage_status"], "UNKNOWN")
            self.assertEqual(row["verification_status"], "SOURCE_BINDING_NOT_VERIFIED")

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


    def test_source_file_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            report = base / "coverage.json"
            report.write_text(json.dumps({"files": {"src/a.py": {"executed_lines": [1], "missing_lines": []}}}), encoding="utf-8")
            mapping, graph = _write_graph_and_mapping(base, ["bf-a"], [
                {"business_function_id": "bf-a", "source_file": "src/a.py", "line_start": 1, "line_end": 1}
            ])
            source = base / "project" / "src" / "a.py"
            outside = base / "outside.py"
            outside.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
            source.unlink()
            try:
                source.symlink_to(outside)
            except OSError as exc:
                self.skipTest(f"platform does not permit symlink creation: {exc}")
            with self.assertRaisesRegex(ValueError, "symbolic links or reparse points"):
                ingest_coverage_results(report, mapping, graph, "coverage-py-json", base / "project")

    def test_project_root_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            report = base / "coverage.json"
            report.write_text(json.dumps({"files": {"src/a.py": {"executed_lines": [1], "missing_lines": []}}}), encoding="utf-8")
            mapping, graph = _write_graph_and_mapping(base, ["bf-a"], [
                {"business_function_id": "bf-a", "source_file": "src/a.py", "line_start": 1, "line_end": 1}
            ])
            alias = base / "project-alias"
            try:
                alias.symlink_to(base / "project", target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"platform does not permit directory symlink creation: {exc}")
            with self.assertRaisesRegex(ValueError, "project_root must not contain symbolic links or reparse points"):
                ingest_coverage_results(report, mapping, graph, "coverage-py-json", alias)


    def test_coverage_py_rejects_duplicate_json_keys(self):
        from usit.test_report_adapters import parse_coverage_py_json

        payload = b'{"files":{"src/a.py":{"executed_lines":[1],"executed_lines":[2],"missing_lines":[]}}}'
        with self.assertRaisesRegex(ValueError, "duplicate JSON key: executed_lines"):
            parse_coverage_py_json(payload)

    def test_coverage_import_rejects_duplicate_mapping_keys(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            mapping, graph = _write_graph_and_mapping(base, ["bf-upload"], [
                {"source_file": "src/a.py", "line_start": 1, "line_end": 2, "business_function_id": "bf-upload"}
            ])
            doc = json.loads(mapping.read_text(encoding="utf-8"))
            mapping.write_text(
                '{"schema_version":"usit-coverage-mapping-v1",'
                '"schema_version":"usit-coverage-mapping-v1",'
                f'"source_graph_sha256":"{hashlib.sha256(graph.read_bytes()).hexdigest()}",'
                '"mappings":[]}',
                encoding="utf-8",
            )
            coverage = base / "coverage.json"
            coverage.write_text(json.dumps({"files":{"src/a.py":{"executed_lines":[1],"missing_lines":[]}}}),encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "duplicate JSON key: schema_version"):
                ingest_coverage_results(coverage, mapping, graph, "coverage-py-json", base / "project")

    def test_coverage_py_rejects_executed_missing_overlap(self):
        from usit.test_report_adapters import parse_coverage_py_json

        payload = json.dumps({"files": {"src/a.py": {
            "executed_lines": [1, 2], "missing_lines": [2, 3]
        }}}).encode("utf-8")
        with self.assertRaisesRegex(ValueError, "both executed and missing"):
            parse_coverage_py_json(payload)

    def test_coverage_py_rejects_duplicate_line_entries(self):
        from usit.test_report_adapters import parse_coverage_py_json

        payload = json.dumps({"files": {"src/a.py": {
            "executed_lines": [1, 1], "missing_lines": [2]
        }}}).encode("utf-8")
        with self.assertRaisesRegex(ValueError, "duplicate line numbers"):
            parse_coverage_py_json(payload)

    def test_coverage_py_rejects_duplicates_in_missing_lines(self):
        from usit.test_report_adapters import parse_coverage_py_json

        payload = json.dumps({"files": {"src/a.py": {
            "executed_lines": [1], "missing_lines": [2, 2]
        }}}).encode("utf-8")
        with self.assertRaisesRegex(ValueError, "duplicate line numbers"):
            parse_coverage_py_json(payload)

if __name__ == "__main__":
    unittest.main()
