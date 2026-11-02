"""Regression tests for JavaScript/TypeScript adapter ownership and API detection."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from usit.js_ts_parser import analyze_js_ts_project
from usit.scanner import discover_files


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


if __name__ == "__main__":
    unittest.main()
