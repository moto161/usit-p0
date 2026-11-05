from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from usit.cli import _read_trace_json


class TraceabilityCliInputTests(unittest.TestCase):
    def test_reads_regular_json_and_returns_exact_bytes(self):
        with tempfile.TemporaryDirectory(prefix="usit-trace-cli-") as td:
            path = Path(td) / "input.json"
            raw = b'{"schema_version":"fixture","value":1}'
            path.write_bytes(raw)
            returned, document = _read_trace_json(path, len(raw))
            self.assertEqual(returned, raw)
            self.assertEqual(document, {"schema_version": "fixture", "value": 1})

    def test_rejects_oversized_input_before_unbounded_read(self):
        with tempfile.TemporaryDirectory(prefix="usit-trace-cli-") as td:
            path = Path(td) / "large.json"
            path.write_bytes(b'{"value":"' + b"x" * 128 + b'"}')
            with self.assertRaisesRegex(ValueError, "exceeds size limit"):
                _read_trace_json(path, 32)

    def test_rejects_symbolic_link_input(self):
        with tempfile.TemporaryDirectory(prefix="usit-trace-cli-") as td:
            root = Path(td)
            target = root / "target.json"
            link = root / "link.json"
            target.write_text('{"safe":true}', encoding="utf-8")
            try:
                link.symlink_to(target)
            except (OSError, NotImplementedError) as exc:
                self.skipTest(f"symbolic link creation unavailable: {exc}")
            with self.assertRaises((OSError, ValueError)):
                _read_trace_json(link, 1024)

    def test_rejects_directory_as_input(self):
        with tempfile.TemporaryDirectory(prefix="usit-trace-cli-") as td:
            with self.assertRaises((OSError, ValueError)):
                _read_trace_json(Path(td), 1024)


if __name__ == "__main__":
    unittest.main()
