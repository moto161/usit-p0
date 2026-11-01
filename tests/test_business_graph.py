from __future__ import annotations

import unittest

from usit.business_graph import project_business_graph


def graph():
    return {
        "schema_version": "usit-graph-v1",
        "nodes": [
            {"id": "file:docs/requirements.md", "type": "file",
             "label": "docs/requirements.md", "evidence": {"path": "docs/requirements.md"}},
            {"id": "file:server/routes/photos.js", "type": "file",
             "label": "server/routes/photos.js", "evidence": {"path": "server/routes/photos.js"}},
        ],
        "edges": [],
    }


class BusinessGraphProjectionTests(unittest.TestCase):
    def test_links_records_only_to_exact_source_files(self):
        semantics = {
            "features": [{"name": "照片可见性", "module": "相册", "status": "DONE",
                          "source": "docs/requirements.md", "line": 12}],
            "entities": [{"name": "photos", "source": "docs/schema.md",
                          "fields": [{"name": "owner_id"}]}],
            "endpoints": [{"method": "GET", "path": "/api/photos",
                           "source": "server/routes/photos.js", "line": 20,
                           "code_auth": "auth"}],
        }
        result = project_business_graph(graph(), semantics)
        business_nodes = [n for n in result["nodes"] if n.get("business_kind")]
        self.assertEqual(len(business_nodes), 2)
        self.assertEqual(result["business_understanding"]["linked_records"], 2)
        self.assertEqual(len(result["business_understanding"]["unlinked_records"]), 1)
        self.assertTrue(all(n["type"] == "candidate" for n in business_nodes))
        self.assertTrue(all(n["verification_status"] == "NOT_VERIFIED" for n in business_nodes))
        self.assertTrue(all(e.get("verified") is False for e in result["edges"]))

    def test_does_not_invent_cross_domain_edges(self):
        semantics = {
            "features": [{"name": "上传照片", "source": "docs/requirements.md"}],
            "entities": [{"name": "photos", "source": "docs/requirements.md",
                          "fields": [{"name": "id"}]}],
            "endpoints": [{"method": "POST", "path": "/api/photos",
                           "source": "docs/requirements.md"}],
        }
        result = project_business_graph(graph(), semantics)
        self.assertEqual(len(result["edges"]), 3)
        self.assertTrue(all(e["type"] == "references" for e in result["edges"]))

    def test_rejects_invalid_graph_schema(self):
        with self.assertRaises(ValueError):
            project_business_graph({"schema_version": "wrong", "nodes": [], "edges": []}, {})

    def test_rejects_path_traversal_and_keeps_record_unlinked(self):
        semantics = {"features": [{"name": "unsafe", "source": "../outside.md"}]}
        result = project_business_graph(graph(), semantics)
        self.assertEqual(result["business_understanding"]["linked_records"], 0)
        self.assertEqual(result["business_understanding"]["unlinked_records"][0]["reason"],
                         "INVALID_OR_MISSING_SOURCE")

    def test_is_deterministic_and_does_not_mutate_inputs(self):
        original = graph()
        semantics = {"features": [{"name": "A", "source": "docs/requirements.md"}]}
        first = project_business_graph(original, semantics)
        second = project_business_graph(original, semantics)
        self.assertEqual(first, second)
        self.assertEqual(len(original["nodes"]), 2)
        self.assertEqual(original["edges"], [])


    def test_resolves_unique_same_file_named_handler_as_candidate_reference(self):
        source = graph()
        source["nodes"].append({
            "id": "symbol:server/routes/photos.js#uploadPhoto:35",
            "type": "function", "label": "uploadPhoto",
            "evidence": {"path": "server/routes/photos.js", "line": 35},
        })
        result = project_business_graph(source, {"endpoints": [{
            "method": "POST", "path": "/photos", "source": "server/routes/photos.js:20",
            "line": 20, "handler_reference": "uploadPhoto",
        }]})
        endpoint = next(n for n in result["nodes"] if n.get("business_kind") == "endpoint")
        self.assertEqual(endpoint["handler_association"]["resolution_status"], "RESOLVED")
        relation = next(e for e in result["edges"] if e.get("method") == "route-handler-static-symbol-match")
        self.assertEqual(relation["target"], "symbol:server/routes/photos.js#uploadPhoto:35")
        self.assertIs(relation["verified"], False)
        self.assertIs(relation["candidate"], True)

    def test_handler_association_collects_only_calls_owned_by_that_symbol(self):
        source = graph()
        source["nodes"].extend([
            {
                "id": "symbol:server/routes/photos.js#uploadPhoto:35",
                "type": "function", "label": "uploadPhoto",
                "evidence": {"path": "server/routes/photos.js", "line": 35},
            },
            {
                "id": "symbol:server/services/photo_store.js#savePhoto:8",
                "type": "function", "label": "savePhoto",
                "evidence": {"path": "server/services/photo_store.js", "line": 8},
            },
        ])
        source["edges"].append({
            "source": "file:server/routes/photos.js",
            "target": "symbol:server/services/photo_store.js#savePhoto:8",
            "type": "calls", "caller_symbol_id": "symbol:server/routes/photos.js#uploadPhoto:35",
            "resolution_status": "RESOLVED", "resolved": True,
            "evidence": {"path": "server/routes/photos.js", "line": 39},
            "target_location": {"path": "server/services/photo_store.js", "line": 8},
            "method": "python-ast-project", "confidence": "high",
        })
        source["edges"].append({
            "source": "file:server/routes/photos.js",
            "target": "symbol:server/services/photo_store.js#savePhoto:8",
            "type": "calls", "caller_symbol_id": "symbol:server/routes/photos.js#anotherHandler:50",
            "resolution_status": "RESOLVED", "resolved": True,
            "evidence": {"path": "server/routes/photos.js", "line": 52},
            "target_location": {"path": "server/services/photo_store.js", "line": 8},
            "method": "python-ast-project", "confidence": "high",
        })
        result = project_business_graph(source, {"endpoints": [{
            "method": "POST", "path": "/photos", "source": "server/routes/photos.js:20",
            "line": 20, "handler_reference": "uploadPhoto",
        }]})
        endpoint = next(n for n in result["nodes"] if n.get("business_kind") == "endpoint")
        downstream = endpoint["handler_association"]["downstream_call_evidence"]
        self.assertEqual(len(downstream), 1)
        self.assertEqual(downstream[0]["call_site"]["line"], 39)
        self.assertEqual(result["business_understanding"]["handler_downstream_call_evidence"], 1)

    def test_handler_association_expands_cross_file_call_chain_and_marks_cycles(self):
        source = graph()
        handler = "symbol:server/routes/photos.js#uploadPhoto:35"
        service = "symbol:server/services/photo_service.js#savePhoto:8"
        repository = "symbol:server/repos/photo_repo.js#insert:12"
        source["nodes"].extend([
            {"id": handler, "type": "function", "label": "uploadPhoto",
             "evidence": {"path": "server/routes/photos.js", "line": 35}},
            {"id": service, "type": "function", "label": "savePhoto",
             "evidence": {"path": "server/services/photo_service.js", "line": 8}},
            {"id": repository, "type": "function", "label": "insert",
             "evidence": {"path": "server/repos/photo_repo.js", "line": 12}},
        ])
        def call(caller, target, path, line):
            return {
                "source": "file:" + path, "target": target, "type": "calls",
                "caller_symbol_id": caller, "resolution_status": "RESOLVED",
                "resolved": True, "evidence": {"path": path, "line": line},
                "target_location": {"path": target.split("#")[0].removeprefix("symbol:")},
                "method": "typescript-compiler-api", "confidence": "high",
            }
        source["edges"].extend([
            call(handler, service, "server/routes/photos.js", 39),
            call(service, repository, "server/services/photo_service.js", 10),
            call(repository, service, "server/repos/photo_repo.js", 14),
        ])
        result = project_business_graph(source, {"endpoints": [{
            "method": "POST", "path": "/photos", "source": "server/routes/photos.js:20",
            "line": 20, "handler_reference": "uploadPhoto",
        }]})
        endpoint = next(n for n in result["nodes"] if n.get("business_kind") == "endpoint")
        association = endpoint["handler_association"]
        self.assertEqual(len(association["downstream_call_evidence"]), 1)
        chains = association["call_chain_evidence"]
        self.assertEqual(len(chains), 1)
        self.assertEqual(len(chains[0]["steps"]), 3)
        self.assertTrue(chains[0]["cycle_detected"])
        self.assertEqual(result["business_understanding"]["handler_call_chains"], 1)

    def test_handler_call_chain_stops_at_configured_depth(self):
        source = graph()
        handler = "symbol:server/routes/photos.js#uploadPhoto:35"
        symbols = [handler] + [
            f"symbol:module_{index}.py#step_{index}:{index + 1}"
            for index in range(9)
        ]
        for index, symbol in enumerate(symbols):
            path = "server/routes/photos.js" if index == 0 else f"module_{index}.py"
            name = "uploadPhoto" if index == 0 else f"step_{index - 1}"
            source["nodes"].append({
                "id": symbol, "type": "function", "label": name,
                "evidence": {"path": path, "line": 35 if index == 0 else index},
            })
        for index in range(len(symbols) - 1):
            path = "server/routes/photos.js" if index == 0 else f"module_{index}.py"
            source["edges"].append({
                "source": "file:" + path, "target": symbols[index + 1],
                "type": "calls", "caller_symbol_id": symbols[index],
                "resolution_status": "RESOLVED", "resolved": True,
                "evidence": {"path": path, "line": index + 40},
                "method": "typescript-compiler-api", "confidence": "high",
            })
        result = project_business_graph(source, {"endpoints": [{
            "method": "POST", "path": "/photos", "source": "server/routes/photos.js:20",
            "line": 20, "handler_reference": "uploadPhoto",
        }]})
        endpoint = next(n for n in result["nodes"] if n.get("business_kind") == "endpoint")
        association = endpoint["handler_association"]
        self.assertTrue(association["call_chain_truncated"])
        self.assertEqual(len(association["call_chain_evidence"][0]["steps"]), 8)
        self.assertTrue(association["call_chain_evidence"][0]["depth_limit_reached"])

    def test_database_access_links_exact_owner_and_documented_table(self):
        source = graph()
        source["nodes"].extend([
            {"id": "file:server/repos/photo_repo.py", "type": "file",
             "label": "server/repos/photo_repo.py",
             "evidence": {"path": "server/repos/photo_repo.py"}},
            {"id": "external:db.execute", "type": "external", "label": "execute",
             "evidence": {"note": "external database client"}},
        ])
        source["nodes"].append({
            "id": "file:docs/schema.md", "type": "file", "label": "docs/schema.md",
            "evidence": {"path": "docs/schema.md"},
        })
        handler = "symbol:server/routes/photos.js#listPhotos:10"
        repository = "symbol:server/repos/photo_repo.py#load:4"
        source["nodes"].extend([
            {"id": handler, "type": "function", "label": "listPhotos",
             "evidence": {"path": "server/routes/photos.js", "line": 10}},
            {"id": repository, "type": "function", "label": "load",
             "evidence": {"path": "server/repos/photo_repo.py", "line": 4}},
        ])
        source["edges"].extend([
            {"source": "file:server/routes/photos.js", "target": repository,
             "type": "calls", "caller_symbol_id": handler, "resolution_status": "RESOLVED",
             "evidence": {"path": "server/routes/photos.js", "line": 15}},
            {"source": "file:server/repos/photo_repo.py", "target": "external:db.execute",
             "type": "calls", "caller_symbol_id": repository, "resolution_status": "EXTERNAL",
             "evidence": {"path": "server/repos/photo_repo.py", "line": 22}},
        ])
        semantics = {
            "entities": [{"name": "photos", "source": "docs/schema.md",
                          "fields": [{"name": "id"}, {"name": "owner_id"}]}],
            "endpoints": [{"method": "GET", "path": "/photos",
                           "source": "server/routes/photos.js:5", "line": 5,
                           "handler_reference": "listPhotos"}],
            "database_accesses": [{
                "source": "server/repos/photo_repo.py", "line": 22,
                "callee": "db.execute", "operation": "SELECT", "tables": ["photos"],
                "columns": ["id", "owner_id"], "parse_status": "RESOLVED",
                "parser": "python-ast-single-line",
            }],
        }
        result = project_business_graph(source, semantics)
        access = next(n for n in result["nodes"] if n.get("business_kind") == "database_access")
        self.assertEqual(access["business_details"]["owner_symbol_id"], repository)
        self.assertEqual(access["business_details"]["owner_resolution_status"], "RESOLVED")
        self.assertTrue(any(e.get("method") == "database-access-to-documented-entity"
                            for e in result["edges"]))
        endpoint = next(n for n in result["nodes"] if n.get("business_kind") == "endpoint")
        evidence = endpoint["handler_association"]["database_access_evidence"]
        self.assertEqual(len(evidence), 1)
        self.assertEqual(evidence[0]["tables"], ["photos"])
        self.assertEqual(evidence[0]["verification_status"], "NOT_VERIFIED")

    def test_authorization_evidence_follows_endpoint_to_service_and_repository_path(self):
        source = graph()
        source["nodes"].extend([
            {"id": "file:server/services/photo_service.py", "type": "file",
             "label": "server/services/photo_service.py",
             "evidence": {"path": "server/services/photo_service.py"}},
            {"id": "file:server/repos/photo_repo.py", "type": "file",
             "label": "server/repos/photo_repo.py",
             "evidence": {"path": "server/repos/photo_repo.py"}},
            {"id": "external:auth.requirePermission", "type": "external",
             "label": "requirePermission"},
            {"id": "external:repo.load", "type": "external", "label": "load"},
        ])
        handler = "symbol:server/routes/photos.js#listPhotos:10"
        service = "symbol:server/services/photo_service.py#listPhotos:4"
        repository = "symbol:server/repos/photo_repo.py#load:4"
        source["nodes"].extend([
            {"id": handler, "type": "function", "label": "listPhotos",
             "evidence": {"path": "server/routes/photos.js", "line": 10}},
            {"id": service, "type": "function", "label": "listPhotos",
             "evidence": {"path": "server/services/photo_service.py", "line": 4}},
            {"id": repository, "type": "function", "label": "load",
             "evidence": {"path": "server/repos/photo_repo.py", "line": 4}},
        ])
        source["edges"].extend([
            {"source": "file:server/routes/photos.js", "target": service,
             "type": "calls", "caller_symbol_id": handler, "resolution_status": "RESOLVED",
             "evidence": {"path": "server/routes/photos.js", "line": 15}},
            {"source": "file:server/services/photo_service.py", "target": "external:auth.requirePermission",
             "type": "calls", "caller_symbol_id": service, "resolution_status": "EXTERNAL",
             "evidence": {"path": "server/services/photo_service.py", "line": 8}},
            {"source": "file:server/services/photo_service.py", "target": repository,
             "type": "calls", "caller_symbol_id": service, "resolution_status": "RESOLVED",
             "evidence": {"path": "server/services/photo_service.py", "line": 9}},
            {"source": "file:server/repos/photo_repo.py", "target": "external:repo.load",
             "type": "calls", "caller_symbol_id": repository, "resolution_status": "EXTERNAL",
             "evidence": {"path": "server/repos/photo_repo.py", "line": 22}},
        ])
        semantics = {
            "endpoints": [{"method": "GET", "path": "/photos",
                           "source": "server/routes/photos.js:20", "line": 20,
                           "handler_reference": "listPhotos", "code_auth": "auth",
                           "authorization_status": "DETECTED"}],
            "authorization_checks": [
                {"source": "server/routes/photos.js", "line": 20,
                 "kind": "route_middleware", "guard_names": ["authRequired"],
                 "parser": "bounded-source-line-patterns"},
                {"source": "server/services/photo_service.py", "line": 8,
                 "kind": "authorization_call", "guard_names": ["requirePermission"],
                 "parser": "bounded-source-line-patterns"},
            ],
            "database_accesses": [{
                "source": "server/repos/photo_repo.py", "line": 22,
                "callee": "cursor.execute", "operation": "SELECT", "tables": ["photos"],
                "columns": ["id"], "parse_status": "RESOLVED",
            }],
        }
        result = project_business_graph(source, semantics)
        endpoint = next(n for n in result["nodes"] if n.get("business_kind") == "endpoint")
        auth = endpoint["authorization_evidence"]
        self.assertEqual(auth["status"], "DETECTED")
        self.assertEqual(len(auth["route_middleware"]), 1)
        self.assertEqual(len(auth["reachable_function_checks"]), 1)
        self.assertEqual(auth["reachable_function_checks"][0]["owner_symbol_id"], service)
        self.assertEqual(len(endpoint["handler_association"]["database_access_evidence"]), 1)

    def test_no_authorization_evidence_is_not_reported_as_proven_vulnerability(self):
        source = graph()
        result = project_business_graph(source, {"endpoints": [{
            "method": "GET", "path": "/photos",
            "source": "server/routes/photos.js:20", "line": 20,
        }]})
        endpoint = next(n for n in result["nodes"] if n.get("business_kind") == "endpoint")
        self.assertEqual(endpoint["authorization_evidence"]["status"], "NOT_DETECTED_STATIC_SCAN")
        self.assertEqual(endpoint["authorization_evidence"]["route_middleware"], [])
        self.assertTrue(endpoint["authorization_evidence"]["limitations"])

    def test_database_access_without_unique_caller_is_not_attached_to_function(self):
        source = graph()
        source["nodes"].append({
            "id": "file:server/repos/photo_repo.py", "type": "file",
            "label": "server/repos/photo_repo.py",
            "evidence": {"path": "server/repos/photo_repo.py"},
        })
        result = project_business_graph(source, {"database_accesses": [{
            "source": "server/repos/photo_repo.py", "line": 9,
            "callee": "db.execute", "operation": "UNKNOWN", "tables": [],
            "columns": [], "parse_status": "UNKNOWN",
        }]})
        access = next(n for n in result["nodes"] if n.get("business_kind") == "database_access")
        self.assertIsNone(access["business_details"]["owner_symbol_id"])
        self.assertEqual(access["business_details"]["owner_resolution_status"], "UNKNOWN")

    def test_duplicate_same_file_handler_symbols_remain_ambiguous(self):
        source = graph()
        for line in (30, 45):
            source["nodes"].append({
                "id": f"symbol:server/routes/photos.js#uploadPhoto:{line}",
                "type": "function", "label": "uploadPhoto",
                "evidence": {"path": "server/routes/photos.js", "line": line},
            })
        result = project_business_graph(source, {"endpoints": [{
            "method": "POST", "path": "/photos", "source": "server/routes/photos.js:20",
            "line": 20, "handler_reference": "uploadPhoto",
        }]})
        endpoint = next(n for n in result["nodes"] if n.get("business_kind") == "endpoint")
        self.assertEqual(endpoint["handler_association"]["resolution_status"], "AMBIGUOUS")
        self.assertEqual(len(endpoint["handler_association"]["candidate_targets"]), 2)
        self.assertFalse(any(e.get("method") == "route-handler-static-symbol-match" for e in result["edges"]))

    def test_member_handler_resolves_only_through_exact_compiler_binding(self):
        source = graph()
        source["nodes"].append({
            "id": "symbol:server/controllers/photos.js#uploadPhoto:12",
            "type": "function", "label": "uploadPhoto",
            "evidence": {"path": "server/controllers/photos.js", "line": 12},
        })
        source["edges"].append({
            "source": "file:server/routes/photos.js",
            "target": "symbol:server/controllers/photos.js#uploadPhoto:12",
            "type": "references", "method": "typescript-route-handler-binding",
            "resolution_status": "RESOLVED", "resolved": True,
            "evidence": {"path": "server/routes/photos.js", "line": 20,
                         "handler_reference": "photos.uploadPhoto"},
            "evidence_chain": [
                {"kind": "route_registration", "file": "server/routes/photos.js", "line": 20},
                {"kind": "handler_definition", "file": "server/controllers/photos.js", "line": 12},
            ],
        })
        result = project_business_graph(source, {"endpoints": [{
            "method": "POST", "path": "/photos", "source": "server/routes/photos.js:20",
            "line": 20, "handler_reference": "photos.uploadPhoto",
        }]})
        endpoint = next(n for n in result["nodes"] if n.get("business_kind") == "endpoint")
        self.assertEqual(endpoint["handler_association"]["resolution_status"], "RESOLVED")
        self.assertEqual(endpoint["handler_association"]["target_symbol_id"],
                         "symbol:server/controllers/photos.js#uploadPhoto:12")
        self.assertTrue(any(e.get("method") == "route-handler-static-symbol-match"
                            and e.get("target") == "symbol:server/controllers/photos.js#uploadPhoto:12"
                            for e in result["edges"]))

    def test_member_handler_is_not_guessed(self):
        source = graph()
        source["nodes"].append({
            "id": "symbol:server/controllers/photos.js#uploadPhoto:12",
            "type": "function", "label": "uploadPhoto",
            "evidence": {"path": "server/controllers/photos.js", "line": 12},
        })
        result = project_business_graph(source, {"endpoints": [{
            "method": "POST", "path": "/photos", "source": "server/routes/photos.js:20",
            "line": 20, "handler_reference": "photosController.uploadPhoto",
        }]})
        endpoint = next(n for n in result["nodes"] if n.get("business_kind") == "endpoint")
        self.assertEqual(endpoint["handler_association"]["resolution_status"], "UNKNOWN")
        self.assertFalse(any(e.get("method") == "route-handler-static-symbol-match" for e in result["edges"]))


    def test_s214_endpoint_centric_function_view_preserves_unlinked_feature_and_test_unknowns(self):
        source = graph()
        result = project_business_graph(source, {"features": [{"name": "Photo gallery", "module": "photos",
            "source": "requirements.md", "line": 4}], "endpoints": [{
            "method": "GET", "path": "/photos", "source": "server/routes/photos.js:3", "line": 3,
            "handler_reference": "listPhotos"}]})
        understanding = result["business_understanding"]
        self.assertEqual(understanding["business_function_view_count"], 1)
        view = understanding["business_function_views"][0]
        self.assertEqual(view["coverage"]["test_execution"], "NOT_MEASURED")
        self.assertEqual(view["coverage"]["frontend_static_linkage"], "NOT_LINKED")
        self.assertEqual(understanding["documented_feature_linkage"][0]["linkage_status"],
                         "NOT_LINKED_NO_EXPLICIT_FEATURE_ENDPOINT_RELATION")

if __name__ == "__main__":
    unittest.main()
