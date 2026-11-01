from __future__ import annotations

import unittest

from usit.business_graph import project_business_graph


class IdentityGraphProjectionTests(unittest.TestCase):
    def test_identity_evidence_links_only_to_exact_database_callsite_and_endpoint_chain(self):
        graph = {
            "schema_version": "usit-graph-v1",
            "nodes": [
                {"id": "file:routes/photos.ts", "type": "file", "label": "routes/photos.ts",
                 "evidence": {"path": "routes/photos.ts"}},
                {"id": "file:repo/photos.ts", "type": "file", "label": "repo/photos.ts",
                 "evidence": {"path": "repo/photos.ts"}},
                {"id": "symbol:handler", "type": "function", "label": "getPhotos",
                 "evidence": {"path": "routes/photos.ts", "line": 1}},
                {"id": "symbol:repo", "type": "function", "label": "loadPhotos",
                 "evidence": {"path": "repo/photos.ts", "line": 1}},
            ],
            "edges": [
                {"source": "symbol:handler", "target": "symbol:repo", "type": "calls",
                 "caller_symbol_id": "symbol:handler", "resolution_status": "RESOLVED",
                 "evidence": {"path": "routes/photos.ts", "line": 3}},
                {"source": "symbol:repo", "target": "external:db", "type": "calls",
                 "caller_symbol_id": "symbol:repo", "resolution_status": "EXTERNAL",
                 "evidence": {"path": "repo/photos.ts", "line": 7}},
            ],
        }
        semantics = {
            "features": [], "entities": [],
            "endpoints": [{
                "source": "routes/photos.ts", "line": 3,
                "method": "GET", "path": "/photos",
                "handler_reference": "getPhotos",
            }],
            "database_accesses": [{
                "source": "repo/photos.ts", "line": 7, "callee": "db.query",
                "operation": "SELECT", "tables": ["photos"], "columns": ["id", "owner_id"],
                "parse_status": "RESOLVED",
            }],
            "authorization_checks": [],
            "identity_evidence": [{
                "evidence_id": "identity-test-direct",
                "kind": "direct_authenticated_identity_to_sql_owner_binding",
                "source": "repo/photos.ts", "line": 7,
                "details": {"identity_expressions": ["req.user.id"],
                            "resource_fields": ["owner_id"],
                            "binding_status": "SAME_LINE_PATTERN_ONLY"},
                "scope": "SAME_LINE_PATTERN",
            }, {
                "evidence_id": "identity-test-wrong-line",
                "kind": "authenticated_identity_source",
                "source": "repo/photos.ts", "line": 8,
                "details": {"identity_expressions": ["req.user.id"]},
                "scope": "SAME_LINE_PATTERN",
            }],
        }
        projected = project_business_graph(graph, semantics)
        endpoint = next(node for node in projected["nodes"]
                        if node.get("business_kind") == "endpoint")
        association = endpoint["handler_association"]
        linked = association["identity_dataflow_evidence"]
        self.assertEqual([row["identity_evidence_id"] for row in linked],
                         ["identity-test-direct"])
        self.assertEqual(linked[0]["kind"],
                         "direct_authenticated_identity_to_sql_owner_binding")
        self.assertEqual(projected["business_understanding"]["identity_evidence_records"], 2)
        self.assertEqual(projected["business_understanding"]["identity_evidence_unlinked"], 0)
        self.assertEqual(projected["business_understanding"]["endpoint_execution_path_count"], 1)
        flow = projected["business_understanding"]["endpoint_execution_paths"][0]
        self.assertEqual(flow["handler"]["resolution_status"], "RESOLVED")
        self.assertEqual(flow["stage_status"]["calls"], "PARTIAL")
        self.assertEqual(flow["stage_status"]["database"], "LINKED")
        self.assertEqual(flow["stage_status"]["identity_resource"], "DETECTED")
        self.assertEqual(flow["verification_status"], "NOT_VERIFIED")


if __name__ == "__main__":
    unittest.main()
