"""Regression tests for JavaScript/TypeScript adapter ownership and API detection."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from usit.js_ts_parser import analyze_js_ts_project, ts_function_owners
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

    # P3: symbols carry their closing line, so a source line can be mapped
    # to its innermost enclosing function.

    _P3_SOURCE = (
        "import { db } from './db';\n"
        "\n"
        "export async function loadPhotos(req: any) {\n"
        "  const photo = await db.getPhoto(req.params.id);\n"
        "  if (photo.owner_id === req.user.id) {\n"
        "    const rows = await db.query(\"SELECT id FROM photos WHERE owner_id = ?\", [req.user.id]);\n"
        "    return rows;\n"
        "  }\n"
        "  return [];\n"
        "}\n"
        "\n"
        "export class PhotoRepo {\n"
        "  async find(id: string) {\n"
        "    const run = async () => {\n"
        "      return 1;\n"
        "    };\n"
        "    return run();\n"
        "  }\n"
        "}\n"
    )

    def _p3_parsed(self, root):
        (root / "photos.ts").write_text(self._P3_SOURCE, encoding="utf-8")
        (root / "db.ts").write_text(
            "export const db = {\n"
            "  async getPhoto(id: string) { return { owner_id: 1 }; },\n"
            "  async query(sql: string, params: any[]) { return []; },\n"
            "};\n",
            encoding="utf-8",
        )
        return analyze_js_ts_project(root, discover_files(root))

    def test_function_symbols_carry_end_line(self):
        with tempfile.TemporaryDirectory() as td:
            parsed = self._p3_parsed(Path(td))
            by_id = {item["symbol_id"]: item
                     for item in parsed["files"]["photos.ts"]["symbols"]}
            load = by_id["symbol:photos.ts#loadPhotos:3"]
            self.assertEqual((load["line"], load["end_line"]), (3, 10))
            find = by_id["symbol:photos.ts#PhotoRepo.find:13"]
            self.assertEqual((find["line"], find["end_line"]), (13, 18))
            run = by_id["symbol:photos.ts#PhotoRepo.run:14"]
            self.assertEqual((run["line"], run["end_line"]), (14, 16))

    def test_ts_function_owners_resolves_innermost_enclosing_function(self):
        with tempfile.TemporaryDirectory() as td:
            parsed = self._p3_parsed(Path(td))
            owners = ts_function_owners(parsed)
            photo = owners["photos.ts"]
            self.assertEqual(photo[5], "symbol:photos.ts#loadPhotos:3")
            self.assertEqual(photo[6], "symbol:photos.ts#loadPhotos:3")
            # A1 (P3 follow-up): owner ids use the bare method name, matching
            # the graph's symbol node ids and caller_symbol_id -- the
            # adapter's qualified symbol_id (PhotoRepo.run / PhotoRepo.find)
            # matches neither and silently broke owner-scope linking for
            # methods and nested functions.
            self.assertEqual(photo[15], "symbol:photos.ts#run:14")
            self.assertEqual(photo[17], "symbol:photos.ts#find:13")
            self.assertNotIn(1, photo)
            self.assertNotIn(12, photo)

    # A1: Angular HttpClient calls and template event bindings.

    _ANGULAR_COMPONENT_TS = (
        "import { Component } from '@angular/core';\n"
        "import { HttpClient } from '@angular/common/http';\n"
        "\n"
        "@Component({\n"
        "  selector: 'app-photo-card',\n"
        "  templateUrl: './photo-card.component.html',\n"
        "})\n"
        "export class PhotoCardComponent {\n"
        "  constructor(private http: HttpClient) {}\n"
        "\n"
        "  savePhoto() {\n"
        "    this.http.get('/api/photos').subscribe((rows) => console.log(rows));\n"
        "  }\n"
        "\n"
        "  createPhoto() {\n"
        "    this.http.post('/api/photos', { title: 'x' }).subscribe();\n"
        "  }\n"
        "}\n"
    )

    def test_http_client_calls_are_recognized_as_api_client_calls(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "photo-card.component.ts").write_text(
                self._ANGULAR_COMPONENT_TS, encoding="utf-8")
            parsed = analyze_js_ts_project(root, discover_files(root))
            calls = parsed["files"]["photo-card.component.ts"]["api_client_calls"]
            by_method = {item["method"]: item for item in calls}
            self.assertEqual(set(by_method), {"GET", "POST"})
            get_call = by_method["GET"]
            self.assertEqual(get_call["path"], "/api/photos")
            self.assertEqual(get_call["resolution_status"], "LITERAL_METHOD_PATH")
            self.assertEqual(get_call["caller_symbol_id"],
                             "symbol:photo-card.component.ts#savePhoto:11")
            self.assertEqual(by_method["POST"]["caller_symbol_id"],
                             "symbol:photo-card.component.ts#createPhoto:15")

    def test_service_method_calls_are_not_api_client_calls(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "photo-list.component.ts").write_text(
                "export class PhotoListComponent {\n"
                "  constructor(private photoService: any) {}\n"
                "  load() {\n"
                "    this.photoService.getPhotos();\n"
                "    this.http.get('/api/photos').subscribe();\n"
                "  }\n"
                "}\n",
                encoding="utf-8",
            )
            parsed = analyze_js_ts_project(root, discover_files(root))
            calls = parsed["files"]["photo-list.component.ts"]["api_client_calls"]
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0]["callee"], "this.http.get")

    def test_angular_template_binding_links_to_api_endpoint(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            web = root / "web"
            web.mkdir()
            (web / "photo-card.component.ts").write_text(
                self._ANGULAR_COMPONENT_TS, encoding="utf-8")
            (web / "photo-card.component.html").write_text(
                '<button (click)="savePhoto()">Save</button>\n', encoding="utf-8")
            routes = root / "server" / "routes"
            routes.mkdir(parents=True)
            (routes / "photos.ts").write_text(
                "import { Router } from 'express';\n"
                "export function getPhotos(req: any, res: any) { res.json([]); }\n"
                "export const router = Router();\n"
                "router.get('/api/photos', getPhotos);\n",
                encoding="utf-8",
            )
            from tests.synthetic_scan import scan_project
            out = root.parent / (root.name + "-ng-output")
            scan_project(str(root), str(out), force=True)
            graph = json.loads((out / "knowledge_graph.json").read_text(encoding="utf-8"))
            bindings = [edge for edge in graph["edges"]
                        if edge.get("method") == "angular-template-event-handler-binding"]
            self.assertEqual(len(bindings), 1)
            binding = bindings[0]
            self.assertEqual(binding["event_name"], "click")
            self.assertEqual(binding["resolution_status"], "RESOLVED")
            self.assertEqual(binding["target"],
                             "symbol:web/photo-card.component.ts#savePhoto:11")
            self.assertEqual(binding["evidence"]["path"],
                             "web/photo-card.component.html")
            from usit.business_graph import project_business_graph
            semantics = {"features": [], "entities": [], "endpoints": [{
                "method": "GET", "path": "/api/photos",
                "source": "server/routes/photos.ts:4", "line": 4,
                "handler_reference": "getPhotos"}]}
            projected = project_business_graph(graph, semantics)
            summary = projected["business_understanding"]
            # Two api calls exist (GET + POST); only the GET one matches the
            # endpoint, the POST link stays NO_MATCH, so count exact links.
            exact = [link for link in summary["frontend_api_endpoint_links"]
                     if link["resolution_status"] == "EXACT_MATCH"]
            self.assertEqual(len(exact), 1)
            self.assertEqual(summary["frontend_business_chain_count"], 1)
            chain = summary["frontend_business_chains"][0]
            self.assertEqual((chain["method"], chain["path"]), ("GET", "/api/photos"))
            self.assertEqual(chain["handler_symbol_id"],
                             "symbol:web/photo-card.component.ts#savePhoto:11")

    def test_angular_inline_template_binding_links_to_api_endpoint(self):
        # A2: the template string lives in the component file; the binding
        # evidence path is the .ts file and its line is the button's file line.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            web = root / "web"
            web.mkdir()
            (web / "photo-card.component.ts").write_text(
                "import { Component } from '@angular/core';\n"
                "import { HttpClient } from '@angular/common/http';\n"
                "\n"
                "@Component({\n"
                "  selector: 'app-photo-card',\n"
                "  template: `\n"
                "    <button (click)=\"savePhoto()\">Save</button>\n"
                "  `,\n"
                "})\n"
                "export class PhotoCardComponent {\n"
                "  constructor(private http: HttpClient) {}\n"
                "\n"
                "  savePhoto() {\n"
                "    this.http.get('/api/photos').subscribe((rows) => console.log(rows));\n"
                "  }\n"
                "}\n",
                encoding="utf-8",
            )
            routes = root / "server" / "routes"
            routes.mkdir(parents=True)
            (routes / "photos.ts").write_text(
                "import { Router } from 'express';\n"
                "export function getPhotos(req: any, res: any) { res.json([]); }\n"
                "export const router = Router();\n"
                "router.get('/api/photos', getPhotos);\n",
                encoding="utf-8",
            )
            from tests.synthetic_scan import scan_project
            out = root.parent / (root.name + "-ng-inline-output")
            scan_project(str(root), str(out), force=True)
            graph = json.loads((out / "knowledge_graph.json").read_text(encoding="utf-8"))
            bindings = [edge for edge in graph["edges"]
                        if edge.get("method") == "angular-template-event-handler-binding"]
            self.assertEqual(len(bindings), 1)
            binding = bindings[0]
            self.assertEqual(binding["event_name"], "click")
            self.assertEqual(binding["resolution_status"], "RESOLVED")
            self.assertEqual(binding["target"],
                             "symbol:web/photo-card.component.ts#savePhoto:13")
            self.assertEqual(binding["evidence"]["path"],
                             "web/photo-card.component.ts")
            self.assertEqual(binding["evidence"]["line"], 7)
            from usit.business_graph import project_business_graph
            semantics = {"features": [], "entities": [], "endpoints": [{
                "method": "GET", "path": "/api/photos",
                "source": "server/routes/photos.ts:4", "line": 4,
                "handler_reference": "getPhotos"}]}
            projected = project_business_graph(graph, semantics)
            summary = projected["business_understanding"]
            self.assertEqual(summary["frontend_business_chain_count"], 1)
            chain = summary["frontend_business_chains"][0]
            self.assertEqual((chain["method"], chain["path"]), ("GET", "/api/photos"))
            self.assertEqual(chain["handler_symbol_id"],
                             "symbol:web/photo-card.component.ts#savePhoto:13")


if __name__ == "__main__":
    unittest.main()
