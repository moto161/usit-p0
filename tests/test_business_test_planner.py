from __future__ import annotations

import unittest

from usit.business_test_planner import plan_business_tests


class BusinessTestPlannerTests(unittest.TestCase):
    def setUp(self):
        self.source = {
            "schema_version": "usit-business-semantics-v1",
            "project_root": "/tmp/project",
            "features": [{
                "name": "照片上传", "module": "媒体", "status": "DONE",
                "priority": "P0", "source": "docs/PRD.md", "line": 12,
            }],
            "endpoints": [{
                "method": "POST", "path": "/api/photos", "code_auth": "auth",
                "source": "server/routes/photos.js:18", "line": 18,
            }],
            "entities": [{
                "name": "photos", "source": "docs/DB.md",
                "fields": [{"name": "owner_id"}, {"name": "storage_key"}],
            }],
            "discrepancies": [{
                "kind": "documented_field_unimplemented",
                "message": "owner_id is not referenced",
                "confidence": "STATIC_INFERENCE",
                "evidence": [{"rel_path": "docs/DB.md", "field": "owner_id", "entity": "photos"}],
            }],
        }

    def test_generates_candidates_for_features_endpoints_entities_and_gaps(self):
        result = plan_business_tests(self.source)
        self.assertEqual(result["candidate_count"], 4)
        self.assertEqual(result["execution_status"], "NOT_EXECUTED")
        self.assertEqual(
            result["category_counts"],
            {
                "data_entity_integrity": 1,
                "discrepancy_verification": 1,
                "endpoint_access": 1,
                "feature_behavior": 1,
            },
        )
        self.assertTrue(all(x["execution_status"] == "NOT_EXECUTED" for x in result["candidates"]))
        self.assertTrue(all(x["design_status"] == "CANDIDATE_NEEDS_REQUIREMENTS" for x in result["candidates"]))

    def test_output_is_deterministic(self):
        self.assertEqual(plan_business_tests(self.source), plan_business_tests(self.source))

    def test_missing_semantics_does_not_fabricate_candidates(self):
        result = plan_business_tests({"entities": [], "features": [], "endpoints": [], "discrepancies": []})
        self.assertEqual(result["candidate_count"], 0)
        self.assertEqual(result["candidates"], [])

    def test_malformed_records_are_skipped_with_warnings(self):
        source = dict(self.source)
        source["features"] = [None, {"name": ""}]
        result = plan_business_tests(source)
        self.assertEqual(result["candidate_count"], 3)
        self.assertEqual(len(result["warnings"]), 2)

    def test_exact_api_contract_emits_verification_candidate_with_execution_evidence(self):
        semantics = {"endpoint_contract_alignment": [{
            "status": "EXACT_MATCH", "method": "GET", "path": "/api/photos",
            "document_evidence": {"source": "docs/api.md", "line": 7},
            "code_evidence": [{"source": "server/routes/photos.js", "line": 18}],
        }]}
        graph = {"business_understanding": {"endpoint_execution_paths": [{
            "method": "GET", "endpoint": "/api/photos",
            "source": {"path": "server/routes/photos.js", "line": 18},
            "handler": {"path": "server/photos.js", "line": 30, "symbol_id": "sym-handler"},
            "call_chain": [{"call_site": {"path": "server/photos.js", "line": 34}, "target_id": "sym-db"}],
            "database_accesses": [{"database_access_id": "db-1", "path": "server/db.js", "line": 9}],
            "authorization": {"evidence": [{"authorization_check_id": "auth-1", "kind": "route_middleware", "path": "server/routes/photos.js", "line": 17}]},
            "identity_resource_flow": [{"identity_evidence_id": "identity-1", "path": "server/db.js", "line": 9}],
        }]}}
        result = plan_business_tests(semantics, graph)
        candidate = next(x for x in result["candidates"] if x["category"] == "api_contract_verification")
        self.assertEqual(candidate["execution_status"], "NOT_EXECUTED")
        self.assertEqual(candidate["design_status"], "CANDIDATE_NEEDS_REQUIREMENTS")
        kinds = {x.get("evidence_kind") for x in candidate["evidence_refs"]}
        self.assertTrue({"documented_api_contract", "code_route", "resolved_handler", "call_chain", "database_access", "authorization", "identity_resource"}.issubset(kinds))
        self.assertTrue(all(x["confidence"] == "STATIC_INFERENCE" for x in candidate["evidence_refs"]))

    def test_unmatched_contracts_are_review_candidates_not_defect_verdicts(self):
        semantics = {"endpoint_contract_alignment": [
            {"status": "DOCUMENTED_NOT_FOUND_IN_CODE", "method": "POST", "path": "/api/missing", "document_evidence": {"source": "api.md", "line": 4}, "code_evidence": []},
            {"status": "CODE_ENDPOINT_NOT_DOCUMENTED", "method": "GET", "path": "/internal", "document_evidence": None, "code_evidence": [{"source": "routes.js", "line": 8}]},
        ]}
        result = plan_business_tests(semantics)
        self.assertEqual(result["candidate_count"], 2)
        self.assertTrue(all(x["category"] == "api_contract_review" for x in result["candidates"]))
        self.assertTrue(all("不是" in "".join(x["limitations"]) or "不证明" in "".join(x["limitations"]) for x in result["candidates"]))

    def test_ambiguous_route_alignment_does_not_attach_execution_path(self):
        semantics = {"endpoint_contract_alignment": [{
            "status": "MULTIPLE_CODE_MATCHES", "method": "GET", "path": "/dup",
            "document_evidence": {"source": "api.md", "line": 2},
            "code_evidence": [{"source": "a.js", "line": 1}, {"source": "b.js", "line": 2}],
        }]}
        graph = {"business_understanding": {"endpoint_execution_paths": [{
            "method": "GET", "endpoint": "/dup", "handler": {"path": "handler.js", "line": 9},
        }]}}
        result = plan_business_tests(semantics, graph)
        candidate = result["candidates"][0]
        self.assertEqual(candidate["category"], "api_contract_review")
        self.assertFalse(any(x.get("evidence_kind") == "resolved_handler" for x in candidate["evidence_refs"]))

    def test_non_list_collection_is_rejected(self):
        with self.assertRaises(ValueError):
            plan_business_tests({"features": {}, "entities": [], "endpoints": [], "discrepancies": []})

    def test_non_mapping_input_is_rejected(self):
        with self.assertRaises(TypeError):
            plan_business_tests([])


if __name__ == "__main__":
    unittest.main()
