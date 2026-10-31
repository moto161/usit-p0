"""S1-C dedicated per-case fixtures / independent tests (C1-C21).

Each ``C<n>`` case from the design reviews (REVIEW-04..09) is mapped here to a
concrete input record set and an expected *projection* count tuple. These are
independent, locatable tests: no target project is scanned, no code is executed,
no network access, no credential reads.

Scope of what is asserted here (implemented + measured):
    N_raw, N_candidate, N_non_candidate, N_construct_ok, N_construct_fail,
    N_not_attempted, N_unexpected_error, N_evidence_none, cf (conflict groups),
    proj (projected_items), plus the counting identities.

Explicitly NOT asserted here (design-only, not implemented in
``understanding_projection.project_records``):
    ``file_conclusion`` and the branch number. Those belong to the REVIEW-07
    §4 decision layer and are NOT produced by ``project_records`` (its output is
    only ``items`` + ``stats``). A test below pins that absence so these columns
    stay ``NOT_VERIFIED`` rather than being silently claimed as PASS.

``N_construct_fail`` / ``N_unexpected_error`` are 0 in every case below because
no exception is injected: that is a *measured* zero (the capture path ran and
found nothing), not a fabricated unknown. The >0 paths are proven by dedicated
injection tests in ``tests/test_understanding_projection.py``.
"""

from __future__ import annotations

import unittest

from usit.understanding_projection import project_record, project_records


def _record(**overrides):
    """Minimal evidence-bearing parser-contract record (candidate)."""
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


def _resolved(**o):
    return _record(resolution_status="RESOLVED", **o)


def _non_success(status="UNKNOWN", **o):
    return _record(resolution_status=status, **o)


def _non_mapping_evidence(**o):
    return _record(resolution_status="RESOLVED", evidence="not-a-mapping", **o)


def _counts(records):
    s = project_records(records).stats
    return {
        "N_raw": s["N_raw"],
        "Nc": s["N_candidate"],
        "Nnc": s["N_non_candidate"],
        "Nok": s["N_construct_ok"],
        "Nfail": s["N_construct_fail"],
        "Nnone": s["N_evidence_none"],
        "Nna": s["N_not_attempted"],
        "Neu": s["N_unexpected_error"],
        "cf": s["conflict"],
        "proj": s["projected_items"],
    }


# (case_id, description, builder -> records, expected counts, design-only note)
# File-level distinctions that do not change per-record projection counts
# (empty file vs no-symbols vs symbols-without-records; orphan / diagnostics
# out-of-range / path-not-in-manifest / TestSpec-missing-field / file
# degradation) affect only the DESIGN-layer file_conclusion and are recorded in
# the note column; they are NOT_VERIFIED here.
_CASES = [
    ("C1", "1xRESOLVED / non-empty dict real summary", lambda: [_resolved()],
     dict(N_raw=1, Nc=1, Nnc=0, Nok=1, Nfail=0, Nnone=0, Nna=0, Neu=0, cf=0, proj=1),
     "file_conclusion INSUFFICIENT_EVIDENCE is [design]"),
    ("C2", "1xRESOLVED / only {graph_node_id}", lambda: [_resolved(evidence={"graph_node_id": "n1"})],
     dict(N_raw=1, Nc=1, Nnc=0, Nok=1, Nfail=0, Nnone=0, Nna=0, Neu=0, cf=0, proj=1),
     "projection still treats a non-empty Mapping as candidate; adapt_graph_v1 no longer fabricates this"),
    ("C3", "1xNON_SUCCESS / fallback non-empty", lambda: [_non_success()],
     dict(N_raw=1, Nc=1, Nnc=0, Nok=1, Nfail=0, Nnone=0, Nna=0, Neu=0, cf=0, proj=1),
     "file_conclusion UNKNOWN_RESOLUTION is [design]"),
    ("C4", "empty file (size_bytes==0) -> 0 records", lambda: [],
     dict(N_raw=0, Nc=0, Nnc=0, Nok=0, Nfail=0, Nnone=0, Nna=0, Neu=0, cf=0, proj=0),
     "file_conclusion NO_SEMANTIC_CONTENT is [design]"),
    ("C5", "0 records, no symbols", lambda: [],
     dict(N_raw=0, Nc=0, Nnc=0, Nok=0, Nfail=0, Nnone=0, Nna=0, Neu=0, cf=0, proj=0),
     "file_conclusion NO_RECORDS(no_symbols) is [design]"),
    ("C6", "0 records, symbols without records", lambda: [],
     dict(N_raw=0, Nc=0, Nnc=0, Nok=0, Nfail=0, Nnone=0, Nna=0, Neu=0, cf=0, proj=0),
     "file_conclusion NO_RECORDS(symbols_no_records) is [design]"),
    ("C7", "1xRESOLVED / non-empty dict + file degradation", lambda: [_resolved()],
     dict(N_raw=1, Nc=1, Nnc=0, Nok=1, Nfail=0, Nnone=0, Nna=0, Neu=0, cf=0, proj=1),
     "degradation is file-level [design]; projection identical to C1"),
    ("C8", "1xNON_SUCCESS / fallback + file degradation", lambda: [_non_success()],
     dict(N_raw=1, Nc=1, Nnc=0, Nok=1, Nfail=0, Nnone=0, Nna=0, Neu=0, cf=0, proj=1),
     "file_conclusion UNKNOWN_RESOLUTION is [design]"),
    ("C9", "1xNON_SUCCESS / fallback non-empty", lambda: [_non_success()],
     dict(N_raw=1, Nc=1, Nnc=0, Nok=1, Nfail=0, Nnone=0, Nna=0, Neu=0, cf=0, proj=1),
     "same projection as C3/C8; file_conclusion [design]"),
    ("C10", "1xRESOLVED + 1xNON_SUCCESS (same identity, diff content -> conflict)",
     lambda: [_resolved(qualified_name="X"), _non_success(qualified_name="X")],
     dict(N_raw=2, Nc=2, Nnc=0, Nok=2, Nfail=0, Nnone=0, Nna=0, Neu=0, cf=1, proj=3),
     "file_conclusion CONFLICT_ONLY is [design]"),
    ("C11", "1xRESOLVED / non-empty dict + orphan", lambda: [_resolved()],
     dict(N_raw=1, Nc=1, Nnc=0, Nok=1, Nfail=0, Nnone=0, Nna=0, Neu=0, cf=0, proj=1),
     "orphan is file-level [design]"),
    ("C12", "1xRESOLVED / non-empty dict + diagnostics out-of-range", lambda: [_resolved()],
     dict(N_raw=1, Nc=1, Nnc=0, Nok=1, Nfail=0, Nnone=0, Nna=0, Neu=0, cf=0, proj=1),
     "diagnostics越界 is file-level [design]"),
    ("C13", "1xRESOLVED / non-empty dict + path not in manifest", lambda: [_resolved()],
     dict(N_raw=1, Nc=1, Nnc=0, Nok=1, Nfail=0, Nnone=0, Nna=0, Neu=0, cf=0, proj=1),
     "path-not-in-manifest is file-level [design]"),
    ("C14", "1xRESOLVED / non-empty dict + line not positive int",
     lambda: [_resolved(source_location={"file_path": "pkg/mod.py", "line": 0, "column": 4})],
     dict(N_raw=1, Nc=1, Nnc=0, Nok=1, Nfail=0, Nnone=0, Nna=0, Neu=0, cf=0, proj=1),
     "invalid line dropped (evidence.line=None) but still a candidate"),
    ("C15", "1xRESOLVED / non-empty dict + TestSpec missing required field", lambda: [_resolved()],
     dict(N_raw=1, Nc=1, Nnc=0, Nok=1, Nfail=0, Nnone=0, Nna=0, Neu=0, cf=0, proj=1),
     "TestSpec缺必填 is file-level [design]"),
    ("C16", "2xRESOLVED identical (duplicate)",
     lambda: [_resolved(), _resolved()],
     dict(N_raw=2, Nc=2, Nnc=0, Nok=2, Nfail=0, Nnone=0, Nna=0, Neu=0, cf=0, proj=2),
     "same identity + same content -> duplicate, NOT a conflict"),
    ("C17", "1xNON_SUCCESS + 1xRESOLVED (mixed, different identities)",
     lambda: [_non_success(qualified_name="A"), _resolved(qualified_name="B")],
     dict(N_raw=2, Nc=2, Nnc=0, Nok=2, Nfail=0, Nnone=0, Nna=0, Neu=0, cf=0, proj=2),
     "different identities -> no conflict"),
    ("C18", "1xRESOLVED / evidence non-Mapping (single file)",
     lambda: [_non_mapping_evidence()],
     dict(N_raw=1, Nc=0, Nnc=1, Nok=0, Nfail=0, Nnone=1, Nna=0, Neu=0, cf=0, proj=1),
     "file_conclusion UNKNOWN_RESOLUTION is [design]"),
    ("C19", "1xRESOLVED non-Mapping evidence + 1xNON_SUCCESS candidate",
     lambda: [_non_mapping_evidence(qualified_name="A"), _non_success(qualified_name="B")],
     dict(N_raw=2, Nc=1, Nnc=1, Nok=1, Nfail=0, Nnone=1, Nna=0, Neu=0, cf=0, proj=2),
     "branch 8 / UNKNOWN is [design]"),
    ("C20", "2xNON_SUCCESS / fallback + file degradation (different identities)",
     lambda: [_non_success(qualified_name="A"), _non_success(qualified_name="B")],
     dict(N_raw=2, Nc=2, Nnc=0, Nok=2, Nfail=0, Nnone=0, Nna=0, Neu=0, cf=0, proj=2),
     "file_conclusion UNKNOWN_RESOLUTION is [design]"),
    ("C21", "2xNON_SUCCESS (same identity, diff content -> conflict)",
     lambda: [_non_success("UNRESOLVED", qualified_name="X"),
              _non_success("AMBIGUOUS", qualified_name="X")],
     dict(N_raw=2, Nc=2, Nnc=0, Nok=2, Nfail=0, Nnone=0, Nna=0, Neu=0, cf=1, proj=3),
     "different non-success states -> conflict; branch-2-priority is [design] and does not change cf"),
]


class S1CCaseProjectionTests(unittest.TestCase):
    def test_each_case_matches_expected_projection_counts(self):
        for case_id, desc, builder, expected, note in _CASES:
            with self.subTest(case=case_id, desc=desc):
                actual = _counts(builder())
                self.assertEqual(actual, expected, msg=f"{case_id} ({desc}); design-note: {note}")

    def test_identities_hold_for_every_case(self):
        for case_id, desc, builder, expected, note in _CASES:
            with self.subTest(case=case_id):
                c = _counts(builder())
                # (1) exhaustive partition of raw records
                self.assertEqual(
                    c["N_raw"],
                    c["Nc"] + c["Nnc"] + c["Nna"] + c["Neu"],
                    msg=f"{case_id} identity (1)",
                )
                # (2) candidate = construct_ok + construct_fail
                self.assertEqual(c["Nc"], c["Nok"] + c["Nfail"], msg=f"{case_id} identity (2)")
                # (3) non_candidate = evidence_none
                self.assertEqual(c["Nnc"], c["Nnone"], msg=f"{case_id} identity (3)")
                # proj = Nr + cf  (Nr == N_raw: one projected item per raw record)
                self.assertEqual(c["proj"], c["N_raw"] + c["cf"], msg=f"{case_id} proj=Nr+cf")
                # (4) simplified form only valid when N_not_attempted==0 and N_unexpected_error==0
                if c["Nna"] == 0 and c["Neu"] == 0:
                    self.assertEqual(
                        c["N_raw"], c["Nok"] + c["Nfail"] + c["Nnone"], msg=f"{case_id} identity (4)"
                    )

    def test_conflict_cases_emit_one_conflict_item_per_group(self):
        for case_id in ("C10", "C21"):
            builder = next(b for cid, _, b, _, _ in _CASES if cid == case_id)
            result = project_records(builder())
            conflict_items = [i for i in result.items if i.conclusion_type.value == "CONFLICT"]
            self.assertEqual(len(conflict_items), 1, msg=case_id)
            self.assertFalse(conflict_items[0].is_success)
            self.assertEqual(result.stats["conflict"], 1)

    def test_duplicate_case_emits_no_conflict(self):
        builder = next(b for cid, _, b, _, _ in _CASES if cid == "C16")
        result = project_records(builder())
        self.assertEqual(result.stats["conflict"], 0)
        self.assertEqual(result.stats["static_inference"], 2)


class S1CConflictAndDuplicateBoundaryTests(unittest.TestCase):
    """Six boundary scenarios required by the ticket."""

    def test_same_identity_same_content_is_duplicate_not_conflict(self):
        result = project_records([_resolved(qualified_name="X"), _resolved(qualified_name="X")])
        self.assertEqual(result.stats["conflict"], 0)

    def test_same_identity_different_content_is_conflict(self):
        result = project_records([
            _resolved(qualified_name="X"),
            _non_success("UNRESOLVED", qualified_name="X"),
        ])
        self.assertEqual(result.stats["conflict"], 1)

    def test_different_identity_same_content_is_not_conflict(self):
        # Same payload, different qualified_name -> different bucket key.
        result = project_records([
            _resolved(qualified_name="A"),
            _resolved(qualified_name="B"),
        ])
        self.assertEqual(result.stats["conflict"], 0)
        self.assertEqual(result.stats["projected_items"], 2)

    def test_different_status_same_identity_is_conflict(self):
        # Same identity (file+qualified_name), different resolution_status ->
        # different fingerprint -> conflict.
        result = project_records([
            _record(qualified_name="X", resolution_status="RESOLVED"),
            _record(qualified_name="X", resolution_status="UNRESOLVED"),
        ])
        self.assertEqual(result.stats["conflict"], 1)
        self.assertEqual(result.stats["projected_items"], 3)

    def test_multiple_conflict_groups_each_emit_one_item(self):
        records = [
            _resolved(qualified_name="X"), _non_success("UNRESOLVED", qualified_name="X"),
            _resolved(qualified_name="Y"), _non_success("AMBIGUOUS", qualified_name="Y"),
        ]
        result = project_records(records)
        self.assertEqual(result.stats["conflict"], 2)
        self.assertEqual(result.stats["N_raw"], 4)
        self.assertEqual(result.stats["projected_items"], 4 + 2)

    def test_zero_records_has_no_conflict_or_duplicate(self):
        result = project_records([])
        self.assertEqual(result.stats["conflict"], 0)
        self.assertEqual(result.stats["N_raw"], 0)
        self.assertEqual(result.stats["projected_items"], 0)

    def test_non_mapping_records_are_not_attempted(self):
        result = project_records(["junk", 42, None, ["a"]])
        self.assertEqual(result.stats["N_not_attempted"], 4)
        self.assertEqual(result.stats["N_candidate"], 0)
        self.assertEqual(result.stats["N_evidence_none"], 0)


class S1CDesignOnlyColumnsNotImplementedTests(unittest.TestCase):
    """file_conclusion / branch are design-only and must NOT be claimed as tested."""

    def test_projection_output_has_no_file_conclusion_or_branch(self):
        result = project_records([_resolved()])
        as_dict = result.to_dict()
        self.assertNotIn("file_conclusion", as_dict)
        self.assertNotIn("branch", as_dict)
        # only items + stats + schema_version are produced
        self.assertEqual(set(as_dict.keys()), {"schema_version", "items", "stats"})

    def test_single_record_projection_is_pure_function(self):
        a = project_record(_resolved()).to_dict()
        b = project_record(_resolved()).to_dict()
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
