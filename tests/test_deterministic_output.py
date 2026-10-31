import tempfile
import unittest
from pathlib import Path

from tests.synthetic_scan import scan_project


class DeterministicOutputTests(unittest.TestCase):
    def test_three_identical_fixture_scans_have_identical_core_bytes(self):
        fixture = Path(__file__).parent / "fixtures" / "javascript_typescript"
        with tempfile.TemporaryDirectory(prefix="usit-determinism-") as td:
            root = Path(td) / "fixture"
            import shutil
            shutil.copytree(fixture, root)
            outputs = []
            for index in range(3):
                out = Path(td) / f"scan-{index + 1}"
                summary = scan_project(str(root), str(out), force=True)
                self.assertEqual(summary["js_ts_analysis"]["parser_version"], "6.0.3")
                outputs.append(out)
            for name in ("knowledge_graph.json", "parser_contract.json", "scan_manifest.json"):
                with self.subTest(artifact=name):
                    payloads = [(out / name).read_bytes() for out in outputs]
                    self.assertEqual(payloads[0], payloads[1])
                    self.assertEqual(payloads[1], payloads[2])


if __name__ == "__main__":
    unittest.main()
