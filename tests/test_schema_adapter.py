import copy
import unittest
from types import MappingProxyType

from usit.schema_adapter import UnsupportedSchemaError, adapt_legacy
from usit.understanding_model import ConclusionType, ExecutionStatus, _canon_json


class SchemaAdapterTests(unittest.TestCase):
    def test_test_plan_known_fields_and_raw_origin_are_preserved(self):
        plan = {"schema_version": "usit-test-plan-v1", "project_root": "project",
                "execution_policy": "PLAN_ONLY_NOT_EXECUTED",
                "items": [{"id": "T001", "type": "build_smoke", "goal": "check build",
                           "suggested_action": "inspect build marker", "evidence": ["pom.xml"],
                           "status": "NOT_RUN"}]}
        original = copy.deepcopy(plan)
        result = adapt_legacy(plan)
        spec = result["test_specifications"][0]
        self.assertEqual(spec.target, "build_smoke")
        self.assertEqual(spec.purpose, "check build")
        self.assertEqual(spec.steps, ("inspect build marker",))
        self.assertEqual(_canon_json(spec.raw_source), _canon_json(plan["items"][0]))
        self.assertIn("usit-test-plan-v1:items[]", spec.mapping_method)
        self.assertEqual(_canon_json(result["raw_input"]), _canon_json(original))
        with self.assertRaises((TypeError, AttributeError)):
            result["raw_input"]["project_root"] = "tampered"
        self.assertEqual(plan, original)

    def test_missing_plan_requirements_are_unknown_not_fabricated(self):
        result = adapt_legacy({"schema_version": "usit-test-plan-v1",
                               "items": [{"id": "T", "type": "manual", "goal": "inspect"}]})
        spec = result["test_specifications"][0]
        self.assertIsNone(spec.expected_result)
        self.assertIsNone(spec.cleanup_strategy)
        self.assertTrue(any("preconditions" in reason for reason in spec.incomplete_reasons))
        self.assertEqual(spec.evidence_refs[0].relative_path, None)
        self.assertIsNone(spec.evidence_refs[0].line)
        self.assertEqual(spec.evidence_refs[0].summary, "inspect")

    def test_new_spec_never_imports_execution_pass(self):
        result = adapt_legacy({"schema_version": "usit-test-plan-v1",
                               "items": [{"id": "T", "type": "x", "goal": "y", "status": "PASS"}]})
        spec = result["test_specifications"][0]
        self.assertEqual(spec.execution.status, ExecutionStatus.NOT_RUN)
        self.assertIsNone(spec.execution.run_id)
        self.assertIsNone(spec.execution.actual_result)
        self.assertTrue(any("legacy execution status" in x for x in spec.incomplete_reasons))

    def test_resolved_graph_edge_is_static_inference_not_verified_fact(self):
        graph = {"schema_version": "usit-graph-v1", "nodes": [],
                 "edges": [{"source": "a", "target": "b", "type": "calls",
                            "resolution_status": "RESOLVED",
                            "evidence": {"path": "src/a.py", "line": 4, "excerpt": "b()"}}]}
        original = copy.deepcopy(graph)
        result = adapt_legacy(graph)
        conclusion = result["conclusions"][0]
        self.assertEqual(conclusion.conclusion_type, ConclusionType.STATIC_INFERENCE)
        self.assertIn("not runtime behavior verification", conclusion.limitations[0])
        self.assertEqual(conclusion.evidence_refs[0].relative_path, "src/a.py")
        self.assertEqual(conclusion.evidence_refs[0].line, 4)
        self.assertFalse(conclusion.evidence_refs[0].source_trusted)
        self.assertEqual(graph, original)

    def test_nonresolved_states_never_become_success(self):
        for state in ("AMBIGUOUS", "DYNAMIC", "UNRESOLVED", "EXTERNAL", "UNKNOWN"):
            with self.subTest(state=state):
                result = adapt_legacy({"schema_version": "usit-graph-v1", "nodes": [],
                                       "edges": [{"source": "a", "target": "b", "type": "calls",
                                                  "resolution_status": state}]})
                self.assertEqual(result["conclusions"][0].conclusion_type, ConclusionType.UNKNOWN)
                self.assertTrue(result["uncertainties"])

    def test_missing_location_digest_and_trust_are_not_invented(self):
        result = adapt_legacy({"schema_version": "usit-graph-v1", "nodes": [],
                               "edges": [{"source": "a", "target": "b", "type": "calls",
                                          "resolution_status": "RESOLVED"}]})
        ev = result["evidence"][0]
        self.assertIsNone(ev.relative_path)
        self.assertIsNone(ev.line)
        self.assertIsNone(ev.symbol)
        self.assertIsNone(ev.digest_expected)
        self.assertIsNone(ev.digest_match)
        self.assertFalse(ev.source_trusted)
        self.assertFalse(ev.execution_verified)

    def test_unknown_schema_rejected(self):
        with self.assertRaises(UnsupportedSchemaError):
            adapt_legacy({"schema_version": "usit-test-plan-v99", "items": []})

    def test_required_legacy_fields_rejected(self):
        with self.assertRaises(UnsupportedSchemaError):
            adapt_legacy({"schema_version": "usit-graph-v1", "nodes": []})

    def test_mapping_output_is_separate_from_old_schema(self):
        source = {"schema_version": "usit-test-plan-v1", "items": []}
        mapped = adapt_legacy(source)
        self.assertEqual(source, {"schema_version": "usit-test-plan-v1", "items": []})
        self.assertEqual(mapped["schema_version"], "usit-understanding-enhancement-v1")
        self.assertNotIn("understanding", source)


class SchemaAdapterNegativeTests(unittest.TestCase):
    """P0.7.18 negative matrix for the read-only adapter."""

    def test_stable_ids_are_deterministic_for_same_input(self):
        src = {"schema_version": "usit-test-plan-v1", "items": [
            {"id": "T001", "type": "build_smoke", "goal": "check build",
             "suggested_action": "inspect", "evidence": ["pom.xml"], "status": "NOT_RUN"}]}
        a = adapt_legacy(src)
        b = adapt_legacy(src)
        self.assertEqual([s.specification_id for s in a["test_specifications"]],
                         [s.specification_id for s in b["test_specifications"]])

    def test_different_content_yields_different_ids(self):
        src_a = {"schema_version": "usit-test-plan-v1", "items": [
            {"id": "T", "type": "x", "goal": "a", "suggested_action": "s"}]}
        src_b = {"schema_version": "usit-test-plan-v1", "items": [
            {"id": "T", "type": "x", "goal": "b", "suggested_action": "s"}]}
        ids_a = {s.specification_id for s in adapt_legacy(src_a)["test_specifications"]}
        ids_b = {s.specification_id for s in adapt_legacy(src_b)["test_specifications"]}
        self.assertNotEqual(ids_a, ids_b)

    def test_adapter_never_emits_verified_fact(self):
        graph = {"schema_version": "usit-graph-v1", "nodes": [], "edges": [
            {"source": "a", "target": "b", "type": "calls",
             "resolution_status": "RESOLVED"}]}
        result = adapt_legacy(graph)
        self.assertNotIn(ConclusionType.VERIFIED_FACT,
                         {c.conclusion_type for c in result["conclusions"]})

    def test_adapter_never_emits_execution_pass(self):
        result = adapt_legacy({"schema_version": "usit-test-plan-v1", "items": [
            {"id": "T", "type": "x", "goal": "y", "status": "PASS"}]})
        for spec in result["test_specifications"]:
            self.assertEqual(spec.execution.status, ExecutionStatus.NOT_RUN)

    def test_unhashable_schema_version_rejected_cleanly(self):
        # A list schema_version must raise UnsupportedSchemaError, not TypeError.
        with self.assertRaises(UnsupportedSchemaError):
            adapt_legacy({"schema_version": [], "items": []})

    def test_unhashable_resolution_status_rejected_cleanly(self):
        graph = {"schema_version": "usit-graph-v1", "nodes": [], "edges": [
            {"source": "a", "target": "b", "type": "calls",
             "resolution_status": ["RESOLVED"]}]}
        # Unhashable state is treated as missing/unsupported -> UNKNOWN, no TypeError.
        result = adapt_legacy(graph)
        self.assertEqual(result["conclusions"][0].conclusion_type, ConclusionType.UNKNOWN)

    def test_raw_input_is_untouched_after_adapt(self):
        src = {"schema_version": "usit-test-plan-v1", "items": [
            {"id": "T", "type": "x", "goal": "y"}]}
        original = __import__("copy").deepcopy(src)
        adapt_legacy(src)
        self.assertEqual(src, original)

    def test_missing_legacy_ids_are_not_fabricated(self):
        result = adapt_legacy({"schema_version": "usit-test-plan-v1", "items": [{"goal": "g"}]})
        self.assertIsNone(result["evidence"][0].source_id)
        self.assertEqual(result["evidence"][0].locator, "$.items[0]")

    def test_same_id_and_content_at_different_locations_do_not_merge(self):
        plan = {"schema_version": "usit-test-plan-v1", "items": [
            {"id": "T", "type": "x", "goal": "same"},
            {"id": "T", "type": "x", "goal": "same"},
        ]}
        result = adapt_legacy(plan)
        self.assertEqual(result["test_specifications"][0].specification_id,
                         result["test_specifications"][1].specification_id)
        self.assertEqual(len(result["test_specifications"]), 2)
        self.assertNotEqual(result["evidence"][0].locator, result["evidence"][1].locator)

    def test_duplicate_graph_content_at_different_locations_is_preserved(self):
        edge = {"id": "E", "source": "a", "target": "b", "type": "calls",
                "resolution_status": "RESOLVED"}
        result = adapt_legacy({"schema_version": "usit-graph-v1", "nodes": [], "edges": [edge, edge]})
        self.assertEqual(len(result["conclusions"]), 1)
        self.assertEqual(len(result["conclusions"][0].evidence_refs), 2)
        self.assertEqual([e.locator for e in result["evidence"]], ["$.edges[0]", "$.edges[1]"])
        self.assertEqual([e.locator for e in result["conclusions"][0].evidence_refs],
                         ["$.edges[0]", "$.edges[1]"])

    def test_same_graph_identity_different_targets_creates_traceable_conflict(self):
        graph = {"schema_version": "usit-graph-v1", "nodes": [], "edges": [
            {"id": "E", "source": "a", "target": "b", "type": "calls",
             "resolution_status": "RESOLVED", "evidence": {"path": "src/a.py", "line": 3}},
            {"id": "E", "source": "a", "target": "c", "type": "calls",
             "resolution_status": "RESOLVED", "evidence": {"path": "src/a.py", "line": 8}},
        ]}
        result = adapt_legacy(graph)
        self.assertEqual(len(result["conclusions"]), 3)
        originals = [c for c in result["conclusions"] if c.conclusion_type is ConclusionType.STATIC_INFERENCE]
        conflicts = [c for c in result["conclusions"] if c.conclusion_type is ConclusionType.CONFLICT]
        self.assertEqual({c.raw_source["target"] for c in originals}, {"b", "c"})
        self.assertEqual(len(conflicts), 1)
        conflict = conflicts[0]
        self.assertIn("E", conflict.content)
        self.assertEqual(conflict.raw_source["left"]["id"], originals[0].conclusion_id)
        self.assertEqual(conflict.raw_source["right"]["id"], originals[1].conclusion_id)
        self.assertEqual({ref.relative_path for ref in conflict.evidence_refs}, {"src/a.py"})
        self.assertEqual({ref.line for ref in conflict.evidence_refs}, {3, 8})
        self.assertEqual({ref.source_id for ref in conflict.evidence_refs}, {"E"})
        self.assertIn("b", conflict.raw_source["left"]["content"])
        self.assertIn("c", conflict.raw_source["right"]["content"])
        self.assertEqual(len(conflict.raw_source["left"]["evidence_refs"]), 1)
        self.assertEqual(len(conflict.raw_source["right"]["evidence_refs"]), 1)
        conflict_uncertainties = [
            uncertainty for uncertainty in result["uncertainties"]
            if uncertainty.conflicting_conclusion_ids
        ]
        self.assertEqual(len(conflict_uncertainties), 1)
        uncertainty = conflict_uncertainties[0]
        self.assertEqual(
            set(uncertainty.conflicting_conclusion_ids),
            {originals[0].conclusion_id, originals[1].conclusion_id},
        )
        self.assertEqual(uncertainty.reason, conflict.content)
        self.assertTrue(uncertainty.minimum_resolution)

    def test_duplicate_conflict_inputs_emit_one_equivalent_uncertainty(self):
        edge_b = {"id": "E", "source": "a", "target": "b", "type": "calls",
                  "resolution_status": "RESOLVED", "evidence": {"path": "src/a.py", "line": 3}}
        edge_c = {"id": "E", "source": "a", "target": "c", "type": "calls",
                  "resolution_status": "RESOLVED", "evidence": {"path": "src/a.py", "line": 8}}
        result = adapt_legacy({"schema_version": "usit-graph-v1", "nodes": [],
                               "edges": [edge_b, edge_c, edge_b, edge_c]})
        conflicts = [c for c in result["conclusions"]
                     if c.conclusion_type is ConclusionType.CONFLICT]
        conflict_uncertainties = [u for u in result["uncertainties"]
                                  if u.conflicting_conclusion_ids]
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(len(conflict_uncertainties), 1)

    def test_distinct_conflict_pairs_and_existing_unknown_uncertainty_are_preserved(self):
        edges = [
            {"id": "E1", "source": "a", "target": "b", "type": "calls",
             "resolution_status": "RESOLVED"},
            {"id": "E1", "source": "a", "target": "c", "type": "calls",
             "resolution_status": "RESOLVED"},
            {"id": "E2", "source": "x", "target": "y", "type": "calls",
             "resolution_status": "RESOLVED"},
            {"id": "E2", "source": "x", "target": "z", "type": "calls",
             "resolution_status": "RESOLVED"},
            {"id": "E3", "source": "q", "target": "r", "type": "calls",
             "resolution_status": "UNKNOWN"},
        ]
        result = adapt_legacy({"schema_version": "usit-graph-v1", "nodes": [],
                               "edges": edges})
        conflicts = [c for c in result["conclusions"]
                     if c.conclusion_type is ConclusionType.CONFLICT]
        conflict_uncertainties = [u for u in result["uncertainties"]
                                  if u.conflicting_conclusion_ids]
        unknown_uncertainties = [u for u in result["uncertainties"]
                                 if not u.conflicting_conclusion_ids]
        self.assertEqual(len(conflicts), 2)
        self.assertEqual(len(conflict_uncertainties), 2)
        self.assertEqual(
            {frozenset(u.conflicting_conclusion_ids) for u in conflict_uncertainties},
            {frozenset((c.raw_source["left"]["id"], c.raw_source["right"]["id"]))
             for c in conflicts},
        )
        self.assertEqual(len(unknown_uncertainties), 1)
        self.assertEqual(
            unknown_uncertainties[0].missing_evidence,
            ("trusted source capture", "runtime execution evidence"),
        )

    def test_same_identity_different_locations_are_preserved_without_false_conflict(self):
        graph = {"schema_version": "usit-graph-v1", "nodes": [], "edges": [
            {"id": "E", "source": "a", "target": "b", "type": "calls",
             "resolution_status": "RESOLVED", "evidence": {"path": "src/a.py", "line": 3}},
            {"id": "E", "source": "a", "target": "b", "type": "calls",
             "resolution_status": "RESOLVED", "evidence": {"path": "src/a.py", "line": 8}},
        ]}
        result = adapt_legacy(graph)
        self.assertEqual(len(result["conclusions"]), 1)
        self.assertEqual(len(result["conclusions"][0].evidence_refs), 2)
        self.assertEqual({e.line for e in result["evidence"]}, {3, 8})
        self.assertEqual({e.line for e in result["conclusions"][0].evidence_refs}, {3, 8})
        self.assertFalse(any(c.conclusion_type is ConclusionType.CONFLICT for c in result["conclusions"]))
        self.assertFalse(any(u.conflicting_conclusion_ids for u in result["uncertainties"]))

    def test_unrelated_graph_metadata_difference_is_not_a_conflict(self):
        graph = {"schema_version": "usit-graph-v1", "nodes": [], "edges": [
            {"id": "E", "source": "a", "target": "b", "type": "calls",
             "resolution_status": "RESOLVED", "note": "first"},
            {"id": "E", "source": "a", "target": "b", "type": "calls",
             "resolution_status": "RESOLVED", "note": "second"},
        ]}
        result = adapt_legacy(graph)
        self.assertEqual(len(result["conclusions"]), 2)
        self.assertFalse(any(c.conclusion_type is ConclusionType.CONFLICT for c in result["conclusions"]))
        self.assertFalse(any(u.conflicting_conclusion_ids for u in result["uncertainties"]))

    def test_different_graph_source_ids_are_not_linked_as_conflict(self):
        graph = {"schema_version": "usit-graph-v1", "nodes": [], "edges": [
            {"id": "E1", "source": "a", "target": "b", "type": "calls",
             "resolution_status": "RESOLVED"},
            {"id": "E2", "source": "a", "target": "c", "type": "calls",
             "resolution_status": "RESOLVED"},
        ]}
        result = adapt_legacy(graph)
        self.assertEqual(len(result["conclusions"]), 2)
        self.assertFalse(any(c.conclusion_type is ConclusionType.CONFLICT for c in result["conclusions"]))
        self.assertFalse(any(u.conflicting_conclusion_ids for u in result["uncertainties"]))

    def test_nested_mappingproxy_is_adapted_without_mutating_input(self):
        evidence = MappingProxyType({"path": "src/a.py", "line": 4,
                                     "metadata": MappingProxyType({"labels": ["x"]})})
        edge = MappingProxyType({"id": "E", "source": "a", "target": "b",
                                 "type": "calls", "resolution_status": "RESOLVED",
                                 "evidence": evidence})
        graph = MappingProxyType({"schema_version": "usit-graph-v1",
                                  "nodes": [], "edges": [edge]})
        result = adapt_legacy(graph)
        self.assertEqual(result["evidence"][0].relative_path, "src/a.py")
        self.assertEqual(evidence["metadata"]["labels"], ["x"])
        self.assertEqual(edge["target"], "b")

    def test_cyclic_and_unsupported_legacy_values_are_rejected_cleanly(self):
        cyclic = {}
        cyclic["self"] = cyclic
        for bad in (cyclic, {"bad": {1, 2}}):
            with self.subTest(kind=type(bad).__name__):
                source = {"schema_version": "usit-test-plan-v1", "items": [bad]}
                with self.assertRaises(UnsupportedSchemaError):
                    adapt_legacy(source)

    def test_unstable_legacy_json_is_rejected_with_schema_error(self):
        with self.assertRaises(UnsupportedSchemaError):
            adapt_legacy({"schema_version": "usit-test-plan-v1", "items": [{"bad": {1, 2}}]})


if __name__ == "__main__":
    unittest.main()
