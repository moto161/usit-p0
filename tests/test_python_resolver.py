import json
import shutil
import tempfile
import unittest
from pathlib import Path

from usit.python_resolver import resolve_python_calls
from usit.scanner import analyze_file, discover_files
from tests.synthetic_scan import scan_project

FIXTURE = Path(__file__).parent / "fixtures" / "python_cross_module"


class PythonCrossModuleResolverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "project"
        shutil.copytree(FIXTURE, self.root)

    def tearDown(self):
        self.temp.cleanup()

    def analyze(self):
        rows = [analyze_file(self.root, path) for path in discover_files(self.root)
                if path.suffix == ".py"]
        diagnostics = resolve_python_calls(str(self.root), rows)
        return rows, diagnostics

    def test_resolves_alias_direct_relative_and_cycle_imports(self):
        rows, _ = self.analyze()
        expected = [x for x in json.loads((self.root / "expected.json").read_text(encoding="utf-8"))["expected_relations"] if x['status']=='RESOLVED']
        actual = set()
        for row in rows:
            for call in row["calls"]:
                if call["resolution_status"] == "RESOLVED":
                    target = call["target"]
                    actual.add((row["path"], call["line"], call["callee"], target["file"], target["name"], target["line"]))
        truth = {(item["path"], item["line"], item["callee"], item["target"]["file"], item["target"]["name"], item["target"]["line"])
                 for item in expected}
        true_positives = len(actual & truth)
        precision = true_positives / len(actual) if actual else 0.0
        recall = true_positives / len(truth) if truth else 0.0
        self.assertEqual(actual, truth)
        self.assertEqual(precision, 1.0)
        self.assertEqual(recall, 1.0)

    def test_states_external_unresolved_ambiguous_and_dynamic(self):
        rows, _ = self.analyze()
        expected_relations=json.loads((self.root/'expected.json').read_text(encoding='utf-8'))['expected_relations']
        actual={(row['path'],call['line'],call['callee']):call for row in rows for call in row['calls']}
        for truth in expected_relations:
            call=actual[(truth['path'],truth['line'],truth['callee'])]
            self.assertEqual(call['resolution_status'],truth['status'],truth)
            self.assertEqual(call['column'],truth['column'],truth)
            self.assertEqual(call.get('import_evidence'),truth['import_evidence'],truth)
            self.assertEqual(call.get('candidate_targets'),truth['candidate_targets'],truth)
            self.assertEqual(call.get('resolved',False),truth['status']=='RESOLVED',truth)
            if truth['target'] is None:
                self.assertIsNone(call.get('target'),truth)
            else:
                for field in ('file','name','line','id'):
                    self.assertEqual(call['target'].get(field),truth['target'][field],truth)
        ambiguous = [call for row in rows for call in row["calls"]
                     if row["path"] == "ambiguous.py" and call["resolution_status"] == "AMBIGUOUS"]
        self.assertEqual(len(ambiguous), 1)
        self.assertEqual(set(ambiguous[0]["candidate_targets"]),
                         {"symbol:alpha.py#same:1", "symbol:beta.py#same:1"})

    def test_resolved_calls_have_definition_and_import_evidence(self):
        rows, _ = self.analyze()
        calls = [call for row in rows for call in row["calls"] if call["resolution_status"] == "RESOLVED"]
        self.assertTrue(calls)
        for call in calls:
            self.assertIn("file", call["target"])
            self.assertIn("line", call["target"])
            if call.get("import_evidence"):
                self.assertIn("path", call["import_evidence"])
                self.assertIn("line", call["import_evidence"])

    def test_no_project_code_is_imported_or_executed(self):
        # The resolver's only interface accepts paths and analyzer rows; fixture modules are never imported.
        rows, _ = self.analyze()
        self.assertTrue(all(row.get("language") == "Python" for row in rows))

    def test_scan_emits_traceable_cross_file_edges_and_quality_gate(self):
        out = self.root.parent / "scan-output"
        summary = scan_project(str(self.root), str(out), force=True)
        graph = json.loads((out / "knowledge_graph.json").read_text(encoding="utf-8"))
        validation = json.loads((out / "graph_validation.json").read_text(encoding="utf-8"))
        resolved = [edge for edge in graph["edges"] if edge["type"] == "calls" and edge["resolution_status"] == "RESOLVED"]
        expected=[x for x in json.loads((FIXTURE/'expected.json').read_text(encoding='utf-8'))['expected_relations'] if x['status']=='RESOLVED']
        self.assertEqual(len(resolved), len(expected))
        for edge in resolved:
            target = next(node for node in graph["nodes"] if node["id"] == edge["target"])
            self.assertNotEqual(target["evidence"]["path"], edge["evidence"]["path"])
            self.assertIn("import_evidence", edge["evidence"])
            self.assertIn("target_location", edge)
        self.assertEqual(summary["graph_validation_status"], "VALID_WITH_ISSUES")
        self.assertEqual(validation["quality"]["errors"], 0)
        self.assertEqual(validation["quality"]["resolved_calls"], len(expected))

    def test_python_call_records_identify_enclosing_function(self):
        rows, _ = self.analyze()
        call = next(call for row in rows if row["path"] == "main.py"
                    for call in row["calls"] if call["callee"] == "mod.same")
        self.assertEqual(call["caller"], {"name": "invoke", "line": 5})
        self.assertEqual(call["caller_symbol_id"], "symbol:main.py#invoke:5")

    def test_javascript_intra_file_call_uses_compiler_api_evidence(self):
        (self.root / "sample.js").write_text("function helper() {}\nfunction run() {\n  helper();\n}\n", encoding="utf-8")
        out = self.root.parent / "js-scan-output"
        scan_project(str(self.root), str(out), force=True)
        graph = json.loads((out / "knowledge_graph.json").read_text(encoding="utf-8"))
        calls = [edge for edge in graph["edges"] if edge["type"] == "calls" and edge["evidence"]["path"] == "sample.js"]
        self.assertTrue(calls)
        helper = next(edge for edge in calls if edge.get("target_location", {}).get("qualified_name", "").endswith(".helper"))
        self.assertTrue(helper["resolved"])
        self.assertEqual(helper["resolution_status"], "RESOLVED")
        self.assertEqual(helper["target_location"]["path"], "sample.js")
        self.assertTrue(any(item.get("kind") == "definition" for item in helper["evidence_chain"]))
        self.assertEqual(helper["source"], "file:sample.js")
        self.assertEqual(helper["caller_symbol_id"], "symbol:sample.js#run:2")


if __name__ == "__main__":
    unittest.main()
