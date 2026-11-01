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

    def test_non_list_collection_is_rejected(self):
        with self.assertRaises(ValueError):
            plan_business_tests({"features": {}, "entities": [], "endpoints": [], "discrepancies": []})

    def test_non_mapping_input_is_rejected(self):
        with self.assertRaises(TypeError):
            plan_business_tests([])


if __name__ == "__main__":
    unittest.main()
