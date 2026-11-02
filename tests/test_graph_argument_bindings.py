"""Focused tests for graph-v1 argument-to-parameter binding validation."""
import copy
import tempfile
import unittest
from pathlib import Path

from usit.graph import validate_graph


class GraphArgumentBindingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "app.py").write_text(
            "def owner(owner_id):\n"
            "    return owner_id\n"
            "\n"
            "def run(user_id):\n"
            "    return owner(user_id)\n",
            encoding="utf-8",
        )
        self.graph = {
            "schema_version": "usit-graph-v1",
            "nodes": [
                {"id": "file:app.py", "type": "file", "label": "app.py",
                 "evidence": {"path": "app.py"}},
                {"id": "symbol:app.py#owner:1", "type": "function", "label": "owner",
                 "evidence": {"path": "app.py", "line": 1}},
                {"id": "symbol:app.py#run:4", "type": "function", "label": "run",
                 "evidence": {"path": "app.py", "line": 4}},
            ],
            "edges": [
                {"source": "file:app.py", "target": "symbol:app.py#owner:1",
                 "type": "defines", "evidence": {"path": "app.py", "line": 1},
                 "method": "python-ast", "confidence": "high"},
                {"source": "file:app.py", "target": "symbol:app.py#run:4",
                 "type": "defines", "evidence": {"path": "app.py", "line": 4},
                 "method": "python-ast", "confidence": "high"},
                {"source": "file:app.py", "target": "symbol:app.py#owner:1",
                 "type": "calls", "evidence": {"path": "app.py", "line": 5},
                 "method": "python-ast", "confidence": "high",
                 "resolved": True, "resolution_status": "RESOLVED"},
            ],
            "edge_type_counts": {"defines": 2, "calls": 1},
            "node_type_counts": {"file": 1, "function": 2},
        }

    def tearDown(self):
        self.temp.cleanup()

    def codes(self, result):
        return {item["code"] for item in result["errors"]}

    def test_valid_resolved_argument_mapping_is_accepted(self):
        graph = copy.deepcopy(self.graph)
        edge = graph["edges"][2]
        edge["argument_binding_status"] = "EXACT_STATIC_MAPPING"
        edge["argument_bindings"] = [{
            "argument_index": 0, "argument_kind": "POSITIONAL",
            "argument_expression": "user_id",
            "argument_location": {"path": "app.py", "line": 5, "column": 17},
            "parameter_name": "owner_id", "parameter_index": 0,
            "parameter_declaration": {"path": "app.py", "line": 1, "column": 11},
        }]
        self.assertTrue(validate_graph(graph, self.root)["is_valid"])

    def test_unsupported_argument_shape_rejects_partial_mapping(self):
        graph = copy.deepcopy(self.graph)
        edge = graph["edges"][2]
        edge["argument_binding_status"] = "UNSUPPORTED_ARGUMENT_SHAPE"
        edge["argument_bindings"] = [{"parameter_name": "owner_id"}]
        self.assertIn("PARTIAL_UNSUPPORTED_ARGUMENT_BINDINGS",
                      self.codes(validate_graph(graph, self.root)))

    def test_argument_mapping_on_unresolved_call_is_rejected(self):
        graph = copy.deepcopy(self.graph)
        edge = graph["edges"][2]
        edge["resolved"] = False
        edge["resolution_status"] = "UNKNOWN"
        edge["argument_binding_status"] = "EXACT_STATIC_MAPPING"
        edge["argument_bindings"] = []
        self.assertIn("ARGUMENT_BINDING_ON_UNRESOLVED_CALL",
                      self.codes(validate_graph(graph, self.root)))


if __name__ == "__main__":
    unittest.main()
