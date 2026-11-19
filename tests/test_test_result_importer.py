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

    def test_importer_rejects_duplicate_json_keys_in_mapping_and_graph(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            report, mapping, graph = self._inputs(
                base, '<testsuite><testcase name="test_upload"/></testsuite>'
            )
            mapping.write_text(
                '{"schema_version":"usit-test-result-mapping-v1",'
                '"schema_version":"usit-test-result-mapping-v1",'
                '"source_graph_sha256":"' + hashlib.sha256(graph.read_bytes()).hexdigest() + '",'
                '"mappings":[]}',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "duplicate JSON key: schema_version"):
                ingest_junit_results(report, mapping, graph)
            report, mapping, graph = self._inputs(
                base, '<testsuite><testcase name="test_upload"/></testsuite>'
            )
            graph.write_text(
                '{"schema_version":"usit-graph-v1","schema_version":"usit-graph-v1",'
                '"business_understanding":{"business_function_views":[]}}',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "duplicate JSON key: schema_version"):
                ingest_junit_results(report, mapping, graph)

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


    def test_test_source_binding_matches_real_file_and_line(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            source = base / "tests" / "test_photo.py"
            source.parent.mkdir()
            source.write_text("def test_upload():\n    assert True\n", encoding="utf-8")
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            mapping_rows = [{
                "test_case_id": "tests.PhotoTests::test_upload",
                "business_function_id": "bf-upload",
                "test_source_file": "tests/test_photo.py",
                "test_source_line": 1,
                "test_source_file_sha256": digest,
                "test_source_symbol": "test_upload",
            }]
            report, mapping, graph = self._inputs(
                base, '<testsuite><testcase classname="tests.PhotoTests" name="test_upload"/></testsuite>',
                mapping_rows,
            )
            result = ingest_junit_results(report, mapping, graph, project_root=base)
            upload = next(x for x in result["business_function_results"]
                          if x["business_function_id"] == "bf-upload")
            self.assertEqual(upload["execution_status"], "PASSED")
            self.assertEqual(upload["mapped_test_cases"][0]["test_source_binding_status"],
                             "TEST_SOURCE_BYTES_MATCHED")
            self.assertEqual(upload["mapped_test_cases"][0]["test_source_binding_evidence"]["source_line_count"], 2)
            self.assertEqual(upload["mapped_test_cases"][0]["test_source_symbol_status"],
                             "TEST_SOURCE_SYMBOL_DECLARATION_MATCHED")
            self.assertEqual(upload["mapped_test_cases"][0]["test_source_symbol_evidence"]["qualified_symbol"],
                             "test_upload")
            from usit.business_evidence_verifier import verify_business_function_evidence
            graph_doc = json.loads(graph.read_text(encoding="utf-8"))
            matrix = verify_business_function_evidence(
                graph_doc, result, business_graph_sha256=result["source_hashes"]["business_graph_sha256"]
            )
            verified = next(x for x in matrix["business_function_evidence"]
                            if x["business_function_id"] == "bf-upload")
            self.assertEqual(verified["test_source_binding_status"], "ALL_SOURCE_BYTES_MATCHED_AT_IMPORT")
            self.assertEqual(verified["test_execution_status"], "PASSED")
            self.assertEqual(
                verified["test_execution_detail"]["mapped_test_cases"][0]["test_source_symbol_status"],
                "TEST_SOURCE_SYMBOL_DECLARATION_MATCHED",
            )

    def test_test_source_binding_fails_closed_for_changed_file_and_unsafe_path(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            source = base / "tests" / "test_photo.py"
            source.parent.mkdir()
            source.write_text("def test_upload():\n    assert True\n", encoding="utf-8")
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            rows = [{
                "test_case_id": "tests.PhotoTests::test_upload",
                "business_function_id": "bf-upload",
                "test_source_file": "tests/test_photo.py",
                "test_source_line": 1,
                "test_source_file_sha256": digest,
            }]
            report, mapping, graph = self._inputs(
                base, '<testsuite><testcase classname="tests.PhotoTests" name="test_upload"/></testsuite>', rows,
            )
            source.write_text("def test_upload():\n    assert False\n", encoding="utf-8")
            result = ingest_junit_results(report, mapping, graph, project_root=base)
            upload = next(x for x in result["business_function_results"]
                          if x["business_function_id"] == "bf-upload")
            self.assertEqual(upload["execution_status"], "PASSED")
            self.assertEqual(upload["mapped_test_cases"][0]["test_source_binding_status"],
                             "TEST_SOURCE_HASH_MISMATCH")
            from usit.business_evidence_verifier import verify_business_function_evidence
            graph_doc = json.loads(graph.read_text(encoding="utf-8"))
            matrix = verify_business_function_evidence(
                graph_doc, result, business_graph_sha256=result["source_hashes"]["business_graph_sha256"]
            )
            verified = next(x for x in matrix["business_function_evidence"]
                            if x["business_function_id"] == "bf-upload")
            self.assertEqual(verified["test_source_binding_status"], "SOURCE_BINDING_NOT_VERIFIED")
            rows[0]["test_source_file"] = "../outside.py"
            report, mapping, graph = self._inputs(
                base, '<testsuite><testcase classname="tests.PhotoTests" name="test_upload"/></testsuite>', rows,
            )
            result = ingest_junit_results(report, mapping, graph, project_root=base)
            upload = next(x for x in result["business_function_results"]
                          if x["business_function_id"] == "bf-upload")
            self.assertEqual(upload["mapped_test_cases"][0]["test_source_binding_status"],
                             "TEST_SOURCE_BINDING_INVALID")
            rows[0]["test_source_file"] = "tests/test_photo.py:stream"
            report, mapping, graph = self._inputs(
                base, '<testsuite><testcase classname="tests.PhotoTests" name="test_upload"/></testsuite>', rows,
            )
            result = ingest_junit_results(report, mapping, graph, project_root=base)
            upload = next(x for x in result["business_function_results"]
                          if x["business_function_id"] == "bf-upload")
            self.assertEqual(upload["mapped_test_cases"][0]["test_source_binding_status"],
                             "TEST_SOURCE_BINDING_INVALID")

    def test_test_source_binding_without_root_is_not_checked(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            source = base / "test_upload.py"
            source.write_text("def test_upload():\n    pass\n", encoding="utf-8")
            rows = [{
                "test_case_id": "tests.PhotoTests::test_upload",
                "business_function_id": "bf-upload",
                "test_source_file": "test_upload.py",
                "test_source_line": 1,
                "test_source_file_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            }]
            report, mapping, graph = self._inputs(
                base, '<testsuite><testcase classname="tests.PhotoTests" name="test_upload"/></testsuite>', rows,
            )
            result = ingest_junit_results(report, mapping, graph)
            upload = next(x for x in result["business_function_results"]
                          if x["business_function_id"] == "bf-upload")
            self.assertEqual(upload["mapped_test_cases"][0]["test_source_binding_status"],
                             "TEST_SOURCE_BINDING_NOT_CHECKED")
            mapping_doc = json.loads(mapping.read_text(encoding="utf-8"))
            mapping_doc["mappings"][0]["test_source_line"] = 3
            mapping.write_text(json.dumps(mapping_doc), encoding="utf-8")
            result = ingest_junit_results(report, mapping, graph, project_root=base)
            upload = next(x for x in result["business_function_results"]
                          if x["business_function_id"] == "bf-upload")
            self.assertEqual(upload["mapped_test_cases"][0]["test_source_binding_status"],
                             "TEST_SOURCE_LINE_OUT_OF_BOUNDS")

    def test_report_reader_rejects_parent_directory_symlink(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            report, mapping, graph = self._inputs(
                base, '<testsuite><testcase classname="tests.PhotoTests" name="test_upload"/></testsuite>'
            )
            real_dir = base / "real-reports"
            real_dir.mkdir()
            real_report = real_dir / "junit.xml"
            report.rename(real_report)
            alias_dir = base / "alias-reports"
            try:
                alias_dir.symlink_to(real_dir, target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"platform does not permit directory symlinks: {exc}")
            with self.assertRaisesRegex(ValueError, "input path must not contain symbolic links"):
                ingest_junit_results(alias_dir / "junit.xml", mapping, graph)

    def test_test_source_binding_rejects_symlink_project_root(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            real_root = base / "real-project"
            source = real_root / "tests" / "test_photo.py"
            source.parent.mkdir(parents=True)
            source.write_text("def test_upload():\n    assert True\n", encoding="utf-8")
            alias_root = base / "project-alias"
            try:
                alias_root.symlink_to(real_root, target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"platform does not permit directory symlinks: {exc}")
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            rows = [{
                "test_case_id": "tests.PhotoTests::test_upload",
                "business_function_id": "bf-upload",
                "test_source_file": "tests/test_photo.py",
                "test_source_line": 1,
                "test_source_file_sha256": digest,
            }]
            report, mapping, graph = self._inputs(
                base, '<testsuite><testcase classname="tests.PhotoTests" name="test_upload"/></testsuite>', rows,
            )
            result = ingest_junit_results(report, mapping, graph, project_root=alias_root)
            upload = next(x for x in result["business_function_results"]
                          if x["business_function_id"] == "bf-upload")
            self.assertEqual(
                upload["mapped_test_cases"][0]["test_source_binding_status"],
                "TEST_SOURCE_BINDING_INVALID",
            )
            self.assertEqual(
                upload["mapped_test_cases"][0]["test_source_binding_evidence"]["reason"],
                "PROJECT_ROOT_CONTAINS_LINK",
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


    def test_python_test_symbol_ast_matches_declared_function_and_line(self):
        from usit.test_result_importer import _verify_python_test_symbol
        source = """import pytest

class TestPhoto:
    @pytest.mark.parametrize('value', [1, 2])
    def test_upload(self, value):
        assert value > 0

async def test_async_upload():
    return True
"""
        result = _verify_python_test_symbol({
            "test_source_file": "tests/test_photo.py",
            "test_source_line": 5,
            "test_source_symbol": "TestPhoto.test_upload",
        }, source)
        self.assertEqual(result["status"], "TEST_SOURCE_SYMBOL_DECLARATION_MATCHED")
        self.assertEqual(result["qualified_symbol"], "TestPhoto.test_upload")
        self.assertEqual(result["symbol_start_line"], 4)
        self.assertEqual(result["symbol_end_line"], 6)

    def test_python_test_symbol_rejects_wrong_line_and_wrong_symbol(self):
        from usit.test_result_importer import _verify_python_test_symbol
        source = """def test_upload():
    assert True

def test_delete():
    assert True
"""
        wrong_line = _verify_python_test_symbol({
            "test_source_file": "tests/test_photo.py",
            "test_source_line": 4,
            "test_source_symbol": "test_upload",
        }, source)
        self.assertEqual(wrong_line["status"], "TEST_SOURCE_SYMBOL_MISMATCH")
        self.assertEqual(wrong_line["reason"], "DECLARED_LINE_OUTSIDE_SYMBOL")
        wrong_symbol = _verify_python_test_symbol({
            "test_source_file": "tests/test_photo.py",
            "test_source_line": 1,
            "test_source_symbol": "test_missing",
        }, source)
        self.assertEqual(wrong_symbol["status"], "TEST_SOURCE_SYMBOL_MISMATCH")
        self.assertEqual(wrong_symbol["reason"], "SYMBOL_NOT_UNIQUE_OR_NOT_FOUND")

    def test_python_test_symbol_fails_closed_when_source_unverified_or_unsupported(self):
        from usit.test_result_importer import _verify_python_test_symbol
        mapping = {
            "test_source_file": "tests/test_photo.py",
            "test_source_line": 1,
            "test_source_symbol": "test_upload",
        }
        unchecked = _verify_python_test_symbol(mapping, None)
        self.assertEqual(unchecked["status"], "TEST_SOURCE_SYMBOL_NOT_CHECKED")
        unsupported = _verify_python_test_symbol({
            **mapping, "test_source_file": "tests/test_photo.ts",
        }, "function test_upload() {}")
        self.assertEqual(unsupported["status"], "TEST_SOURCE_SYMBOL_NOT_SUPPORTED")

    def test_execution_provenance_is_verified_before_report_import(self):
        from usit.test_execution_provenance import (
            SCHEMA_VERSION, canonical_json_bytes, sha256_hex,
        )
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            mapping_rows = [{
                "test_case_id": "tests.PhotoTests::test_upload",
                "business_function_id": "bf-upload",
                "test_source_file": "tests/test_photo.py",
                "test_source_line": 1,
                "test_source_file_sha256": "a" * 64,
            }]
            report, mapping, graph = self._inputs(
                base,
                '<testsuite><testcase classname="tests.PhotoTests" name="test_upload"/></testsuite>',
                mapping_rows,
            )
            report_bytes = report.read_bytes()
            manifest = {
                "schema_version": "usit-manifest-v1",
                "files": [{"path": "tests/test_photo.py", "sha256": "a" * 64}],
            }
            manifest_path = base / "source-manifest.json"
            manifest_path.write_bytes(json.dumps(manifest).encode("utf-8"))
            provenance = {
                "schema_version": SCHEMA_VERSION,
                "execution_id": "ci-run-123",
                "started_at": "2026-11-03T10:00:00Z",
                "completed_at": "2026-11-03T10:00:02Z",
                "runner": {"name": "pytest", "version": "8.3.5"},
                "report_format": "JUNIT_XML",
                "report_relative_path": "reports/junit.xml",
                "report_sha256": sha256_hex(report_bytes),
                "source_snapshot": {
                    "manifest_schema_version": "usit-manifest-v1",
                    "manifest_sha256": sha256_hex(canonical_json_bytes(manifest)),
                    "file_count": 1,
                    "capture_method": "PRE_EXECUTION_READ_ONLY_CAPTURE",
                },
                "working_tree_state": {"state": "CLEAN", "untracked_files": "INCLUDED"},
                "command_fingerprint": "b" * 64,
                "environment_fingerprint": "c" * 64,
            }
            record_path = base / "execution-provenance.json"
            record_path.write_text(json.dumps(provenance), encoding="utf-8")
            result = ingest_junit_results(
                report, mapping, graph,
                provenance_record_path=record_path,
                source_manifest_path=manifest_path,
            )
            self.assertEqual(
                result["execution_provenance"]["record_validation_status"],
                "STRUCTURE_AND_DIGESTS_VALIDATED",
            )
            self.assertEqual(
                result["execution_provenance"]["provenance_authenticity_status"],
                "UNAUTHENTICATED",
            )
            self.assertEqual(
                result["execution_provenance"]["execution_source_binding_status"],
                "NOT_ESTABLISHED",
            )
            mapped_upload = next(x for x in result["business_function_results"]
                                 if x["business_function_id"] == "bf-upload")
            self.assertEqual(
                mapped_upload["mapped_test_cases"][0]["test_source_snapshot_binding_status"],
                "TEST_SOURCE_SNAPSHOT_ENTRY_MATCHED",
            )
            self.assertEqual(
                next(x for x in result["business_function_results"]
                     if x["business_function_id"] == "bf-upload")["execution_status"],
                "PASSED",
            )

            report.write_bytes(report_bytes + b" ")
            with self.assertRaisesRegex(ValueError, "report_sha256"):
                ingest_junit_results(
                    report, mapping, graph,
                    provenance_record_path=record_path,
                    source_manifest_path=manifest_path,
                )

    def test_nonzero_exit_code_downgrades_passed_report_to_unknown(self):
        """A PASSED report with a non-zero parent-observed exit code is UNKNOWN.

        The exit code is observed by the parent process and cannot be
        rewritten by the test process, while the JUnit file lives in staging
        and may have been forged after pytest wrote it.
        """
        from usit.test_execution_provenance import (
            SCHEMA_VERSION, canonical_json_bytes, sha256_hex,
        )
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            report, mapping, graph = self._inputs(
                base,
                '<testsuite><testcase classname="tests.PhotoTests" name="test_upload"/></testsuite>',
            )
            report_bytes = report.read_bytes()
            manifest = {
                "schema_version": "usit-manifest-v1",
                "files": [{"path": "tests/test_photo.py", "sha256": "a" * 64}],
            }
            manifest_path = base / "source-manifest.json"
            manifest_path.write_bytes(json.dumps(manifest).encode("utf-8"))
            provenance = {
                "schema_version": SCHEMA_VERSION,
                "execution_id": "ci-run-124",
                "started_at": "2026-11-03T10:00:00Z",
                "completed_at": "2026-11-03T10:00:02Z",
                "runner": {"name": "pytest", "version": "8.3.5"},
                "report_format": "JUNIT_XML",
                "report_relative_path": "reports/junit.xml",
                "report_sha256": sha256_hex(report_bytes),
                "pytest_exit_code": 1,
                "pytest_exit_status": "FAIL",
                "source_snapshot": {
                    "manifest_schema_version": "usit-manifest-v1",
                    "manifest_sha256": sha256_hex(canonical_json_bytes(manifest)),
                    "file_count": 1,
                    "capture_method": "PRE_EXECUTION_READ_ONLY_CAPTURE",
                },
                "working_tree_state": {"state": "CLEAN", "untracked_files": "INCLUDED"},
                "command_fingerprint": "b" * 64,
                "environment_fingerprint": "c" * 64,
            }
            record_path = base / "execution-provenance.json"
            record_path.write_text(json.dumps(provenance), encoding="utf-8")
            result = ingest_junit_results(
                report, mapping, graph,
                provenance_record_path=record_path,
                source_manifest_path=manifest_path,
            )
            self.assertEqual(result["execution_provenance"]["pytest_exit_code"], 1)
            upload = next(x for x in result["business_function_results"]
                          if x["business_function_id"] == "bf-upload")
            self.assertEqual(upload["execution_status"], "UNKNOWN")
            self.assertEqual(
                upload["execution_status_reason"],
                "REPORT_PASSED_BUT_RUNNER_EXIT_CODE_NONZERO",
            )

    def _provenance_import(self, base, report, mapping, graph, completed_at,
                           worker_count=None):
        """Import a report with a provenance record spanning a 2s wall clock.

        ``completed_at`` is the run's end timestamp; the start is fixed at
        2026-11-03T10:00:00Z, so the wall clock is 2s. ``worker_count``
        records how the run was distributed (None = not recorded).
        """
        from usit.test_execution_provenance import (
            SCHEMA_VERSION, canonical_json_bytes, sha256_hex,
        )
        report_bytes = report.read_bytes()
        manifest = {
            "schema_version": "usit-manifest-v1",
            "files": [{"path": "tests/test_photo.py", "sha256": "a" * 64}],
        }
        manifest_path = base / "source-manifest.json"
        manifest_path.write_bytes(json.dumps(manifest).encode("utf-8"))
        provenance = {
            "schema_version": SCHEMA_VERSION,
            "execution_id": "ci-run-duration",
            "started_at": "2026-11-03T10:00:00Z",
            "completed_at": completed_at,
            "runner": {"name": "pytest", "version": "8.3.5"},
            "report_format": "JUNIT_XML",
            "report_relative_path": "reports/junit.xml",
            "report_sha256": sha256_hex(report_bytes),
            "source_snapshot": {
                "manifest_schema_version": "usit-manifest-v1",
                "manifest_sha256": sha256_hex(canonical_json_bytes(manifest)),
                "file_count": 1,
                "capture_method": "PRE_EXECUTION_READ_ONLY_CAPTURE",
            },
            "working_tree_state": {"state": "CLEAN", "untracked_files": "INCLUDED"},
            "command_fingerprint": "b" * 64,
            "environment_fingerprint": "c" * 64,
        }
        if worker_count is not None:
            provenance["worker_count"] = worker_count
        record_path = base / "execution-provenance.json"
        record_path.write_text(json.dumps(provenance), encoding="utf-8")
        return ingest_junit_results(
            report, mapping, graph,
            provenance_record_path=record_path,
            source_manifest_path=manifest_path,
        )

    def test_junit_retains_durations_without_provenance(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            report, mapping, graph = self._inputs(base, """<testsuite time="1.75">
              <testcase classname="tests.PhotoTests" name="test_upload" time="1.25"/>
              <testcase classname="tests.PhotoTests" name="test_delete" time="0.5"/>
            </testsuite>""")
            result = ingest_junit_results(report, mapping, graph)
            by_id = {x["business_function_id"]: x for x in result["business_function_results"]}
            self.assertEqual(by_id["bf-upload"]["execution_status"], "PASSED")
            self.assertEqual(
                by_id["bf-upload"]["mapped_test_cases"][0]["duration_seconds"], 1.25)
            self.assertEqual(
                by_id["bf-delete"]["mapped_test_cases"][0]["duration_seconds"], 0.5)
            check = result["duration_cross_check"]
            self.assertEqual(check["status"], "NOT_CHECKED")
            self.assertEqual(check["reason"], "NO_EXECUTION_PROVENANCE")
            self.assertEqual(check["total_case_duration_seconds"], 1.75)
            self.assertEqual(check["report_duration_seconds"], 1.75)

    def test_case_duration_exceeding_wall_clock_downgrades_only_its_function(self):
        # One test cannot outlast the whole run, serial or parallel, so the
        # per-case rule still applies with worker_count=2 -- while the sum
        # check is correctly skipped for a parallel run.
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            report, mapping, graph = self._inputs(base, """<testsuite>
              <testcase classname="tests.PhotoTests" name="test_upload" time="10.0"/>
              <testcase classname="tests.PhotoTests" name="test_delete" time="0.5"/>
            </testsuite>""")
            result = self._provenance_import(
                base, report, mapping, graph, "2026-11-03T10:00:02Z", worker_count=2)
            by_id = {x["business_function_id"]: x for x in result["business_function_results"]}
            self.assertEqual(by_id["bf-upload"]["execution_status"], "UNKNOWN")
            self.assertEqual(by_id["bf-upload"]["execution_status_reason"],
                             "TEST_DURATION_EXCEEDS_WALL_CLOCK")
            self.assertEqual(by_id["bf-delete"]["execution_status"], "PASSED")
            check = result["duration_cross_check"]
            self.assertEqual(check["status"], "CONFLICT")
            self.assertEqual(check["per_case_violations"], ["tests.PhotoTests::test_upload"])
            self.assertFalse(check["sum_check_applied"])

    def test_summed_durations_exceeding_wall_clock_downgrade_serial_run(self):
        # Each case fits inside the 2s wall clock (limit 4s with tolerance),
        # but the 5s sum cannot fit a serial run.
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            report, mapping, graph = self._inputs(base, """<testsuite>
              <testcase classname="tests.PhotoTests" name="test_upload" time="2.5"/>
              <testcase classname="tests.PhotoTests" name="test_delete" time="2.5"/>
            </testsuite>""")
            result = self._provenance_import(
                base, report, mapping, graph, "2026-11-03T10:00:02Z", worker_count=1)
            by_id = {x["business_function_id"]: x for x in result["business_function_results"]}
            self.assertEqual(result["duration_cross_check"]["status"], "CONFLICT")
            self.assertIn("TOTAL_TEST_DURATION_EXCEEDS_WALL_CLOCK",
                          result["duration_cross_check"]["violations"])
            self.assertTrue(result["duration_cross_check"]["sum_check_applied"])
            for function_id in ("bf-upload", "bf-delete"):
                self.assertEqual(by_id[function_id]["execution_status"], "UNKNOWN")
                self.assertEqual(by_id[function_id]["execution_status_reason"],
                                 "TOTAL_TEST_DURATION_EXCEEDS_WALL_CLOCK")

    def test_parallel_run_skips_summed_duration_check(self):
        # The same 5s of case durations is legitimate under pytest-xdist:
        # two workers run in parallel, so the sum may exceed the wall clock.
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            report, mapping, graph = self._inputs(base, """<testsuite>
              <testcase classname="tests.PhotoTests" name="test_upload" time="2.5"/>
              <testcase classname="tests.PhotoTests" name="test_delete" time="2.5"/>
            </testsuite>""")
            result = self._provenance_import(
                base, report, mapping, graph, "2026-11-03T10:00:02Z", worker_count=2)
            by_id = {x["business_function_id"]: x for x in result["business_function_results"]}
            self.assertEqual(result["duration_cross_check"]["status"], "CHECKED")
            self.assertFalse(result["duration_cross_check"]["sum_check_applied"])
            self.assertEqual(by_id["bf-upload"]["execution_status"], "PASSED")
            self.assertEqual(by_id["bf-delete"]["execution_status"], "PASSED")

    def test_report_total_exceeding_wall_clock_downgrades_all_passed(self):
        # Cases are short, but the report's own claimed total (60s) cannot
        # fit the 2s parent-measured wall clock.
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            report, mapping, graph = self._inputs(base, """<testsuite time="60.0">
              <testcase classname="tests.PhotoTests" name="test_upload" time="0.5"/>
              <testcase classname="tests.PhotoTests" name="test_delete" time="0.5"/>
            </testsuite>""")
            result = self._provenance_import(
                base, report, mapping, graph, "2026-11-03T10:00:02Z", worker_count=1)
            by_id = {x["business_function_id"]: x for x in result["business_function_results"]}
            self.assertIn("REPORT_DURATION_EXCEEDS_WALL_CLOCK",
                          result["duration_cross_check"]["violations"])
            for function_id in ("bf-upload", "bf-delete"):
                self.assertEqual(by_id[function_id]["execution_status"], "UNKNOWN")
                self.assertEqual(by_id[function_id]["execution_status_reason"],
                                 "REPORT_DURATION_EXCEEDS_WALL_CLOCK")

    def test_duration_within_tolerance_passes(self):
        # Wall clock 2s, second-precision timestamps: a 3.5s case is inside
        # the 2s tolerance and must not be flagged.
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            report, mapping, graph = self._inputs(base, """<testsuite>
              <testcase classname="tests.PhotoTests" name="test_upload" time="3.5"/>
            </testsuite>""")
            result = self._provenance_import(
                base, report, mapping, graph, "2026-11-03T10:00:02Z", worker_count=2)
            by_id = {x["business_function_id"]: x for x in result["business_function_results"]}
            self.assertEqual(result["duration_cross_check"]["status"], "CHECKED")
            self.assertEqual(by_id["bf-upload"]["execution_status"], "PASSED")

    def test_evidence_bundle_rejects_symlinked_evidence_directory(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            real_root = base / "real-evidence"
            real_root.mkdir()
            linked_root = base / "linked-evidence"
            try:
                linked_root.symlink_to(real_root, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("directory symlinks are unavailable on this platform")
            with self.assertRaisesRegex(ValueError, "symbolic links or reparse points"):
                ingest_junit_results(
                    linked_root / "junit.xml",
                    base / "mapping.json",
                    base / "graph.json",
                    provenance_record_path=base / "execution-provenance.json",
                    source_manifest_path=base / "source-manifest.json",
                    evidence_dir=linked_root,
                )

    def test_execution_provenance_inputs_must_be_paired(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            report, mapping, graph = self._inputs(
                base, '<testsuite><testcase name="test_upload"/></testsuite>'
            )
            with self.assertRaisesRegex(ValueError, "must be provided together"):
                ingest_junit_results(
                    report, mapping, graph,
                    provenance_record_path=base / "record.json",
                )


    def test_runner_source_locator_is_preserved_and_compared_to_reviewed_mapping(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            snapshot = base / "source_snapshot"
            (snapshot / "tests").mkdir(parents=True)
            source = snapshot / "tests" / "test_photo.py"
            source.write_text("def test_upload():\n    assert True\n", encoding="utf-8")
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            mapping_rows = [{
                "test_case_id": "tests.test_photo::test_upload",
                "business_function_id": "bf-upload",
                "test_source_file": "tests/test_photo.py",
                "test_source_line": 1,
                "test_source_file_sha256": digest,
                "test_source_symbol": "test_upload",
                "pytest_nodeid": "tests/test_photo.py::test_upload",
            }]
            xml = """<testsuite><testcase classname="tests.test_photo" name="test_upload">
              <properties>
                <property name="usit_source_file" value="tests/test_photo.py"/>
                <property name="usit_source_line" value="1"/>
                <property name="usit_source_symbol" value="test_upload"/>
                <property name="usit_nodeid" value="tests/test_photo.py::test_upload"/>
              </properties>
            </testcase></testsuite>"""
            report, mapping, graph = self._inputs(base, xml, mapping_rows)
            result = ingest_junit_results(report, mapping, graph, project_root=snapshot)
            upload = next(x for x in result["business_function_results"]
                          if x["business_function_id"] == "bf-upload")
            testcase = upload["mapped_test_cases"][0]
            self.assertEqual(
                testcase["test_source_report_locator_status"],
                "REPORT_SOURCE_LOCATOR_MATCHED_UNAUTHENTICATED",
            )
            self.assertEqual(
                testcase["test_source_symbol_evidence"]["status"],
                "TEST_SOURCE_SYMBOL_DECLARATION_MATCHED",
            )
            self.assertEqual(
                testcase["test_source_binding_status"],
                "TEST_SOURCE_BYTES_MATCHED",
            )

            mismatched_xml = xml.replace('value="test_upload"/>', 'value="test_delete"/>', 1)
            report.write_text(mismatched_xml, encoding="utf-8")
            mismatch = ingest_junit_results(report, mapping, graph, project_root=snapshot)
            mismatch_upload = next(x for x in mismatch["business_function_results"]
                                   if x["business_function_id"] == "bf-upload")
            self.assertEqual(
                mismatch_upload["mapped_test_cases"][0]["test_source_report_locator_status"],
                "REPORT_SOURCE_LOCATOR_MISMATCH",
            )


if __name__ == "__main__":
    unittest.main()
