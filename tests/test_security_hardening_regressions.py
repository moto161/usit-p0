import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from usit.cli import main
from tests.test_graph_validation import FIXTURES


class GraphCliTrustDefaultsTests(unittest.TestCase):
    def test_embedded_line_counts_are_untrusted_by_default(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            graph = root / "graph.json"
            graph.write_text((FIXTURES / "valid_v1.json").read_text(encoding="utf-8"), encoding="utf-8")
            with patch("usit.cli.validate_graph_file", return_value={"is_valid": True}) as validate:
                rc = main([
                    "validate-graph", str(graph),
                    "--out-json", str(root / "result.json"),
                    "--out-md", str(root / "result.md"),
                ])
            self.assertEqual(rc, 0)
            self.assertTrue(validate.call_args.kwargs["require_trusted"])

    def test_embedded_line_counts_need_explicit_weak_mode_opt_in(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            graph = root / "graph.json"
            graph.write_text((FIXTURES / "valid_v1.json").read_text(encoding="utf-8"), encoding="utf-8")
            with patch("usit.cli.validate_graph_file", return_value={"is_valid": True}) as validate:
                rc = main([
                    "validate-graph", str(graph),
                    "--out-json", str(root / "result.json"),
                    "--out-md", str(root / "result.md"),
                    "--allow-untrusted-embedded-line-counts",
                ])
            self.assertEqual(rc, 0)
            self.assertFalse(validate.call_args.kwargs["require_trusted"])


class AndroidOutputWriterTests(unittest.TestCase):
    def test_json_writer_emits_parseable_json_with_real_newline(self):
        from usit.cli import _write_json_output
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "artifact.json"
            _write_json_output(path, {"ok": True, "items": [1, 2]}, overwrite=False)
            raw = path.read_bytes()
            self.assertEqual(json.loads(raw.decode("utf-8")), {"ok": True, "items": [1, 2]})
            self.assertTrue(raw.endswith(b"\\n"))

    def test_failed_exclusive_write_removes_partial_artifact(self):
        from usit.cli import _write_exclusive_bytes
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "partial.bin"
            with patch("usit.cli.os.write", side_effect=OSError("injected write failure")):
                with self.assertRaises(OSError):
                    _write_exclusive_bytes(path, b"payload")
            self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
