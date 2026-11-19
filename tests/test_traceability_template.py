from __future__ import annotations
import hashlib
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from usit.traceability_template import create_feature_mapping_template


class TraceabilityTemplateTests(unittest.TestCase):
    def test_creates_exact_feature_identities_without_inventing_links(self):
        semantics = {"features": [
            {"name": "照片列表", "source": "docs/req.md", "line": 8},
            {"name": "照片删除", "source": "docs/req.md", "line": 15},
        ]}
        result = create_feature_mapping_template(semantics, "a" * 64)
        self.assertEqual(result["template_status"], "INCOMPLETE_REVIEW_REQUIRED")
        self.assertEqual(result["mapping_count"], 2)
        self.assertTrue(all(item["decision"] == "NEEDS_REVIEW" for item in result["mappings"]))
        self.assertTrue(all(item["business_function_id"] is None for item in result["mappings"]))
        self.assertTrue(all(item["reviewed_by"] == "" and item["reviewed_at"] == "" for item in result["mappings"]))

    def test_rejects_duplicate_feature_identity_and_invalid_hash(self):
        with self.assertRaisesRegex(ValueError, "ambiguous duplicate"):
            create_feature_mapping_template({"features": [
                {"name": "A", "source": "req.md", "line": 1},
                {"name": "A", "source": "req.md", "line": 1},
            ]}, "a" * 64)
        with self.assertRaisesRegex(ValueError, "64-character"):
            create_feature_mapping_template({"features": []}, "bad")

    def test_cli_creates_hash_bound_template_and_never_overwrites(self):
        from usit.cli import main
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            sem_path, graph_path, out_path = base / "sem.json", base / "graph.json", base / "mapping.json"
            sem_path.write_text(json.dumps({"features": [{"name": "照片列表", "source": "req.md", "line": 3}]}), encoding="utf-8")
            graph_bytes = json.dumps({"schema_version": "usit-graph-v1", "business_understanding": {}}).encode("utf-8")
            graph_path.write_bytes(graph_bytes)
            with redirect_stdout(StringIO()):
                code = main(["init-traceability-template", "--semantics", str(sem_path),
                    "--graph", str(graph_path), "--out", str(out_path)])
            self.assertEqual(code, 0)
            result = json.loads(out_path.read_text(encoding="utf-8"))
            self.assertEqual(result["source_graph_sha256"], hashlib.sha256(graph_bytes).hexdigest())
            with redirect_stdout(StringIO()):
                second_code = main(["init-traceability-template", "--semantics", str(sem_path),
                    "--graph", str(graph_path), "--out", str(out_path)])
            self.assertEqual(second_code, 2)
            self.assertEqual(json.loads(out_path.read_text(encoding="utf-8")), result)


if __name__ == "__main__":
    unittest.main()
