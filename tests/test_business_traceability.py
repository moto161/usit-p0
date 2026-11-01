from __future__ import annotations
import unittest
from usit.business_traceability import build_business_traceability
from usit.business_traceability_report import render_business_traceability_markdown


class BusinessTraceabilityTests(unittest.TestCase):
    def setUp(self):
        self.graph_hash = "a" * 64
        self.semantics = {"schema_version": "usit-business-semantics-v1", "features": [
            {"name": "查看照片", "source": "docs/requirements.md", "line": 12},
            {"name": "删除照片", "source": "docs/requirements.md", "line": 20},
        ]}
        self.graph = {"schema_version": "usit-graph-v1", "business_understanding": {
            "business_function_views": [{
                "business_function_id": "bf-list", "endpoint_id": "ep-list", "method": "GET",
                "endpoint": "/api/photos", "frontend_entrypoint_count": 1,
                "frontend_entrypoints": [{"source_file": "web/Photos.tsx", "event_line": 4,
                    "api_call_file": "web/api.ts", "api_call_line": 18}],
                "source": {"path": "server/routes.js", "line": 8},
                "handler": {"path": "server/photos.js", "line": 22},
                "call_chain": [{"call_site": {"path": "server/photos.js", "line": 25}}],
                "database_accesses": [{"path": "server/db.js", "line": 9}],
                "authorization": {"evidence": [{"path": "server/auth.js", "line": 3}]},
                "identity_resource_flow": [{"path": "server/db.js", "line": 9}],
                "stage_status": {"handler": "RESOLVED", "calls": "RESOLVED_STATIC_CHAIN",
                    "database": "LINKED", "authorization": "EVIDENCE_FOUND", "identity_resource": "EVIDENCE_FOUND"},
            }],
        }}
        self.mapping = {"schema_version": "usit-feature-function-mapping-v1",
            "source_graph_sha256": self.graph_hash, "mappings": [{
                "feature_name": "查看照片", "source_path": "docs/requirements.md", "source_line": 12,
                "decision": "APPROVED_LINK", "business_function_id": "bf-list",
                "reviewed_by": "reviewer-1", "reviewed_at": "2026-11-01T12:00:00+08:00",
                "rationale": "需求验收范围明确对应照片列表接口及页面入口。",
            }, {
                "feature_name": "删除照片", "source_path": "docs/requirements.md", "source_line": 20,
                "decision": "NEEDS_REVIEW", "reviewed_by": "reviewer-1",
                "reviewed_at": "2026-11-01T12:00:00+08:00", "rationale": "尚未确认业务入口。",
            }]}
        self.hashes = {"business_graph_sha256": self.graph_hash}

    def test_only_approved_exact_mapping_builds_cross_layer_traceability(self):
        plan = {"schema_version": "usit-business-test-plan-v1", "candidates": [
            {"test_id": "test-photo-list", "business_function_id": "bf-list"}]}
        evidence = {"schema_version": "usit-business-evidence-verification-v1",
            "business_function_evidence": [{"business_function_id": "bf-list",
                "test_execution_status": "PASSED", "code_coverage_status": "PARTIAL"}]}
        result = build_business_traceability(self.semantics, self.graph, self.mapping, plan, evidence, self.hashes)
        first = next(row for row in result["rows"] if row["feature_name"] == "查看照片")
        second = next(row for row in result["rows"] if row["feature_name"] == "删除照片")
        self.assertEqual(first["business_function_id"], "bf-list")
        self.assertEqual(first["traceability_status"], "TRACEABLE_WITH_RUNTIME_EVIDENCE")
        self.assertEqual(first["stage_status"]["business_function_to_frontend_entrypoint"], "LINKED")
        self.assertEqual(first["stage_status"]["api_to_backend_handler"], "RESOLVED")
        self.assertEqual(first["stage_status"]["test_execution"], "PASSED")
        self.assertEqual(first["test_candidate_ids"], ["test-photo-list"])
        self.assertEqual(second["traceability_status"], "REVIEW_REQUIRED")
        self.assertIsNone(second["business_function_id"])

    def test_no_mapping_never_uses_feature_name_or_module_to_guess(self):
        result = build_business_traceability(self.semantics, self.graph,
            {"schema_version": "usit-feature-function-mapping-v1", "source_graph_sha256": self.graph_hash, "mappings": []},
            input_hashes=self.hashes)
        self.assertEqual(result["unmapped_feature_count"], 2)
        self.assertTrue(all(row["business_function_id"] is None for row in result["rows"]))

    def test_rejects_stale_graph_hash_and_unknown_feature(self):
        stale = dict(self.mapping, source_graph_sha256="b" * 64)
        with self.assertRaisesRegex(ValueError, "does not match"):
            build_business_traceability(self.semantics, self.graph, stale, input_hashes=self.hashes)
        unknown = {"schema_version": "usit-feature-function-mapping-v1",
            "source_graph_sha256": self.graph_hash, "mappings": [dict(self.mapping["mappings"][0],
                feature_name="not in requirements")]}
        with self.assertRaisesRegex(ValueError, "unknown feature identity"):
            build_business_traceability(self.semantics, self.graph, unknown, input_hashes=self.hashes)

    def test_rejects_unreviewed_or_invalid_approved_mapping(self):
        invalid = {"schema_version": "usit-feature-function-mapping-v1",
            "source_graph_sha256": self.graph_hash, "mappings": [dict(self.mapping["mappings"][0], reviewed_by=" ")]}
        with self.assertRaisesRegex(ValueError, "reviewed_by"):
            build_business_traceability(self.semantics, self.graph, invalid, input_hashes=self.hashes)
        invalid["mappings"][0]["reviewed_by"] = "reviewer"
        invalid["mappings"][0]["business_function_id"] = ""
        with self.assertRaisesRegex(ValueError, "requires business_function_id"):
            build_business_traceability(self.semantics, self.graph, invalid, input_hashes=self.hashes)

    def test_reports_missing_function_target_and_unlinked_test_ids(self):
        mapping = {"schema_version": "usit-feature-function-mapping-v1",
            "source_graph_sha256": self.graph_hash, "mappings": [dict(self.mapping["mappings"][0],
                business_function_id="missing-function")]}
        plan = {"schema_version": "usit-business-test-plan-v1", "candidates": [
            {"test_id": "test-unknown", "business_function_id": "missing-function"}]}
        result = build_business_traceability(self.semantics, self.graph, mapping, plan, input_hashes=self.hashes)
        mapped_row = next(row for row in result["rows"] if row["feature_name"] == "查看照片")
        self.assertEqual(mapped_row["traceability_status"], "MAPPING_TARGET_NOT_FOUND_IN_GRAPH")
        self.assertEqual(result["unlinked_test_design_function_ids"], ["missing-function"])

    def test_rejects_duplicate_feature_identity_and_duplicate_mapping(self):
        duplicate_semantics = {"features": self.semantics["features"] + [dict(self.semantics["features"][0])]}
        with self.assertRaisesRegex(ValueError, "ambiguous duplicate"):
            build_business_traceability(duplicate_semantics, self.graph, self.mapping, input_hashes=self.hashes)
        duplicate_mapping = dict(self.mapping, mappings=self.mapping["mappings"] + [dict(self.mapping["mappings"][0])])
        with self.assertRaisesRegex(ValueError, "duplicate mapping"):
            build_business_traceability(self.semantics, self.graph, duplicate_mapping, input_hashes=self.hashes)

    def test_cli_writes_parseable_traceability_matrix_and_hashes(self):
        import hashlib
        import json
        import tempfile
        from contextlib import redirect_stdout
        from io import StringIO
        from pathlib import Path
        from usit.cli import main
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            sem_path, graph_path = base / "sem.json", base / "graph.json"
            mapping_path, out_path, md_path, runtime_path = base / "mapping.json", base / "trace.json", base / "trace.md", base / "runtime.json"
            sem_path.write_text(json.dumps(self.semantics), encoding="utf-8")
            graph_bytes = json.dumps(self.graph).encode("utf-8")
            graph_path.write_bytes(graph_bytes)
            graph_hash = hashlib.sha256(graph_bytes).hexdigest()
            mapping = dict(self.mapping, source_graph_sha256=graph_hash)
            mapping_path.write_text(json.dumps(mapping), encoding="utf-8")
            runtime_path.write_text(json.dumps({
                "schema_version": "usit-runtime-evidence-validation-v1",
                "assessment_status": "VALIDATED_EVENT_STRUCTURE",
                "source_hashes": {"business_graph_sha256": graph_hash, "runtime_evidence_sha256": "c" * 64},
                "business_function_ids_observed": ["bf-list"],
                "correlations": [{"correlation_id": "corr-cli", "business_function_ids": ["bf-list"]}],
            }), encoding="utf-8")
            with redirect_stdout(StringIO()):
                code = main(["trace-business", "--semantics", str(sem_path), "--graph", str(graph_path),
                    "--mapping", str(mapping_path), "--runtime-evidence", str(runtime_path),
                    "--out", str(out_path), "--out-md", str(md_path)])
            self.assertEqual(code, 0)
            report = json.loads(out_path.read_text(encoding="utf-8"))
            self.assertEqual(report["schema_version"], "usit-business-traceability-matrix-v1")
            self.assertEqual(report["input_hashes"]["business_graph_sha256"], hashlib.sha256(graph_bytes).hexdigest())
            self.assertEqual(report["reviewed_mapping_count"], 2)
            self.assertEqual(report["input_hashes"]["runtime_evidence_validation_sha256"], hashlib.sha256(runtime_path.read_bytes()).hexdigest())
            self.assertEqual(next(row for row in report["rows"] if row["feature_name"] == "查看照片")["stage_status"]["runtime_observation"], "RUNTIME_OBSERVED")
            markdown = md_path.read_text(encoding="utf-8")
            self.assertIn("# USIT 业务需求跨层追溯报告", markdown)
            self.assertIn("查看照片", markdown)
            self.assertIn("不证明运行时页面可达", markdown)

    def test_rejects_invalid_review_timestamp_and_reports_invalid_runtime_state(self):
        invalid = dict(self.mapping, mappings=[dict(self.mapping["mappings"][0], reviewed_at="yesterday")])
        with self.assertRaisesRegex(ValueError, "ISO-8601"):
            build_business_traceability(self.semantics, self.graph, invalid, input_hashes=self.hashes)
        evidence = {"schema_version": "usit-business-evidence-verification-v1",
            "business_function_evidence": [{"business_function_id": "bf-list",
                "test_execution_status": "MAYBE", "code_coverage_status": "COVERED"}]}
        result = build_business_traceability(self.semantics, self.graph, self.mapping,
            evidence_matrix=evidence, input_hashes=self.hashes)
        mapped_row = next(row for row in result["rows"] if row["feature_name"] == "查看照片")
        self.assertEqual(mapped_row["stage_status"]["test_execution"], "INVALID")
        self.assertEqual(result["invalid_runtime_status_function_ids"], ["bf-list"])

    def test_markdown_renderer_rejects_wrong_schema_and_escapes_cells(self):
        report = build_business_traceability(self.semantics, self.graph,
            {"schema_version": "usit-feature-function-mapping-v1",
             "source_graph_sha256": self.graph_hash, "mappings": []},
            input_hashes=self.hashes)
        report["rows"][0]["feature_name"] = "A|B"
        markdown = render_business_traceability_markdown(report)
        self.assertIn(r"A\|B", markdown)
        with self.assertRaisesRegex(ValueError, "usit-business-traceability-matrix-v1"):
            render_business_traceability_markdown({"schema_version": "wrong"})

    def test_runtime_observation_is_separate_from_test_execution_and_requires_explicit_function_id(self):
        runtime = {
            "schema_version": "usit-runtime-evidence-validation-v1",
            "assessment_status": "VALIDATED_EVENT_STRUCTURE",
            "source_hashes": {"business_graph_sha256": self.graph_hash, "runtime_evidence_sha256": "b" * 64},
            "business_function_ids_observed": ["bf-list"],
            "correlations": [{"correlation_id": "corr-1", "business_function_ids": ["bf-list"],
                "runtime_observation_status": "RUNTIME_OBSERVED"}],
        }
        result = build_business_traceability(self.semantics, self.graph, self.mapping,
            input_hashes=self.hashes, runtime_evidence_validation=runtime)
        linked = next(row for row in result["rows"] if row["feature_name"] == "查看照片")
        unreviewed = next(row for row in result["rows"] if row["feature_name"] == "删除照片")
        self.assertEqual(linked["stage_status"]["runtime_observation"], "RUNTIME_OBSERVED")
        self.assertEqual(linked["stage_status"]["test_execution"], "NOT_MEASURED")
        self.assertEqual(linked["traceability_status"], "TRACEABLE_WITH_RUNTIME_OBSERVATION")
        self.assertEqual(linked["runtime_observation"]["correlation_ids"], ["corr-1"])
        self.assertEqual(unreviewed["stage_status"]["runtime_observation"], "NOT_OBSERVED")
        self.assertIsNone(unreviewed["business_function_id"])

    def test_runtime_evidence_graph_hash_mismatch_is_rejected(self):
        runtime = {
            "schema_version": "usit-runtime-evidence-validation-v1",
            "source_hashes": {"business_graph_sha256": "0" * 64},
            "business_function_ids_observed": [],
            "correlations": [],
        }
        with self.assertRaisesRegex(ValueError, "does not match"):
            build_business_traceability(self.semantics, self.graph, self.mapping,
                input_hashes=self.hashes, runtime_evidence_validation=runtime)

    def test_rejects_invalid_runtime_evidence_shape(self):
        evidence = {"schema_version": "usit-business-evidence-verification-v1",
            "business_function_evidence": [{"business_function_id": "bf-list"}, {"business_function_id": "bf-list"}]}
        with self.assertRaisesRegex(ValueError, "duplicate runtime evidence"):
            build_business_traceability(self.semantics, self.graph, self.mapping, evidence_matrix=evidence,
                input_hashes=self.hashes)


if __name__ == "__main__":
    unittest.main()
