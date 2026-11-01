"""Regression coverage for discovery-to-open identity substitution."""
import os
import tempfile
import unittest
from pathlib import Path

from usit.safety import ScanBlocked
from usit.scanner import discover_files, open_input_file


class DiscoveryCtimeIdentityTests(unittest.TestCase):
    def test_changed_ctime_after_discovery_blocks_open_even_if_file_id_matches(self):
        if os.name == "nt":
            self.skipTest("Windows uses its stable file index; st_ctime is not used as identity")
        with tempfile.TemporaryDirectory(prefix="usit-ctime-") as tmp:
            root = Path(tmp) / "project"
            root.mkdir()
            target = root / "a.py"
            target.write_text("value = 1\n", encoding="utf-8")
            identities = {}
            discover_files(root, identities=identities)
            identity = identities["a.py"]
            self.assertEqual(len(identity), 5)
            expected = (identity[0], identity[1], identity[2] + 1, *identity[3:])
            with self.assertRaises(ScanBlocked) as blocked:
                open_input_file(target, 1024, expected_identity=expected)
            self.assertEqual(blocked.exception.code, "FILE_SUBSTITUTED_DURING_SCAN")


if __name__ == "__main__":
    unittest.main()
