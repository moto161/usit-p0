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


def _api_edge(route_path, resolution="TEMPLATE_METHOD_PATH", caller=None,
              method="GET", target="api-client-call:server/routes/photos.js:2:0"):
    edge = {
        "source": "file:server/routes/photos.js", "target": target, "type": "references",
        "method": "typescript-api-client-call", "route_method": method,
        "route_path": route_path, "resolution_status": resolution,
        "evidence": {"path": "server/routes/photos.js", "line": 2},
    }
    if caller is not None:
        edge["caller_symbol_id"] = caller
    return edge


def _api_link_result(endpoint_paths, api_edges, extra_nodes=None, extra_edges=None):
    source = graph()
    for node in extra_nodes or []:
        source["nodes"].append(node)
    source["edges"].extend(api_edges)
    source["edges"].extend(extra_edges or [])
    semantics = {"endpoints": [
        {"method": "GET", "path": path, "source": "server/routes/photos.js", "line": 5}
        for path in endpoint_paths
    ]}
    result = project_business_graph(source, semantics)
    endpoints = [n for n in result["nodes"] if n.get("business_kind") == "endpoint"]
    return result, endpoints


_OWNER_SCOPE_LOAD = "symbol:repo/photos.py#load:1"


def _owner_scope_identity_record(evidence_id, kind, line, owner=None, details=None):
    record = {
        "evidence_id": evidence_id, "kind": kind,
        "source": "repo/photos.py", "line": line,
        "details": details or {"identity_expressions": ["request.user.id"]},
        "scope": "SAME_LINE_PATTERN",
    }
    if owner is not None:
        record["owner_symbol_id"] = owner
        record["owner_resolution_status"] = "RESOLVED"
    return record


def _owner_scope_db_graph(db_lines, identity_records, extra_symbols=()):
    """Python repo file: one function `load` (line 1) owns every DB call line."""
    load = _OWNER_SCOPE_LOAD
    source = graph()
    source["nodes"].extend([
        {"id": "file:repo/photos.py", "type": "file", "label": "repo/photos.py",
         "evidence": {"path": "repo/photos.py"}},
        {"id": load, "type": "function", "label": "load",
         "evidence": {"path": "repo/photos.py", "line": 1}},
    ])
    for symbol_id, path, line in extra_symbols:
        source["nodes"].append({
            "id": symbol_id, "type": "function", "label": symbol_id.rsplit("#", 1)[-1],
            "evidence": {"path": path, "line": line},
        })
    for line in db_lines:
        source["edges"].append({
            "source": "file:repo/photos.py", "target": "external:db", "type": "calls",
            "caller_symbol_id": load, "resolution_status": "EXTERNAL",
            "evidence": {"path": "repo/photos.py", "line": line},
        })
    semantics = {
        "endpoints": [],
        "database_accesses": [{
            "source": "repo/photos.py", "line": line, "callee": "cursor.execute",
            "operation": "SELECT", "tables": ["photos"], "columns": ["id", "owner_id"],
            "parse_status": "RESOLVED",
        } for line in db_lines],
        "authorization_checks": [],
        "identity_evidence": identity_records,
    }
    return project_business_graph(source, semantics), load


def _owner_scope_endpoint_graph(identity_records):
    """Route handler list_photos calls repo function load; load runs the DB call."""
    route_path = "server/routes/photos.py"
    repo_path = "repo/photos.py"
    handler = "symbol:" + route_path + "#list_photos:2"
    load = _OWNER_SCOPE_LOAD
    source = graph()
    source["nodes"].extend([
        {"id": "file:" + route_path, "type": "file", "label": route_path,
         "evidence": {"path": route_path}},
        {"id": "file:" + repo_path, "type": "file", "label": repo_path,
         "evidence": {"path": repo_path}},
        {"id": handler, "type": "function", "label": "list_photos",
         "evidence": {"path": route_path, "line": 2}},
        {"id": load, "type": "function", "label": "load",
         "evidence": {"path": repo_path, "line": 1}},
    ])
    source["edges"].extend([
        {"source": "file:" + route_path, "target": load, "type": "calls",
         "caller_symbol_id": handler, "resolution_status": "RESOLVED",
         "evidence": {"path": route_path, "line": 5}},
        {"source": "file:" + repo_path, "target": "external:db", "type": "calls",
         "caller_symbol_id": load, "resolution_status": "EXTERNAL",
         "evidence": {"path": repo_path, "line": 9}},
    ])
    semantics = {
        "endpoints": [{"method": "GET", "path": "/photos",
                       "source": route_path + ":3", "line": 3,
                       "handler_reference": "list_photos"}],
        "database_accesses": [{"source": repo_path, "line": 9, "callee": "cursor.execute",
                               "operation": "SELECT", "tables": ["photos"],
                               "columns": ["id", "owner_id"], "parse_status": "RESOLVED"}],
        "authorization_checks": [],
        "identity_evidence": identity_records,
    }
    return project_business_graph(source, semantics), handler, load


_TS_OWNER_SCOPE_LOAD = "symbol:web/photos.ts#loadPhotos:3"


def _ts_owner_scope_identity_record(evidence_id, kind, line, owner=None, details=None):
    record = {
        "evidence_id": evidence_id, "kind": kind,
        "source": "web/photos.ts", "line": line,
        "details": details or {"identity_expressions": ["req.user.id"]},
        "scope": "SAME_LINE_PATTERN",
    }
    if owner is not None:
        record["owner_symbol_id"] = owner
        record["owner_resolution_status"] = "RESOLVED"
    return record


def _ts_owner_scope_db_graph(db_lines, identity_records, extra_symbols=()):
    """TS repo file: function `loadPhotos` (line 3) owns every DB call line."""
    load = _TS_OWNER_SCOPE_LOAD
    source = graph()
    source["nodes"].extend([
        {"id": "file:web/photos.ts", "type": "file", "label": "web/photos.ts",
         "evidence": {"path": "web/photos.ts"}},
        {"id": load, "type": "function", "label": "loadPhotos",
         "evidence": {"path": "web/photos.ts", "line": 3}},
    ])
    for symbol_id, path, line in extra_symbols:
        source["nodes"].append({
            "id": symbol_id, "type": "function", "label": symbol_id.rsplit("#", 1)[-1],
            "evidence": {"path": path, "line": line},
        })
    for line in db_lines:
        source["edges"].append({
            "source": "file:web/photos.ts", "target": "external:db", "type": "calls",
            "caller_symbol_id": load, "resolution_status": "EXTERNAL",
            "evidence": {"path": "web/photos.ts", "line": line},
        })
    semantics = {
        "endpoints": [],
        "database_accesses": [{
            "source": "web/photos.ts", "line": line, "callee": "db.query",
            "operation": "SELECT", "tables": ["photos"], "columns": ["id", "owner_id"],
            "parse_status": "RESOLVED",
        } for line in db_lines],
        "authorization_checks": [],
        "identity_evidence": identity_records,
    }
    return project_business_graph(source, semantics), load


def _ts_owner_scope_endpoint_graph(identity_records):
    """Route handler getPhotos calls repo function loadPhotos; loadPhotos runs the DB call."""
    route_path = "web/routes.ts"
    repo_path = "web/photos.ts"
    handler = "symbol:" + route_path + "#getPhotos:2"
    load = _TS_OWNER_SCOPE_LOAD
    source = graph()
    source["nodes"].extend([
        {"id": "file:" + route_path, "type": "file", "label": route_path,
         "evidence": {"path": route_path}},
        {"id": "file:" + repo_path, "type": "file", "label": repo_path,
         "evidence": {"path": repo_path}},
        {"id": handler, "type": "function", "label": "getPhotos",
         "evidence": {"path": route_path, "line": 2}},
        {"id": load, "type": "function", "label": "loadPhotos",
         "evidence": {"path": repo_path, "line": 3}},
    ])
    source["edges"].extend([
        {"source": "file:" + route_path, "target": load, "type": "calls",
         "caller_symbol_id": handler, "resolution_status": "RESOLVED",
         "evidence": {"path": route_path, "line": 5}},
        {"source": "file:" + repo_path, "target": "external:db", "type": "calls",
         "caller_symbol_id": load, "resolution_status": "EXTERNAL",
         "evidence": {"path": repo_path, "line": 6}},
    ])
    semantics = {
        "endpoints": [{"method": "GET", "path": "/photos",
                       "source": route_path + ":3", "line": 3,
                       "handler_reference": "getPhotos"}],
        "database_accesses": [{"source": repo_path, "line": 6, "callee": "db.query",
                               "operation": "SELECT", "tables": ["photos"],
                               "columns": ["id", "owner_id"], "parse_status": "RESOLVED"}],
        "authorization_checks": [],
        "identity_evidence": identity_records,
    }
    return project_business_graph(source, semantics), handler, load


def _entity_link_graph(entities, accesses):
    """Graph with repo/photos.py plus the docs files entities cite."""
    source = graph()
    for path in ("repo/photos.py", "docs/schema.md", "docs/other.md"):
        source["nodes"].append({
            "id": "file:" + path, "type": "file", "label": path,
            "evidence": {"path": path},
        })
    semantics = {
        "endpoints": [],
        "entities": entities,
        "database_accesses": accesses,
        "authorization_checks": [],
        "identity_evidence": [],
    }
    return project_business_graph(source, semantics)


def _db_access(line, tables, parse_status="RESOLVED"):
    return {"source": "repo/photos.py", "line": line, "callee": "cursor.execute",
            "operation": "SELECT", "tables": tables, "columns": ["id"],
            "parse_status": parse_status}


def _db_access_nodes(result):
    nodes = [n for n in result["nodes"] if n.get("business_kind") == "database_access"]
    return sorted(nodes, key=lambda n: n["evidence"]["line"])


def _entity_ids(result, label):
    return sorted(n["id"] for n in result["nodes"]
                  if n.get("business_kind") == "entity" and n.get("label") == label)


def _view_access(tables, line=22, parse_status="RESOLVED"):
    return {"source": "server/repos/photo_repo.py", "line": line,
            "callee": "db.execute", "operation": "SELECT", "tables": tables,
            "columns": ["id", "owner_id"], "parse_status": parse_status,
            "parser": "python-ast-single-line"}


def _view_graph(entities, accesses):
    """Endpoint + handler + repository fixture for function-view tests."""
    source = graph()
    source["nodes"].extend([
        {"id": "file:server/repos/photo_repo.py", "type": "file",
         "label": "server/repos/photo_repo.py",
         "evidence": {"path": "server/repos/photo_repo.py"}},
        {"id": "file:docs/schema.md", "type": "file", "label": "docs/schema.md",
         "evidence": {"path": "docs/schema.md"}},
        {"id": "file:docs/other.md", "type": "file", "label": "docs/other.md",
         "evidence": {"path": "docs/other.md"}},
        {"id": "external:db.execute", "type": "external", "label": "execute",
         "evidence": {"note": "external database client"}},
    ])
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
        "entities": entities,
        "endpoints": [{"method": "GET", "path": "/photos",
                       "source": "server/routes/photos.js:5", "line": 5,
                       "handler_reference": "listPhotos"}],
        "database_accesses": accesses,
    }
    return project_business_graph(source, semantics)


def _function_view(result):
    views = result["business_understanding"]["business_function_views"]
    assert len(views) == 1
    return views[0]


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

    def test_depth_truncation_records_the_cut_edge(self):
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
        self.assertEqual(association["call_chain_max_depth"], 8)
        self.assertEqual(association["call_chain_max_chains"], 100)
        frontier = association["call_chain_truncated_frontier"]
        self.assertEqual(len(frontier), 1)
        self.assertEqual(frontier[0]["reason"], "DEPTH_LIMIT")
        self.assertEqual(frontier[0]["caller_symbol_id"], symbols[8])
        self.assertEqual(frontier[0]["target_id"], symbols[9])
        self.assertEqual(frontier[0]["call_site"], {"path": "module_8.py", "line": 48})
        self.assertEqual(len(association["call_chain_evidence"][0]["steps"]), 8)
        self.assertTrue(association["call_chain_evidence"][0]["depth_limit_reached"])

    def test_chain_count_truncation_records_the_triggering_edge(self):
        source = graph()
        handler = "symbol:server/routes/photos.js#uploadPhoto:35"
        source["nodes"].append({
            "id": handler, "type": "function", "label": "uploadPhoto",
            "evidence": {"path": "server/routes/photos.js", "line": 35},
        })
        for index in range(101):
            leaf = f"symbol:leaf.py#leaf_{index}:1"
            source["nodes"].append({
                "id": leaf, "type": "function", "label": f"leaf_{index}",
                "evidence": {"path": "leaf.py", "line": 1},
            })
            source["edges"].append({
                "source": "file:server/routes/photos.js", "target": leaf,
                "type": "calls", "caller_symbol_id": handler,
                "resolution_status": "RESOLVED", "resolved": True,
                "evidence": {"path": "server/routes/photos.js", "line": 40 + index},
                "method": "typescript-compiler-api", "confidence": "high",
            })
        result = project_business_graph(source, {"endpoints": [{
            "method": "POST", "path": "/photos", "source": "server/routes/photos.js:20",
            "line": 20, "handler_reference": "uploadPhoto",
        }]})
        endpoint = next(n for n in result["nodes"] if n.get("business_kind") == "endpoint")
        association = endpoint["handler_association"]
        self.assertTrue(association["call_chain_truncated"])
        self.assertEqual(len(association["call_chain_evidence"]), 100)
        frontier = association["call_chain_truncated_frontier"]
        self.assertEqual(len(frontier), 1)
        self.assertEqual(frontier[0]["reason"], "CHAIN_LIMIT")
        self.assertEqual(frontier[0]["caller_symbol_id"], handler)
        self.assertEqual(frontier[0]["target_id"], "symbol:leaf.py#leaf_100:1")
        self.assertEqual(frontier[0]["call_site"],
                         {"path": "server/routes/photos.js", "line": 140})

    def test_untruncated_chain_reports_an_empty_frontier(self):
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
        ])
        result = project_business_graph(source, {"endpoints": [{
            "method": "POST", "path": "/photos", "source": "server/routes/photos.js:20",
            "line": 20, "handler_reference": "uploadPhoto",
        }]})
        endpoint = next(n for n in result["nodes"] if n.get("business_kind") == "endpoint")
        association = endpoint["handler_association"]
        self.assertFalse(association["call_chain_truncated"])
        self.assertEqual(association["call_chain_truncated_frontier"], [])
        self.assertEqual(association["call_chain_max_chains"], 100)
        paths = result["business_understanding"]["endpoint_execution_paths"]
        self.assertEqual(len(paths), 1)
        self.assertEqual(paths[0]["call_chain_truncated_frontier"], [])

    def test_deep_database_access_attaches_despite_chain_truncation(self):
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
            source["nodes"].append({
                "id": "file:" + path, "type": "file", "label": path,
                "evidence": {"path": path},
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
        deep = symbols[-1]
        source["edges"].append({
            "source": "file:module_9.py", "target": "external:db.execute",
            "type": "calls", "caller_symbol_id": deep, "resolution_status": "EXTERNAL",
            "evidence": {"path": "module_9.py", "line": 99},
        })
        result = project_business_graph(source, {
            "endpoints": [{
                "method": "POST", "path": "/photos",
                "source": "server/routes/photos.js:20", "line": 20,
                "handler_reference": "uploadPhoto",
            }],
            "database_accesses": [{
                "source": "module_9.py", "line": 99,
                "callee": "db.execute", "operation": "SELECT", "tables": ["photos"],
                "columns": ["id"], "parse_status": "RESOLVED",
                "parser": "python-ast-single-line",
            }],
        })
        endpoint = next(n for n in result["nodes"] if n.get("business_kind") == "endpoint")
        association = endpoint["handler_association"]
        # Chain detail still truncates at depth 8, but the database evidence
        # must not be lost with it.
        self.assertTrue(association["call_chain_truncated"])
        self.assertEqual(len(association["call_chain_evidence"][0]["steps"]), 8)
        self.assertEqual(association["reachable_symbol_count"], 10)
        self.assertEqual(association["reachable_max_depth"], 9)
        evidence = association["database_access_evidence"]
        self.assertEqual(len(evidence), 1)
        self.assertEqual(evidence[0]["owner_symbol_id"], deep)
        self.assertEqual(evidence[0]["tables"], ["photos"])

    def test_wide_fanout_database_access_attaches_despite_chain_cap(self):
        source = graph()
        handler = "symbol:server/routes/photos.js#uploadPhoto:35"
        leaf_path = "server/leaves.py"
        source["nodes"].append({
            "id": handler, "type": "function", "label": "uploadPhoto",
            "evidence": {"path": "server/routes/photos.js", "line": 35},
        })
        source["nodes"].append({
            "id": "file:" + leaf_path, "type": "file", "label": leaf_path,
            "evidence": {"path": leaf_path},
        })
        leaves = [f"symbol:{leaf_path}#leaf_{index}:{index + 1}" for index in range(150)]
        for index, leaf in enumerate(leaves):
            source["nodes"].append({
                "id": leaf, "type": "function", "label": f"leaf_{index}",
                "evidence": {"path": leaf_path, "line": index + 1},
            })
            source["edges"].append({
                "source": "file:server/routes/photos.js", "target": leaf,
                "type": "calls", "caller_symbol_id": handler,
                "resolution_status": "RESOLVED", "resolved": True,
                "evidence": {"path": "server/routes/photos.js", "line": 40 + index},
                "method": "typescript-compiler-api", "confidence": "high",
            })
        last_leaf = leaves[-1]
        source["edges"].append({
            "source": "file:" + leaf_path, "target": "external:db.execute",
            "type": "calls", "caller_symbol_id": last_leaf,
            "resolution_status": "EXTERNAL",
            "evidence": {"path": leaf_path, "line": 200},
        })
        result = project_business_graph(source, {
            "endpoints": [{
                "method": "POST", "path": "/photos",
                "source": "server/routes/photos.js:20", "line": 20,
                "handler_reference": "uploadPhoto",
            }],
            "database_accesses": [{
                "source": leaf_path, "line": 200,
                "callee": "db.execute", "operation": "SELECT", "tables": ["photos"],
                "columns": ["id"], "parse_status": "RESOLVED",
                "parser": "python-ast-single-line",
            }],
        })
        endpoint = next(n for n in result["nodes"] if n.get("business_kind") == "endpoint")
        association = endpoint["handler_association"]
        self.assertTrue(association["call_chain_truncated"])
        self.assertEqual(len(association["call_chain_evidence"]), 100)
        self.assertEqual(association["reachable_symbol_count"], 151)
        evidence = association["database_access_evidence"]
        self.assertEqual(len(evidence), 1)
        self.assertEqual(evidence[0]["owner_symbol_id"], last_leaf)

    def test_database_access_linked_table_records_entity_id(self):
        result = _entity_link_graph(
            [{"name": "albums", "source": "docs/schema.md", "fields": [{"name": "id"}]}],
            [_db_access(9, ["albums"])],
        )
        nodes = _db_access_nodes(result)
        self.assertEqual(len(nodes), 1)
        self.assertEqual(nodes[0]["business_details"]["documented_entity_link"], [
            {"table": "albums", "status": "LINKED",
             "entity_ids": _entity_ids(result, "albums")},
        ])
        edges = [e for e in result["edges"]
                 if e.get("method") == "database-access-to-documented-entity"]
        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0]["evidence"]["table"], "albums")
        self.assertEqual(result["business_understanding"]["database_access_table_links"], 1)

    def test_database_access_undocumented_table_records_no_entity(self):
        result = _entity_link_graph(
            [{"name": "albums", "source": "docs/schema.md", "fields": [{"name": "id"}]}],
            [_db_access(10, ["tags"])],
        )
        nodes = _db_access_nodes(result)
        self.assertEqual(nodes[0]["business_details"]["documented_entity_link"], [
            {"table": "tags", "status": "NO_DOCUMENTED_ENTITY", "entity_ids": []},
        ])
        edges = [e for e in result["edges"]
                 if e.get("method") == "database-access-to-documented-entity"]
        self.assertEqual(edges, [])
        summary = result["business_understanding"]
        self.assertEqual(summary["database_access_table_links"], 0)
        self.assertEqual(summary["database_access_table_link_no_documented_entity"], 1)
        self.assertEqual(summary["database_access_table_link_ambiguous_entity"], 0)

    def test_database_access_ambiguous_table_lists_candidates_without_linking(self):
        result = _entity_link_graph(
            [{"name": "photos", "source": "docs/schema.md", "fields": [{"name": "id"}]},
             {"name": "photos", "source": "docs/other.md", "fields": [{"name": "id"}]}],
            [_db_access(11, ["photos"])],
        )
        nodes = _db_access_nodes(result)
        self.assertEqual(nodes[0]["business_details"]["documented_entity_link"], [
            {"table": "photos", "status": "AMBIGUOUS_ENTITY",
             "entity_ids": _entity_ids(result, "photos")},
        ])
        self.assertEqual(len(_entity_ids(result, "photos")), 2)
        edges = [e for e in result["edges"]
                 if e.get("method") == "database-access-to-documented-entity"]
        self.assertEqual(edges, [])
        summary = result["business_understanding"]
        self.assertEqual(summary["database_access_table_links"], 0)
        self.assertEqual(summary["database_access_table_link_no_documented_entity"], 0)
        self.assertEqual(summary["database_access_table_link_ambiguous_entity"], 1)

    def test_database_access_mixed_and_unparsed_tables_record_outcomes(self):
        result = _entity_link_graph(
            [{"name": "albums", "source": "docs/schema.md", "fields": [{"name": "id"}]},
             {"name": "photos", "source": "docs/schema.md", "fields": [{"name": "id"}]},
             {"name": "photos", "source": "docs/other.md", "fields": [{"name": "id"}]}],
            [_db_access(9, ["albums"]),
             _db_access(10, ["tags"]),
             _db_access(11, ["photos"]),
             _db_access(12, [], parse_status="UNKNOWN"),
             _db_access(13, ["albums", "tags"])],
        )
        nodes = _db_access_nodes(result)
        self.assertEqual(len(nodes), 5)
        albums_id = _entity_ids(result, "albums")
        photos_ids = _entity_ids(result, "photos")
        self.assertEqual(nodes[0]["business_details"]["documented_entity_link"], [
            {"table": "albums", "status": "LINKED", "entity_ids": albums_id},
        ])
        self.assertEqual(nodes[1]["business_details"]["documented_entity_link"], [
            {"table": "tags", "status": "NO_DOCUMENTED_ENTITY", "entity_ids": []},
        ])
        self.assertEqual(nodes[2]["business_details"]["documented_entity_link"], [
            {"table": "photos", "status": "AMBIGUOUS_ENTITY", "entity_ids": photos_ids},
        ])
        self.assertEqual(nodes[3]["business_details"]["documented_entity_link"], [])
        self.assertEqual(nodes[4]["business_details"]["documented_entity_link"], [
            {"table": "albums", "status": "LINKED", "entity_ids": albums_id},
            {"table": "tags", "status": "NO_DOCUMENTED_ENTITY", "entity_ids": []},
        ])
        summary = result["business_understanding"]
        self.assertEqual(summary["database_access_table_links"], 2)
        self.assertEqual(summary["database_access_table_link_no_documented_entity"], 2)
        self.assertEqual(summary["database_access_table_link_ambiguous_entity"], 1)

    def test_unreachable_database_access_is_not_attached(self):
        source = graph()
        handler = "symbol:server/routes/photos.js#listPhotos:10"
        service = "symbol:server/services/photo_service.py#listPhotos:4"
        orphan = "symbol:server/repos/photo_repo.py#load:4"
        for path in ("server/services/photo_service.py", "server/repos/photo_repo.py"):
            source["nodes"].append({
                "id": "file:" + path, "type": "file", "label": path,
                "evidence": {"path": path},
            })
        source["nodes"].extend([
            {"id": handler, "type": "function", "label": "listPhotos",
             "evidence": {"path": "server/routes/photos.js", "line": 10}},
            {"id": service, "type": "function", "label": "listPhotos",
             "evidence": {"path": "server/services/photo_service.py", "line": 4}},
            {"id": orphan, "type": "function", "label": "load",
             "evidence": {"path": "server/repos/photo_repo.py", "line": 4}},
        ])
        source["edges"].extend([
            {"source": "file:server/routes/photos.js", "target": service,
             "type": "calls", "caller_symbol_id": handler,
             "resolution_status": "RESOLVED",
             "evidence": {"path": "server/routes/photos.js", "line": 15}},
            {"source": "file:server/repos/photo_repo.py", "target": "external:db.execute",
             "type": "calls", "caller_symbol_id": orphan,
             "resolution_status": "EXTERNAL",
             "evidence": {"path": "server/repos/photo_repo.py", "line": 22}},
        ])
        result = project_business_graph(source, {
            "endpoints": [{
                "method": "GET", "path": "/photos",
                "source": "server/routes/photos.js:20", "line": 20,
                "handler_reference": "listPhotos",
            }],
            "database_accesses": [{
                "source": "server/repos/photo_repo.py", "line": 22,
                "callee": "db.execute", "operation": "SELECT", "tables": ["photos"],
                "columns": ["id"], "parse_status": "RESOLVED",
                "parser": "python-ast-single-line",
            }],
        })
        # The orphan's access exists in the graph with a resolved owner, but
        # it is not reachable from the handler, so the endpoint must not claim it.
        access = next(n for n in result["nodes"] if n.get("business_kind") == "database_access")
        self.assertEqual(access["business_details"]["owner_symbol_id"], orphan)
        endpoint = next(n for n in result["nodes"] if n.get("business_kind") == "endpoint")
        self.assertEqual(endpoint["handler_association"]["database_access_evidence"], [])

    def test_identity_propagates_beyond_chain_depth_limit(self):
        source = graph()
        route_path = "server/routes/photos.py"
        symbols = ["symbol:" + route_path + "#listPhotos:10"] + [
            f"symbol:module_{index}.py#step_{index}:{index + 1}"
            for index in range(9)
        ]
        for index, symbol in enumerate(symbols):
            path = route_path if index == 0 else f"module_{index}.py"
            name = "listPhotos" if index == 0 else f"step_{index - 1}"
            source["nodes"].append({
                "id": symbol, "type": "function", "label": name,
                "evidence": {"path": path, "line": 10 if index == 0 else index},
            })
            source["nodes"].append({
                "id": "file:" + path, "type": "file", "label": path,
                "evidence": {"path": path},
            })

        def call_edge(caller, callee, path, line, expression, parameter):
            return {
                "source": "file:" + path, "target": callee, "type": "calls",
                "caller_symbol_id": caller, "resolution_status": "RESOLVED",
                "evidence": {"path": path, "line": line},
                "argument_binding_status": "EXACT_STATIC_MAPPING",
                "argument_bindings": [{
                    "argument_index": 0, "argument_kind": "POSITIONAL",
                    "argument_expression": expression,
                    "argument_location": {"path": path, "line": line, "column": 20},
                    "parameter_name": parameter, "parameter_index": 0,
                    "parameter_kind": "positional_or_keyword",
                    "parameter_declaration": {"path": path, "line": line, "column": 24},
                }],
                "target_location": {"path": path, "line": line},
            }

        for index in range(len(symbols) - 1):
            path = route_path if index == 0 else f"module_{index}.py"
            expression = "uid" if index == 0 else f"p{index}"
            source["edges"].append(call_edge(
                symbols[index], symbols[index + 1], path, 14 + index,
                expression, f"p{index + 1}"))
        result = project_business_graph(source, {
            "endpoints": [{
                "method": "GET", "path": "/photos",
                "source": route_path + ":5", "line": 5,
                "handler_reference": "listPhotos",
            }],
            "identity_evidence": [{
                "evidence_id": "identity-source-deep",
                "kind": "authenticated_identity_source",
                "source": route_path, "line": 12,
                "details": {"identity_expressions": ["request.user.id"],
                            "source_variable": "uid"},
                "scope": "SAME_LINE_PATTERN",
                "confidence": "STATIC_INFERENCE",
                "verification_status": "NOT_VERIFIED",
                "owner_symbol_id": symbols[0],
                "owner_resolution_status": "RESOLVED",
            }, {
                "evidence_id": "sql-owner-binding-deep",
                "kind": "sql_owner_parameter_binding",
                "source": "module_9.py", "line": 11,
                "details": {"binding_parameters": ["p9"],
                            "resource_fields": ["owner_id"],
                            "binding_status": "EXACT_BARE_IDENTIFIER_ARGUMENT"},
                "scope": "SAME_LINE_PATTERN",
                "confidence": "STATIC_INFERENCE",
                "verification_status": "NOT_VERIFIED",
                "owner_symbol_id": symbols[-1],
                "owner_resolution_status": "RESOLVED",
            }],
        })
        endpoint = next(n for n in result["nodes"] if n.get("business_kind") == "endpoint")
        association = endpoint["handler_association"]
        self.assertTrue(association["call_chain_truncated"])
        propagated = association["identity_parameter_propagation_evidence"]
        self.assertEqual(len(propagated), 9)
        by_depth = {item["propagation_depth"]: item for item in propagated}
        self.assertEqual(by_depth[9]["parameter_name"], "p9")
        self.assertEqual(by_depth[9]["propagation_status"],
                         "MULTI_HOP_STATIC_PARAMETER_MAPPING")
        self.assertEqual(len(by_depth[9]["propagation_chain"]), 9)
        sql_links = association["identity_sql_sink_evidence"]
        self.assertEqual(len(sql_links), 1)
        self.assertEqual(sql_links[0]["binding_parameter"], "p9")
        self.assertEqual(sql_links[0]["binding_status"],
                         "PROPAGATED_PARAMETER_TO_SQL_OWNER_BINDING")

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

    def test_database_access_carries_has_dynamic_parts_into_node_details(self):
        source = graph()
        source["nodes"].append({
            "id": "file:server/repos/photo_repo.py", "type": "file",
            "label": "server/repos/photo_repo.py",
            "evidence": {"path": "server/repos/photo_repo.py"},
        })
        result = project_business_graph(source, {"database_accesses": [
            {"source": "server/repos/photo_repo.py", "line": 9,
             "callee": "db.execute", "operation": "SELECT", "tables": ["photos"],
             "columns": ["id"], "parse_status": "RESOLVED",
             "has_dynamic_parts": True},
            {"source": "server/repos/photo_repo.py", "line": 12,
             "callee": "db.execute", "operation": "SELECT", "tables": ["photos"],
             "columns": ["id"], "parse_status": "RESOLVED"},
        ]})
        accesses = [n for n in result["nodes"] if n.get("business_kind") == "database_access"]
        by_line = {n["evidence"]["line"]: n for n in accesses}
        self.assertTrue(by_line[9]["business_details"]["has_dynamic_parts"])
        # A record from before the flag existed defaults to False.
        self.assertFalse(by_line[12]["business_details"]["has_dynamic_parts"])

    def test_template_skeleton_matches_single_route_parameter_endpoint(self):
        result, endpoints = _api_link_result(["/photos/:id"], [_api_edge("/photos/{?}")])
        links = result["business_understanding"]["frontend_api_endpoint_links"]
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0]["resolution_status"], "TEMPLATE_MATCH")
        self.assertEqual(links[0]["endpoint_node_id"], endpoints[0]["id"])
        self.assertEqual(links[0]["path"], "/photos/{?}")

    def test_template_skeleton_with_two_same_shape_endpoints_is_ambiguous(self):
        result, endpoints = _api_link_result(
            ["/photos/:id", "/photos/:photoId"], [_api_edge("/photos/{?}")])
        links = result["business_understanding"]["frontend_api_endpoint_links"]
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0]["resolution_status"], "AMBIGUOUS")
        self.assertEqual(links[0]["candidate_endpoint_ids"],
                         sorted(n["id"] for n in endpoints))

    def test_template_skeleton_without_match_is_no_match(self):
        result, _endpoints = _api_link_result(["/photos/:id"], [_api_edge("/api/unknown/{?}")])
        links = result["business_understanding"]["frontend_api_endpoint_links"]
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0]["resolution_status"], "NO_MATCH")

    def test_partial_segment_placeholder_does_not_match(self):
        result, _endpoints = _api_link_result(["/photos/:id"], [_api_edge("/photos/prefix-{?}")])
        links = result["business_understanding"]["frontend_api_endpoint_links"]
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0]["resolution_status"], "NO_MATCH")

    def test_template_skeleton_matches_brace_style_route_parameter(self):
        result, endpoints = _api_link_result(["/photos/{id}"], [_api_edge("/photos/{?}")])
        links = result["business_understanding"]["frontend_api_endpoint_links"]
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0]["resolution_status"], "TEMPLATE_MATCH")
        self.assertEqual(links[0]["endpoint_node_id"], endpoints[0]["id"])

    def test_vue_template_binding_joins_frontend_chain(self):
        handler = "symbol:web/PhotoCard.vue#savePhoto:7"
        binding_edge = {
            "source": "file:web/PhotoCard.vue", "target": handler, "type": "references",
            "method": "vue-template-event-handler-binding",
            "resolution_status": "RESOLVED", "event_name": "click",
            "binding_expression": "savePhoto",
            "evidence": {"path": "web/PhotoCard.vue", "line": 2},
        }
        result, endpoints = _api_link_result(
            ["/photos"], [_api_edge("/photos", resolution="LITERAL_METHOD_PATH", caller=handler)],
            extra_nodes=[{"id": "file:web/PhotoCard.vue", "type": "file",
                          "label": "web/PhotoCard.vue", "evidence": {"path": "web/PhotoCard.vue"}}],
            extra_edges=[binding_edge])
        chains = result["business_understanding"]["frontend_business_chains"]
        self.assertEqual(len(chains), 1)
        self.assertEqual(chains[0]["resolution_status"], "EXACT_MATCH")
        self.assertEqual(chains[0]["endpoint_node_id"], endpoints[0]["id"])
        self.assertEqual(chains[0]["handler_symbol_id"], handler)

    def test_angular_template_binding_joins_frontend_chain(self):
        handler = "symbol:web/photo-card.component.ts#savePhoto:11"
        binding_edge = {
            "source": "file:web/photo-card.component.html", "target": handler, "type": "references",
            "method": "angular-template-event-handler-binding",
            "resolution_status": "RESOLVED", "event_name": "click",
            "binding_expression": "savePhoto()",
            "evidence": {"path": "web/photo-card.component.html", "line": 1},
        }
        result, endpoints = _api_link_result(
            ["/api/photos"], [_api_edge("/api/photos", resolution="LITERAL_METHOD_PATH", caller=handler)],
            extra_nodes=[{"id": "file:web/photo-card.component.html", "type": "file",
                          "label": "web/photo-card.component.html", "evidence": {"path": "web/photo-card.component.html"}}],
            extra_edges=[binding_edge])
        chains = result["business_understanding"]["frontend_business_chains"]
        self.assertEqual(len(chains), 1)
        self.assertEqual(chains[0]["resolution_status"], "EXACT_MATCH")
        self.assertEqual(chains[0]["endpoint_node_id"], endpoints[0]["id"])
        self.assertEqual(chains[0]["handler_symbol_id"], handler)

    def test_frontend_chain_includes_template_match(self):
        handler = "symbol:web/app.tsx#loadPhoto:8"
        binding_edge = {
            "source": "file:web/app.tsx", "target": handler, "type": "references",
            "method": "typescript-jsx-event-handler-binding",
            "resolution_status": "RESOLVED", "event_name": "onClick",
            "binding_expression": "onClick={loadPhoto}",
            "evidence": {"path": "web/app.tsx", "line": 10},
        }
        result, endpoints = _api_link_result(
            ["/photos/:id"], [_api_edge("/photos/{?}", caller=handler)],
            extra_nodes=[{"id": "file:web/app.tsx", "type": "file",
                          "label": "web/app.tsx", "evidence": {"path": "web/app.tsx"}}],
            extra_edges=[binding_edge])
        chains = result["business_understanding"]["frontend_business_chains"]
        self.assertEqual(len(chains), 1)
        self.assertEqual(chains[0]["resolution_status"], "TEMPLATE_MATCH")
        self.assertEqual(chains[0]["endpoint_node_id"], endpoints[0]["id"])
        self.assertEqual(chains[0]["handler_symbol_id"], handler)

    def test_literal_value_matches_single_route_parameter_endpoint(self):
        result, endpoints = _api_link_result(
            ["/photos/:id"], [_api_edge("/photos/123", resolution="LITERAL_METHOD_PATH")])
        links = result["business_understanding"]["frontend_api_endpoint_links"]
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0]["resolution_status"], "VALUE_MATCH")
        self.assertEqual(links[0]["endpoint_node_id"], endpoints[0]["id"])
        self.assertEqual(links[0]["path"], "/photos/123")

    def test_exact_match_wins_over_value_match(self):
        result, endpoints = _api_link_result(
            ["/photos/list", "/photos/:id"],
            [_api_edge("/photos/list", resolution="LITERAL_METHOD_PATH")])
        links = result["business_understanding"]["frontend_api_endpoint_links"]
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0]["resolution_status"], "EXACT_MATCH")
        self.assertEqual(links[0]["endpoint_node_id"], endpoints[0]["id"])

    def test_literal_value_with_two_same_shape_endpoints_is_ambiguous(self):
        result, endpoints = _api_link_result(
            ["/photos/:id", "/photos/:photoId"],
            [_api_edge("/photos/123", resolution="LITERAL_METHOD_PATH")])
        links = result["business_understanding"]["frontend_api_endpoint_links"]
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0]["resolution_status"], "AMBIGUOUS")
        self.assertEqual(links[0]["candidate_endpoint_ids"],
                         sorted(n["id"] for n in endpoints))

    def test_literal_value_without_param_endpoint_is_no_match(self):
        result, _endpoints = _api_link_result(
            ["/photos/list"], [_api_edge("/photos/123", resolution="LITERAL_METHOD_PATH")])
        links = result["business_understanding"]["frontend_api_endpoint_links"]
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0]["resolution_status"], "NO_MATCH")

    def test_literal_value_matches_brace_style_route_parameter(self):
        result, endpoints = _api_link_result(
            ["/photos/{id}"], [_api_edge("/photos/123", resolution="LITERAL_METHOD_PATH")])
        links = result["business_understanding"]["frontend_api_endpoint_links"]
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0]["resolution_status"], "VALUE_MATCH")
        self.assertEqual(links[0]["endpoint_node_id"], endpoints[0]["id"])

    def test_frontend_chain_includes_value_match(self):
        handler = "symbol:web/app.tsx#loadPhoto:8"
        binding_edge = {
            "source": "file:web/app.tsx", "target": handler, "type": "references",
            "method": "typescript-jsx-event-handler-binding",
            "resolution_status": "RESOLVED", "event_name": "onClick",
            "binding_expression": "onClick={loadPhoto}",
            "evidence": {"path": "web/app.tsx", "line": 10},
        }
        result, endpoints = _api_link_result(
            ["/photos/:id"],
            [_api_edge("/photos/123", resolution="LITERAL_METHOD_PATH", caller=handler)],
            extra_nodes=[{"id": "file:web/app.tsx", "type": "file",
                          "label": "web/app.tsx", "evidence": {"path": "web/app.tsx"}}],
            extra_edges=[binding_edge])
        chains = result["business_understanding"]["frontend_business_chains"]
        self.assertEqual(len(chains), 1)
        self.assertEqual(chains[0]["resolution_status"], "VALUE_MATCH")
        self.assertEqual(chains[0]["endpoint_node_id"], endpoints[0]["id"])
        self.assertEqual(chains[0]["handler_symbol_id"], handler)

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

    def test_function_view_lists_undocumented_table(self):
        result = _view_graph(
            [{"name": "photos", "source": "docs/schema.md",
              "fields": [{"name": "id"}, {"name": "owner_id"}]}],
            [_view_access(["tags"])],
        )
        view = _function_view(result)
        access_id = view["database_accesses"][0]["database_access_id"]
        self.assertEqual(view["documented_entity_links"], [])
        self.assertEqual(view["unlinked_database_tables"], [
            {"table": "tags", "status": "NO_DOCUMENTED_ENTITY", "entity_ids": [],
             "database_access_id": access_id,
             "path": "server/repos/photo_repo.py", "line": 22},
        ])
        self.assertEqual(view["coverage"]["database_entity_linkage"], "NOT_LINKED")

    def test_function_view_lists_ambiguous_table_without_linking(self):
        result = _view_graph(
            [{"name": "photos", "source": "docs/schema.md", "fields": [{"name": "id"}]},
             {"name": "photos", "source": "docs/other.md", "fields": [{"name": "id"}]}],
            [_view_access(["photos"])],
        )
        view = _function_view(result)
        self.assertEqual(view["documented_entity_links"], [])
        self.assertEqual(len(view["unlinked_database_tables"]), 1)
        unlinked = view["unlinked_database_tables"][0]
        self.assertEqual(unlinked["table"], "photos")
        self.assertEqual(unlinked["status"], "AMBIGUOUS_ENTITY")
        self.assertEqual(unlinked["entity_ids"], _entity_ids(result, "photos"))
        self.assertEqual([e for e in result["edges"]
                          if e.get("method") == "database-access-to-documented-entity"], [])
        self.assertEqual(view["coverage"]["database_entity_linkage"], "NOT_LINKED")

    def test_function_view_mixed_tables_are_partially_linked(self):
        result = _view_graph(
            [{"name": "photos", "source": "docs/schema.md",
              "fields": [{"name": "id"}, {"name": "owner_id"}]}],
            [_view_access(["photos", "tags"])],
        )
        view = _function_view(result)
        self.assertEqual(len(view["documented_entity_links"]), 1)
        self.assertEqual(view["documented_entity_links"][0]["entity_name"], "photos")
        self.assertEqual(len(view["unlinked_database_tables"]), 1)
        self.assertEqual(view["unlinked_database_tables"][0]["table"], "tags")
        self.assertEqual(view["unlinked_database_tables"][0]["status"], "NO_DOCUMENTED_ENTITY")
        self.assertEqual(view["coverage"]["database_entity_linkage"], "PARTIALLY_LINKED")

    def test_function_view_fully_linked_table_reports_linked(self):
        result = _view_graph(
            [{"name": "photos", "source": "docs/schema.md",
              "fields": [{"name": "id"}, {"name": "owner_id"}]}],
            [_view_access(["photos"])],
        )
        view = _function_view(result)
        self.assertEqual(len(view["documented_entity_links"]), 1)
        self.assertEqual(view["unlinked_database_tables"], [])
        self.assertEqual(view["coverage"]["database_entity_linkage"], "LINKED")

    def test_function_view_without_database_access_reports_no_access(self):
        result = _view_graph(
            [{"name": "photos", "source": "docs/schema.md",
              "fields": [{"name": "id"}, {"name": "owner_id"}]}],
            [],
        )
        view = _function_view(result)
        self.assertEqual(view["database_accesses"], [])
        self.assertEqual(view["unlinked_database_tables"], [])
        self.assertEqual(view["coverage"]["database_entity_linkage"], "NO_DATABASE_ACCESS")

    def test_function_view_access_without_tables_keeps_unknown(self):
        result = _view_graph(
            [{"name": "photos", "source": "docs/schema.md",
              "fields": [{"name": "id"}, {"name": "owner_id"}]}],
            [_view_access([], parse_status="UNKNOWN")],
        )
        view = _function_view(result)
        self.assertEqual(len(view["database_accesses"]), 1)
        self.assertEqual(view["unlinked_database_tables"], [])
        self.assertEqual(view["coverage"]["database_entity_linkage"], "NOT_LINKED_OR_UNKNOWN")

    def _deep_chain_view(self):
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
        return _function_view(result)

    def test_function_view_surfaces_depth_truncation_frontier(self):
        view = self._deep_chain_view()
        frontier = view["call_chain_truncated_frontier"]
        self.assertEqual(len(frontier), 1)
        self.assertEqual(frontier[0]["reason"], "DEPTH_LIMIT")
        self.assertEqual(frontier[0]["caller_symbol_id"], "symbol:module_7.py#step_7:8")
        self.assertEqual(frontier[0]["target_id"], "symbol:module_8.py#step_8:9")
        self.assertEqual(frontier[0]["call_site"], {"path": "module_8.py", "line": 48})
        self.assertEqual(view["coverage"]["call_chain_completeness"], "TRUNCATED")
        self.assertIn("Call chain truncated at analysis limit; calls beyond the frontier were not expanded.",
                      view["unlinked_requirements"])

    def test_function_view_surfaces_chain_count_truncation_frontier(self):
        source = graph()
        handler = "symbol:server/routes/photos.js#uploadPhoto:35"
        source["nodes"].append({
            "id": handler, "type": "function", "label": "uploadPhoto",
            "evidence": {"path": "server/routes/photos.js", "line": 35},
        })
        for index in range(101):
            leaf = f"symbol:leaf.py#leaf_{index}:1"
            source["nodes"].append({
                "id": leaf, "type": "function", "label": f"leaf_{index}",
                "evidence": {"path": "leaf.py", "line": 1},
            })
            source["edges"].append({
                "source": "file:server/routes/photos.js", "target": leaf,
                "type": "calls", "caller_symbol_id": handler,
                "resolution_status": "RESOLVED", "resolved": True,
                "evidence": {"path": "server/routes/photos.js", "line": 40 + index},
                "method": "typescript-compiler-api", "confidence": "high",
            })
        result = project_business_graph(source, {"endpoints": [{
            "method": "POST", "path": "/photos", "source": "server/routes/photos.js:20",
            "line": 20, "handler_reference": "uploadPhoto",
        }]})
        view = _function_view(result)
        frontier = view["call_chain_truncated_frontier"]
        self.assertEqual(len(frontier), 1)
        self.assertEqual(frontier[0]["reason"], "CHAIN_LIMIT")
        self.assertEqual(frontier[0]["caller_symbol_id"], handler)
        self.assertEqual(frontier[0]["target_id"], "symbol:leaf.py#leaf_100:1")
        self.assertEqual(frontier[0]["call_site"],
                         {"path": "server/routes/photos.js", "line": 140})
        self.assertEqual(view["coverage"]["call_chain_completeness"], "TRUNCATED")

    def test_function_view_reports_complete_chain_without_truncation(self):
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
        ])
        result = project_business_graph(source, {"endpoints": [{
            "method": "POST", "path": "/photos", "source": "server/routes/photos.js:20",
            "line": 20, "handler_reference": "uploadPhoto",
        }]})
        view = _function_view(result)
        self.assertEqual(view["call_chain_truncated_frontier"], [])
        self.assertEqual(view["coverage"]["call_chain_completeness"], "COMPLETE")
        self.assertEqual(len(view["unlinked_requirements"]), 3)
        self.assertNotIn("Call chain truncated at analysis limit; calls beyond the frontier were not expanded.",
                         view["unlinked_requirements"])

    def test_function_view_with_no_calls_reports_complete_chain(self):
        source = graph()
        handler = "symbol:server/routes/photos.js#uploadPhoto:35"
        source["nodes"].append({
            "id": handler, "type": "function", "label": "uploadPhoto",
            "evidence": {"path": "server/routes/photos.js", "line": 35},
        })
        result = project_business_graph(source, {"endpoints": [{
            "method": "POST", "path": "/photos", "source": "server/routes/photos.js:20",
            "line": 20, "handler_reference": "uploadPhoto",
        }]})
        view = _function_view(result)
        self.assertEqual(view["call_chain"], [])
        self.assertEqual(view["call_chain_truncated_frontier"], [])
        self.assertEqual(view["coverage"]["call_chain_completeness"], "COMPLETE")


    def test_propagates_python_identity_source_to_exact_resolved_call_parameter(self):
        source = graph()
        route_path = "server/routes/photos.py"
        service_path = "server/services/photo_service.py"
        source["nodes"].extend([
            {"id": "file:" + route_path, "type": "file", "label": route_path,
             "evidence": {"path": route_path}},
            {"id": "file:" + route_path, "type": "file", "label": route_path,
             "evidence": {"path": route_path}},
            {"id": "file:" + service_path, "type": "file", "label": service_path,
             "evidence": {"path": service_path}},
            {"id": "symbol:" + route_path + "#listPhotos:10", "type": "function",
             "label": "listPhotos", "evidence": {"path": route_path, "line": 10}},
            {"id": "symbol:" + service_path + "#findOwnedPhotos:4", "type": "function",
             "label": "findOwnedPhotos", "evidence": {"path": service_path, "line": 4}},
        ])
        handler = "symbol:" + route_path + "#listPhotos:10"
        callee = "symbol:" + service_path + "#findOwnedPhotos:4"
        source["edges"].append({
            "source": "file:" + route_path, "target": callee, "type": "calls",
            "caller_symbol_id": handler, "resolution_status": "RESOLVED",
            "evidence": {"path": route_path, "line": 14},
            "argument_binding_status": "EXACT_STATIC_MAPPING",
            "argument_bindings": [{
                "argument_index": 0, "argument_kind": "POSITIONAL",
                "argument_expression": "uid",
                "argument_location": {"path": route_path, "line": 14, "column": 25},
                "parameter_name": "owner_id", "parameter_index": 0,
                "parameter_kind": "positional_or_keyword",
                "parameter_declaration": {"path": service_path, "line": 4, "column": 22},
            }],
            "target_location": {"path": service_path, "line": 4},
        })
        result = project_business_graph(source, {
            "endpoints": [{
                "method": "GET", "path": "/photos", "source": route_path + ":5",
                "line": 5, "handler_reference": "listPhotos",
            }],
            "identity_evidence": [{
                "evidence_id": "identity-source-1",
                "kind": "authenticated_identity_source",
                "source": route_path, "line": 12,
                "details": {"identity_expressions": ["request.user.id"],
                            "source_variable": "uid"},
                "scope": "SAME_LINE_PATTERN",
                "confidence": "STATIC_INFERENCE",
                "verification_status": "NOT_VERIFIED",
                "owner_symbol_id": handler,
                "owner_resolution_status": "RESOLVED",
            }],
        })
        endpoint = next(n for n in result["nodes"] if n.get("business_kind") == "endpoint")
        association = endpoint["handler_association"]
        propagated = association["identity_parameter_propagation_evidence"]
        self.assertEqual(len(propagated), 1)
        self.assertEqual(propagated[0]["caller_symbol_id"], handler)
        self.assertEqual(propagated[0]["callee_symbol_id"], callee)
        self.assertEqual(propagated[0]["argument_expression"], "uid")
        self.assertEqual(propagated[0]["parameter_name"], "owner_id")
        self.assertEqual(propagated[0]["verification_status"], "NOT_VERIFIED")
        self.assertEqual(propagated[0]["propagation_status"],
                         "CALL_ARGUMENT_TO_PARAMETER_STATIC_MAPPING")
        flow = result["business_understanding"]["endpoint_execution_paths"][0]
        self.assertEqual(len(flow["identity_parameter_propagation"]), 1)
        self.assertEqual(flow["stage_status"]["identity_parameter_propagation"],
                         "STATIC_MAPPING_DETECTED")

    def test_does_not_propagate_identity_without_exact_owner_or_argument_mapping(self):
        source = graph()
        route_path = "server/routes/photos.py"
        service_path = "server/services/photo_service.py"
        source["nodes"].extend([
            {"id": "file:" + route_path, "type": "file", "label": route_path,
             "evidence": {"path": route_path}},
            {"id": "file:" + service_path, "type": "file", "label": service_path,
             "evidence": {"path": service_path}},
            {"id": "symbol:" + route_path + "#listPhotos:10", "type": "function",
             "label": "listPhotos", "evidence": {"path": route_path, "line": 10}},
            {"id": "symbol:" + service_path + "#findOwnedPhotos:4", "type": "function",
             "label": "findOwnedPhotos", "evidence": {"path": service_path, "line": 4}},
        ])
        handler = "symbol:" + route_path + "#listPhotos:10"
        callee = "symbol:" + service_path + "#findOwnedPhotos:4"
        source["edges"].append({
            "source": "file:" + route_path, "target": callee, "type": "calls",
            "caller_symbol_id": handler, "resolution_status": "RESOLVED",
            "evidence": {"path": route_path, "line": 14},
            "argument_binding_status": "UNSUPPORTED_ARGUMENT_SHAPE",
            "argument_bindings": [],
        })
        result = project_business_graph(source, {
            "endpoints": [{
                "method": "GET", "path": "/photos", "source": route_path + ":5",
                "line": 5, "handler_reference": "listPhotos",
            }],
            "identity_evidence": [{
                "evidence_id": "identity-source-2",
                "kind": "authenticated_identity_source",
                "source": route_path, "line": 12,
                "details": {"identity_expressions": ["request.user.id"],
                            "source_variable": "uid"},
                "scope": "SAME_LINE_PATTERN",
                "owner_symbol_id": handler,
                "owner_resolution_status": "RESOLVED",
            }],
        })
        endpoint = next(n for n in result["nodes"] if n.get("business_kind") == "endpoint")
        self.assertEqual(endpoint["handler_association"]["identity_parameter_propagation_evidence"], [])


    def test_propagates_identity_across_multiple_exact_resolved_call_edges(self):
        source = graph()
        route_path = "server/routes/photos.py"
        service_path = "server/services/photo_service.py"
        repository_path = "server/repositories/photo_repository.py"
        symbols = {
            "handler": "symbol:" + route_path + "#listPhotos:10",
            "service": "symbol:" + service_path + "#findOwnedPhotos:4",
            "repository": "symbol:" + repository_path + "#queryOwnedPhotos:8",
        }
        for path in (route_path, service_path, repository_path):
            source["nodes"].append({
                "id": "file:" + path, "type": "file", "label": path,
                "evidence": {"path": path},
            })
        for key, path, name, line in (
            ("handler", route_path, "listPhotos", 10),
            ("service", service_path, "findOwnedPhotos", 4),
            ("repository", repository_path, "queryOwnedPhotos", 8),
        ):
            source["nodes"].append({
                "id": symbols[key], "type": "function", "label": name,
                "evidence": {"path": path, "line": line},
            })

        def call_edge(caller, callee, path, line, expression, parameter):
            return {
                "source": "file:" + path, "target": callee, "type": "calls",
                "caller_symbol_id": caller, "resolution_status": "RESOLVED",
                "evidence": {"path": path, "line": line},
                "argument_binding_status": "EXACT_STATIC_MAPPING",
                "argument_bindings": [{
                    "argument_index": 0, "argument_kind": "POSITIONAL",
                    "argument_expression": expression,
                    "argument_location": {"path": path, "line": line, "column": 20},
                    "parameter_name": parameter, "parameter_index": 0,
                    "parameter_kind": "positional_or_keyword",
                    "parameter_declaration": {"path": path, "line": line, "column": 24},
                }],
                "target_location": {"path": path, "line": line},
            }

        source["edges"].extend([
            call_edge(symbols["handler"], symbols["service"], route_path, 14, "uid", "owner_id"),
            call_edge(symbols["service"], symbols["repository"], service_path, 9, "owner_id", "user_id"),
        ])
        result = project_business_graph(source, {
            "endpoints": [{
                "method": "GET", "path": "/photos", "source": route_path + ":5",
                "line": 5, "handler_reference": "listPhotos",
            }],
            "identity_evidence": [{
                "evidence_id": "identity-source-multihop",
                "kind": "authenticated_identity_source",
                "source": route_path, "line": 12,
                "details": {"identity_expressions": ["request.user.id"],
                            "source_variable": "uid"},
                "scope": "SAME_LINE_PATTERN",
                "confidence": "STATIC_INFERENCE",
                "verification_status": "NOT_VERIFIED",
                "owner_symbol_id": symbols["handler"],
                "owner_resolution_status": "RESOLVED",
            }, {
                "evidence_id": "sql-owner-binding-multihop",
                "kind": "sql_owner_parameter_binding",
                "source": repository_path, "line": 11,
                "details": {"binding_parameters": ["user_id"],
                            "resource_fields": ["owner_id"],
                            "binding_status": "EXACT_BARE_IDENTIFIER_ARGUMENT"},
                "scope": "SAME_LINE_PATTERN",
                "confidence": "STATIC_INFERENCE",
                "verification_status": "NOT_VERIFIED",
                "owner_symbol_id": symbols["repository"],
                "owner_resolution_status": "RESOLVED",
            }],
        })
        endpoint = next(n for n in result["nodes"] if n.get("business_kind") == "endpoint")
        propagated = endpoint["handler_association"]["identity_parameter_propagation_evidence"]
        self.assertEqual(len(propagated), 2)
        by_depth = {item["propagation_depth"]: item for item in propagated}
        self.assertEqual(by_depth[1]["parameter_name"], "owner_id")
        self.assertEqual(by_depth[1]["propagation_status"],
                         "CALL_ARGUMENT_TO_PARAMETER_STATIC_MAPPING")
        self.assertEqual(by_depth[2]["argument_expression"], "owner_id")
        self.assertEqual(by_depth[2]["parameter_name"], "user_id")
        self.assertEqual(by_depth[2]["propagation_status"],
                         "MULTI_HOP_STATIC_PARAMETER_MAPPING")
        self.assertEqual(len(by_depth[2]["propagation_chain"]), 2)
        self.assertEqual(by_depth[2]["verification_status"], "NOT_VERIFIED")
        sql_links = endpoint["handler_association"]["identity_sql_sink_evidence"]
        self.assertEqual(len(sql_links), 1)
        self.assertEqual(sql_links[0]["binding_parameter"], "user_id")
        self.assertEqual(sql_links[0]["resource_fields"], ["owner_id"])
        self.assertEqual(sql_links[0]["binding_status"],
                         "PROPAGATED_PARAMETER_TO_SQL_OWNER_BINDING")
        self.assertEqual(sql_links[0]["verification_status"], "NOT_VERIFIED")

    # P1: S2.9 owner-scope identity linking.

    def test_owner_scope_links_comparison_on_another_line_to_database_access(self):
        result, handler, load = _owner_scope_endpoint_graph([
            _owner_scope_identity_record(
                "identity-compare-1", "resource_owner_comparison", 5, owner=_OWNER_SCOPE_LOAD,
                details={"comparisons": [{"resource_field": "owner_id",
                                          "identity_expression": "request.user.id",
                                          "operator": "=="}]},
            ),
        ])
        db = next(n for n in result["nodes"] if n.get("business_kind") == "database_access")
        self.assertEqual(db["business_details"].get("identity_evidence_ids"),
                         ["identity-compare-1"])
        endpoint = next(n for n in result["nodes"] if n.get("business_kind") == "endpoint")
        linked = endpoint["handler_association"]["identity_dataflow_evidence"]
        self.assertEqual([row["identity_evidence_id"] for row in linked],
                         ["identity-compare-1"])
        self.assertEqual(linked[0]["link_method"], "SAME_FUNCTION_SCOPE")
        self.assertEqual(linked[0]["verification_status"], "NOT_VERIFIED")
        self.assertEqual(result["business_understanding"]["identity_evidence_owner_scope_links"], 1)
        flow = result["business_understanding"]["endpoint_execution_paths"][0]
        self.assertEqual(flow["stage_status"]["identity_resource"], "DETECTED")

    def test_same_line_link_wins_without_duplicate(self):
        result, handler, load = _owner_scope_endpoint_graph([
            _owner_scope_identity_record(
                "identity-sink-1", "direct_authenticated_identity_to_sql_owner_binding",
                9, owner=_OWNER_SCOPE_LOAD,
                details={"identity_expressions": ["request.user.id"],
                         "resource_fields": ["owner_id"],
                         "binding_status": "SAME_LINE_PARAMETER_EXPRESSION"},
            ),
        ])
        endpoint = next(n for n in result["nodes"] if n.get("business_kind") == "endpoint")
        linked = endpoint["handler_association"]["identity_dataflow_evidence"]
        self.assertEqual(len(linked), 1)
        self.assertEqual(linked[0]["link_method"], "SAME_LINE")
        self.assertEqual(result["business_understanding"]["identity_evidence_owner_scope_links"], 0)

    def test_owner_scope_does_not_link_across_functions(self):
        other = "symbol:repo/photos.py#other:20"
        result, load = _owner_scope_db_graph([9], [
            _owner_scope_identity_record(
                "identity-compare-2", "resource_owner_comparison", 21, owner=other,
                details={"comparisons": [{"resource_field": "owner_id",
                                          "identity_expression": "request.user.id",
                                          "operator": "=="}]},
            ),
        ], extra_symbols=[(other, "repo/photos.py", 20)])
        db = next(n for n in result["nodes"] if n.get("business_kind") == "database_access")
        self.assertFalse(db["business_details"].get("identity_evidence_ids"))
        self.assertEqual(result["business_understanding"]["identity_evidence_owner_scope_links"], 0)

    def test_owner_scope_does_not_link_without_resolved_owner(self):
        # JS/TS shape: no owner_symbol_id, not on the DB line.
        result, load = _owner_scope_db_graph([9], [
            _owner_scope_identity_record("identity-compare-3", "resource_owner_comparison", 5),
        ])
        db = next(n for n in result["nodes"] if n.get("business_kind") == "database_access")
        self.assertFalse(db["business_details"].get("identity_evidence_ids"))
        self.assertEqual(result["business_understanding"]["identity_evidence_owner_scope_links"], 0)

    def test_sink_binding_kind_does_not_link_by_owner_scope(self):
        result, load = _owner_scope_db_graph([9], [
            _owner_scope_identity_record(
                "identity-sink-2", "direct_authenticated_identity_to_sql_owner_binding",
                5, owner=_OWNER_SCOPE_LOAD,
                details={"identity_expressions": ["request.user.id"],
                         "resource_fields": ["owner_id"],
                         "binding_status": "INTRAPROCEDURAL_LINEAR_ASSIGNMENT",
                         "propagation": [{"path": "repo/photos.py", "line": 3, "variable": "uid"}]},
            ),
        ])
        db = next(n for n in result["nodes"] if n.get("business_kind") == "database_access")
        self.assertFalse(db["business_details"].get("identity_evidence_ids"))
        self.assertEqual(result["business_understanding"]["identity_evidence_owner_scope_links"], 0)

    def test_owner_scope_links_record_to_every_database_access_in_the_function(self):
        result, load = _owner_scope_db_graph([9, 14], [
            _owner_scope_identity_record(
                "identity-source-3", "authenticated_identity_source", 5, owner=_OWNER_SCOPE_LOAD,
            ),
        ])
        dbs = [n for n in result["nodes"] if n.get("business_kind") == "database_access"]
        self.assertEqual(len(dbs), 2)
        for db in dbs:
            self.assertEqual(db["business_details"].get("identity_evidence_ids"),
                             ["identity-source-3"])
        self.assertEqual(result["business_understanding"]["identity_evidence_owner_scope_links"], 2)

    def test_owner_scope_link_count_leaves_record_count_unchanged(self):
        result, load = _owner_scope_db_graph([9], [
            _owner_scope_identity_record(
                "identity-source-4", "authenticated_identity_source", 5, owner=_OWNER_SCOPE_LOAD,
            ),
            _owner_scope_identity_record(
                "identity-compare-4", "resource_owner_comparison", 6, owner=_OWNER_SCOPE_LOAD,
                details={"comparisons": [{"resource_field": "owner_id",
                                          "identity_expression": "request.user.id",
                                          "operator": "=="}]},
            ),
        ])
        self.assertEqual(result["business_understanding"]["identity_evidence_owner_scope_links"], 2)
        self.assertEqual(result["business_understanding"]["identity_evidence_records"], 2)

    # P3: S2.9 owner-scope identity linking, TypeScript shape (records carry
    # the function owner resolved by the TS parser; the graph logic is the
    # same one P1 added for Python).

    def test_ts_owner_scope_links_comparison_on_another_line_to_database_access(self):
        result, handler, load = _ts_owner_scope_endpoint_graph([
            _ts_owner_scope_identity_record(
                "identity-ts-compare-1", "resource_owner_comparison", 5,
                owner=_TS_OWNER_SCOPE_LOAD,
                details={"comparisons": [{"resource_field": "owner_id",
                                          "identity_expression": "req.user.id",
                                          "operator": "==="}]},
            ),
        ])
        db = next(n for n in result["nodes"] if n.get("business_kind") == "database_access")
        self.assertEqual(db["business_details"].get("identity_evidence_ids"),
                         ["identity-ts-compare-1"])
        endpoint = next(n for n in result["nodes"] if n.get("business_kind") == "endpoint")
        linked = endpoint["handler_association"]["identity_dataflow_evidence"]
        self.assertEqual([row["identity_evidence_id"] for row in linked],
                         ["identity-ts-compare-1"])
        self.assertEqual(linked[0]["link_method"], "SAME_FUNCTION_SCOPE")
        self.assertEqual(linked[0]["verification_status"], "NOT_VERIFIED")
        self.assertEqual(result["business_understanding"]["identity_evidence_owner_scope_links"], 1)
        flow = result["business_understanding"]["endpoint_execution_paths"][0]
        self.assertEqual(flow["stage_status"]["identity_resource"], "DETECTED")

    def test_ts_same_line_link_wins_without_duplicate(self):
        result, handler, load = _ts_owner_scope_endpoint_graph([
            _ts_owner_scope_identity_record(
                "identity-ts-sink-1", "direct_authenticated_identity_to_sql_owner_binding",
                6, owner=_TS_OWNER_SCOPE_LOAD,
                details={"identity_expressions": ["req.user.id"],
                         "resource_fields": ["owner_id"],
                         "binding_status": "SAME_LINE_PARAMETER_EXPRESSION"},
            ),
        ])
        endpoint = next(n for n in result["nodes"] if n.get("business_kind") == "endpoint")
        linked = endpoint["handler_association"]["identity_dataflow_evidence"]
        self.assertEqual(len(linked), 1)
        self.assertEqual(linked[0]["link_method"], "SAME_LINE")
        self.assertEqual(result["business_understanding"]["identity_evidence_owner_scope_links"], 0)

    def test_ts_owner_scope_does_not_link_across_functions(self):
        other = "symbol:web/photos.ts#other:20"
        result, load = _ts_owner_scope_db_graph([6], [
            _ts_owner_scope_identity_record(
                "identity-ts-compare-2", "resource_owner_comparison", 21, owner=other,
                details={"comparisons": [{"resource_field": "owner_id",
                                          "identity_expression": "req.user.id",
                                          "operator": "==="}]},
            ),
        ], extra_symbols=[(other, "web/photos.ts", 20)])
        db = next(n for n in result["nodes"] if n.get("business_kind") == "database_access")
        self.assertFalse(db["business_details"].get("identity_evidence_ids"))
        self.assertEqual(result["business_understanding"]["identity_evidence_owner_scope_links"], 0)

    def test_ts_owner_scope_does_not_link_without_resolved_owner(self):
        # Parser-unavailable shape: no owner_symbol_id, not on the DB line.
        result, load = _ts_owner_scope_db_graph([6], [
            _ts_owner_scope_identity_record(
                "identity-ts-compare-3", "resource_owner_comparison", 5),
        ])
        db = next(n for n in result["nodes"] if n.get("business_kind") == "database_access")
        self.assertFalse(db["business_details"].get("identity_evidence_ids"))
        self.assertEqual(result["business_understanding"]["identity_evidence_owner_scope_links"], 0)

    def test_ts_sink_binding_kind_does_not_link_by_owner_scope(self):
        result, load = _ts_owner_scope_db_graph([6], [
            _ts_owner_scope_identity_record(
                "identity-ts-sink-2", "direct_authenticated_identity_to_sql_owner_binding",
                5, owner=_TS_OWNER_SCOPE_LOAD,
                details={"identity_expressions": ["req.user.id"],
                         "resource_fields": ["owner_id"],
                         "binding_status": "INTRAPROCEDURAL_LINEAR_ASSIGNMENT",
                         "propagation": [{"path": "web/photos.ts", "line": 4, "variable": "uid"}]},
            ),
        ])
        db = next(n for n in result["nodes"] if n.get("business_kind") == "database_access")
        self.assertFalse(db["business_details"].get("identity_evidence_ids"))
        self.assertEqual(result["business_understanding"]["identity_evidence_owner_scope_links"], 0)

    def test_ts_owner_scope_link_count_leaves_record_count_unchanged(self):
        result, load = _ts_owner_scope_db_graph([6], [
            _ts_owner_scope_identity_record(
                "identity-ts-source-4", "authenticated_identity_source", 4,
                owner=_TS_OWNER_SCOPE_LOAD,
            ),
            _ts_owner_scope_identity_record(
                "identity-ts-compare-4", "resource_owner_comparison", 5,
                owner=_TS_OWNER_SCOPE_LOAD,
                details={"comparisons": [{"resource_field": "owner_id",
                                          "identity_expression": "req.user.id",
                                          "operator": "==="}]},
            ),
        ])
        self.assertEqual(result["business_understanding"]["identity_evidence_owner_scope_links"], 2)
        self.assertEqual(result["business_understanding"]["identity_evidence_records"], 2)


if __name__ == "__main__":
    unittest.main()
