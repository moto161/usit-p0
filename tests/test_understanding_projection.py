"""S1-A: dedicated tests for the read-only understanding projection.

All tests here are static/unit level. No target project is scanned, no code is
executed, no network access and no credentials are read.
"""

from __future__ import annotations

import copy
import unittest
from unittest import mock

from usit import understanding_projection
from usit.parser_contract import CONTRACT_VERSION
from usit.understanding_model import (
    ConclusionRecord,
    ConclusionType,
    EvidenceReference,
    SourceType,
)
from usit.understanding_projection import (
    NON_SUCCESS,
    PROJECTION_SCHEMA,
    project_record,
    project_records,
)


def _record(**overrides):
    """A minimal, evidence-bearing parser-contract record."""
    record = {
        "language": "Python",
        "file_path": "pkg/mod.py",
        "parser_name": "python-ast-project",
        "parser_version": "stdlib-ast",
        "node_type": "function",
        "qualified_name": "pkg.mod.alpha",
        "source_location": {"file_path": "pkg/mod.py", "line": 12, "column": 4},
        "relation_type": None,
        "resolution_status": "RESOLVED",
        "confidence": 0.9,
        "evidence": {"locator": "pkg/mod.py:12", "kind": "definition"},
        "diagnostics": [],
    }
    record.update(overrides)
    return record


class ConclusionTypeLegalityTests(unittest.TestCase):
    def test_all_five_conclusion_types_exist(self):
        expected = {
            "VERIFIED_FACT",
            "STATIC_INFERENCE",
            "AI_HYPOTHESIS",
            "UNKNOWN",
            "CONFLICT",
        }
        self.assertEqual(expected, {item.value for item in ConclusionType})

    def test_verified_fact_cannot_be_constructed(self):
        """Static data may never claim runtime verification."""
        with self.assertRaises(ValueError):
            ConclusionRecord(
                conclusion_id="c1",
                content="x",
                conclusion_type=ConclusionType.VERIFIED_FACT,
                source_type=SourceType.GRAPH,
            )

    def test_evidence_reference_rejects_caller_trust_claims(self):
        with self.assertRaises(ValueError):
            EvidenceReference(
                source_schema=CONTRACT_VERSION,
                source_id="s1",
                source_trusted=True,
            )
        with self.assertRaises(ValueError):
            EvidenceReference(
                source_schema=CONTRACT_VERSION,
                source_id="s1",
                execution_verified=True,
            )


class ProjectionClassificationTests(unittest.TestCase):
    def test_resolved_with_evidence_is_static_inference(self):
        item = project_record(_record())
        self.assertIs(item.conclusion_type, ConclusionType.STATIC_INFERENCE)
        self.assertTrue(item.is_success)
        self.assertIsNotNone(item.conclusion)
        self.assertEqual(1, len(item.conclusion.evidence_refs))

    def test_projection_never_emits_verified_fact(self):
        result = project_records([_record(), _record(resolution_status="UNKNOWN")])
        self.assertEqual(0, result.stats["verified_fact"])
        self.assertFalse(any(i.conclusion_type is ConclusionType.VERIFIED_FACT for i in result.items))

    def test_missing_evidence_is_unknown_not_fabricated(self):
        item = project_record(_record(evidence={}))
        self.assertIs(item.conclusion_type, ConclusionType.UNKNOWN)
        self.assertFalse(item.is_success)
        self.assertIsNone(item.conclusion)
        self.assertIsNotNone(item.uncertainty)
        self.assertIn("missing_or_empty_evidence", item.uncertainty.missing_evidence)

    def test_non_success_states_preserved_as_unknown(self):
        for state in sorted(NON_SUCCESS):
            with self.subTest(state=state):
                item = project_record(_record(resolution_status=state))
                self.assertIs(item.conclusion_type, ConclusionType.UNKNOWN)
                self.assertFalse(item.is_success)
                self.assertEqual(state, item.resolution_status)

    def test_non_success_states_not_counted_as_success(self):
        records = [_record(resolution_status=s) for s in sorted(NON_SUCCESS)]
        result = project_records(records)
        self.assertEqual(0, result.stats["success_counted"])
        self.assertEqual(len(records), result.stats["unknown"])

    def test_invalid_or_missing_status_is_unknown(self):
        for overrides in ({"resolution_status": "NOT_A_STATE"}, {"resolution_status": 5}):
            with self.subTest(overrides=overrides):
                item = project_record(_record(**overrides))
                self.assertIs(item.conclusion_type, ConclusionType.UNKNOWN)
        missing = dict(_record())
        missing.pop("resolution_status")
        self.assertIs(project_record(missing).conclusion_type, ConclusionType.UNKNOWN)

    def test_ai_hypothesis_is_not_success(self):
        item = project_record(_record(hypothesis=True))
        self.assertIs(item.conclusion_type, ConclusionType.AI_HYPOTHESIS)
        self.assertFalse(item.is_success)

    def test_confidence_is_not_assigned(self):
        item = project_record(_record())
        self.assertIsNone(item.conclusion.confidence)


class EvidenceProvenanceTests(unittest.TestCase):
    def test_path_symbol_and_line_are_taken_from_evidence_when_present(self):
        item = project_record(_record())
        evidence = item.conclusion.evidence_refs[0]
        self.assertEqual("pkg/mod.py", evidence.relative_path)
        self.assertEqual(12, evidence.line)
        self.assertEqual("pkg.mod.alpha", evidence.symbol)

    def test_missing_location_is_not_invented(self):
        record = _record(source_location={}, file_path=None)
        item = project_record(record)
        # Resolved + evidence still classifies, but no fabricated locator values.
        evidence = item.conclusion.evidence_refs[0]
        self.assertIsNone(evidence.line)
        self.assertNotEqual("pkg/mod.py", evidence.relative_path)

    def test_invalid_line_is_dropped_not_guessed(self):
        item = project_record(_record(source_location={"file_path": "pkg/mod.py", "line": 0}))
        self.assertIsNone(item.conclusion.evidence_refs[0].line)


class RobustnessTests(unittest.TestCase):
    def test_non_mapping_record_degrades_to_unknown(self):
        for bad in (None, 42, "text", [1, 2]):
            with self.subTest(bad=bad):
                item = project_record(bad)
                self.assertIs(item.conclusion_type, ConclusionType.UNKNOWN)
                self.assertFalse(item.is_success)

    def test_empty_input_yields_empty_projection(self):
        result = project_records([])
        self.assertEqual((), result.items)
        self.assertEqual(0, result.stats["total_records"])
        self.assertEqual(0, result.stats["success_counted"])

    def test_non_sequence_input_is_rejected(self):
        for bad in ("not-a-sequence", {"a": 1}, 7):
            with self.subTest(bad=bad):
                with self.assertRaises(TypeError):
                    project_records(bad)

    def test_duplicate_records_are_stable_and_not_conflicts(self):
        records = [_record(), _record()]
        result = project_records(records)
        self.assertEqual(2, result.stats["static_inference"])
        self.assertEqual(0, result.stats["conflict"])


class DeterminismTests(unittest.TestCase):
    def test_repeated_projection_is_identical(self):
        records = [_record(), _record(resolution_status="UNKNOWN"), _record(evidence={})]
        first = project_records(records).to_dict()
        second = project_records(records).to_dict()
        self.assertEqual(first, second)

    def test_schema_version_is_stamped(self):
        self.assertEqual(PROJECTION_SCHEMA, project_records([]).to_dict()["schema_version"])


class InputImmutabilityTests(unittest.TestCase):
    def test_projection_does_not_mutate_input(self):
        records = [_record(), _record(resolution_status="UNKNOWN")]
        snapshot = copy.deepcopy(records)
        project_records(records)
        self.assertEqual(snapshot, records)


class ConflictTests(unittest.TestCase):
    def test_same_identity_different_content_is_conflict(self):
        records = [
            _record(resolution_status="RESOLVED"),
            _record(resolution_status="UNRESOLVED"),
        ]
        result = project_records(records)
        conflicts = [i for i in result.items if i.conclusion_type is ConclusionType.CONFLICT]
        self.assertEqual(1, len(conflicts))
        self.assertEqual("same_identity_different_content", conflicts[0].reasons[0])
        self.assertTrue(conflicts[0].uncertainty.conflicting_conclusion_ids)
        self.assertFalse(conflicts[0].is_success)


class RecordClassificationCountTests(unittest.TestCase):
    """Per-record mutually-exclusive classification counts (Task B)."""

    def _counts(self, records):
        return project_records(records).stats

    def test_single_candidate(self):
        s = self._counts([_record()])
        self.assertEqual((s["N_raw"], s["N_candidate"], s["N_construct_ok"]), (1, 1, 1))
        self.assertEqual((s["N_non_candidate"], s["N_evidence_none"], s["N_not_attempted"]), (0, 0, 0))
        self.assertEqual((s["N_construct_fail"], s["N_unexpected_error"]), (0, 0))

    def test_non_mapping_evidence_is_non_candidate_evidence_none(self):
        s = self._counts([_record(evidence="not-a-mapping")])
        self.assertEqual((s["N_candidate"], s["N_non_candidate"], s["N_evidence_none"]), (0, 1, 1))
        self.assertEqual(s["N_construct_ok"], 0)

    def test_empty_evidence_is_non_candidate(self):
        s = self._counts([_record(evidence={})])
        self.assertEqual((s["N_candidate"], s["N_non_candidate"], s["N_evidence_none"]), (0, 1, 1))

    def test_non_success_with_real_evidence_is_still_a_candidate(self):
        # NON_SUCCESS + non-empty evidence -> candidate whose _build_evidence succeeds.
        s = self._counts([_record(resolution_status="UNKNOWN")])
        self.assertEqual((s["N_candidate"], s["N_construct_ok"]), (1, 1))

    def test_non_mapping_record_is_not_attempted(self):
        s = self._counts(["junk", 42, None])
        self.assertEqual((s["N_raw"], s["N_not_attempted"]), (3, 3))
        self.assertEqual((s["N_candidate"], s["N_evidence_none"]), (0, 0))

    def test_mixed_batch_partitions_exhaustively(self):
        records = [
            _record(),                                  # candidate -> construct_ok
            _record(qualified_name="B", evidence={}),   # non-candidate -> evidence_none
            "junk",                                     # not_attempted
        ]
        s = self._counts(records)
        self.assertEqual((s["N_raw"], s["N_candidate"], s["N_non_candidate"], s["N_not_attempted"]), (3, 1, 1, 1))
        self.assertEqual((s["N_construct_ok"], s["N_evidence_none"]), (1, 1))
        # identity (1): exhaustive
        self.assertEqual(s["N_raw"], s["N_candidate"] + s["N_non_candidate"] + s["N_not_attempted"] + s["N_unexpected_error"])
        # identity (2) and (3)
        self.assertEqual(s["N_candidate"], s["N_construct_ok"] + s["N_construct_fail"])
        self.assertEqual(s["N_non_candidate"], s["N_evidence_none"])

    def test_simplified_identity_only_without_not_attempted_or_error(self):
        records = [_record(), _record(qualified_name="B", evidence={})]  # all Mapping, no exception
        s = self._counts(records)
        self.assertEqual((s["N_not_attempted"], s["N_unexpected_error"]), (0, 0))
        # simplified identity (4) valid here
        self.assertEqual(s["N_raw"], s["N_construct_ok"] + s["N_construct_fail"] + s["N_evidence_none"])

    def test_simplified_identity_invalid_when_not_attempted_present(self):
        s = self._counts([_record(), "junk"])  # N_not_attempted = 1
        self.assertEqual(s["N_not_attempted"], 1)
        # full identity (1) still holds ...
        self.assertEqual(s["N_raw"], s["N_candidate"] + s["N_non_candidate"] + s["N_not_attempted"] + s["N_unexpected_error"])
        # ... but the simplified form must NOT hold (RHS omits N_not_attempted)
        self.assertNotEqual(s["N_raw"], s["N_construct_ok"] + s["N_construct_fail"] + s["N_evidence_none"])

    def test_projected_equals_nr_plus_conflict(self):
        records = [
            _record(qualified_name="X", resolution_status="RESOLVED"),
            _record(qualified_name="X", resolution_status="UNRESOLVED"),
        ]
        s = self._counts(records)
        self.assertEqual((s["Nr"], s["conflict"], s["projected_items"]), (2, 1, 3))
        self.assertEqual(s["projected_items"], s["Nr"] + s["conflict"])

    def test_existing_stats_keys_preserved_and_additive(self):
        s = self._counts([_record()])
        for key in (
            "total_records", "projected_items", "static_inference", "unknown", "conflict",
            "ai_hypothesis", "verified_fact", "success_counted",
            "N_raw", "N_candidate", "N_non_candidate", "N_construct_ok", "N_construct_fail",
            "N_not_attempted", "N_unexpected_error", "N_evidence_none", "Nr",
        ):
            self.assertIn(key, s)
        self.assertEqual(s["verified_fact"], 0)


class ExceptionCaptureTests(unittest.TestCase):
    """Captured per-record exceptions must be counted, never promoted to success."""

    def test_candidate_construction_failure_counted_as_construct_fail(self):
        target = _record()  # candidate (non-empty Mapping evidence)
        real = understanding_projection.project_record

        def flaky(record):
            if record is target:
                raise ValueError("injected construction failure")
            return real(record)

        with mock.patch.object(understanding_projection, "project_record", side_effect=flaky):
            result = project_records([target])
        s = result.stats
        self.assertEqual((s["N_raw"], s["N_candidate"]), (1, 1))
        self.assertEqual(s["N_construct_fail"], 1)
        self.assertEqual((s["N_construct_ok"], s["N_unexpected_error"], s["N_evidence_none"]), (0, 0, 0))
        self.assertEqual(s["projected_items"], 1)  # still one item per record (1:1)
        # degraded item must never be a success and must carry no conclusion
        self.assertTrue(all(not item.is_success for item in result.items))
        self.assertTrue(all(item.conclusion is None for item in result.items))
        self.assertIn("construct_fail:ValueError", result.items[0].reasons)

    def test_non_candidate_exception_counted_as_unexpected_error(self):
        target = _record(evidence={})  # non-candidate (empty evidence)
        real = understanding_projection.project_record

        def flaky(record):
            if record is target:
                raise RuntimeError("injected unexpected failure")
            return real(record)

        with mock.patch.object(understanding_projection, "project_record", side_effect=flaky):
            result = project_records([target])
        s = result.stats
        self.assertEqual(s["N_unexpected_error"], 1)
        # mutual exclusivity: it is NOT also counted as evidence_none
        self.assertEqual((s["N_construct_fail"], s["N_evidence_none"], s["N_candidate"]), (0, 0, 0))
        self.assertTrue(all(not item.is_success for item in result.items))

    def test_exception_capture_keeps_identity_balanced(self):
        target = _record()
        real = understanding_projection.project_record

        def flaky(record):
            if record is target:
                raise ValueError("boom")
            return real(record)

        with mock.patch.object(understanding_projection, "project_record", side_effect=flaky):
            s = project_records([target, _record(qualified_name="B", evidence={})]).stats
        # target -> construct_fail (candidate); second -> evidence_none
        self.assertEqual((s["N_construct_fail"], s["N_evidence_none"]), (1, 1))
        self.assertEqual(s["N_raw"], s["N_candidate"] + s["N_non_candidate"] + s["N_not_attempted"] + s["N_unexpected_error"])
        self.assertEqual(s["N_candidate"], s["N_construct_ok"] + s["N_construct_fail"])

    def test_batch_level_failure_is_not_disguised_as_per_record_count(self):
        # A non-sequence input raises BEFORE the loop; no ProjectionResult is
        # produced, so nothing is (mis)counted as a per-record category.
        for bad in ("not-a-sequence", {"a": 1}, 7):
            with self.subTest(bad=bad):
                with self.assertRaises(TypeError):
                    project_records(bad)

    def test_conflict_detection_failure_is_not_a_per_record_count(self):
        # A function-level failure inside _detect_conflicts (after the per-record
        # loop) must propagate as a batch error, not be folded into N_* counts.
        with mock.patch.object(understanding_projection, "_detect_conflicts", side_effect=RuntimeError("batch boom")):
            with self.assertRaises(RuntimeError):
                project_records([_record()])


if __name__ == "__main__":
    unittest.main()
