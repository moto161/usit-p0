from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from usit import scanner


class PreviousManifestSafetyTests(unittest.TestCase):
    def test_loads_valid_previous_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            (output / "scan_manifest.json").write_text(
                json.dumps({"files": [{"path": "src/app.py", "sha256": "abc"}]}),
                encoding="utf-8",
            )
            self.assertEqual(
                scanner.load_previous(output), {"src/app.py": "abc"}
            )

    def test_rejects_oversized_previous_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            (output / "scan_manifest.json").write_bytes(b" " * 33)
            with patch.object(scanner, "MAX_PREVIOUS_MANIFEST_BYTES", 32):
                self.assertEqual(scanner.load_previous(output), {})

    def test_rejects_symlinked_previous_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root / "outside.json"
            outside.write_text('{"files":[{"path":"secret.py","sha256":"x"}]}', encoding="utf-8")
            output = root / "output"
            output.mkdir()
            try:
                (output / "scan_manifest.json").symlink_to(outside)
            except (OSError, NotImplementedError):
                self.skipTest("symlink creation is unavailable on this runner")
            self.assertEqual(scanner.load_previous(output), {})

    def test_malformed_or_wrong_shape_manifest_falls_back_to_empty(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            path = output / "scan_manifest.json"
            path.write_text("{", encoding="utf-8")
            self.assertEqual(scanner.load_previous(output), {})
            path.write_text('{"files": {}}', encoding="utf-8")
            self.assertEqual(scanner.load_previous(output), {})


if __name__ == "__main__":
    unittest.main()
