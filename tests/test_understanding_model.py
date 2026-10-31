import unittest

from usit.understanding_model import (
    SCHEMA_VERSION, CollectionStatus, ConclusionRecord, ConclusionType,
    EvidenceReference, ExecutionRecord, ExecutionStatus, SourceType,
    TestSpecification, TrustLevel, UncertaintyRecord, make_conflict,
    validate_document, _canon_json, _stable_id,
    _dedupe_conclusions_with_conflict, InvalidModelInputError,
)


def evidence(**overrides):
    values = dict(source_schema="usit-graph-v1", source_id="edge-1",
                  collection_status=CollectionStatus.PRESENT,
                  trust_level=TrustLevel.NOT_VERIFIED, content_present=True,
                  digest_expected=None, digest_match=None, source_trusted=False,
                  execution_verified=False, raw_value={"x": 1})
    values.update(overrides)
    return EvidenceReference(**values)


class UnderstandingModelTests(unittest.TestCase):
    def test_schema_document_requires_all_sections(self):
        validate_document({"schema_version": SCHEMA_VERSION, "conclusions": [],
                           "evidence": [], "test_specifications": [], "uncertainties": []})

    def test_unknown_schema_rejected(self):
        with self.assertRaisesRegex(ValueError, "unsupported"):
            validate_document({"schema_version": "usit-understanding-v99"})

    def test_missing_required_section_rejected(self):
        with self.assertRaisesRegex(ValueError, "evidence"):
            validate_document({"schema_version": SCHEMA_VERSION, "conclusions": [],
                               "test_specifications": [], "uncertainties": []})

    def test_illegal_conclusion_enum_rejected(self):
        with self.assertRaisesRegex(ValueError, "ConclusionType"):
            ConclusionRecord("c1", "claim", "RESOLVED")

    def test_verified_fact_requires_trusted_executed_evidence(self):
        # P0.7.18: no trusted external verification protocol is authorized, so
        # VERIFIED_FACT is hard-blocked regardless of self-asserted evidence.
        with self.assertRaisesRegex(ValueError, "VERIFIED_FACT requires"):
            ConclusionRecord("c1", "claim", ConclusionType.VERIFIED_FACT,
                             evidence_refs=(evidence(),), source_type=SourceType.GRAPH)

    def test_matching_digest_does_not_raise_source_trust(self):
        ref = evidence(digest_expected="abc", digest_match=True)
        ref.validate()
        self.assertFalse(ref.source_trusted)
        self.assertEqual(ref.trust_level, TrustLevel.NOT_VERIFIED)

    def test_missing_digest_remains_unknown(self):
        ref = evidence()
        ref.validate()
        self.assertIsNone(ref.digest_expected)
        self.assertIsNone(ref.digest_match)

    def test_not_run_cannot_claim_run_or_result(self):
        with self.assertRaisesRegex(ValueError, "empty run_id"):
            ExecutionRecord(ExecutionStatus.NOT_RUN, run_id="fake", actual_result="PASS")

    def test_illegal_execution_enum_rejected(self):
        with self.assertRaisesRegex(ValueError, "ExecutionStatus"):
            ExecutionRecord(status="PASS")

    def test_test_specification_defaults_to_not_run(self):
        spec = TestSpecification("t1", None, None, incomplete_reasons=("missing",))
        spec.validate()
        self.assertFalse(spec.complete)
        self.assertEqual(spec.execution.status, ExecutionStatus.NOT_RUN)
        self.assertIsNone(spec.execution.run_id)
        self.assertIsNone(spec.execution.actual_result)

    def test_conflict_keeps_both_claims_and_links_ids(self):
        left = ConclusionRecord("left", "A", ConclusionType.STATIC_INFERENCE,
                               evidence_refs=(evidence(source_id="left-src"),))
        right = ConclusionRecord("right", "B", ConclusionType.UNKNOWN,
                                 evidence_refs=(evidence(source_id="right-src"),))
        conflict, uncertainty = make_conflict(left, right, "same property has incompatible values")
        self.assertEqual(conflict.conclusion_type, ConclusionType.CONFLICT)
        self.assertEqual(uncertainty.conflicting_conclusion_ids, ("left", "right"))
        self.assertEqual({e.source_id for e in conflict.evidence_refs}, {"left-src", "right-src"})

    def test_unresolved_uncertainty_requires_reason(self):
        with self.assertRaises(ValueError):
            UncertaintyRecord("u1", "").validate()


class UnderstandingModelNegativeTests(unittest.TestCase):
    """P0.7.18 negative matrix: forged trust, stable IDs, frozen nesting, typing."""

    def test_fabricated_source_trusted_flag_is_rejected(self):
        # Forged source_trusted + TRUSTED must still fail to produce VERIFIED_FACT.
        with self.assertRaises(ValueError):
            EvidenceReference("g", "e1", collection_status=CollectionStatus.PRESENT,
                              trust_level=TrustLevel.TRUSTED, content_present=True,
                              source_trusted=True, execution_verified=True,
                              raw_value={"k": 1})

    def test_execution_verified_flag_does_not_create_verified_fact(self):
        with self.assertRaises(ValueError):
            EvidenceReference("g", "e2", collection_status=CollectionStatus.PRESENT,
                              trust_level=TrustLevel.NOT_VERIFIED, content_present=True,
                              execution_verified=True, raw_value={"k": 1})

    def test_missing_source_id_is_not_fabricated(self):
        # A reference without source_id validates; the model must not invent one.
        ref = EvidenceReference(None, None, collection_status=CollectionStatus.PRESENT,
                                content_present=True, raw_value={"k": 1})
        ref.validate()
        self.assertIsNone(ref.source_id)

    def test_digest_match_does_not_derive_source_trust(self):
        ref = evidence(digest_expected="abc", digest_match=True)
        ref.validate()
        self.assertFalse(ref.source_trusted)
        self.assertEqual(ref.trust_level, TrustLevel.NOT_VERIFIED)

    def test_hand_crafted_pass_rejected_without_execution_record(self):
        with self.assertRaises(ValueError):
            ExecutionRecord(status=ExecutionStatus.PASS, run_id="fake")

    def test_specification_incomplete_when_expected_or_cleanup_missing(self):
        spec = TestSpecification("t1", "target", "purpose", steps=("s",),
                                preconditions=("p",), identity_and_data_requirements=("i",),
                                evidence_requirements=("e",))
        self.assertFalse(spec.complete)
        self.assertIn("expected_result", spec.incomplete_reasons)
        self.assertIn("cleanup_strategy", spec.incomplete_reasons)

    def test_complete_is_derived_from_fields_not_flag(self):
        # Empty spec: complete must be False even though caller passed no flag.
        spec = TestSpecification("t1", None, None, incomplete_reasons=())
        self.assertFalse(spec.complete)
        self.assertGreater(len(spec.incomplete_reasons), 0)

    def test_stable_id_is_deterministic_for_same_input(self):
        parts = ("usit-graph-v1", "edge-1", _canon_json({"a": 1, "b": [1, 2]}))
        self.assertEqual(_stable_id("con", *parts), _stable_id("con", *parts))

    def test_stable_id_differs_for_different_content(self):
        a = ("usit-graph-v1", "edge-1", _canon_json({"a": 1}))
        b = ("usit-graph-v1", "edge-1", _canon_json({"a": 2}))
        self.assertNotEqual(_stable_id("con", *a), _stable_id("con", *b))

    def test_unstable_input_rejected_not_random_fallback(self):
        with self.assertRaises(InvalidModelInputError):
            _canon_json({"s": {1, 2}})  # set is not a supported JSON value

    def test_illegal_conclusion_enum_constructor_rejected(self):
        with self.assertRaises(ValueError):
            ConclusionType("BOGUS")

    def test_nested_raw_source_is_frozen_against_mutation(self):
        payload = {"nested": {"x": 1}, "list": [1, 2]}
        record = ConclusionRecord("c", "claim", ConclusionType.UNKNOWN, raw_source=payload)
        # Mutating the caller's original must not affect the stored record.
        payload["nested"]["x"] = 99
        payload["list"].append(3)
        frozen = _canon_json(record.raw_source)
        expected = _canon_json({"nested": {"x": 1}, "list": [1, 2]})
        self.assertEqual(frozen, expected)

    def test_output_raw_source_cannot_be_tampered_via_alias(self):
        record = ConclusionRecord("c", "claim", ConclusionType.UNKNOWN, raw_source={"k": 1})
        with self.assertRaises((TypeError, AttributeError)):
            record.raw_source["k"] = 2  # frozen tuple structure rejects assignment

    def test_dedupe_keeps_both_on_conflicting_content(self):
        left = ConclusionRecord("dup", "A", ConclusionType.STATIC_INFERENCE,
                                evidence_refs=(evidence(source_id="l"),))
        right = ConclusionRecord("dup", "B", ConclusionType.STATIC_INFERENCE,
                                 evidence_refs=(evidence(source_id="r"),))
        out = _dedupe_conclusions_with_conflict([left, right])
        # A reused ID and different text do not by themselves prove contradiction.
        self.assertEqual(out, [left, right])
        self.assertFalse(any(c.conclusion_type is ConclusionType.CONFLICT for c in out))

    def test_dedupe_keeps_semantic_field_differences(self):
        base = ConclusionRecord(
            "same", "claim", ConclusionType.STATIC_INFERENCE,
            evidence_refs=(evidence(source_id="src", locator="$.a", raw_value={"v": 1}),),
            source_type=SourceType.GRAPH, confidence=0.5, scope="module A",
            limitations=("static only",), raw_source={"claim": 1}, mapping_method="map-v1",
        )
        variants = (
            ConclusionRecord("same", "claim", ConclusionType.UNKNOWN, base.evidence_refs,
                             SourceType.GRAPH, 0.5, "module A", ("static only",), {"claim": 1}, "map-v1"),
            ConclusionRecord("same", "claim", ConclusionType.STATIC_INFERENCE, base.evidence_refs,
                             SourceType.TEST_PLAN, 0.5, "module A", ("static only",), {"claim": 1}, "map-v1"),
            ConclusionRecord("same", "claim", ConclusionType.STATIC_INFERENCE, base.evidence_refs,
                             SourceType.GRAPH, 0.5, "module B", ("static only",), {"claim": 1}, "map-v1"),
            ConclusionRecord("same", "claim", ConclusionType.STATIC_INFERENCE, base.evidence_refs,
                             SourceType.GRAPH, 0.5, "module A", ("other limit",), {"claim": 1}, "map-v1"),
            ConclusionRecord("same", "claim", ConclusionType.STATIC_INFERENCE, base.evidence_refs,
                             SourceType.GRAPH, 0.5, "module A", ("static only",), {"claim": 2}, "map-v1"),
            ConclusionRecord("same", "claim", ConclusionType.STATIC_INFERENCE,
                             (evidence(source_id="other", locator="$.b", raw_value={"v": 1}),),
                             SourceType.GRAPH, 0.5, "module A", ("static only",), {"claim": 1}, "map-v1"),
            ConclusionRecord("same", "claim", ConclusionType.STATIC_INFERENCE, base.evidence_refs,
                             SourceType.GRAPH, 0.7, "module A", ("static only",), {"claim": 1}, "map-v1"),
            ConclusionRecord("same", "claim", ConclusionType.STATIC_INFERENCE, base.evidence_refs,
                             SourceType.GRAPH, 0.5, "module A", ("static only",), {"claim": 1}, "map-v2"),
        )
        for candidate in variants:
            with self.subTest(candidate=candidate):
                self.assertEqual(_dedupe_conclusions_with_conflict([base, candidate]), [base, candidate])

    def test_freeze_rejects_self_referential_list_and_mapping(self):
        from usit.understanding_model import _freeze_json

        cyclic_list = []
        cyclic_list.append(cyclic_list)
        cyclic_mapping = {}
        cyclic_mapping["self"] = cyclic_mapping
        for value in (cyclic_list, cyclic_mapping):
            with self.subTest(kind=type(value).__name__):
                with self.assertRaisesRegex(InvalidModelInputError, "cyclic JSON value"):
                    _freeze_json(value)

    def test_freeze_accepts_shared_but_noncyclic_value(self):
        from usit.understanding_model import _freeze_json

        shared = {"leaf": [1]}
        frozen = _freeze_json({"left": shared, "right": shared})
        self.assertEqual(_canon_json(frozen["left"]), _canon_json(frozen["right"]))

    def test_dedupe_drops_identical_duplicate(self):
        rec = ConclusionRecord("same", "A", ConclusionType.STATIC_INFERENCE,
                               evidence_refs=(evidence(),))
        out = _dedupe_conclusions_with_conflict([rec, rec])
        self.assertEqual(len(out), 1)

    def test_make_conflict_preserves_both_evidence_sides(self):
        left = ConclusionRecord("left", "A", ConclusionType.STATIC_INFERENCE,
                                evidence_refs=(evidence(source_id="left-src"),))
        right = ConclusionRecord("right", "B", ConclusionType.UNKNOWN,
                                 evidence_refs=(evidence(source_id="right-src"),))
        conflict, uncertainty = make_conflict(left, right, "incompatible values")
        self.assertEqual(uncertainty.conflicting_conclusion_ids, ("left", "right"))
        self.assertEqual({e.source_id for e in conflict.evidence_refs},
                         {"left-src", "right-src"})

    def test_conflict_keeps_same_identity_refs_with_different_raw_content(self):
        left_ref = evidence(source_id="same", locator="$.x", raw_value={"v": 1})
        right_ref = evidence(source_id="same", locator="$.x", raw_value={"v": 2})
        left = ConclusionRecord("l", "A", ConclusionType.STATIC_INFERENCE, evidence_refs=(left_ref,))
        right = ConclusionRecord("r", "B", ConclusionType.UNKNOWN, evidence_refs=(right_ref,))
        conflict, _ = make_conflict(left, right, "incompatible")
        self.assertEqual(len(conflict.evidence_refs), 2)
        self.assertNotEqual(_canon_json(conflict.evidence_refs[0].raw_value),
                            _canon_json(conflict.evidence_refs[1].raw_value))

    def test_sequence_fields_are_frozen_and_bad_nested_types_rejected(self):
        steps = ["one"]
        spec = TestSpecification("t", "target", "purpose", preconditions=["p"],
                                 identity_and_data_requirements=["i"], steps=steps,
                                 expected_result="expected", cleanup_strategy="cleanup",
                                 evidence_requirements=["e"])
        steps.append("later")
        self.assertEqual(spec.steps, ("one",))
        with self.assertRaises(ValueError):
            TestSpecification("t", "target", "purpose", steps="not-a-sequence")

    def test_unstable_json_keys_and_nonfinite_numbers_rejected(self):
        with self.assertRaises(InvalidModelInputError):
            _canon_json({1: "not-json-key"})
        with self.assertRaises(InvalidModelInputError):
            _canon_json(float("nan"))


if __name__ == "__main__":
    unittest.main()
