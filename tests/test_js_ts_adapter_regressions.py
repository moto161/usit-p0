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
