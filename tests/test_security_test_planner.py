from __future__ import annotations

import unittest

from usit.security_test_planner import plan_security_tests


def endpoint(label="/api/photos", *, auth_status="NOT_DETECTED_STATIC_SCAN",
             guards=None, columns=None, database_id="db-1"):
    return {
        "id": "endpoint:" + label,
        "type": "candidate",
        "business_kind": "endpoint",
        "label": "GET " + label,
        "business_details": {"method": "GET", "endpoint": label},
        "evidence": {"path": "routes/photos.js", "line": 12},
        "authorization_evidence": {
            "status": auth_status,
            "route_middleware": [],
            "reachable_function_checks": ([{
                "path": "services/photos.js", "line": 20,
                "guard_names": guards or [],
                "verification_status": "NOT_VERIFIED",
            }] if guards else []),
            "limitations": ["static only"],
        },
        "handler_association": {
            "resolution_status": "RESOLVED",
            "target_path": "routes/photos.js", "target_line": 30,
            "call_chain_evidence": [{"steps": [{
                "caller_symbol_id": "handler:photos",
                "target_id": "service:photos",
                "resolution_status": "RESOLVED",
                "call_site": {"path": "routes/photos.js", "line": 35},
            }]}],
            "database_access_evidence": ([{
                "database_access_id": database_id,
                "path": "repos/photos.js", "line": 44,
                "operation": "SELECT", "tables": ["photos"],
                "columns": columns or ["id", "owner_id"],
                "verification_status": "NOT_VERIFIED",
            }] if columns is not None or database_id else []),
        },
    }


class SecurityTestPlannerTests(unittest.TestCase):
    def test_generates_owner_and_auth_candidates_from_evidence(self):
        result = plan_security_tests({"nodes": [endpoint()]})
        categories = {item["category"] for item in result["candidates"]}
        self.assertIn("security_authentication_boundary", categories)
        self.assertIn("security_cross_user_resource_isolation", categories)
        self.assertIn("security_owner_parameter_tampering", categories)
        self.assertTrue(all(item["execution_status"] == "NOT_EXECUTED"
                            for item in result["candidates"]))
        self.assertTrue(all(item["design_status"] == "CANDIDATE_NEEDS_REQUIREMENTS"
                            for item in result["candidates"]))

    def test_security_candidates_share_structured_actions_and_evidence_contract(self):
        result = plan_security_tests({"nodes": [endpoint(guards=["adminRequired"])]})
        self.assertTrue(result["candidates"])
        for candidate in result["candidates"]:
            self.assertEqual(candidate["expected_results_status"], "OWNER_CONFIRMATION_REQUIRED")
            self.assertEqual(len(candidate["actions"]), len(candidate["steps"]))
            self.assertTrue(all(action["action"] and action["expected_result"]
                                and action["evidence_to_collect"] for action in candidate["actions"]))
            evidence_types = {item["evidence_type"] for item in candidate["evidence_requirements"]}
            self.assertTrue({"RUNTIME_TEST_RESULT", "AUTHORIZATION_DECISION", "BUSINESS_ORACLE"}
                            .issubset(evidence_types))
            self.assertEqual(candidate["execution_status"], "NOT_EXECUTED")

    def test_links_security_candidates_only_to_unique_endpoint_business_function_view(self):
        node = endpoint()
        graph = {"nodes": [node], "business_understanding": {"business_function_views": [{
            "business_function_id": "bf-photos", "endpoint_id": node["id"]}]}}
        result = plan_security_tests(graph)
        self.assertTrue(result["candidates"])
        self.assertTrue(all(item["business_function_id"] == "bf-photos" for item in result["candidates"]))
        self.assertEqual(result["unlinked_business_function_candidate_count"], 0)

    def test_ambiguous_endpoint_business_function_views_remain_unlinked(self):
        node = endpoint()
        graph = {"nodes": [node], "business_understanding": {"business_function_views": [
            {"business_function_id": "bf-one", "endpoint_id": node["id"]},
            {"business_function_id": "bf-two", "endpoint_id": node["id"]}]}}
        result = plan_security_tests(graph)
        self.assertTrue(result["candidates"])
        self.assertTrue(all(item["business_function_id"] is None for item in result["candidates"]))
        self.assertEqual(result["unlinked_business_function_candidate_count"], result["candidate_count"])

    def test_missing_auth_evidence_is_not_called_a_vulnerability(self):
        result = plan_security_tests({"nodes": [endpoint()]})
        auth = next(item for item in result["candidates"]
                    if item["category"] == "security_authentication_boundary")
        self.assertIn("不代表接口缺少认证", " ".join(auth["limitations"]))
        self.assertNotIn("vulnerability", auth)
        self.assertEqual(auth["basis"], "STATIC_INFERENCE")

    def test_role_candidate_requires_guard_and_sensitive_database_evidence(self):
        no_guard = endpoint()
        guarded = endpoint(label="/api/admin/photos", guards=["adminRequired"])
        result = plan_security_tests({"nodes": [no_guard, guarded]})
        role = [item for item in result["candidates"]
                if item["category"] == "security_role_boundary"]
        self.assertEqual(len(role), 1)
        self.assertIn("/api/admin/photos", role[0]["target"])

    def test_no_owner_columns_means_no_idor_or_parameter_tampering_candidate(self):
        item = endpoint(columns=["id", "created_at"])
        result = plan_security_tests({"nodes": [item]})
        categories = {candidate["category"] for candidate in result["candidates"]}
        self.assertNotIn("security_cross_user_resource_isolation", categories)
        self.assertNotIn("security_owner_parameter_tampering", categories)

    def test_alternate_entrypoint_requires_shared_db_evidence_and_different_auth_signal(self):
        left = endpoint("/api/photos", auth_status="DETECTED", database_id="shared-db")
        right = endpoint("/api/export", auth_status="NOT_DETECTED_STATIC_SCAN", database_id="shared-db")
        result = plan_security_tests({"nodes": [left, right]})
        alternate = [item for item in result["candidates"]
                     if item["category"] == "security_alternate_entrypoint_authorization"]
        self.assertEqual(len(alternate), 2)
        self.assertTrue(all("不证明存在绕过" in " ".join(item["limitations"])
                            for item in alternate))

    def test_request_controlled_identity_binding_generates_review_candidate(self):
        item = endpoint()
        item["handler_association"]["identity_dataflow_evidence"] = [{
            "identity_evidence_id": "identity-request",
            "kind": "direct_request_identity_to_sql_owner_binding",
            "path": "repo/photos.js", "line": 44,
            "scope": "SAME_LINE_PATTERN",
            "details": {"request_expressions": ["req.body.user_id"]},
        }]
        result = plan_security_tests({"nodes": [item]})
        candidate = next(x for x in result["candidates"]
                         if x["category"] == "security_request_identity_owner_binding")
        self.assertEqual(candidate["execution_status"], "NOT_EXECUTED")
        self.assertIn("不是 IDOR 或漏洞结论", " ".join(candidate["limitations"]))
        self.assertEqual(candidate["identity_dataflow_evidence"][0]["identity_evidence_id"],
                         "identity-request")

    def test_trusted_identity_binding_generates_verification_candidate_not_proof(self):
        item = endpoint()
        item["handler_association"]["identity_dataflow_evidence"] = [{
            "identity_evidence_id": "identity-trusted",
            "kind": "direct_authenticated_identity_to_sql_owner_binding",
            "path": "repo/photos.js", "line": 44,
            "scope": "SAME_LINE_PATTERN",
            "details": {"identity_expressions": ["req.user.id"]},
        }]
        result = plan_security_tests({"nodes": [item]})
        candidate = next(x for x in result["candidates"]
                         if x["category"] == "security_authenticated_identity_owner_binding")
        self.assertIn("不证明对象可信", " ".join(candidate["limitations"]))

    def test_deterministic_and_empty_graph(self):
        graph = {"nodes": [endpoint()]}
        self.assertEqual(plan_security_tests(graph), plan_security_tests(graph))
        self.assertEqual(plan_security_tests({"nodes": []})["candidate_count"], 0)

    def test_rejects_malformed_nodes_collection(self):
        with self.assertRaises(ValueError):
            plan_security_tests({"nodes": {}})


if __name__ == "__main__":
    unittest.main()
