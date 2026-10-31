"""S1-C IMPLEMENT-02A: unit tests for the file-level understanding layer.

Pure unit tests (no scan, no I/O, no network) validating the frozen
DESIGN-10..14 contract implemented in ``usit.file_understanding``:
file coverage, record attribution, file-level branch conclusions, project
diagnostics, unmatched records, raw-record counting / projection-failure
preservation, summary identities K1-K8, and the usit-file-understanding-v1
serialization / unknown-field strategy.

Every case has explicit input, expected output and negative assertions.
"""

from __future__ import annotations

import unittest
from unittest import mock

from usit import understanding_projection
from usit.file_understanding import (
    FILE_UNDERSTANDING_SCHEMA,
    FileConclusionType,
    FileContext,
    build_file_understanding,
    decide_file_conclusion,
)
from usit.understanding_projection import project_records


def _rec(**overrides):
    """A minimal evidence-bearing parser-contract record (candidate)."""
    record = {
        "language": "Python", "file_path": "pkg/mod.py", "parser_name": "p", "parser_version": "v",
        "node_type": "function", "qualified_name": "pkg.mod.alpha",
        "source_location": {"file_path": "pkg/mod.py", "line": 12, "column": 4},
        "relation_type": None, "resolution_status": "RESOLVED", "confidence": 0.9,
        "evidence": {"locator": "x:12", "kind": "definition"}, "diagnostics": [],
    }
    record.update(overrides)
    return record


def _at(path, **overrides):
    """A record whose source_location.file_path is ``path``."""
    loc = {"file_path": path, "line": 1, "column": 1}
    return _rec(source_location=loc, file_path=path, **overrides)


def _mf(path, **overrides):
    """A scanner manifest file entry (tri-state signals; None => unknown)."""
    entry = {
        "path": path, "size_bytes": 100, "readable_text": True,
        "symbols": [{"name": "f", "line": 1}], "analysis_degradation": {"degraded": False},
    }
    entry.update(overrides)
    return entry


def _projdiag(code="JS_X"):
    """A confirmed project-level diagnostic record (empty path + diagnostic identity)."""
    return {
        "language": "JavaScript/TypeScript", "file_path": "", "parser_name": "tsc", "parser_version": "6",
        "node_type": "diagnostic", "qualified_name": code,
        "source_location": {"file_path": "", "line": None, "column": None}, "relation_type": None,
        "resolution_status": "UNKNOWN", "confidence": 0.0,
        "evidence": {"diagnostic": code}, "diagnostics": [{"code": code}],
    }


def _file_node(path):
    """A graph file-node record as adapt_graph_v1 emits it: non-success (UNKNOWN),
    candidate evidence ({"path": ...}), node_type 'file'. Every scanned file has one."""
    return {
        "language": "Python", "file_path": path, "parser_name": "graph-v1-adapter", "parser_version": "graph-v1",
        "node_type": "file", "qualified_name": path,
        "source_location": {"file_path": path, "line": None, "column": None}, "relation_type": None,
        "resolution_status": "UNKNOWN", "confidence": 0.0,
        "evidence": {"path": path}, "diagnostics": [],
    }


def _fc(result, path):
    return next(f for f in result["files"] if f["file_path"] == path)


# ---------------------------------------------------------------------------
# (1) Full coverage + zero-record files
# ---------------------------------------------------------------------------

class FileCoverageTests(unittest.TestCase):
    def test_every_manifest_file_gets_exactly_one_conclusion(self):
        result = build_file_understanding(
            [_mf("a.py"), _mf("b.py"), _mf("c.py")],
            [_at("a.py"), _at("c.py")],  # b.py has no records
        )
        paths = [f["file_path"] for f in result["files"]]
        self.assertEqual(paths, ["a.py", "b.py", "c.py"])  # manifest order, full coverage
        self.assertEqual(_fc(result, "b.py")["file_conclusion"], "NO_RECORDS")
        self.assertEqual(_fc(result, "b.py")["n_records"], 0)

    def test_empty_manifest_yields_no_files(self):
        result = build_file_understanding([], [])
        self.assertEqual(result["files"], [])
        self.assertEqual(result["totals"]["files"], 0)

    def test_file_list_is_manifest_driven_not_record_driven(self):
        # A record whose path is NOT in the manifest must not create a file entry.
        # (a ghost-only input is systemic and blocked; see BlockingConditionTests.)
        result = build_file_understanding([_mf("a.py")], [_at("a.py"), _at("ghost.py")])
        paths = [f["file_path"] for f in result["files"]]
        self.assertEqual(paths, ["a.py"])  # ghost.py does NOT appear as a file
        self.assertIn("no_matching_manifest_file", [u["reason"] for u in result["unmatched_records"]])


# ---------------------------------------------------------------------------
# (2) File content states: zero-byte / unreadable-with-records / unreadable-zero / context-missing
# ---------------------------------------------------------------------------

class FileContentStateTests(unittest.TestCase):
    def test_zero_byte_is_no_semantic_content(self):
        result = build_file_understanding([_mf("z.py", size_bytes=0)], [_at("z.py")])
        f = _fc(result, "z.py")
        self.assertEqual(f["file_conclusion"], "NO_SEMANTIC_CONTENT")
        self.assertEqual(f["branch"], 0)
        self.assertEqual(f["content_status"], "empty")

    def test_unreadable_nonempty_with_record_is_not_empty(self):
        # Realistic: an unreadable file's only record is its (non-success) file node.
        result = build_file_understanding([_mf("u.py", readable_text=False)], [_file_node("u.py")])
        f = _fc(result, "u.py")
        self.assertEqual(f["content_status"], "unreadable")
        self.assertNotEqual(f["file_conclusion"], "NO_SEMANTIC_CONTENT")  # NOT treated as empty
        self.assertEqual(f["file_conclusion"], "UNKNOWN_RESOLUTION")  # non-success record -> branch 2

    def test_unreadable_branch_follows_records_not_content_status(self):
        # content_status=unreadable never decides the branch; records do.
        # An unreadable file WITH a RESOLVED+candidate record -> branch 7 (still not empty).
        result = build_file_understanding([_mf("u.py", readable_text=False)], [_at("u.py")])
        f = _fc(result, "u.py")
        self.assertEqual(f["content_status"], "unreadable")
        self.assertEqual(f["file_conclusion"], "INSUFFICIENT_EVIDENCE")
        self.assertNotEqual(f["file_conclusion"], "NO_SEMANTIC_CONTENT")

    def test_unreadable_nonempty_zero_record_is_no_records(self):
        result = build_file_understanding([_mf("u.py", readable_text=False, symbols=[])], [])
        f = _fc(result, "u.py")
        self.assertEqual(f["file_conclusion"], "NO_RECORDS")
        self.assertEqual(f["content_status"], "unreadable")  # context carried, not fabricated
        self.assertEqual(f["n_records"], 0)
        self.assertNotEqual(f["file_conclusion"], "UNKNOWN_RESOLUTION")  # no fabricated record

    def test_context_missing_size_is_unknown_not_empty(self):
        # size_bytes missing -> cannot assert empty; has a record -> normal branch
        result = build_file_understanding([_mf("m.py", size_bytes=None)], [_at("m.py")])
        f = _fc(result, "m.py")
        self.assertEqual(f["content_status"], "unknown")
        self.assertNotEqual(f["file_conclusion"], "NO_SEMANTIC_CONTENT")  # unknown != empty

    def test_context_missing_symbols_uses_symbol_status_unknown(self):
        result = build_file_understanding([_mf("s.py", symbols=None)], [])
        f = _fc(result, "s.py")
        self.assertEqual(f["file_conclusion"], "NO_RECORDS")
        self.assertEqual(f["reasons"], ["symbol_status_unknown"])  # not defaulted to no_symbols


# ---------------------------------------------------------------------------
# (3) hypothesis-only -> branch 8 UNKNOWN
# ---------------------------------------------------------------------------

class HypothesisBranchTests(unittest.TestCase):
    def test_resolved_ai_hypothesis_is_unknown_branch8(self):
        result = build_file_understanding([_mf("h.py")], [_at("h.py", hypothesis=True)])
        f = _fc(result, "h.py")
        self.assertEqual(f["file_conclusion"], "UNKNOWN")
        self.assertEqual(f["branch"], 8)
        self.assertEqual(f["resolved_ev_count"], 0)  # AI_HYPOTHESIS excluded
        self.assertEqual(f["counts"]["N_candidate"], 1)  # still a candidate
        self.assertEqual(f["counts"]["N_construct_ok"], 1)
        # negative: not upgraded to insufficient/conflict/verified
        self.assertNotEqual(f["file_conclusion"], "INSUFFICIENT_EVIDENCE")
        self.assertNotEqual(f["file_conclusion"], "CONFLICT_ONLY")
        self.assertNotIn("VERIFIED_FACT", FileConclusionType.__members__)


# ---------------------------------------------------------------------------
# (4) project diagnostic vs empty-path normal record isolation
# ---------------------------------------------------------------------------

class ProjectDiagnosticIsolationTests(unittest.TestCase):
    def test_confirmed_project_diagnostic_isolated(self):
        result = build_file_understanding([_mf("a.py")], [_at("a.py"), _projdiag("JS_X")])
        self.assertEqual(len(result["project_diagnostics"]), 1)
        self.assertEqual(result["project_diagnostics"][0]["diagnostic_code"], "JS_X")
        self.assertEqual(result["project_diagnostics"][0]["file_path"], "")
        # not counted as a file record nor as unmatched
        self.assertEqual(_fc(result, "a.py")["n_records"], 1)  # only the matched record
        self.assertEqual(result["totals"]["project_diagnostic_total"], 1)
        self.assertEqual(result["totals"]["unmatched_total"], 0)

    def test_empty_path_non_diagnostic_is_unmatched_not_project_diagnostic(self):
        # external-style record: empty path but node_type != diagnostic, no diagnostic field
        ext = _at("", node_type="external", evidence={"note": "x"})
        result = build_file_understanding([_mf("a.py")], [_at("a.py"), ext])
        self.assertEqual(len(result["project_diagnostics"]), 0)  # NOT disguised as diagnostic
        reasons = [u["reason"] for u in result["unmatched_records"]]
        self.assertIn("empty_path_not_confirmed_project_diagnostic", reasons)

    def test_diagnostic_with_missing_field_is_unmatched(self):
        # node_type == diagnostic but evidence lacks the diagnostic field -> unconfirmed
        bad = _at("", node_type="diagnostic", evidence={"locator": "x"})
        result = build_file_understanding([_mf("a.py")], [_at("a.py"), bad])
        self.assertEqual(len(result["project_diagnostics"]), 0)
        self.assertIn("empty_path_not_confirmed_project_diagnostic",
                      [u["reason"] for u in result["unmatched_records"]])


# ---------------------------------------------------------------------------
# (5) non-empty unmatched path
# ---------------------------------------------------------------------------

class UnmatchedPathTests(unittest.TestCase):
    def test_nonempty_path_not_in_manifest_is_unmatched(self):
        result = build_file_understanding([_mf("a.py")], [_at("a.py"), _at("C:/abs/x.py")])
        reasons = [u["reason"] for u in result["unmatched_records"]]
        self.assertIn("no_matching_manifest_file", reasons)
        self.assertEqual(result["unmatched_records"][0]["file_path"], "C:/abs/x.py")
        # not attributed to any file
        self.assertEqual(result["totals"]["n_records_total"], 1)  # only a.py's record
        self.assertEqual(result["totals"]["unmatched_total"], 1)

    def test_unmatched_record_preserves_projection(self):
        result = build_file_understanding([_mf("a.py")], [_at("a.py"), _at("C:/abs/x.py")])
        entry = next(u for u in result["unmatched_records"] if u["reason"] == "no_matching_manifest_file")
        self.assertIn("projection", entry)  # preserved, not dropped


# ---------------------------------------------------------------------------
# (6) non-Mapping / invalid path / projection exception
# ---------------------------------------------------------------------------

class MalformedAndExceptionTests(unittest.TestCase):
    def test_non_mapping_record_is_unmatched(self):
        result = build_file_understanding([_mf("a.py")], [_at("a.py"), "junk", 42])
        reasons = [u["reason"] for u in result["unmatched_records"]]
        self.assertEqual(reasons.count("invalid_or_missing_file_path"), 2)
        self.assertEqual(result["totals"]["unmatched_total"], 2)

    def test_invalid_path_type_is_unmatched(self):
        bad = _rec(source_location={"file_path": None})
        result = build_file_understanding([_mf("a.py")], [_at("a.py"), bad])
        self.assertIn("invalid_or_missing_file_path", [u["reason"] for u in result["unmatched_records"]])

    def test_projection_failure_still_counted_in_n_raw(self):
        target = _at("a.py")
        real = understanding_projection.project_record

        def flaky(record):
            if record is target:
                raise ValueError("injected construction failure")
            return real(record)

        with mock.patch.object(understanding_projection, "project_record", side_effect=flaky):
            result = build_file_understanding([_mf("a.py")], [target])
        f = _fc(result, "a.py")
        self.assertEqual(f["n_records"], 1)  # NOT undercounted despite projection failure
        self.assertEqual(f["counts"]["N_construct_fail"], 1)
        self.assertEqual(f["counts"]["N_construct_ok"], 0)
        self.assertEqual(result["totals"]["n_records_total"], 1)  # K2 still holds
        # degraded item is non-success -> branch 2, never a success
        self.assertNotEqual(f["file_conclusion"], "INSUFFICIENT_EVIDENCE")

    def test_projection_failure_among_successes_counts_both(self):
        bad = _at("a.py", qualified_name="bad")
        good = _at("a.py", qualified_name="good")
        real = understanding_projection.project_record

        def flaky(record):
            if record is bad:
                raise RuntimeError("boom")
            return real(record)

        with mock.patch.object(understanding_projection, "project_record", side_effect=flaky):
            result = build_file_understanding([_mf("a.py")], [bad, good])
        f = _fc(result, "a.py")
        self.assertEqual(f["n_records"], 2)
        self.assertEqual(f["counts"]["N_construct_fail"], 1)
        self.assertEqual(f["counts"]["N_construct_ok"], 1)
        self.assertEqual(f["counts"]["N_unexpected_error"], 0)


# ---------------------------------------------------------------------------
# (7) same-identity different-content conflict + reference id type
# ---------------------------------------------------------------------------

class ConflictTests(unittest.TestCase):
    def test_same_identity_diff_content_is_conflict(self):
        result = build_file_understanding([_mf("c.py")], [
            _at("c.py", qualified_name="X", resolution_status="RESOLVED"),
            _at("c.py", qualified_name="X", resolution_status="UNRESOLVED"),
        ])
        f = _fc(result, "c.py")
        self.assertEqual(f["file_conclusion"], "CONFLICT_ONLY")
        self.assertEqual(f["branch"], 3)
        self.assertEqual(f["conflict_count"], 1)
        self.assertTrue(f["has_conflict"])
        self.assertEqual(f["n_records"], 2)  # conflict item NOT counted as a raw record
        self.assertEqual(result["totals"]["conflict_total"], 1)

    def test_conflicting_refs_are_projection_ids_not_conclusion_ids(self):
        result = build_file_understanding([_mf("c.py")], [
            _at("c.py", qualified_name="X", resolution_status="RESOLVED"),
            _at("c.py", qualified_name="X", resolution_status="UNRESOLVED"),
        ])
        f = _fc(result, "c.py")
        self.assertEqual(len(f["conflicting_refs"]), 2)
        self.assertTrue(all(r.startswith("proj:") for r in f["conflicting_refs"]))
        self.assertTrue(all(not r.startswith("conc:") for r in f["conflicting_refs"]))


# ---------------------------------------------------------------------------
# (8) raw-record counting comes from pre-projection grouping
# ---------------------------------------------------------------------------

class RawRecordCountingTests(unittest.TestCase):
    def test_n_raw_from_grouping_not_from_successful_items(self):
        # 3 records: 1 candidate, 1 non-candidate(empty evidence), 1 non-Mapping-ish path
        result = build_file_understanding([_mf("a.py")], [
            _at("a.py"),                                        # candidate
            _at("a.py", qualified_name="b", evidence={}),       # non-candidate
            _at("a.py", qualified_name="c", resolution_status="UNKNOWN"),  # non-success candidate
        ])
        f = _fc(result, "a.py")
        self.assertEqual(f["n_records"], 3)  # all 3 grouped records, regardless of outcome
        self.assertEqual(f["counts"]["N_raw"], 3)
        self.assertEqual(f["counts"]["N_candidate"], 2)
        self.assertEqual(f["counts"]["N_evidence_none"], 1)


# ---------------------------------------------------------------------------
# (9) K1-K8 summary identities
# ---------------------------------------------------------------------------

class SummaryIdentityTests(unittest.TestCase):
    def _mixed(self):
        return build_file_understanding(
            [_mf("a.py"), _mf("b.py", symbols=[]), _mf("c.py")],
            [
                _at("a.py", qualified_name="X", resolution_status="RESOLVED"),
                _at("a.py", qualified_name="X", resolution_status="UNRESOLVED"),  # conflict
                _at("c.py"),
                _projdiag("JS_X"),
                _at("C:/abs/x.py"),  # unmatched
            ],
        )

    def test_k1_k7_on_result(self):
        r = self._mixed()
        t = r["totals"]
        files = r["files"]
        # K5
        self.assertEqual(t["files"], len(files))
        self.assertEqual(t["files"], 3)
        # K6
        self.assertEqual(sum(t["by_conclusion"].values()), t["files"])
        # K2
        matched = sum(f["n_records"] for f in files)
        self.assertEqual(t["n_records_total"], matched)
        self.assertEqual(t["n_records_total"], 3)  # a.py(2) + b.py(0) + c.py(1)
        # K3 / K4
        self.assertEqual(t["unmatched_total"], 1)
        self.assertEqual(t["project_diagnostic_total"], 1)
        # K1 exhaustive partition
        self.assertEqual(5, t["n_records_total"] + t["project_diagnostic_total"] + t["unmatched_total"])
        # K7
        self.assertEqual(t["conflict_total"], 1)
        self.assertEqual(t["conflict_total"], sum(f["conflict_count"] for f in files))
        # no VERIFIED_FACT anywhere
        for f in files:
            self.assertNotEqual(f["file_conclusion"], "VERIFIED_FACT")

    def test_k8_projected_items_equals_nr_plus_cf(self):
        # conflict file group: projected_items == Nr + cf (projection-level identity)
        group = [
            _at("a.py", qualified_name="X", resolution_status="RESOLVED"),
            _at("a.py", qualified_name="X", resolution_status="UNRESOLVED"),
        ]
        proj = project_records(group)
        result = build_file_understanding([_mf("a.py")], group)
        f = _fc(result, "a.py")
        self.assertEqual(len(proj.items), f["n_records"] + f["conflict_count"])  # K8
        self.assertEqual(f["conflict_count"], 1)


# ---------------------------------------------------------------------------
# (10) serialization / schema version / unknown-field strategy
# ---------------------------------------------------------------------------

class SerializationContractTests(unittest.TestCase):
    def test_schema_version_and_top_level_fields(self):
        result = build_file_understanding([_mf("a.py")], [_at("a.py")])
        self.assertEqual(result["schema_version"], FILE_UNDERSTANDING_SCHEMA)
        self.assertEqual(result["schema_version"], "usit-file-understanding-v1")
        self.assertEqual(set(result.keys()),
                         {"schema_version", "projection_schema_version", "generated_from",
                          "files", "project_diagnostics", "unmatched_records", "totals"})

    def test_file_entry_has_frozen_field_set(self):
        result = build_file_understanding([_mf("a.py")], [_at("a.py")])
        expected = {
            "file_path", "file_conclusion", "branch", "reasons", "has_conflict",
            "conflict_count", "file_degraded", "content_status", "readable_text",
            "n_records", "resolved_ev_count", "meaningful_count", "counts",
            "evidence_refs", "conflicting_refs", "projection_schema_version",
        }
        self.assertEqual(set(result["files"][0].keys()), expected)

    def test_tri_state_serialized_as_strings(self):
        result = build_file_understanding([_mf("m.py", size_bytes=None, readable_text=None,
                                                symbols=None, analysis_degradation=None)], [])
        f = _fc(result, "m.py")
        self.assertEqual(f["content_status"], "unknown")
        self.assertEqual(f["readable_text"], "unknown")
        self.assertEqual(f["file_degraded"], "unknown")

    def test_unknown_input_fields_are_tolerated(self):
        # extra/unknown keys in manifest entry and record must not break the build
        entry = _mf("a.py", extra_unknown_key="ignored", future_field={"x": 1})
        record = _at("a.py", some_future_field=[1, 2, 3])
        result = build_file_understanding([entry], [record])
        self.assertEqual(_fc(result, "a.py")["file_conclusion"], "INSUFFICIENT_EVIDENCE")

    def test_no_verified_fact_and_meaningful_unreachable(self):
        # A fully "good" file (RESOLVED + candidate) is still only INSUFFICIENT_EVIDENCE.
        result = build_file_understanding([_mf("a.py")], [_at("a.py")])
        f = _fc(result, "a.py")
        self.assertEqual(f["file_conclusion"], "INSUFFICIENT_EVIDENCE")
        self.assertEqual(f["meaningful_count"], 0)  # MEANINGFUL_EVIDENCE unreachable


# ---------------------------------------------------------------------------
# Blocking conditions
# ---------------------------------------------------------------------------

class BlockingConditionTests(unittest.TestCase):
    def test_systemic_mismatch_blocks(self):
        with self.assertRaises(ValueError):
            build_file_understanding([_mf("a.py")], [_at("C:/abs/x.py")])

    def test_empty_manifest_with_nonempty_path_records_blocks(self):
        with self.assertRaises(ValueError):
            build_file_understanding([], [_at("a.py")])

    def test_strict_mode_blocks_on_unmatched(self):
        with self.assertRaises(ValueError):
            build_file_understanding([_mf("a.py")], [_at("a.py"), _at("C:/abs/x.py")], strict=True)

    def test_invalid_manifest_type_raises_typeerror(self):
        for bad in ("not-a-sequence", {"a": 1}, 7):
            with self.subTest(bad=bad):
                with self.assertRaises(TypeError):
                    build_file_understanding(bad, [])

    def test_duplicate_manifest_path_raises(self):
        with self.assertRaises(ValueError):
            build_file_understanding([_mf("a.py"), _mf("a.py")], [_at("a.py")])


if __name__ == "__main__":
    unittest.main()
