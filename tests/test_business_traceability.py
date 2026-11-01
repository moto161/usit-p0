from __future__ import annotations
import unittest
from usit.business_traceability import build_business_traceability


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
        first, second = result["rows"]
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
        self.assertEqual(result["rows"][0]["traceability_status"], "MAPPING_TARGET_NOT_FOUND_IN_GRAPH")
        self.assertEqual(result["unlinked_test_design_function_ids"], ["missing-function"])

    def test_rejects_duplicate_feature_identity_and_duplicate_mapping(self):
        duplicate_semantics = {"features": self.semantics["features"] + [dict(self.semantics["features"][0])]}
        with self.assertRaisesRegex(ValueError, "ambiguous duplicate"):
            build_business_traceability(duplicate_semantics, self.graph, self.mapping, input_hashes=self.hashes)
        duplicate_mapping = dict(self.mapping, mappings=self.mapping["mappings"] + [dict(self.mapping["mappings"][0])])
        with self.assertRaisesRegex(ValueError, "duplicate mapping"):
            build_business_traceability(self.semantics, self.graph, duplicate_mapping, input_hashes=self.hashes)

    def test_rejects_invalid_runtime_evidence_shape(self):
        evidence = {"schema_version": "usit-business-evidence-verification-v1",
            "business_function_evidence": [{"business_function_id": "bf-list"}, {"business_function_id": "bf-list"}]}
        with self.assertRaisesRegex(ValueError, "duplicate runtime evidence"):
            build_business_traceability(self.semantics, self.graph, self.mapping, evidence_matrix=evidence,
                input_hashes=self.hashes)


if __name__ == "__main__":
    unittest.main()
