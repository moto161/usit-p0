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
        self.assertTrue(all(x["actions"] for x in result["candidates"]))
        self.assertTrue(all(x["evidence_requirements"] for x in result["candidates"]))
        self.assertTrue(all(x["expected_results_status"] == "OWNER_CONFIRMATION_REQUIRED"
                            for x in result["candidates"]))

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
        graph = {"business_understanding": {
            "business_function_views": [{
                "business_function_id": "bf-photo-list", "endpoint_id": "ep-photo-list",
                "method": "GET", "endpoint": "/api/photos",
            }],
            "endpoint_execution_paths": [{
            "endpoint_id": "ep-photo-list", "method": "GET", "endpoint": "/api/photos",
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
        self.assertEqual(candidate["business_function_id"], "bf-photo-list")
        self.assertEqual(candidate["function_coverage_linkage"], "TEST_DESIGN_CANDIDATE_LINKED")
        self.assertEqual(candidate["test_execution_coverage"], "NOT_MEASURED")
        self.assertEqual(candidate["expected_results_status"], "OWNER_CONFIRMATION_REQUIRED")
        self.assertTrue(candidate["actions"])
        self.assertTrue(all(item["expected_result"] for item in candidate["actions"]))
        self.assertTrue(candidate["evidence_requirements"])
        kinds = {x.get("evidence_kind") for x in candidate["evidence_refs"]}
        self.assertTrue({"documented_api_contract", "code_route", "resolved_handler", "call_chain", "database_access", "authorization", "identity_resource"}.issubset(kinds))
        self.assertTrue(all(x["confidence"] == "STATIC_INFERENCE" for x in candidate["evidence_refs"]))

    def test_exact_api_candidate_does_not_link_ambiguous_function_views(self):
        semantics = {"endpoint_contract_alignment": [{
            "status": "EXACT_MATCH", "method": "GET", "path": "/api/photos",
            "document_evidence": {"source": "api.md", "line": 3},
            "code_evidence": [{"source": "routes.py", "line": 9}],
        }]}
        graph = {"business_understanding": {
            "business_function_views": [
                {"business_function_id": "bf-a", "method": "GET", "endpoint": "/api/photos"},
                {"business_function_id": "bf-b", "method": "GET", "endpoint": "/api/photos"},
            ],
            "endpoint_execution_paths": [{
                "method": "GET", "endpoint": "/api/photos",
                "source": {"path": "routes.py", "line": 9},
            }],
        }}
        result = plan_business_tests(semantics, graph)
        candidate = next(x for x in result["candidates"] if x["category"] == "api_contract_verification")
        self.assertNotIn("business_function_id", candidate)
        self.assertTrue(any("multiple business function views" in warning for warning in result["warnings"]))

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

    def test_exact_frontend_backend_chain_generates_evidence_linked_flow_candidate(self):
        semantics = {"features": [], "entities": [], "endpoints": [], "discrepancies": []}
        graph = {"business_understanding": {
            "endpoint_execution_paths": [{
                "endpoint_id": "endpoint-1", "method": "GET", "endpoint": "/api/photos",
                "source": {"path": "server/routes.ts", "line": 8},
                "handler": {"path": "server/photos.ts", "line": 12, "symbol_id": "sym-handler"},
                "call_chain": [{"call_site": {"path": "server/photos.ts", "line": 15}, "target_id": "sym-db"}],
                "database_accesses": [{"database_access_id": "db-1", "path": "server/db.ts", "line": 4}],
                "authorization": {"evidence": [{"authorization_check_id": "auth-1", "path": "server/routes.ts", "line": 7}]},
                "identity_resource_flow": [{"identity_evidence_id": "identity-1", "path": "server/db.ts", "line": 4}],
                "stage_status": {"handler": "RESOLVED", "calls": "RESOLVED_STATIC_CHAIN", "database": "LINKED"},
            }],
            "frontend_business_chains": [{
                "source_file": "web/PhotoPage.tsx", "event_line": 4, "event_name": "onClick",
                "handler_symbol_id": "sym-ui", "endpoint_node_id": "endpoint-1",
                "api_call_node_id": "api-call-1", "method": "GET", "path": "/api/photos",
                "api_call_file": "web/photos.ts", "api_call_line": 20,
                "resolution_status": "EXACT_MATCH",
            }],
        }}
        result = plan_business_tests(semantics, graph)
        candidate = next(x for x in result["candidates"] if x["category"] == "frontend_backend_flow")
        self.assertEqual(candidate["execution_status"], "NOT_EXECUTED")
        self.assertEqual(candidate["design_status"], "CANDIDATE_NEEDS_REQUIREMENTS")
        kinds = {item.get("evidence_kind") for item in candidate["evidence_refs"]}
        self.assertTrue({"frontend_event", "frontend_api_call", "backend_route", "backend_handler",
                         "backend_call_chain", "database_access", "authorization",
                         "identity_resource"}.issubset(kinds))
        self.assertTrue(any("真实页面" in step for step in candidate["steps"]))

    def test_unverified_or_ambiguous_frontend_chain_does_not_generate_flow_candidate(self):
        semantics = {"features": [], "entities": [], "endpoints": [], "discrepancies": []}
        graph = {"business_understanding": {"frontend_business_chains": [
            {"method": "GET", "path": "/api/photos", "resolution_status": "NO_MATCH"},
            {"method": "GET", "path": "/api/photos", "resolution_status": "AMBIGUOUS"},
        ]}}
        result = plan_business_tests(semantics, graph)
        self.assertFalse(any(x["category"] == "frontend_backend_flow" for x in result["candidates"]))

    def test_non_list_collection_is_rejected(self):
        with self.assertRaises(ValueError):
            plan_business_tests({"features": {}, "entities": [], "endpoints": [], "discrepancies": []})

    def test_non_mapping_input_is_rejected(self):
        with self.assertRaises(TypeError):
            plan_business_tests([])


    def test_s214_function_view_links_design_candidate_without_claiming_execution(self):
        semantics = {"features": [], "entities": [], "endpoints": [], "discrepancies": []}
        graph = {"business_understanding": {
            "business_function_views": [{"business_function_id": "bf-1", "endpoint_id": "ep-1"}],
            "endpoint_execution_paths": [{
                "endpoint_id": "ep-1", "method": "GET", "endpoint": "/api/photos",
                "source": {"path": "server/routes.ts", "line": 8},
                "handler": {"path": "server/photos.ts", "line": 12, "symbol_id": "handler"},
                "call_chain": [], "database_accesses": [], "authorization": {"evidence": []},
                "identity_resource_flow": [], "stage_status": {"handler": "RESOLVED"}}],
            "frontend_business_chains": [{
                "source_file": "web/PhotoPage.tsx", "event_line": 4, "event_name": "onClick",
                "handler_symbol_id": "ui", "endpoint_node_id": "ep-1", "api_call_node_id": "api-1",
                "method": "GET", "path": "/api/photos", "api_call_file": "web/api.ts",
                "api_call_line": 20, "resolution_status": "EXACT_MATCH"}]}}
        result = plan_business_tests(semantics, graph)
        candidate = next(x for x in result["candidates"] if x["category"] == "frontend_backend_flow")
        self.assertEqual(candidate["business_function_id"], "bf-1")
        self.assertEqual(candidate["function_coverage_linkage"], "TEST_DESIGN_CANDIDATE_LINKED")
        self.assertEqual(candidate["test_execution_coverage"], "NOT_MEASURED")
        self.assertEqual(result["business_function_coverage"]["function_views_with_test_design_candidate"], 1)
        self.assertEqual(result["business_function_coverage"]["test_execution_coverage"], "NOT_MEASURED")

    def test_duplicate_endpoint_function_views_do_not_arbitrarily_link_flow_candidate(self):
        semantics = {"features": [], "entities": [], "endpoints": [], "discrepancies": []}
        graph = {"business_understanding": {
            "business_function_views": [
                {"business_function_id": "bf-photo-list", "endpoint_id": "ep-photos"},
                {"business_function_id": "bf-photo-search", "endpoint_id": "ep-photos"},
            ],
            "endpoint_execution_paths": [{
                "endpoint_id": "ep-photos", "method": "GET", "endpoint": "/api/photos",
                "source": {"path": "server/routes.ts", "line": 8},
                "handler": {"path": "server/photos.ts", "line": 12, "symbol_id": "handler"},
                "call_chain": [], "database_accesses": [],
                "authorization": {"evidence": []}, "identity_resource_flow": [],
                "stage_status": {"handler": "RESOLVED"},
            }],
            "frontend_business_chains": [{
                "source_file": "web/PhotoPage.tsx", "event_line": 4, "event_name": "onClick",
                "handler_symbol_id": "ui", "endpoint_node_id": "ep-photos",
                "api_call_node_id": "api-photos", "method": "GET", "path": "/api/photos",
                "api_call_file": "web/api.ts", "api_call_line": 20,
                "resolution_status": "EXACT_MATCH",
            }],
        }}
        result = plan_business_tests(semantics, graph)
        candidate = next(x for x in result["candidates"] if x["category"] == "frontend_backend_flow")
        self.assertNotIn("business_function_id", candidate)
        self.assertNotIn("function_coverage_linkage", candidate)
        self.assertEqual(result["business_function_coverage"]["function_views_with_test_design_candidate"], 0)
        self.assertTrue(any("multiple business function views" in item for item in result["warnings"]))

if __name__ == "__main__":
    unittest.main()
