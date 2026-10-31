"""USIT-P0.7.10-Z / P4: Z1-Z10 security tests for the trusted line-count snapshot.

These tests exercise the real implementation.  No mock is used in place of a
behavioural assertion: where a function is wrapped it is only to *count* calls
while still delegating to the real one, so a wrapper cannot make a test pass.

Everything runs against throw-away fixtures under a temporary directory.  No
real business project path is ever passed to a scan or a validation call.

Z2 embeds the SHA-256 of ``tests/test_graph_validation.py`` as it stood in this
ticket's PRE snapshot, so that "the frozen test was not touched" is verified by
the test suite itself rather than merely asserted in a report.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import pickle
import shutil
import sys
import tempfile
import time
import traceback
import tracemalloc
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

import usit.scanner as scanner                                        # noqa: E402
import usit.snapshot as snapshot_mod                                  # noqa: E402
from usit.graph import (validate_graph, validate_graph_file,           # noqa: E402
                        validate_graph_trusted)
from usit.snapshot import LineCountSnapshot, capture                  # noqa: E402
from tests.synthetic_scan import scan_project                         # noqa: E402

# PRE value recorded in outputs/p0710z/PRE_SHA256.json for the frozen test.
FROZEN_TEST_SHA256 = "72fcae081526ce0d67e0f3fbf1ba59ee1b2d887a10267149190e2b8c2d60b7f0"

APP = "def f():\n    return 1\n\n\ndef g():\n    return 2\n"   # 6 lines
LIB = "def helper():\n    return 1\n"                          # 2 lines
APP_LINES, LIB_LINES = 6, 2


def make_project(base: Path) -> Path:
    """Create the isolated fixture project."""
    root = base / "project"
    (root / "pkg").mkdir(parents=True, exist_ok=True)
    (root / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (root / "pkg" / "lib.py").write_text(LIB, encoding="utf-8")
    (root / "app.py").write_text(APP, encoding="utf-8")
    return root


def graph_for(*, node_line: int = 2, edge_line: int = 5, counts=None,
              extra_file: str | None = None) -> dict:
    """Build a minimal v1 graph; optionally add a second file node."""
    nodes = [
        {"id": "file:app.py", "type": "file", "label": "app.py",
         "evidence": {"path": "app.py"}},
        {"id": "symbol:app.py#f:1", "type": "function", "label": "f",
         "evidence": {"path": "app.py", "line": node_line}},
    ]
    edges = [
        {"id": "e1", "source": "file:app.py", "target": "symbol:app.py#f:1",
         "type": "defines", "evidence": {"path": "app.py", "line": edge_line}},
    ]
    if extra_file is not None:
        nodes.append({"id": f"file:{extra_file}", "type": "file", "label": extra_file,
                      "evidence": {"path": extra_file}})
        nodes.append({"id": f"symbol:{extra_file}#h:1", "type": "function", "label": "h",
                      "evidence": {"path": extra_file, "line": 1}})
    g = {"schema_version": "usit-graph-v1", "nodes": nodes, "edges": edges}
    if counts is not None:
        g["source_line_counts"] = counts
    return g


class ReadSpy:
    """Record ``Path.read_text`` calls attributable to usit/graph.py."""

    def __init__(self) -> None:
        self.graph_reads: list[str] = []
        self.all_reads: list[str] = []
        self._orig = Path.read_text

    def __enter__(self):
        spy = self

        def wrapper(self_path, *a, **k):
            frames = [f.filename for f in traceback.extract_stack()]
            spy.all_reads.append(str(self_path))
            if any(f.endswith("graph.py") for f in frames):
                spy.graph_reads.append(str(self_path))
            return spy._orig(self_path, *a, **k)

        self._p = patch.object(Path, "read_text", wrapper)
        self._p.start()
        return self

    def __exit__(self, *exc):
        self._p.stop()
        return False

    def of(self, root: Path) -> list[str]:
        return [r for r in self.graph_reads if r.startswith(str(root.resolve()))]


def codes(result: dict, group: str = "errors") -> list[str]:
    return [d["code"] for d in result[group]]


def all_codes(result: dict) -> list[str]:
    return [c for g in ("errors", "warnings", "unknown") for c in codes(result, g)]


class _Base(unittest.TestCase):
    """Shared fixture: an isolated project plus its trusted snapshot."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="z-"))
        self.root = make_project(self.tmp)
        self.records: list[dict] = []

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def snap(self, extra: dict[str, bytes] | None = None) -> LineCountSnapshot:
        payload = {"app.py": (self.root / "app.py").read_bytes(),
                   "pkg/__init__.py": b"",
                   "pkg/lib.py": (self.root / "pkg" / "lib.py").read_bytes()}
        if extra:
            payload.update(extra)
        return capture(self.root, payload, origin="test")

    def rec(self, tid: str, **kw) -> None:
        kw["test"] = tid
        self.records.append(kw)

    def write_graph(self, name: str, graph: dict) -> Path:
        p = self.tmp / name
        p.write_text(json.dumps(graph), encoding="utf-8")
        return p


# =============================================================== Z1
class Z1TrustedSnapshotHappyPath(_Base):
    def test_z1_counts_are_authoritative_and_no_source_is_read(self):
        snap = self.snap()
        self.assertEqual(snap.line_count("app.py"), APP_LINES)
        self.assertEqual(snap.line_count("pkg/lib.py"), LIB_LINES)
        g = graph_for(node_line=99, edge_line=99)
        with ReadSpy() as spy:
            result = validate_graph_trusted(g, snap, project_root=self.root)
        self.assertEqual(spy.of(self.root), [],
                         f"strict entry re-read project content: {spy.of(self.root)}")
        self.assertEqual(codes(result).count("LINE_OUT_OF_RANGE"), 2,
                         "line 99 must be out of range against the captured 6 lines")
        self.assertEqual(result["status"], "INVALID")
        self.rec("Z1", expected="0 project content reads; 2 LINE_OUT_OF_RANGE from the snapshot",
                 actual={"graph_reads": len(spy.of(self.root)),
                         "line_out_of_range": codes(result).count("LINE_OUT_OF_RANGE"),
                         "status": result["status"]})


# =============================================================== Z2
class Z2FrozenTestCompatibility(_Base):
    def test_z2a_frozen_test_file_is_byte_identical(self):
        digest = hashlib.sha256((HERE / "test_graph_validation.py").read_bytes()).hexdigest()
        self.assertEqual(digest, FROZEN_TEST_SHA256,
                         "tests/test_graph_validation.py was modified; the frozen test must stay pristine")
        self.rec("Z2a", expected="frozen test SHA-256 unchanged", actual={"sha256": digest})

    def test_z2b_positional_project_root_and_out_of_range_still_work(self):
        """Reproduce the frozen assertions without touching the frozen file."""
        self.root.joinpath("app.py").write_text("def run():\n    return run()\n", encoding="utf-8")
        fixture = json.loads((HERE / "fixtures" / "graphs" / "valid_v1.json")
                             .read_text(encoding="utf-8"))
        legacy = validate_graph(fixture, self.root)                 # positional root, no counts
        self.assertEqual(legacy["status"], "VALID",
                         "the legacy positional call must keep its historical verdict")
        bad = copy.deepcopy(fixture)
        bad["edges"][0]["evidence"]["line"] = 99
        with ReadSpy() as spy:
            result = validate_graph(bad, self.root)                 # exactly as :67
        self.assertIn("LINE_OUT_OF_RANGE", codes(result),
                      "the frozen :67 assertion depends on the legacy by-path branch")
        self.assertGreater(len(spy.of(self.root)), 0,
                           "the legacy branch must genuinely read (otherwise :67 proves nothing)")
        self.rec("Z2b", expected="legacy signature/behaviour intact, :67 equivalent passes",
                 actual={"valid_status": legacy["status"],
                         "out_of_range": "LINE_OUT_OF_RANGE" in codes(result),
                         "legacy_reads": len(spy.of(self.root))})


# =============================================================== Z3
class Z3ForgeryIsNotTrusted(_Base):
    def test_z3a_plain_mapping_is_rejected(self):
        snap = self.snap()
        g = graph_for(node_line=99, edge_line=99)
        for bogus in ({"app.py": 10 ** 6}, snap.as_mapping(), {}, ["app.py"], "", 6, None):
            with self.assertRaises(TypeError, msg=f"strict entry accepted {type(bogus).__name__}"):
                validate_graph_trusted(g, bogus, project_root=self.root)
        self.rec("Z3a", expected="dict/list/str/int/None all raise TypeError",
                 actual="TypeError for 7 non-snapshot values")

    def test_z3b_graph_field_cannot_override_the_snapshot(self):
        snap = self.snap()
        g = graph_for(node_line=99, edge_line=99, counts={"app.py": 10 ** 6})
        with ReadSpy() as spy:
            result = validate_graph_trusted(g, snap, project_root=self.root)
        self.assertEqual(codes(result).count("LINE_OUT_OF_RANGE"), 2,
                         "the forged graph field must not suppress the real violation")
        self.assertEqual(spy.of(self.root), [])
        self.rec("Z3b", expected="forged graph['source_line_counts'] ignored by the strict entry",
                 actual={"line_out_of_range": codes(result).count("LINE_OUT_OF_RANGE")})

    def test_z3c_validate_graph_file_field_is_not_evidence(self):
        snap = self.snap()
        forged = graph_for(node_line=99, edge_line=99, counts={"app.py": 10 ** 6})
        gp = self.write_graph("forged.json", forged)
        trusted = validate_graph_file(gp, self.root, trusted_snapshot=snap)
        self.assertEqual(codes(trusted).count("LINE_OUT_OF_RANGE"), 2,
                         "a supplied snapshot must win over the embedded field")
        closed = validate_graph_file(gp, self.root, require_trusted=True)
        self.assertNotIn("LINE_OUT_OF_RANGE", codes(closed),
                         "with require_trusted and no snapshot the field must grant nothing")
        self.assertIn("NODE_LINE_UNVERIFIED", all_codes(closed))
        legacy = validate_graph_file(gp, self.root)
        self.assertNotIn("LINE_OUT_OF_RANGE", codes(legacy),
                         "documented residual: the legacy convenience path still honours the field")
        self.rec("Z3c",
                 expected="snapshot wins; require_trusted ignores the field; legacy path still honours it (disclosed)",
                 actual={"with_snapshot": codes(trusted).count("LINE_OUT_OF_RANGE"),
                         "require_trusted_codes": all_codes(closed),
                         "legacy_codes": codes(legacy)})


# =============================================================== Z4
class Z4Staleness(_Base):
    def test_z4a_grown_file_degrades_to_unverified(self):
        snap = self.snap()
        (self.root / "app.py").write_text("x = 1\n" * 40, encoding="utf-8")
        integrity = snap.integrity()
        self.assertEqual(integrity["files"]["app.py"], "DRIFTED")
        self.assertNotIn("app.py", snap.verified_mapping())
        with ReadSpy() as spy:
            result = validate_graph_trusted(graph_for(node_line=20, edge_line=20), snap,
                                            project_root=self.root)
        self.assertIn("NODE_LINE_UNVERIFIED", all_codes(result))
        self.assertNotIn("LINE_OUT_OF_RANGE", codes(result),
                         "a drifted file must not be range-checked against the stale count")
        self.assertEqual(spy.of(self.root), [],
                         "drift must never trigger a re-read")
        self.rec("Z4a", expected="DRIFTED -> key omitted -> UNVERIFIED, 0 reads",
                 actual={"status": integrity["files"]["app.py"],
                         "codes": all_codes(result), "reads": len(spy.of(self.root))})

    def test_z4b_shrunk_file_degrades_to_unverified(self):
        snap = self.snap()
        (self.root / "app.py").write_text("a\nb\nc\n", encoding="utf-8")
        result = validate_graph_trusted(graph_for(node_line=5, edge_line=5), snap,
                                        project_root=self.root)
        self.assertIn("NODE_LINE_UNVERIFIED", all_codes(result))
        self.assertNotIn("LINE_OUT_OF_RANGE", codes(result))
        self.rec("Z4b", expected="shrink -> UNVERIFIED", actual={"codes": all_codes(result)})

    def test_z4c_metadata_is_not_a_content_proof(self):
        """A size- and mtime-preserving rewrite is NOT detected: documented limitation."""
        snap = self.snap()
        target = self.root / "app.py"
        st = target.stat()
        # Derive the forgery from the CAPTURED BYTES rather than from the source
        # string: write_text expands "\n" to "\r\n" on Windows, so rebuilding from
        # the literal would change the on-disk size and make this a size-difference
        # test instead of a metadata-blind-spot test.
        original = target.read_bytes()
        forged = original.replace(b"return 1", b"return 9").replace(b"return 2", b"return 8")
        self.assertEqual(len(forged), len(original),
                         "fixture must be byte-length preserving for this test to mean anything")
        self.assertNotEqual(forged, original, "the fixture content must actually change")
        target.write_bytes(forged)
        os.utime(target, ns=(st.st_atime_ns, st.st_mtime_ns))
        integrity = snap.integrity()
        survives = integrity["files"]["app.py"] == "OK"
        self.assertTrue(survives,
                        "if this now fails the identity check got stronger -- update the docs, not the test")
        self.rec("Z4c",
                 expected="size+mtime-preserving rewrite is undetected (stat != content proof)",
                 actual={"status": integrity["files"]["app.py"],
                         "content_changed": True,
                         "size_and_mtime_preserved": True,
                         "limitation": "identity metadata detects replacement, not in-place edits "
                                       "that preserve size and mtime_ns"})

    def test_z4d_cross_project_snapshot_is_refused(self):
        snap = self.snap()
        other = self.tmp / "other"
        other.mkdir()
        (other / "app.py").write_text(APP, encoding="utf-8")
        with self.assertRaises(ValueError):
            validate_graph_trusted(graph_for(), snap, project_root=other)
        self.rec("Z4d", expected="cross-project root -> ValueError", actual="ValueError")


# =============================================================== Z5
class Z5Serialisation(_Base):
    def test_z5_no_transport_carries_trust(self):
        snap = self.snap()
        g = graph_for()
        with self.assertRaises(TypeError):
            pickle.dumps(snap)
        with self.assertRaises(TypeError):
            copy.copy(snap)
        with self.assertRaises(TypeError):
            copy.deepcopy(snap)
        round_tripped = json.loads(json.dumps(snap.as_mapping()))
        self.assertEqual(round_tripped, snap.as_mapping())
        with self.assertRaises(TypeError):
            validate_graph_trusted(g, round_tripped, project_root=self.root)
        with self.assertRaises(TypeError):
            LineCountSnapshot(object(), root=str(self.root), root_identity=None,
                              files={}, origin="forged", version=1)
        self.rec("Z5", expected="pickle/copy/deepcopy/direct-construct/json-round-trip all refused",
                 actual={"as_mapping_is_plain_data": True,
                         "json_round_trip_rejected": True})


# =============================================================== Z6
class Z6MalformedInputs(_Base):
    def test_z6a_missing_key_and_empty_snapshot(self):
        snap = self.snap()
        g = graph_for(node_line=1, edge_line=5, extra_file="pkg/lib.py")
        only_app = capture(self.root, {"app.py": (self.root / "app.py").read_bytes()})
        empty = capture(self.root, {})
        with ReadSpy() as spy:
            partial = validate_graph_trusted(g, only_app, project_root=self.root)
            none_at_all = validate_graph_trusted(graph_for(), empty, project_root=self.root)
        self.assertIn("NODE_LINE_UNVERIFIED", all_codes(partial))
        self.assertIn("NODE_LINE_UNVERIFIED", all_codes(none_at_all))
        self.assertEqual(spy.of(self.root), [], "a missing key must never fall back to a read")
        self.rec("Z6a", expected="missing key / empty snapshot -> UNVERIFIED, 0 reads",
                 actual={"partial": all_codes(partial), "empty": all_codes(none_at_all)})

    def test_z6b_supplied_numbers_and_bad_values_are_rejected(self):
        cases = {
            "int_count": 6,
            "negative": -1,
            "bool": True,
            "float": 6.0,
            "str": "6",
            "none": None,
        }
        for name, value in cases.items():
            with self.assertRaises(TypeError, msg=f"capture accepted {name}"):
                capture(self.root, {"app.py": value})
        self.rec("Z6b", expected="counts can never be supplied as numbers (TypeError)",
                 actual=f"TypeError for all of {sorted(cases)}")

    def test_z6c_illegal_paths_are_rejected(self):
        bad_keys = ["../outside.py", "pkg/../../x.py", "/abs.py", "app\\py", "./app.py",
                    "app.py/", "", "."]
        for key in bad_keys:
            with self.assertRaises((ValueError, TypeError), msg=f"capture accepted {key!r}"):
                capture(self.root, {key: b""})
        self.rec("Z6c", expected="escape/absolute/backslash/non-normalised keys rejected",
                 actual=f"rejected {bad_keys}")

    def test_z6d_non_mapping_payload_is_rejected(self):
        for payload in ([("app.py", b"")], "app.py", 6, None):
            with self.assertRaises(TypeError):
                capture(self.root, payload)
        self.rec("Z6d", expected="non-mapping contents -> TypeError", actual="TypeError")

    def test_z6e_invalid_graph_lines_never_read(self):
        snap = self.snap()
        with ReadSpy() as spy:
            zero = validate_graph_trusted(graph_for(node_line=0, edge_line=-3), snap,
                                          project_root=self.root)
        self.assertIn("INVALID_LINE", codes(zero))
        self.assertEqual(spy.of(self.root), [])
        self.rec("Z6e", expected="invalid line -> INVALID_LINE, 0 reads",
                 actual={"codes": codes(zero)})

    def test_z6f_closed_and_wrong_version_snapshot(self):
        snap = self.snap()
        snap.close()
        self.assertEqual(snap.state, "closed")
        with self.assertRaises(ValueError):
            validate_graph_trusted(graph_for(), snap, project_root=self.root)
        self.rec("Z6f", expected="closed snapshot -> ValueError", actual="ValueError")


# =============================================================== Z7
class Z7EscapeAndReplacement(_Base):
    def test_z7a_graph_path_escape_never_reads(self):
        snap = self.snap()
        g = graph_for()
        g["edges"][0]["evidence"]["path"] = "pkg/../../outside.py"
        with ReadSpy() as spy:
            result = validate_graph_trusted(g, snap, project_root=self.root)
        self.assertIn("EDGE_EVIDENCE_PATH", codes(result))
        self.assertEqual([r for r in spy.all_reads if "outside.py" in r], [])
        self.rec("Z7a", expected="escape rejected before any read", actual={"codes": codes(result)})

    def test_z7b_symlink_to_outside_is_not_read(self):
        outside = self.tmp / "outside.py"
        outside.write_text("secret\n" * 50, encoding="utf-8")
        link = self.root / "app.py"
        created = False
        try:
            link.unlink()
            os.symlink(outside, link)
            created = True
        except (OSError, NotImplementedError) as exc:
            self.rec("Z7b", status="NOT TESTED",
                     reason=f"host refused to create a symlink: {type(exc).__name__}")
            self.skipTest(f"cannot create a symlink on this host: {exc}")
        if created:
            snap = capture(self.root, {"app.py": (self.root / "app.py").read_bytes()})
            with ReadSpy() as spy:
                result = validate_graph_trusted(graph_for(node_line=2, edge_line=5), snap,
                                                project_root=self.root)
            self.assertEqual([r for r in spy.graph_reads if "outside.py" in r], [],
                             "the strict path must never read the symlink target")
            self.assertEqual(spy.of(self.root), [])
            self.rec("Z7b", expected="symlinked file validated via metadata only, no content read",
                     actual={"codes": all_codes(result), "reads": len(spy.of(self.root))})

    def test_z7c_replacement_is_detected(self):
        snap = self.snap()
        target = self.root / "app.py"
        target.unlink()
        target.write_text("brand new content\n" * 7, encoding="utf-8")
        self.assertEqual(snap.integrity()["files"]["app.py"], "DRIFTED")
        with ReadSpy() as spy:
            result = validate_graph_trusted(graph_for(node_line=2, edge_line=5), snap,
                                            project_root=self.root)
        self.assertIn("NODE_LINE_UNVERIFIED", all_codes(result))
        self.assertEqual(spy.of(self.root), [])
        self.rec("Z7c", expected="replacement -> DRIFTED -> UNVERIFIED, 0 reads",
                 actual={"codes": all_codes(result)})


# =============================================================== Z8
class Z8ProductionChain(_Base):
    def test_z8_scanner_uses_the_strict_entry_and_never_the_legacy_one(self):
        self.assertIs(scanner.validate_graph_trusted, validate_graph_trusted,
                      "the scanner must bind the real strict entry, not a local copy")
        calls = {"legacy": 0, "legacy_alias": 0, "trusted": 0}
        seen: list[dict] = []
        real_legacy = scanner.validate_graph
        real_alias = scanner._LEGACY_VALIDATE
        real_trusted = scanner.validate_graph_trusted

        def counting_legacy(*a, **k):
            calls["legacy"] += 1
            return real_legacy(*a, **k)

        def counting_alias(*a, **k):
            calls["legacy_alias"] += 1
            return real_alias(*a, **k)

        def counting_trusted(graph, snap, **k):
            calls["trusted"] += 1
            seen.append({"is_snapshot": isinstance(snap, LineCountSnapshot),
                         "files": len(snap),
                         "origin": snap.origin,
                         "state": snap.state})
            return real_trusted(graph, snap, **k)

        out = self.tmp / "out"
        with patch.object(scanner, "validate_graph", counting_legacy), \
             patch.object(scanner, "_LEGACY_VALIDATE", counting_alias), \
             patch.object(scanner, "validate_graph_trusted", counting_trusted), \
             ReadSpy() as spy:
            summary = scan_project(self.root, out, force=True)

        self.assertEqual(calls["legacy"], 0,
                         "the production scan reached the legacy validate_graph entry")
        self.assertEqual(calls["legacy_alias"], 0,
                         "the production scan reached the legacy audit alias")
        self.assertEqual(calls["trusted"], 1, "the strict entry must be called exactly once")
        self.assertTrue(seen and seen[0]["is_snapshot"],
                        "the scanner must pass a real LineCountSnapshot")
        self.assertEqual(seen[0]["files"], 3, "the snapshot must cover all captured files")
        self.assertEqual(spy.of(self.root), [],
                         f"the scan re-read project content during validation: {spy.of(self.root)}")
        published = json.loads((out / "knowledge_graph.json").read_text(encoding="utf-8"))
        self.assertEqual(published["source_line_counts"]["app.py"], APP_LINES,
                         "the published field must equal the captured byte count")
        self.assertIn("graph_validation_status", summary)
        self.rec("Z8", expected="legacy 0 / legacy-alias 0 / trusted 1 / 0 project reads during validation",
                 actual={"legacy_calls": calls["legacy"],
                         "legacy_alias_calls": calls["legacy_alias"],
                         "trusted_calls": calls["trusted"],
                         "snapshot": seen[0] if seen else None,
                         "validation_reads": len(spy.of(self.root)),
                         "published_counts": published["source_line_counts"],
                         "status": summary["graph_validation_status"]})


# =============================================================== Z9
class Z9FailurePaths(_Base):
    def test_z9a_bad_capture_root_is_refused(self):
        with self.assertRaises(ValueError):
            capture(self.tmp / "does-not-exist", {"app.py": b"x"})
        self.rec("Z9a", expected="nonexistent capture root -> ValueError", actual="ValueError")

    def test_z9b_validator_crash_publishes_nothing_valid(self):
        out = self.tmp / "out"
        boom = RuntimeError("injected validator failure")

        def exploding(*a, **k):
            raise boom

        with patch.object(scanner, "validate_graph_trusted", exploding):
            with self.assertRaises(RuntimeError):
                scan_project(self.root, out, force=True)
        self.assertFalse((out / "knowledge_graph.json").exists(),
                         "a crashed validation must not publish a graph")
        self.assertFalse((out / "scan_status.json").exists(),
                         "a crashed validation must not publish a COMPLETE status")
        blocked = list(out.parent.glob("*scan-status*")) + list(out.parent.glob("*.status.json"))
        self.rec("Z9b", expected="exception propagates, no VALID artifact published",
                 actual={"graph_published": (out / "knowledge_graph.json").exists(),
                         "complete_status_published": (out / "scan_status.json").exists(),
                         "blocked_status_files": [p.name for p in blocked]})

    def test_z9c_invalid_graph_structure_is_reported_not_validated(self):
        snap = self.snap()
        result = validate_graph_trusted({"nodes": "nope"}, snap, project_root=self.root)
        self.assertEqual(result["status"], "INVALID")
        self.assertIn("MISSING_FIELD", codes(result))
        self.rec("Z9c", expected="malformed graph -> INVALID with structural diagnostics",
                 actual={"codes": codes(result)})


# =============================================================== Z10
class Z10RegressionAndPerformance(_Base):
    def test_z10a_trusted_path_matches_legacy_verdicts_on_the_fixture(self):
        snap = self.snap()
        g = graph_for(node_line=2, edge_line=5)
        trusted = validate_graph_trusted(g, snap, project_root=self.root)
        legacy = validate_graph(g, self.root, line_counts=snap.as_mapping())
        self.assertEqual(trusted["status"], legacy["status"])
        self.assertEqual(all_codes(trusted), all_codes(legacy),
                         "the trusted path must not change the diagnostic set")
        self.rec("Z10a", expected="identical status and diagnostics vs the explicit-mapping path",
                 actual={"status": trusted["status"], "codes": all_codes(trusted)})

    def test_z10b_validation_overhead_is_metadata_only(self):
        snap = self.snap()
        g = graph_for(node_line=2, edge_line=5)
        # trusted (identity verification on) vs explicit mapping (no verification)
        t0 = time.perf_counter()
        for _ in range(200):
            validate_graph_trusted(g, snap, project_root=self.root)
        trusted_s = time.perf_counter() - t0
        mapping = snap.as_mapping()
        t0 = time.perf_counter()
        for _ in range(200):
            validate_graph(g, self.root, line_counts=mapping)
        legacy_s = time.perf_counter() - t0
        tracemalloc.start()
        validate_graph_trusted(g, snap, project_root=self.root)
        _, trusted_peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        per_call_trusted_ms = trusted_s / 200 * 1000
        per_call_legacy_ms = legacy_s / 200 * 1000
        self.assertLess(per_call_trusted_ms, 50.0,
                        f"strict validation unexpectedly slow: {per_call_trusted_ms:.3f} ms/call")
        self.rec("Z10b", expected="strict entry stays in the sub-millisecond-to-few-ms range",
                 actual={"trusted_ms_per_call": round(per_call_trusted_ms, 4),
                         "explicit_mapping_ms_per_call": round(per_call_legacy_ms, 4),
                         "overhead_x": (round(per_call_trusted_ms / per_call_legacy_ms, 2)
                                        if per_call_legacy_ms else None),
                         "trusted_peak_bytes": trusted_peak,
                         "files": len(snap)})


if __name__ == "__main__":
    unittest.main(verbosity=2)
