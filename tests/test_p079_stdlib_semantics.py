"""P0.7.9 P2: standard-library vs project-source semantics (independent expectations).

A minimal isolated fixture is built in a temp directory and analysed through the
real production path (``analyze_js_ts_project`` + the real adapter).  Every case
carries its OWN expectation derived from semantics — not "whatever the old
baseline said":

  1. Math.random            -> EXTERNAL, declared in the trusted TS standard library
  2. Array.prototype.join   -> EXTERNAL, same
  3. string method          -> EXTERNAL, same
  4. user-defined function  -> RESOLVED, defined inside the analysed project
  5. cross-file call        -> RESOLVED, resolved into the sibling project file
  6. missing symbol         -> NOT resolved (no fabricated target)
  7. real external package  -> EXTERNAL, target under the project's node_modules

Cases 1-3 intentionally contradict the pre-P0.7.9 baseline (which said DYNAMIC).
That old classification was an artefact of ``lib/*.d.ts`` never being loaded, not
a semantic judgement; see outputs/p079/TRUTHSET_RECLASSIFICATION.json.
"""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from usit import scanner
from usit.js_ts_parser import analyze_js_ts_project

MAIN_TS = """
import { other } from './other';
import { extDep } from 'extlib';

export function custom(): number {
  return 1;
}

export function runAll(): void {
  const r = Math.random();
  const j = [1, 2, 3].join(',');
  const u = 'x'.toUpperCase();
  const c = custom();
  const o = other();
  const e = extDep();
  missingSymbol();
  void r; void j; void u; void c; void o; void e;
}
"""

OTHER_TS = """
export function other(): number {
  return 2;
}
"""


class _Fixture:
    def __init__(self, case: unittest.TestCase):
        self._tmp = tempfile.TemporaryDirectory(prefix="p079-fixture-")
        case.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()
        (self.root / "src").mkdir(parents=True, exist_ok=True)
        (self.root / "src" / "main.ts").write_text(MAIN_TS, encoding="utf-8")
        (self.root / "src" / "other.ts").write_text(OTHER_TS, encoding="utf-8")
        ext = self.root / "node_modules" / "extlib"
        ext.mkdir(parents=True, exist_ok=True)
        (ext / "package.json").write_text(
            json.dumps({"name": "extlib", "version": "1.0.0", "main": "index.js"}),
            encoding="utf-8")
        (ext / "index.js").write_text(
            "export function extDep() { return 3; }\n", encoding="utf-8")
        (ext / "index.d.ts").write_text(
            "export declare function extDep(): number;\n", encoding="utf-8")


class StdlibSemanticsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        holder = Path(tempfile.mkdtemp(prefix="p079-holder-")).resolve()
        cls._holder = holder
        fixture = _Fixture(unittest.TestCase())  # placeholder, replaced per test
        # Build once for the class and analyse once (the analysis is the slow part).
        root = holder / "project"
        (root / "src").mkdir(parents=True, exist_ok=True)
        (root / "src" / "main.ts").write_text(MAIN_TS, encoding="utf-8")
        (root / "src" / "other.ts").write_text(OTHER_TS, encoding="utf-8")
        ext = root / "node_modules" / "extlib"
        ext.mkdir(parents=True, exist_ok=True)
        (ext / "package.json").write_text(
            json.dumps({"name": "extlib", "version": "1.0.0", "main": "index.js"}),
            encoding="utf-8")
        (ext / "index.js").write_text(
            "export function extDep() { return 3; }\n", encoding="utf-8")
        (ext / "index.d.ts").write_text(
            "export declare function extDep(): number;\n", encoding="utf-8")
        paths = scanner.discover_files(root)
        source = {p.relative_to(root).as_posix(): (
            scanner.decode_text(scanner.open_input_file(p, scanner.MAX_FILE_BYTES)) or "")
            for p in paths}
        cls.root = root
        cls.result = analyze_js_ts_project(root, paths, js_ts_source=source)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls._holder, ignore_errors=True)

    def _calls(self) -> dict[str, dict]:
        row = self.result["files"]["src/main.ts"]
        out: dict[str, dict] = {}
        for c in row.get("calls", []):
            out[str(c.get("callee"))] = c
        return out

    def test_analysis_ran_with_trusted_compiler(self) -> None:
        self.assertTrue(self.result["available"], str(self.result.get("project_diagnostics")))
        self.assertEqual(self.result["parser_version"], "6.0.3")

    def test_1_math_random_is_external_stdlib(self) -> None:
        call = self._calls().get("Math.random")
        self.assertIsNotNone(call, "Math.random call must be extracted")
        self.assertEqual(call["resolution_status"], "EXTERNAL")
        target = call.get("target") or {}
        self.assertTrue(str(target.get("file", "")).startswith("<ts-lib>/"),
                        f"Standard-library target must be normalised, got {target}")

    def test_2_array_join_is_external_stdlib(self) -> None:
        calls = self._calls()
        join = next((c for k, c in calls.items() if ".join" in k), None)
        self.assertIsNotNone(join, "Array .join call must be extracted")
        self.assertEqual(join["resolution_status"], "EXTERNAL")
        self.assertTrue(str((join.get("target") or {}).get("file", "")).startswith("<ts-lib>/"))

    def test_3_string_method_is_external_stdlib(self) -> None:
        calls = self._calls()
        upper = next((c for k, c in calls.items() if "toUpperCase" in k), None)
        self.assertIsNotNone(upper, "String method call must be extracted")
        self.assertEqual(upper["resolution_status"], "EXTERNAL")
        self.assertTrue(str((upper.get("target") or {}).get("file", "")).startswith("<ts-lib>/"))

    def test_4_user_defined_function_is_resolved_in_project(self) -> None:
        call = self._calls().get("custom")
        self.assertIsNotNone(call)
        self.assertEqual(call["resolution_status"], "RESOLVED")
        self.assertTrue(call["resolved"], "A project-local function must resolve")
        target = call.get("target") or {}
        self.assertEqual(target.get("file"), "src/main.ts",
                         "Resolution must stay inside the project, not the stdlib.")

    def test_5_cross_file_call_is_resolved(self) -> None:
        call = self._calls().get("other")
        self.assertIsNotNone(call)
        self.assertEqual(call["resolution_status"], "RESOLVED")
        self.assertEqual((call.get("target") or {}).get("file"), "src/other.ts")

    def test_6_missing_symbol_is_not_fabricated(self) -> None:
        call = self._calls().get("missingSymbol")
        if call is None:
            self.skipTest("missingSymbol not extracted by this parser version")
        self.assertNotEqual(call["resolution_status"], "RESOLVED")
        self.assertFalse(call.get("resolved"), "A missing symbol must not be reported resolved.")
        self.assertIsNone(call.get("target"), "No fabricated target for a missing symbol.")

    def test_7_real_external_package_is_external_and_project_relative(self) -> None:
        call = self._calls().get("extDep")
        self.assertIsNotNone(call)
        self.assertEqual(call["resolution_status"], "EXTERNAL")
        target_file = str((call.get("target") or {}).get("file", ""))
        self.assertIn("node_modules", target_file,
                      "A project dependency resolves under the project's node_modules.")
        self.assertFalse(target_file.startswith("<ts-lib>/"),
                         "A project dependency is NOT the TS standard library.")

    def test_stdlib_paths_never_leak_absolute_install_dir(self) -> None:
        blob = json.dumps(self.result, ensure_ascii=False)
        self.assertNotIn("node_modules/typescript", blob,
                         "The analyzer's absolute install path must not reach results.")


if __name__ == "__main__":
    unittest.main()
