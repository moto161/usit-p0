import copy
import json
import tempfile
import unittest
from pathlib import Path

from usit.graph import validate_graph, validate_graph_file, render_validation_markdown

FIXTURES = Path(__file__).parent / "fixtures" / "graphs"


class GraphValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "app.py").write_text("def run():\n    return run()\n", encoding="utf-8")
        self.graph = json.loads((FIXTURES / "valid_v1.json").read_text(encoding="utf-8"))

    def tearDown(self):
        self.temp.cleanup()

    def codes(self, result, group="errors"):
        return {item["code"] for item in result[group]}

    def test_valid_v1_fixture_and_metrics(self):
        result = validate_graph(self.graph, self.root)
        self.assertEqual(result["status"], "VALID")
        self.assertEqual(result["quality"]["nodes_total"], 2)
        self.assertEqual(result["quality"]["edges_total"], 2)
        self.assertEqual(result["quality"]["endpoint_valid_rate"], 1.0)
        self.assertEqual(result["quality"]["resolved_calls"], 1)
        self.assertEqual(result["quality"]["unknown_calls"], 0)

    def test_duplicate_node_id(self):
        graph = copy.deepcopy(self.graph)
        graph["nodes"].append(copy.deepcopy(graph["nodes"][0]))
        self.assertIn("DUPLICATE_NODE_ID", self.codes(validate_graph(graph, self.root)))

    def test_missing_source_node(self):
        graph = copy.deepcopy(self.graph)
        graph["edges"][0]["source"] = "file:missing.py"
        self.assertIn("MISSING_SOURCE", self.codes(validate_graph(graph, self.root)))

    def test_missing_target_node(self):
        graph = copy.deepcopy(self.graph)
        graph["edges"][0]["target"] = "symbol:missing"
        self.assertIn("MISSING_TARGET", self.codes(validate_graph(graph, self.root)))

    def test_missing_evidence(self):
        graph = copy.deepcopy(self.graph)
        del graph["edges"][0]["evidence"]
        self.assertIn("EDGE_EVIDENCE", self.codes(validate_graph(graph, self.root)))

    def test_unknown_relation_type(self):
        graph = copy.deepcopy(self.graph)
        graph["edges"][0]["type"] = "teleports"
        self.assertIn("RELATION_TYPE", self.codes(validate_graph(graph, self.root)))

    def test_invalid_line_number(self):
        graph = copy.deepcopy(self.graph)
        graph["edges"][0]["evidence"]["line"] = 0
        self.assertIn("INVALID_LINE", self.codes(validate_graph(graph, self.root)))

    def test_out_of_range_line_number(self):
        graph = copy.deepcopy(self.graph)
        graph["edges"][0]["evidence"]["line"] = 99
        self.assertIn("LINE_OUT_OF_RANGE", self.codes(validate_graph(graph, self.root)))

    def test_path_traversal_is_rejected(self):
        graph = copy.deepcopy(self.graph)
        graph["edges"][0]["evidence"]["path"] = "../outside.py"
        self.assertIn("EDGE_EVIDENCE_PATH", self.codes(validate_graph(graph, self.root)))

    def test_windows_absolute_path_is_rejected(self):
        graph = copy.deepcopy(self.graph)
        graph["edges"][0]["evidence"]["path"] = "C:/outside.py"
        self.assertIn("EDGE_EVIDENCE_PATH", self.codes(validate_graph(graph, self.root)))

    def test_missing_project_file_reference_is_rejected(self):
        graph = copy.deepcopy(self.graph)
        graph["nodes"][0]["id"] = "file:missing.py"
        graph["nodes"][0]["label"] = "missing.py"
        graph["nodes"][0]["evidence"]["path"] = "missing.py"
        graph["nodes"][1]["evidence"]["path"] = "missing.py"
        for edge in graph["edges"]:
            edge["source"] = "file:missing.py"
            edge["evidence"]["path"] = "missing.py"
        result = validate_graph(graph, self.root)
        self.assertIn("FILE_REFERENCE_MISSING", self.codes(result))
        self.assertIn("EDGE_FILE_MISSING", self.codes(result))

    def test_unknown_relation_status_is_not_success(self):
        graph = copy.deepcopy(self.graph)
        graph["edges"][1]["resolved"] = False
        graph["edges"][1]["resolution_status"] = "MAYBE"
        result = validate_graph(graph, self.root)
        self.assertIn("UNKNOWN_RELATION_STATUS", self.codes(result, "unknown"))
        self.assertEqual(result["quality"]["resolved_calls"], 0)
        self.assertEqual(result["quality"]["unknown_calls"], 1)

    def test_legacy_v1_boolean_does_not_claim_resolved_without_explicit_state(self):
        graph = copy.deepcopy(self.graph)
        del graph["edges"][1]["resolution_status"]
        result = validate_graph(graph, self.root)
        self.assertTrue(result["is_valid"])
        self.assertEqual(result["status"], "VALID_WITH_ISSUES")
        self.assertEqual(result["quality"]["resolved_calls"], 0)
        self.assertEqual(result["quality"]["unknown_calls"], 1)
        self.assertIn("LEGACY_RESOLUTION_STATE", self.codes(result, "unknown"))

    def test_wrong_version_blocks_valid_status(self):
        graph = copy.deepcopy(self.graph)
        graph["schema_version"] = "usit-graph-v99"
        self.assertEqual(validate_graph(graph, self.root)["status"], "INVALID")

    def test_empty_graph_is_explicitly_warned(self):
        result = validate_graph({"schema_version": "usit-graph-v1", "nodes": [], "edges": []}, self.root)
        self.assertTrue(result["is_valid"])
        self.assertIn("EMPTY_GRAPH", self.codes(result, "warnings"))
        self.assertIsNone(result["quality"]["endpoint_valid_rate"])

    def test_corrupt_json_has_machine_and_human_reports(self):
        source = FIXTURES / "corrupt.json"
        json_out = self.root / "validation.json"
        md_out = self.root / "validation.md"
        result = validate_graph_file(source, self.root, json_out, md_out)
        self.assertEqual(result["status"], "INVALID")
        self.assertIn("INVALID_JSON", self.codes(result))
        self.assertEqual(json.loads(json_out.read_text(encoding="utf-8"))["status"], "INVALID")
        self.assertIn("INVALID_JSON", md_out.read_text(encoding="utf-8"))

    def test_duplicate_edge_ids_when_present(self):
        graph = copy.deepcopy(self.graph)
        graph["edges"][0]["id"] = "edge-1"
        graph["edges"][1]["id"] = "edge-1"
        self.assertIn("DUPLICATE_EDGE_ID", self.codes(validate_graph(graph, self.root)))

    def test_resolved_call_requires_real_definition_edge(self):
        graph = copy.deepcopy(self.graph)
        graph["edges"].pop(0)
        graph["edge_type_counts"] = {"calls": 1}
        result = validate_graph(graph, self.root)
        self.assertIn("RESOLVED_TARGET_UNDEFINED", self.codes(result))
        self.assertEqual(result["status"], "INVALID")

    def test_markdown_lists_relation_reference_and_reason(self):
        graph = copy.deepcopy(self.graph)
        graph["edges"][0]["target"] = "missing"
        result = validate_graph(graph, self.root)
        report = render_validation_markdown(result)
        self.assertIn("edge:000001", report)
        self.assertIn("MISSING_TARGET", report)


if __name__ == "__main__":
    unittest.main()
