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


if __name__ == "__main__":
    unittest.main()
