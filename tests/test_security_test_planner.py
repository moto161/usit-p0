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

    def test_deterministic_and_empty_graph(self):
        graph = {"nodes": [endpoint()]}
        self.assertEqual(plan_security_tests(graph), plan_security_tests(graph))
        self.assertEqual(plan_security_tests({"nodes": []})["candidate_count"], 0)

    def test_rejects_malformed_nodes_collection(self):
        with self.assertRaises(ValueError):
            plan_security_tests({"nodes": {}})


if __name__ == "__main__":
    unittest.main()
