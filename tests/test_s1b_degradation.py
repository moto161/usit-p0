"""S1-B: dedicated tests for Python AST degradation traceability.

The tests create only temporary files (auto-removed) and never modify the
project tree. They verify that ``analyze_file`` exposes an additive, row-level
``analysis_degradation`` record whose *values* match the real execution path,
and that existing ``symbols[]`` / ``calls[]`` structures are unchanged.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from usit.scanner import analyze_file

VALID_PY = (
    "import os\n"
    "class Svc:\n"
    "    def run(self):\n"
    "        return helper()\n"
    "def helper():\n"
    "    return 1\n"
)

BROKEN_PY = "def broken(:\n    pass\n"

JS_SOURCE = "import { named } from './a';\nnamed();\n"


def _row(filename: str, text: str) -> dict:
    """Analyze one temp file and return the row."""
    with tempfile.TemporaryDirectory(prefix="usit-s1b-") as td:
        root = Path(td)
        path = root / filename
        path.write_text(text, encoding="utf-8")
        return analyze_file(root, path)


class DegradationFieldTests(unittest.TestCase):
    # A. normal AST parse is not reported as degraded
    def test_valid_python_is_not_degraded(self):
        row = _row("ok.py", VALID_PY)
        deg = row["analysis_degradation"]
        self.assertFalse(deg["degraded"])
        self.assertIsNone(deg["reason"])
        self.assertEqual("python-ast", deg["method"])
        self.assertEqual("RESOLVED", deg["resolution_status"])
        self.assertIsNone(deg["diagnostics_ref"])

    # B. syntax error is explicitly degraded and traceable
    def test_broken_python_reports_syntax_error_degradation(self):
        row = _row("broken.py", BROKEN_PY)
        deg = row["analysis_degradation"]
        self.assertTrue(deg["degraded"])
        self.assertEqual("PYTHON_SYNTAX_ERROR", deg["reason"])
        self.assertEqual("regex-candidate", deg["method"])
        self.assertEqual("UNKNOWN", deg["resolution_status"])
        self.assertIsNotNone(deg["diagnostics_ref"])
        diag = row["analysis_diagnostics"][deg["diagnostics_ref"]]
        self.assertEqual("PYTHON_SYNTAX_ERROR", diag["code"])
        self.assertEqual("ERROR", diag["severity"])

    # C. semantic parser unavailable is recorded, never faked as success
    def test_semantic_parser_unavailable_is_recorded(self):
        row = _row("main.js", JS_SOURCE)
        deg = row["analysis_degradation"]
        self.assertTrue(deg["degraded"])
        self.assertEqual("SEMANTIC_PARSER_UNAVAILABLE", deg["reason"])
        self.assertEqual("regex-candidate", deg["method"])
        self.assertNotEqual("RESOLVED", deg["resolution_status"])

    # D. degraded symbols are linked to a row-level degradation record
    def test_degraded_symbols_linked_to_row_degradation(self):
        row = _row("broken.py", BROKEN_PY)
        self.assertTrue(row["analysis_degradation"]["degraded"])
        # symbols may still be produced by the regex fallback; the row-level
        # record is what makes them identifiable as degraded.
        for symbol in row["symbols"]:
            self.assertIn("name", symbol)
            self.assertIn("line", symbol)

    # E. non-degraded files never carry a false degradation flag
    def test_no_false_degradation_for_clean_file(self):
        row = _row("ok.py", VALID_PY)
        self.assertFalse(row["analysis_degradation"]["degraded"])
        self.assertEqual([], row.get("analysis_diagnostics") or [])

    # F. diagnostics_ref resolves to a real, readable diagnostic
    def test_diagnostics_ref_is_resolvable(self):
        row = _row("broken.py", BROKEN_PY)
        ref = row["analysis_degradation"]["diagnostics_ref"]
        self.assertIsInstance(ref, int)
        self.assertGreaterEqual(ref, 0)
        self.assertLess(ref, len(row["analysis_diagnostics"]))
        self.assertEqual("PYTHON_SYNTAX_ERROR", row["analysis_diagnostics"][ref]["code"])

    # G. existing symbol/call structures and keys are unchanged
    def test_existing_symbol_and_call_keys_unchanged(self):
        row = _row("ok.py", VALID_PY)
        for symbol in row["symbols"]:
            self.assertEqual({"name", "kind", "line", "evidence"}, set(symbol))
        for call in row["calls"]:
            for key in ("callee", "line", "method", "confidence", "resolved"):
                self.assertIn(key, call)

    # H. repeated analysis is deterministic
    def test_repeated_analysis_is_deterministic(self):
        first = _row("ok.py", VALID_PY)
        second = _row("ok.py", VALID_PY)
        self.assertEqual(first["symbols"], second["symbols"])
        self.assertEqual(first["calls"], second["calls"])
        self.assertEqual(first["analysis_degradation"], second["analysis_degradation"])

    # I. non-success states are not counted as resolved
    def test_non_success_status_is_not_resolved(self):
        row = _row("broken.py", BROKEN_PY)
        self.assertNotEqual("RESOLVED", row["analysis_degradation"]["resolution_status"])
        for call in row["calls"]:
            if call.get("resolution_status") is not None:
                self.assertNotEqual("RESOLVED", call["resolution_status"])

    # extra: unsupported language keeps an explicit unknown state
    def test_unsupported_language_is_explicit(self):
        row = _row("notes.xyz", "def not_really(:")
        deg = row["analysis_degradation"]
        self.assertTrue(deg["degraded"])
        self.assertEqual("UNSUPPORTED_LANGUAGE_NO_SEMANTIC_PARSER", deg["reason"])
        self.assertEqual("discovery-only", deg["method"])
        self.assertEqual("UNKNOWN", deg["resolution_status"])


if __name__ == "__main__":
    unittest.main()
