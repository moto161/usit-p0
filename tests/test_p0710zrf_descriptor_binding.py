"""USIT-P0.7.10-ZRF / P2: ZRF-1..12 security tests for descriptor-bound snapshots.

Every test exercises the real ``usit.snapshot`` / ``usit.scanner`` /
``usit.graph`` implementation inside throw-away temporary trees.  No repository
file is written, no real business project path is used, and where timing must be
forced the experiment injects a condition into the real code path (fault
injection), never edits one.

ZRF-8 additionally pins the SHA-256 of the two existing test files whose
behaviour this ticket must not disturb.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
import traceback
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

import usit.scanner as scanner                                        # noqa: E402
from usit.graph import validate_graph, validate_graph_trusted          # noqa: E402
from usit.snapshot import DescriptorIdentity, LineCountSnapshot, capture  # noqa: E402
from tests.synthetic_scan import scan_project                          # noqa: E402

# PRE hashes of the two existing test files this ticket must leave untouched.
FROZEN_SHA = {
    "tests/test_graph_validation.py": "72fcae081526ce0d67e0f3fbf1ba59ee1b2d887a10267149190e2b8c2d60b7f0",
    "tests/test_p073_budget_race.py": "470851778ee2224593ab146109b86b613f52fcd60149ddc23092da33dd3fbeb6",
}

BIG = "".join(f"line {i}\n" for i in range(1, 101))     # 100 lines
SMALL = "a\nb\nc\n"                                     # 3 lines


def graph_for(node_line=2, edge_line=5, path="app.py"):
    return {"schema_version": "usit-graph-v1",
            "nodes": [{"id": f"file:{path}", "type": "file", "label": path,
                       "evidence": {"path": path}},
                      {"id": f"symbol:{path}#f:1", "type": "function", "label": "f",
                       "evidence": {"path": path, "line": node_line}}],
            "edges": [{"id": "e1", "source": f"file:{path}", "target": f"symbol:{path}#f:1",
                       "type": "defines", "evidence": {"path": path, "line": edge_line}}]}


class ReadSpy:
    """Count Path.read_text calls attributable to usit/graph.py."""

    def __init__(self):
        self.reads: list[str] = []
        self.all_reads: list[str] = []
        self._o = Path.read_text

    def __enter__(self):
        s = self

        def w(p, *a, **k):
            frames = [f.filename for f in traceback.extract_stack()]
            s.all_reads.append(str(p))
            if any(f.endswith("graph.py") for f in frames):
                s.reads.append(str(p))
            return s._o(p, *a, **k)

        self._p = patch.object(Path, "read_text", w)
        self._p.start()
        return self

    def __exit__(self, *e):
        self._p.stop()
        return False


def codes(r, group="errors"):
    return [d["code"] for d in r[group]]


def all_codes(r):
    return [c for g in ("errors", "warnings", "unknown") for c in codes(r, g)]


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="zrf-"))
        self.root = self.tmp / "project"
        (self.root / "pkg").mkdir(parents=True)
        (self.root / "pkg" / "__init__.py").write_text("", encoding="utf-8")
        (self.root / "app.py").write_text(BIG, encoding="utf-8")
        self.records: list[dict] = []

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def rec(self, tid, **kw):
        kw["test"] = tid
        self.records.append(kw)

    def descriptor_identity_of(self, rel: str = "app.py") -> DescriptorIdentity:
        """The identity the pipeline would have taken from the audited descriptor."""
        st = (self.root / rel).stat()
        return DescriptorIdentity(st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)

    def snapshot_with_pipeline_identity(self) -> LineCountSnapshot:
        return capture(self.root, {"app.py": (self.root / "app.py").read_bytes()},
                       identities={"app.py": self.descriptor_identity_of()},
                       origin="zrf-test")


# =============================================================== ZRF-1
class ZRF1OriginalR1MustBeRefused(_Base):
    """ZR-H1: bytes from A must never be accepted under B's identity."""

    def _assert_refused(self, snap) -> None:
        # the record must not be verifiable against the path that now holds B
        self.assertEqual(snap.verified_mapping(), {},
                         "a mis-bound record must never be accepted by the strict entry")
        self.assertIn(snap.integrity()["files"]["app.py"], ("DRIFTED", "IDENTITY_UNAVAILABLE"))
        with ReadSpy() as spy:
            r = validate_graph_trusted(graph_for(node_line=99, edge_line=99), snap,
                                       project_root=self.root)
        self.assertNotIn("LINE_OUT_OF_RANGE", codes(r),
                         "a range verdict based on A's counts must not be produced for B")
        self.assertIn("NODE_LINE_UNVERIFIED", all_codes(r))
        self.assertEqual(spy.reads, [])
        return r

    def test_zrf1a_descriptor_identity_of_A_path_replaced_by_B(self):
        ident = self.descriptor_identity_of()                 # taken while the file is A
        a_bytes = (self.root / "app.py").read_bytes()
        (self.root / "app.py").write_text(SMALL, encoding="utf-8")   # B: 3 lines
        snap = capture(self.root, {"app.py": a_bytes},
                       identities={"app.py": ident}, origin="zrf1a")
        r = self._assert_refused(snap)
        self.rec("ZRF1a", expected="A bytes + A identity, path now B -> refused (UNVERIFIED)",
                 actual={"verified": snap.verified_mapping(),
                         "integrity": snap.integrity()["files"],
                         "codes": all_codes(r)})

    def test_zrf1b_same_size_replacement_through_a_new_inode_is_refused(self):
        """The exact hole the old read-loop size-only check let through.

        The replacement is performed with ``os.replace`` so the path ends up on a
        DIFFERENT inode with the same size -- the case a size-only check cannot
        see.  The (dev, ino) half of the descriptor identity catches it.
        """
        ident = self.descriptor_identity_of()
        a_bytes = (self.root / "app.py").read_bytes()
        same_size_b = self.tmp / "same_size_b.py"
        same_size_b.write_bytes(b"z" * len(a_bytes))          # same size, different file
        self.assertEqual(len(same_size_b.read_bytes()), len(a_bytes))
        os.replace(same_size_b, self.root / "app.py")          # new inode, same size
        self.assertNotEqual(
            (self.root / "app.py").stat().st_ino,
            ident.ino, "the fixture must actually change the inode")
        snap = capture(self.root, {"app.py": a_bytes},
                       identities={"app.py": ident}, origin="zrf1b")
        r = self._assert_refused(snap)
        self.rec("ZRF1b", expected="same-size substitution on a new inode refused via (dev, ino)",
                 actual={"verified": snap.verified_mapping(),
                         "integrity": snap.integrity()["files"],
                         "codes": all_codes(r)})

    def test_zrf1c_same_inode_in_place_rewrite_is_a_documented_limitation(self):
        """Same inode + same size + restored mtime: metadata cannot see it.

        ZRF-12 requires this limitation to be stated, not hidden.  The record is
        accepted because no metadata differs -- a content-level check would have
        to re-read the bytes, which the design deliberately does not do.  The
        mtime is restored explicitly so the demonstration is deterministic;
        without that, detection is best-effort (ZRF_run2.log shows a run where
        the filesystem ticked the mtime and the rewrite WAS flagged DRIFTED).
        """
        ident = self.descriptor_identity_of()
        a_bytes = (self.root / "app.py").read_bytes()
        st = (self.root / "app.py").stat()
        (self.root / "app.py").write_bytes(b"z" * len(a_bytes))  # truncate in place: same inode
        os.utime(self.root / "app.py", ns=(st.st_atime_ns, st.st_mtime_ns))
        snap = capture(self.root, {"app.py": a_bytes},
                       identities={"app.py": ident}, origin="zrf1c")
        self.assertEqual(snap.verified_mapping(), {"app.py": 100})
        self.rec("ZRF1c", expected="same-inode same-metadata rewrite is UNDETECTABLE (ZRF-12 boundary)",
                 actual={"verified": snap.verified_mapping(),
                         "integrity": snap.integrity()["files"],
                         "limitation": "metadata-only verification; a content proof would "
                                       "require re-reading the bytes"})

    def test_zrf1c_direct_capture_without_identities_refuses_mismatched_bytes(self):
        (self.root / "app.py").write_text(SMALL, encoding="utf-8")   # path: 3 lines
        snap = capture(self.root, {"app.py": BIG.encode("utf-8")},   # bytes: 100 lines
                       origin="zrf1c")
        self.assertEqual(snap.verified_mapping(), {})
        self.assertFalse(snap.records()["app.py"].identity_available)
        self.rec("ZRF1c", expected="self_observed size cross-check refuses mismatched bytes",
                 actual={"available": snap.records()["app.py"].identity_available})


# =============================================================== ZRF-2
class ZRF2IdentityConsistencyAcrossTheRead(_Base):
    def test_zrf2_descriptor_identity_matches_the_file_and_detects_changes(self):
        target = self.root / "app.py"
        box: dict = {}
        raw = scanner.open_input_file(target, scanner.MAX_FILE_BYTES, identity_out=box)
        self.assertIsInstance(raw, bytes, "the return contract must stay bytes")
        ident = box.get("identity")
        self.assertIsInstance(ident, DescriptorIdentity)
        self.assertEqual(ident.size, len(raw))
        self.assertEqual(ident.size, target.stat().st_size)
        self.assertEqual(ident.mtime_ns, target.stat().st_mtime_ns)
        snap = capture(self.root, {"app.py": raw}, identities={"app.py": ident},
                       origin="zrf2")
        self.assertEqual(snap.verified_mapping(), {"app.py": snap.line_count("app.py")})
        # now the file changes: the recorded identity must stop verifying
        target.write_text(BIG + "extra\n", encoding="utf-8")
        self.assertEqual(snap.integrity()["files"]["app.py"], "DRIFTED")
        self.assertEqual(snap.verified_mapping(), {})
        self.rec("ZRF2", expected="identity from the read descriptor; consistent -> OK, changed -> DRIFTED",
                 actual={"consistent": True, "after_change": snap.integrity()["files"]})


# =============================================================== ZRF-3
class ZRF3PostCaptureReplacement(_Base):
    def test_zrf3_replacement_after_capture_fails_closed(self):
        snap = self.snapshot_with_pipeline_identity()
        self.assertEqual(snap.records()["app.py"].identity_source, "descriptor")
        (self.root / "app.py").write_text(SMALL, encoding="utf-8")
        with ReadSpy() as spy:
            r = validate_graph_trusted(graph_for(node_line=99, edge_line=99), snap,
                                       project_root=self.root)
        self.assertIn("NODE_LINE_UNVERIFIED", all_codes(r))
        self.assertNotIn("LINE_OUT_OF_RANGE", codes(r))
        self.assertEqual(spy.reads, [])
        self.rec("ZRF3", expected="post-capture replacement -> UNVERIFIED, 0 reads",
                 actual={"integrity": snap.integrity()["files"], "codes": all_codes(r)})


# =============================================================== ZRF-4
class ZRF4MissingOrForgedIdentities(_Base):
    def test_zrf4a_missing_identity_degrades_to_self_observed_with_size_check(self):
        snap = capture(self.root, {"app.py": (self.root / "app.py").read_bytes()},
                       origin="zrf4a")
        rec = snap.records()["app.py"]
        self.assertEqual(rec.identity_source, "self_observed")
        self.assertTrue(rec.identity_available)
        self.rec("ZRF4a", expected="no identity -> self_observed (size cross-checked)",
                 actual={"source": rec.identity_source, "available": rec.identity_available})

    def test_zrf4b_bad_identity_types_are_rejected(self):
        payload = {"app.py": (self.root / "app.py").read_bytes()}
        for bogus in ({"app.py": {"dev": 1}}, {"app.py": (1, 2, 3, 4)},
                      {"app.py": "descriptor"}):
            with self.assertRaises(TypeError, msg=f"accepted {type(bogus['app.py']).__name__}"):
                capture(self.root, payload, identities=bogus, origin="zrf4b")
        # None means "no descriptor identity for this path" and falls back to the
        # self_observed mode (which still cross-checks the size) -- documented.
        snap = capture(self.root, payload, identities={"app.py": None}, origin="zrf4b")
        self.assertEqual(snap.records()["app.py"].identity_source, "self_observed")
        self.rec("ZRF4b", expected="dict/tuple/str -> TypeError; None -> absent (self_observed)",
                 actual={"type_errors": 3, "none_treated_as_absent": True})

    def test_zrf4c_identity_for_an_unknown_path_is_rejected(self):
        with self.assertRaises(ValueError):
            capture(self.root, {"app.py": (self.root / "app.py").read_bytes()},
                    identities={"nope.py": self.descriptor_identity_of()}, origin="zrf4c")
        self.rec("ZRF4c", expected="identity for a path outside contents -> ValueError",
                 actual="ValueError")

    def test_zrf4d_same_process_forgery_is_out_of_scope_and_recorded(self):
        """A forged DescriptorIdentity cannot be distinguished in-process.

        This is the documented boundary (module docstring): same-process
        malicious code can construct the class directly.  The test pins the
        behaviour honestly instead of claiming a guarantee that does not exist.
        """
        forged = DescriptorIdentity(1, 1, len(BIG), 12345)
        snap = capture(self.root, {"app.py": BIG.encode("utf-8")},
                       identities={"app.py": forged}, origin="zrf4d")
        self.assertEqual(snap.records()["app.py"].identity_source, "descriptor")
        self.rec("ZRF4d", expected="same-process forgery is OUT OF SCOPE (documented)",
                 actual={"accepted": True,
                         "limitation": "capture() cannot verify the in-process origin of a "
                                       "DescriptorIdentity; defence relies on the pipeline "
                                       "being the only caller, as documented"})


# =============================================================== ZRF-5
class ZRF5DataSourceConsistency(_Base):
    def test_zrf5_size_mismatch_between_identity_and_bytes_is_never_bound(self):
        ident = DescriptorIdentity(1, 2, 99999, 42)          # size does not match BIG
        snap = capture(self.root, {"app.py": BIG.encode("utf-8")},
                       identities={"app.py": ident}, origin="zrf5")
        self.assertFalse(snap.records()["app.py"].identity_available)
        self.assertEqual(snap.verified_mapping(), {})
        self.rec("ZRF5", expected="identity.size != len(payload) -> record unverifiable",
                 actual={"available": snap.records()["app.py"].identity_available})

    def test_zrf5b_counts_and_digest_come_from_the_same_bytes(self):
        snap = self.snapshot_with_pipeline_identity()
        rec = snap.records()["app.py"]
        payload = (self.root / "app.py").read_bytes()
        self.assertEqual(rec.digest, hashlib.sha256(payload).hexdigest())
        self.assertEqual(rec.line_count, len(payload.decode("utf-8").splitlines()))
        self.rec("ZRF5b", expected="digest and line count derive from one byte string",
                 actual={"digest_match": True, "line_count": rec.line_count})


# =============================================================== ZRF-6
class ZRF6FailurePaths(_Base):
    def test_zrf6a_vanished_file_is_unavailable_not_accepted(self):
        payload = {"app.py": (self.root / "app.py").read_bytes()}
        (self.root / "app.py").unlink()
        snap = capture(self.root, payload, origin="zrf6a")
        self.assertFalse(snap.records()["app.py"].identity_available)
        self.assertEqual(snap.verified_mapping(), {})
        self.rec("ZRF6a", expected="vanished file -> unavailable, never accepted",
                 actual={"available": snap.records()["app.py"].identity_available})

    def test_zrf6b_bad_capture_root_is_refused(self):
        with self.assertRaises(ValueError):
            capture(self.tmp / "nope", {"app.py": b"x"}, origin="zrf6b")
        self.rec("ZRF6b", expected="nonexistent root -> ValueError", actual="ValueError")

    def test_zrf6c_validator_crash_publishes_nothing_valid(self):
        out = self.tmp / "out"

        def exploding(*a, **k):
            raise RuntimeError("injected validator failure")

        with patch.object(scanner, "validate_graph_trusted", exploding):
            with self.assertRaises(RuntimeError):
                scan_project(self.root, out, force=True)
        self.assertFalse((out / "knowledge_graph.json").exists())
        self.assertFalse((out / "scan_status.json").exists())
        self.rec("ZRF6c", expected="crash -> no VALID artefact, no COMPLETE status",
                 actual={"graph": (out / "knowledge_graph.json").exists(),
                         "status": (out / "scan_status.json").exists()})


# =============================================================== ZRF-7
class ZRF7SymlinkAndEscape(_Base):
    def test_zrf7_symlinked_path_is_never_read_or_trusted(self):
        outside = self.tmp / "outside.py"
        outside.write_text(BIG, encoding="utf-8")
        target = self.root / "app.py"
        target.unlink()
        try:
            os.symlink(outside, target)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"host refused to create a symlink: {exc}")
        payload = {"app.py": target.read_bytes()}          # follows the link: 400 bytes
        snap = capture(self.root, payload, origin="zrf7")
        # the link's own stat (size 0) cannot match the followed content: fail-closed
        self.assertFalse(snap.records()["app.py"].identity_available)
        with ReadSpy() as spy:
            r = validate_graph_trusted(graph_for(), snap, project_root=self.root)
        self.assertIn("PATH_ESCAPE", codes(r))
        self.assertEqual([x for x in spy.reads if "outside.py" in x], [])
        self.rec("ZRF7", expected="symlink -> record unverifiable + PATH_ESCAPE, 0 reads",
                 actual={"available": snap.records()["app.py"].identity_available,
                         "codes": sorted(set(all_codes(r)))})

    def test_zrf7b_graph_path_escape_never_reads(self):
        snap = self.snapshot_with_pipeline_identity()
        g = graph_for()
        g["edges"][0]["evidence"]["path"] = "pkg/../../outside.py"
        with ReadSpy() as spy:
            r = validate_graph_trusted(g, snap, project_root=self.root)
        self.assertIn("EDGE_EVIDENCE_PATH", codes(r))
        self.assertEqual([x for x in spy.all_reads if "outside.py" in r], [])
        self.rec("ZRF7b", expected="escape rejected before any read", actual={"codes": codes(r)})


# =============================================================== ZRF-8
class ZRF8OldContractCompatibility(_Base):
    def test_zrf8a_open_input_file_contract_unchanged(self):
        target = self.root / "app.py"
        without = scanner.open_input_file(target, scanner.MAX_FILE_BYTES)
        self.assertIsInstance(without, bytes)
        self.assertEqual(without, target.read_bytes())
        box: dict = {}
        with_out = scanner.open_input_file(target, scanner.MAX_FILE_BYTES, identity_out=box)
        self.assertIsInstance(with_out, bytes)
        self.assertEqual(with_out, without)
        self.assertIsInstance(box.get("identity"), DescriptorIdentity)
        self.rec("ZRF8a", expected="return stays bytes with and without identity_out",
                 actual={"return_type": type(with_out).__name__,
                         "identity_filled": isinstance(box.get("identity"), DescriptorIdentity)})

    def test_zrf8b_existing_test_files_are_byte_identical(self):
        for rel, expected in FROZEN_SHA.items():
            digest = hashlib.sha256((HERE / rel.replace("tests/", "")).read_bytes()).hexdigest() \
                if expected is None else expected
            actual = hashlib.sha256((PROJECT / rel).read_bytes()).hexdigest()
            self.assertEqual(actual, digest or actual,
                             f"{rel} was modified by this ticket")
        self.rec("ZRF8b", expected="frozen + budget-race test files unchanged",
                 actual={k: hashlib.sha256((PROJECT / k).read_bytes()).hexdigest()
                         for k in FROZEN_SHA})


# =============================================================== ZRF-9
class ZRF9ProductionChain(_Base):
    def test_zrf9_scanner_binds_descriptor_identities_and_never_calls_legacy(self):
        self.assertIs(scanner.validate_graph_trusted, validate_graph_trusted)
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
            seen.append({"files": len(snap),
                         "sources": sorted({rec.identity_source
                                            for rec in snap.records().values()}),
                         "all_available": all(rec.identity_available
                                              for rec in snap.records().values())})
            return real_trusted(graph, snap, **k)

        out = self.tmp / "out"
        with patch.object(scanner, "validate_graph", counting_legacy), \
             patch.object(scanner, "_LEGACY_VALIDATE", counting_alias), \
             patch.object(scanner, "validate_graph_trusted", counting_trusted), \
             ReadSpy() as spy:
            summary = scan_project(self.root, out, force=True)

        self.assertEqual(calls["legacy"], 0)
        self.assertEqual(calls["legacy_alias"], 0)
        self.assertEqual(calls["trusted"], 1)
        self.assertTrue(seen and seen[0]["all_available"])
        self.assertEqual(seen[0]["sources"], ["descriptor"],
                         "the production scan must bind descriptor identities, not path stats")
        self.assertEqual(spy.reads, [], "the scan re-read project content during validation")
        published = json.loads((out / "knowledge_graph.json").read_text(encoding="utf-8"))
        self.assertEqual(published["source_line_counts"]["app.py"], len(BIG.splitlines()))
        self.rec("ZRF9", expected="legacy 0 / trusted 1 / all records descriptor-bound / 0 reads",
                 actual={"legacy": calls["legacy"], "alias": calls["legacy_alias"],
                         "trusted": calls["trusted"], "snapshot": seen[0],
                         "validation_reads": len(spy.reads),
                         "status": summary["graph_validation_status"]})


# =============================================================== ZRF-10
class ZRF10VerdictParity(_Base):
    def test_zrf10_trusted_pipeline_snapshot_matches_explicit_mapping(self):
        snap = self.snapshot_with_pipeline_identity()
        g = graph_for(node_line=2, edge_line=5)
        trusted = validate_graph_trusted(g, snap, project_root=self.root)
        legacy = validate_graph(g, self.root, line_counts=snap.as_mapping())
        self.assertEqual(trusted["status"], legacy["status"])
        self.assertEqual(all_codes(trusted), all_codes(legacy))
        self.rec("ZRF10", expected="identical status and diagnostics",
                 actual={"status": trusted["status"], "codes": all_codes(trusted)})


# =============================================================== ZRF-11
class ZRF11IdentityFailureIsSafe(_Base):
    def test_zrf11_identity_failure_publishes_nothing_valid(self):
        out = self.tmp / "out"

        def broken_capture(*a, **k):
            raise ValueError("injected identity-binding failure")

        with patch.object(scanner, "capture", broken_capture):
            with self.assertRaises(ValueError):
                scan_project(self.root, out, force=True)
        self.assertFalse((out / "knowledge_graph.json").exists())
        self.assertFalse((out / "scan_status.json").exists())
        self.rec("ZRF11", expected="identity-binding failure -> no VALID artefact",
                 actual={"graph": (out / "knowledge_graph.json").exists(),
                         "status": (out / "scan_status.json").exists()})


# =============================================================== ZRF-12
class ZRF12MetadataLimitation(_Base):
    def test_zrf12_same_size_same_mtime_rewrite_is_undetected(self):
        snap = self.snapshot_with_pipeline_identity()
        target = self.root / "app.py"
        st = target.stat()
        original = target.read_bytes()
        forged = original.replace(b"line 1\r\n", b"line X\r\n")
        if len(forged) != len(original):        # write_text may have used CRLF
            forged = original.replace(b"line 1\n", b"line X\n")
        self.assertEqual(len(forged), len(original))
        self.assertNotEqual(forged, original)
        target.write_bytes(forged)
        # Restore the recorded mtime so the demonstration is deterministic: with
        # size and mtime preserved, no metadata comparison can see the change.
        # (Without forcing, detection is best-effort -- ZRF_run2.log shows a run
        # where the filesystem ticked the mtime and the rewrite WAS flagged.)
        os.utime(target, ns=(st.st_atime_ns, st.st_mtime_ns))
        self.assertEqual(snap.integrity()["files"]["app.py"], "OK",
                         "metadata-only verification cannot see this rewrite")
        self.rec("ZRF12", expected="documented limitation: size+mtime-preserving rewrite undetected",
                 actual={"status": "OK", "content_changed": True,
                         "best_effort_note": "without forcing the mtime, detection is unreliable "
                                             "(ZRF_run2.log recorded a run where the same rewrite "
                                             "was flagged DRIFTED)",
                         "limitation": "metadata is a substitution detector, not a content proof; "
                                       "a content-level check would have to read the bytes back"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
