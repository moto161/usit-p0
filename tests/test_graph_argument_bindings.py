"""Focused validation tests for call argument-to-parameter evidence."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from usit.graph import validate_graph

FIXTURES = Path(__file__).parent / "fixtures" / "graphs"


class GraphArgumentBindingValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "app.py").write_text("def run():\n    return run()\n", encoding="utf-8")
        self.graph = json.loads((FIXTURES / "valid_v1.json").read_text(encoding="utf-8"))

    def tearDown(self):
        self.temp.cleanup()

    def codes(self, result):
        return {item["code"] for item in result["errors"]}

    def test_valid_resolved_argument_mapping_is_accepted(self):
        graph = copy.deepcopy(self.graph)
        edge = graph["edges"][1]
        edge["argument_binding_status"] = "EXACT_STATIC_MAPPING"
        edge["argument_bindings"] = [{
            "argument_index": 0, "argument_kind": "POSITIONAL",
            "argument_expression": "user_id",
            "argument_location": {"path": "app.py", "line": 2, "column": 9},
            "parameter_name": "owner_id", "parameter_index": 0,
            "parameter_declaration": {"path": "app.py", "line": 1, "column": 9},
        }]
        self.assertTrue(validate_graph(graph, self.root)["is_valid"])

    def test_unsupported_argument_shape_rejects_partial_mapping(self):
        graph = copy.deepcopy(self.graph)
        edge = graph["edges"][1]
        edge["argument_binding_status"] = "UNSUPPORTED_ARGUMENT_SHAPE"
        edge["argument_bindings"] = [{"parameter_name": "owner_id"}]
        self.assertIn("PARTIAL_UNSUPPORTED_ARGUMENT_BINDINGS",
                      self.codes(validate_graph(graph, self.root)))

    def test_argument_mapping_on_unresolved_call_is_rejected(self):
        graph = copy.deepcopy(self.graph)
        edge = graph["edges"][1]
        edge["resolved"] = False
        edge["resolution_status"] = "UNKNOWN"
        edge["argument_binding_status"] = "EXACT_STATIC_MAPPING"
        edge["argument_bindings"] = []
        self.assertIn("ARGUMENT_BINDING_ON_UNRESOLVED_CALL",
                      self.codes(validate_graph(graph, self.root)))


if __name__ == "__main__":
    unittest.main()
