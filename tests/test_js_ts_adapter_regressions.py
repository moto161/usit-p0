"""Regression tests for JavaScript/TypeScript adapter ownership and API detection."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from usit.js_ts_parser import analyze_js_ts_project
from usit.scanner import discover_files
from usit.vue_parser import vue_script_for_adapter

_VUE_SETUP_SFC = """<template>
  <button @click="savePhoto">Save</button>
</template>

<script setup lang="ts">
import { savePhotoApi } from './api';
const savePhoto = async () => { await savePhotoApi(1); };
</script>
"""

_VUE_OPTIONS_SFC = """<template>
  <button @click="save">Save</button>
</template>

<script>
export default {
  methods: {
    save() { return 1; },
  },
};
</script>
"""


class JsTsAdapterRegressionTests(unittest.TestCase):
    def test_api_client_calls_and_test_function_names_are_recognized(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "api.js"
            source.write_text(
                "function testLoadsItems() { axios.get('/api/items'); }\n"
                "function testSavesItems() { client.post('/api/items'); }\n",
                encoding="utf-8",
            )
            parsed = analyze_js_ts_project(root, discover_files(root))
            row = parsed["files"]["api.js"]
            calls = row["api_client_calls"]
            self.assertEqual({item["callee"] for item in calls}, {"axios.get", "client.post"})
            by_callee = {item["callee"]: item for item in row["calls"]}
            self.assertEqual(by_callee["axios.get"]["test_name"], "testLoadsItems")
            self.assertEqual(by_callee["client.post"]["test_name"], "testSavesItems")

    def test_resolved_typescript_call_records_exact_argument_mapping(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "flow.ts").write_text(
                "function sink(ownerId: string) { return ownerId; }\n"
                "function caller(req: any) { const uid = req.user.id; sink(uid); }\n",
                encoding="utf-8",
            )
            parsed = analyze_js_ts_project(root, discover_files(root))
            row = parsed["files"]["flow.ts"]
            call = next(item for item in row["calls"] if item["callee"] == "sink")
            self.assertEqual(call["resolution_status"], "RESOLVED", call)
            self.assertEqual(call["argument_binding_status"], "EXACT_STATIC_MAPPING")
            self.assertEqual(len(call["argument_bindings"]), 1)
            binding = call["argument_bindings"][0]
            self.assertEqual(binding["argument_expression"], "uid")
            self.assertEqual(binding["parameter_name"], "ownerId")
            self.assertEqual(binding["parameter_index"], 0)
            self.assertEqual(binding["argument_location"]["line"], 2)
            self.assertEqual(binding["parameter_declaration"]["line"], 1)

    def test_graph_call_edge_preserves_typescript_argument_mapping(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "flow.ts").write_text(
                "function sink(ownerId: string) { return ownerId; }\n"
                "function caller(req: any) { sink(req.user.id); }\n",
                encoding="utf-8",
            )
            from tests.synthetic_scan import scan_project
            out = root.parent / (root.name + "-graph-output")
            scan_project(str(root), str(out), force=True)
            graph = __import__("json").loads((out / "knowledge_graph.json").read_text(encoding="utf-8"))
            edge = next(item for item in graph["edges"]
                        if item["type"] == "calls" and item["evidence"]["path"] == "flow.ts"
                        and item["resolution_status"] == "RESOLVED")
            self.assertEqual(edge["argument_binding_status"], "EXACT_STATIC_MAPPING")
            self.assertEqual(edge["argument_bindings"][0]["argument_expression"], "req.user.id")
            self.assertEqual(edge["argument_bindings"][0]["parameter_name"], "ownerId")

    def test_typescript_spread_arguments_do_not_emit_partial_mapping(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "flow.ts").write_text(
                "function sink(ownerId: string) { return ownerId; }\n"
                "function caller(args: any[]) { sink(...args); }\n",
                encoding="utf-8",
            )
            parsed = analyze_js_ts_project(root, discover_files(root))
            call = next(item for item in parsed["files"]["flow.ts"]["calls"] if item["callee"] == "sink")
            self.assertEqual(call["resolution_status"], "RESOLVED", call)
            self.assertEqual(call["argument_binding_status"], "UNSUPPORTED_ARGUMENT_SHAPE")
            self.assertEqual(call["argument_bindings"], [])

    def test_top_level_math_random_resolves_to_typescript_lib_declaration(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "top_level.ts").write_text(
                "const randomValue = Math.random();\n",
                encoding="utf-8",
            )
            parsed = analyze_js_ts_project(root, discover_files(root))
            row = parsed["files"]["top_level.ts"]
            call = next(item for item in row["calls"] if item["callee"] == "Math.random")
            self.assertEqual(call["resolution_status"], "EXTERNAL", call)
            self.assertIn("lib.es5.d.ts", call.get("target", {}).get("file", ""))

    def test_template_literal_fetch_url_resolves_to_skeleton_path(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "api.js").write_text(
                "function loadPhoto(id) { return fetch(`/api/photos/${id}`); }\n",
                encoding="utf-8",
            )
            parsed = analyze_js_ts_project(root, discover_files(root))
            calls = parsed["files"]["api.js"]["api_client_calls"]
            self.assertEqual(len(calls), 1)
            call = calls[0]
            self.assertEqual(call["method"], "GET")
            self.assertEqual(call["path"], "/api/photos/{?}")
            self.assertEqual(call["resolution_status"], "TEMPLATE_METHOD_PATH")

    def test_template_literal_axios_url_with_multiple_segments(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "api.js").write_text(
                "async function loadPosts(uid) { return axios.get(`/api/users/${uid}/posts`); }\n",
                encoding="utf-8",
            )
            parsed = analyze_js_ts_project(root, discover_files(root))
            calls = parsed["files"]["api.js"]["api_client_calls"]
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0]["path"], "/api/users/{?}/posts")
            self.assertEqual(calls[0]["resolution_status"], "TEMPLATE_METHOD_PATH")

    def test_base_url_prefix_template_resolves_to_path(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "api.js").write_text(
                "const BASE = 'https://api.example.com';\n"
                "function loadPhoto(id) { return fetch(`${BASE}/api/photos/${id}`); }\n",
                encoding="utf-8",
            )
            parsed = analyze_js_ts_project(root, discover_files(root))
            calls = parsed["files"]["api.js"]["api_client_calls"]
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0]["path"], "/api/photos/{?}")
            self.assertEqual(calls[0]["resolution_status"], "TEMPLATE_METHOD_PATH")

    def test_plus_chain_url_literals(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "api.js").write_text(
                "function a() { return axios.get(\"/api/\" + \"photos\"); }\n"
                "function b(id) { return axios.get(\"/api/photos/\" + id); }\n",
                encoding="utf-8",
            )
            parsed = analyze_js_ts_project(root, discover_files(root))
            calls = sorted(parsed["files"]["api.js"]["api_client_calls"],
                           key=lambda item: item["line"])
            self.assertEqual(len(calls), 2)
            self.assertEqual(calls[0]["path"], "/api/photos")
            self.assertEqual(calls[0]["resolution_status"], "LITERAL_METHOD_PATH")
            self.assertIsNone(calls[1]["path"])
            self.assertEqual(calls[1]["resolution_status"], "DYNAMIC_OR_UNSUPPORTED_URL")

    def test_variable_url_stays_dynamic(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "api.js").write_text(
                "function load(url) { return fetch(url); }\n",
                encoding="utf-8",
            )
            parsed = analyze_js_ts_project(root, discover_files(root))
            calls = parsed["files"]["api.js"]["api_client_calls"]
            self.assertEqual(len(calls), 1)
            self.assertIsNone(calls[0]["path"])
            self.assertEqual(calls[0]["resolution_status"], "DYNAMIC_OR_UNSUPPORTED_URL")

    def test_vue_sfc_script_is_analyzed_with_file_relative_lines(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            web = root / "web"
            web.mkdir()
            sfc = web / "PhotoCard.vue"
            sfc.write_text(_VUE_SETUP_SFC, encoding="utf-8")
            api = web / "api.ts"
            api.write_text(
                "export async function savePhotoApi(id: number) {"
                " return fetch('/photos', { method: 'POST' }); }\n",
                encoding="utf-8",
            )
            source = {
                "web/PhotoCard.vue": vue_script_for_adapter(sfc.read_text(encoding="utf-8")),
                "web/api.ts": api.read_text(encoding="utf-8"),
            }
            parsed = analyze_js_ts_project(root, discover_files(root), js_ts_source=source)
            row = parsed["files"]["web/PhotoCard.vue"]
            self.assertEqual([(item["name"], item["line"]) for item in row["symbols"]],
                             [("savePhoto", 7)])
            call = next(item for item in row["calls"] if item["callee"] == "savePhotoApi")
            self.assertEqual(call["resolution_status"], "RESOLVED", call)
            self.assertEqual(call["target"]["id"], "symbol:web/api.ts#savePhotoApi:1")

    def test_vue_sfc_template_binding_links_to_api_endpoint(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            web = root / "web"
            web.mkdir()
            (web / "PhotoCard.vue").write_text(_VUE_SETUP_SFC, encoding="utf-8")
            (web / "PhotoForm.vue").write_text(_VUE_OPTIONS_SFC, encoding="utf-8")
            (web / "api.ts").write_text(
                "export async function savePhotoApi(id: number) {"
                " return fetch('/photos', { method: 'POST' }); }\n",
                encoding="utf-8",
            )
            (web / "routes.ts").write_text(
                "import { savePhotoApi } from './api';\n"
                "router.post('/photos', savePhotoApi);\n",
                encoding="utf-8",
            )
            from tests.synthetic_scan import scan_project
            out = root.parent / (root.name + "-vue-output")
            scan_project(str(root), str(out), force=True)
            graph = json.loads((out / "knowledge_graph.json").read_text(encoding="utf-8"))
            bindings = [edge for edge in graph["edges"]
                        if edge.get("method") == "vue-template-event-handler-binding"]
            self.assertEqual(len(bindings), 2)
            setup_binding = next(edge for edge in bindings
                                 if edge["evidence"]["path"] == "web/PhotoCard.vue")
            self.assertEqual(setup_binding["event_name"], "click")
            self.assertEqual(setup_binding["resolution_status"], "RESOLVED")
            self.assertEqual(setup_binding["target"],
                             "symbol:web/PhotoCard.vue#savePhoto:7")
            options_binding = next(edge for edge in bindings
                                   if edge["evidence"]["path"] == "web/PhotoForm.vue")
            self.assertEqual(options_binding["resolution_status"], "RESOLVED")
            self.assertEqual(options_binding["target"], "symbol:web/PhotoForm.vue#save:8")
            from usit.business_graph import project_business_graph
            semantics = {"features": [], "entities": [], "endpoints": [{
                "method": "POST", "path": "/photos", "source": "web/routes.ts:2", "line": 2,
                "handler_reference": "savePhotoApi"}]}
            projected = project_business_graph(graph, semantics)
            summary = projected["business_understanding"]
            self.assertEqual(summary["frontend_api_endpoint_link_count"], 1)
            self.assertEqual(summary["frontend_api_endpoint_links"][0]["resolution_status"],
                             "EXACT_MATCH")
            self.assertEqual(summary["frontend_business_chain_count"], 1)
            chain = summary["frontend_business_chains"][0]
            self.assertEqual((chain["method"], chain["path"]), ("POST", "/photos"))
            self.assertEqual(chain["handler_symbol_id"], "symbol:web/PhotoCard.vue#savePhoto:7")
            self.assertEqual(chain["verification_status"], "NOT_VERIFIED")
            # The router.post registration must not be misclassified as a
            # frontend API client call.
            api_call_edges = [edge for edge in graph["edges"]
                              if edge.get("method") == "typescript-api-client-call"]
            self.assertEqual(len(api_call_edges), 1)
            self.assertEqual(api_call_edges[0].get("route_path"), "/photos")


if __name__ == "__main__":
    unittest.main()
