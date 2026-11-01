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
