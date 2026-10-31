"""USIT-S1-B-FIX-01｜`analysis_degradation.diagnostics_ref` 端到端有效性回归。

这些用例只验证一件事：在**真实扫描产物**（经 `scan_manifest.json` 序列化往返）
中，`diagnostics_ref` 指向真实存在、可读、且 code 与 `reason` 相符的诊断条目。

FIX-01 之前的缺陷：JS/TS 分支会用解析器结果整体覆盖 `analysis_diagnostics`，
导致 `analyze_file` 预先算好的 `diagnostics_ref` 悬空。修复不得靠把引用置
`None` 规避——那会隐藏降级原因，因此这里显式断言 `ref is not None`。

本模块不扩展图谱或 Markdown 消费；不修改 `symbols[]` / `calls[]` 结构。
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from usit.scanner import scan_project
from usit.safety import authorization_record

# 用 chr(10) 构造换行：heredoc / 字面 "\\n" 容易在写入时残留为两个字符。
NL = chr(10)

VALID_PY = NL.join(
    ["import os", "class S:", "    def r(self):", "        return h()",
     "def h():", "    return 1", ""]
)
BROKEN_PY = NL.join(["def broken(:", "    pass", ""])
JS_FILE = NL.join(["import { a } from './a';", "a();", ""])
XYZ_FILE = NL.join(["def x(:", ""])


def _write(path: Path, text: str) -> None:
    """Write UTF-8 bytes so line endings are never mangled."""
    path.write_bytes(text.encode("utf-8"))


def _scan_once(base: Path, tag: str) -> dict[str, dict]:
    """Run one full scan into a temp dir and return {relative path: row}."""
    proj = base / ("project-" + tag)
    proj.mkdir()
    _write(proj / "ok.py", VALID_PY)
    _write(proj / "broken.py", BROKEN_PY)
    _write(proj / "main.js", JS_FILE)
    _write(proj / "notes.xyz", XYZ_FILE)
    out = base / ("out-" + tag)
    scan_project(
        str(proj), str(out), force=True,
        authorization=authorization_record(proj, out),
    )
    manifest = json.loads((out / "scan_manifest.json").read_text(encoding="utf-8"))
    return {f["path"]: f for f in manifest["files"]}


def _resolve(files: dict[str, dict], name: str):
    """Return (row, degradation, diagnostics, ref, ref_valid, ref_code)."""
    row = files[name]
    deg = row.get("analysis_degradation") or {}
    diags = row.get("analysis_diagnostics", []) or []
    ref = deg.get("diagnostics_ref")
    valid = isinstance(ref, int) and 0 <= ref < len(diags)
    code = diags[ref].get("code") if valid else None
    return row, deg, diags, ref, valid, code


class DiagnosticsRefEndToEnd(unittest.TestCase):
    """A–G: reference validity, semantics, determinism and schema compatibility."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="usit-s1b-fix01-")
        self.base = Path(self._tmp.name).resolve()

    def tearDown(self):
        self._tmp.cleanup()

    # --- A -------------------------------------------------------------
    def test_a_js_ts_ref_resolves_after_serialisation(self):
        files = _scan_once(self.base, "a")
        _row, deg, diags, ref, valid, code = _resolve(files, "main.js")
        self.assertEqual("SEMANTIC_PARSER_UNAVAILABLE", deg.get("reason"))
        self.assertTrue(deg.get("degraded"))
        self.assertIsNotNone(ref, "diagnostics_ref 不得为 None（禁止靠置空规避）")
        self.assertTrue(valid, f"ref={ref} 越界；diags={diags}")
        self.assertEqual("SEMANTIC_PARSER_UNAVAILABLE", code)
        self.assertNotEqual("RESOLVED", deg.get("resolution_status"))

    # --- B -------------------------------------------------------------
    def test_b_python_syntax_error_ref_still_valid(self):
        files = _scan_once(self.base, "b")
        _row, deg, diags, ref, valid, code = _resolve(files, "broken.py")
        self.assertEqual("PYTHON_SYNTAX_ERROR", deg.get("reason"))
        self.assertTrue(deg.get("degraded"))
        self.assertTrue(valid, f"ref={ref} 越界；diags={diags}")
        self.assertEqual("PYTHON_SYNTAX_ERROR", code)
        self.assertNotEqual("RESOLVED", deg.get("resolution_status"))

    # --- C -------------------------------------------------------------
    def test_c_clean_ast_has_no_false_degradation(self):
        files = _scan_once(self.base, "c")
        _row, deg, diags, ref, valid, _code = _resolve(files, "ok.py")
        self.assertFalse(deg.get("degraded"))
        self.assertIsNone(deg.get("reason"))
        self.assertEqual("python-ast", deg.get("method"))
        self.assertEqual("RESOLVED", deg.get("resolution_status"))
        self.assertIsNone(ref)
        self.assertEqual([], diags)

    # --- D -------------------------------------------------------------
    def test_d_unsupported_language_unchanged(self):
        files = _scan_once(self.base, "d")
        _row, deg, diags, ref, valid, _code = _resolve(files, "notes.xyz")
        self.assertEqual("UNSUPPORTED_LANGUAGE_NO_SEMANTIC_PARSER", deg.get("reason"))
        self.assertEqual("discovery-only", deg.get("method"))
        self.assertEqual("UNKNOWN", deg.get("resolution_status"))
        if ref is not None:
            self.assertTrue(valid, f"ref={ref} 越界；diags={diags}")

    # --- E -------------------------------------------------------------
    def test_e_serialisation_roundtrip_keeps_ref_valid(self):
        files = _scan_once(self.base, "e")
        for name in ("ok.py", "broken.py", "main.js", "notes.xyz"):
            _row, deg, diags, ref, valid, code = _resolve(files, name)
            if ref is None:
                continue
            self.assertTrue(valid, f"{name}: ref={ref} 越界；diags={diags}")
            self.assertEqual(
                deg.get("reason"), code,
                f"{name}: diagnostics_ref 指向的 code 与 reason 不一致",
            )

    # --- F -------------------------------------------------------------
    def test_f_repeated_scan_is_deterministic(self):
        f1 = _scan_once(self.base, "f1")
        f2 = _scan_once(self.base, "f2")
        self.assertEqual(
            {k: v.get("analysis_degradation") for k, v in f1.items()},
            {k: v.get("analysis_degradation") for k, v in f2.items()},
        )
        self.assertEqual(
            {k: v.get("analysis_diagnostics") for k, v in f1.items()},
            {k: v.get("analysis_diagnostics") for k, v in f2.items()},
        )

    # --- G -------------------------------------------------------------
    def test_g_symbol_and_call_structures_unchanged(self):
        files = _scan_once(self.base, "g")
        for row in files.values():
            for call in row.get("calls", []):
                self.assertIn("resolution_status", call)
                self.assertIn("resolved", call)
            if row.get("language") == "Python":
                for sym in row.get("symbols", []):
                    self.assertEqual({"name", "kind", "line", "evidence"}, set(sym))
        ok = files["ok.py"]
        self.assertTrue(any(s["name"] == "h" for s in ok.get("symbols", [])))


if __name__ == "__main__":
    unittest.main()
