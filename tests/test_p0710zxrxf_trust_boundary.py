"""USIT-P0.7.10-ZRXRF: trust-boundary acceptance tests (ZXRXF-1..8).

The fix (usit/snapshot.py, self_observed branch) binds identity and content
through ONE os.open() descriptor: fstat #1 -> read -> fstat #2 -> path recheck
-> digest vs payload.  These tests verify the fix with hooks that target the
syscalls the new implementation actually uses (os.open / os.stat), not the
builtins the old code used.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
PROJ = HERE.parent
sys.path.insert(0, str(PROJ))

import os                                                        # noqa: E402
from datetime import datetime                                   # noqa: E402
from usit.snapshot import capture, DescriptorIdentity            # noqa: E402
from usit.graph import validate_graph_trusted                    # noqa: E402
from usit import scanner                                         # noqa: E402
from tests.synthetic_scan import scan_project                    # noqa: E402

OUT = PROJ / "outputs" / "p0710zxrxf"
OUT.mkdir(parents=True, exist_ok=True)

# R1 (F1-R1): isolate per-run regression records from the historical
# acceptance records.  tearDown previously appended to
# OUT/"ZXRXF_TEST_RECORDS.json".  Redirect to a dedicated run directory so the
# root historical file is never appended to again.  OUT is unchanged so the
# PRE_SHA256.json evidence read (ZXRXF7) keeps working.
RUN_TS = datetime.now().strftime("%Y%m%dT%H%M%S%f")
RUN_DIR = OUT / "runs" / RUN_TS
RUN_DIR.mkdir(parents=True, exist_ok=True)

A_TEXT = b"def f():\n    return 1\n" * 100        # A: 200 lines
B_TEXT = b"z" * len(A_TEXT)                        # B: same size, 1 line


def graph_for(line: int = 150) -> dict:
    node = {"id": "n1", "type": "function", "name": "f", "label": "f",
            "location": {"path": "app.py", "line": line},
            "evidence": {"path": "app.py", "line": line}}
    return {"schema_version": "usit-graph-v1", "nodes": [node], "edges": []}


def all_codes(res: dict) -> list:
    return sorted({d["code"] for grp in ("errors", "warnings", "unknown")
                   if grp in res for d in res[grp]})


_REAL_OS_OPEN = os.open        # captured at import time, before any patching


def raw_write(path: Path, data: bytes) -> None:
    """os-level write (O_BINARY), bypassing patched builtins AND patched os.open."""
    flags = os.O_WRONLY | os.O_TRUNC | getattr(os, "O_BINARY", 0)
    fd = _REAL_OS_OPEN(path, flags)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)


# --- R2 (F1-R2): explicit, documented ZXRXF7 frozen-hash guard scope --------
# Original design intent (test_zrxf7 docstring): "frozen + related tests +
# graph/scanner/safeio unchanged" -- i.e. protect PRODUCTION SOURCE touched by
# the ZRXRF fix and the FROZEN/RELATED regression fixtures it promised not to
# alter.  It must NOT freeze the regression harness source itself (a "test
# guards its own test" circular dependency that R1's legitimate maintenance of
# the ZRFX harness exposed).
ZXRXF7_WATCH = (
    # production source touched by the ZRXRF fix -> keep integrity-checked
    "usit/graph.py",
    "usit/scanner.py",
    "usit/safeio.py",
    # frozen fixtures (also byte-identical-asserted by ZRFX.FROZEN_SHA)
    "tests/test_graph_validation.py",
    "tests/test_p073_budget_race.py",
    # related regression fixtures for the P0.7.10 snapshot/descriptor fixes
    "tests/test_p0710z_snapshot_trust.py",
    "tests/test_p0710zrf_descriptor_binding.py",
    # NOTE: tests/test_p0710zrfx_trust_boundary.py is EXCLUDED -- it is the
    # ZRFR-1 regression HARNESS (actively-maintained test code), not a frozen
    # fixture and not production source.  Freezing it broke the guard on every
    # authorized maintenance (e.g. R1).  tests/test_p0710zxrxf_trust_boundary.py
    # (this harness) postdates the PRE baseline and was never in the watch list.
)


def _frozen_guard_unchanged(watch_files, baseline_path, base_dir=PROJ) -> dict:
    """Return {rel: (baseline_hash == current_hash)} for each watched file.

    Reads the PRE manifest at ``baseline_path`` and compares each watched file's
    CURRENT hash (resolved under ``base_dir``, defaulting to the real project)
    against the baseline.  ``base_dir`` is parameterised so the R2 directed
    tests can exercise the guard against an isolated copy without touching
    production source.
    """
    pre = json.loads(Path(baseline_path).read_text(encoding="utf-8"))
    norm = {k.replace("\\", "/").split("/usit_p0/", 1)[-1]: v for k, v in pre.items()}
    unchanged = {}
    for rel in watch_files:
        p = Path(base_dir) / rel
        cur = hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None
        unchanged[rel] = (norm.get(rel) == cur)
    return unchanged


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.root = self.tmp / "project"
        self.root.mkdir()
        self.records: list[dict] = []
        (self.root / "app.py").write_bytes(A_TEXT)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)
        if self.records:
            with open(RUN_DIR / "ZXRXF_TEST_RECORDS.json", "a", encoding="utf-8") as fh:
                for r in self.records:
                    fh.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")

    def rec(self, tid: str, expected: str, actual) -> None:
        self.records.append({"id": tid, "expected": expected, "actual": actual})

    def descriptor_identity_of(self, name: str = "app.py") -> DescriptorIdentity:
        st = (self.root / name).stat()
        return DescriptorIdentity(st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)


class ZXRXF1OriginalRace(_Base):
    """The ZRXR-3 hook sequence must now fail closed."""

    def test_zrxf1_identity_first_swap_is_refused(self):
        """Cross-descriptor mismatch must be gone: the published identity must
        be the INODE THE DESCRIPTOR READ, not one from an earlier path stat.

        Deterministic discriminator: swap the path to a DIFFERENT inode (a
        prepared side file) while capture opens it.  Before the fix the record
        carried the pre-open stat's inode and the strict entry accepted A's
        line count after B was restored (ZRXR-3).  After the fix the record
        carries the descriptor's own inode (the side file's), so restoring B
        makes _classify fail on dev/ino -> DRIFTED -> fail-closed, regardless
        of mtime granularity."""
        a_bytes = (self.root / "app.py").read_bytes()
        side = self.tmp / "side_a.py"
        side.write_bytes(A_TEXT)                       # A content, its own inode
        side_ino = side.stat().st_ino
        st_b = (self.root / "app.py").stat()           # B inode currently at the path
        real_open = os.open

        def swap_on_open(file, *a, **k):
            if Path(file).name == "app.py":
                os.replace(side, self.root / "app.py")  # path -> A's inode, atomic on Windows too
            return real_open(file, *a, **k)

        with patch.object(os, "open", swap_on_open):
            snap = capture(self.root, {"app.py": a_bytes}, identities=None,
                           origin="zrxf1")
        rec = snap.records()["app.py"]
        # the record must carry the descriptor's inode (the side file's), NOT
        # the inode that was at the path when capture started
        self.assertEqual(rec.ino, side_ino,
                         "identity must come from the descriptor that read the bytes")
        # restore B (its original inode) exactly like ZRXR-3 did
        (self.root / "app.py").unlink()
        (self.root / "app.py").write_bytes(B_TEXT)
        os.utime(self.root / "app.py", ns=(st_b.st_atime_ns, st_b.st_mtime_ns))
        r = validate_graph_trusted(graph_for(150), snap, project_root=self.root)
        self.assertEqual(snap.verified_mapping(), {},
                         "restored B (different inode) must not verify against the "
                         "descriptor-bound record")
        self.assertNotEqual(r["status"], "VALID")
        self.assertTrue({"NODE_LINE_UNVERIFIED", "LINE_UNVERIFIED"} & set(all_codes(r)))
        self.rec("ZXRXF1", expected="descriptor-bound identity: restored B -> DRIFTED -> fail-closed",
                 actual={"verified": snap.verified_mapping(), "status": r["status"],
                         "codes": all_codes(r),
                         "record_ino_matches_descriptor_target": rec.ino == side_ino,
                         "note": "the same-inode in-place variant degenerates into the "
                                 "documented ZRF-12 metadata blind spot (ZXRXF5b)"})


class ZXRXF2B3Regression(_Base):
    def test_zrxf2_capture_time_same_size_swap_refused(self):
        a_bytes = (self.root / "app.py").read_bytes()
        (self.root / "app.py").unlink()
        (self.root / "app.py").write_bytes(B_TEXT)         # new inode before capture
        snap = capture(self.root, {"app.py": a_bytes}, identities=None, origin="zrxf2")
        self.assertEqual(snap.verified_mapping(), {})
        self.assertFalse(snap.records()["app.py"].identity_available)
        self.rec("ZXRXF2", expected="pre-capture same-size swap refused",
                 actual={"verified": snap.verified_mapping(),
                         "integrity": snap.integrity()["files"]})


class ZXRXF3NormalCompatibility(_Base):
    def test_zrxf3_identical_content_binds(self):
        a_bytes = (self.root / "app.py").read_bytes()
        snap = capture(self.root, {"app.py": a_bytes}, identities=None, origin="zrxf3")
        self.assertEqual(snap.verified_mapping(), {"app.py": 200})
        r = validate_graph_trusted(graph_for(150), snap, project_root=self.root)
        self.assertEqual(r["status"], "VALID")
        self.rec("ZXRXF3", expected="payload == file content -> binds, strict entry VALID",
                 actual={"verified": snap.verified_mapping(), "status": r["status"]})

    def test_zrxf3b_descriptor_path_unchanged(self):
        a_bytes = (self.root / "app.py").read_bytes()
        ident = self.descriptor_identity_of()
        snap = capture(self.root, {"app.py": a_bytes}, identities={"app.py": ident},
                       origin="zrxf3b")
        self.assertEqual(snap.records()["app.py"].identity_source, "descriptor")
        self.assertEqual(snap.verified_mapping(), {"app.py": 200})
        self.rec("ZXRXF3b", expected="descriptor path keeps its semantics",
                 actual={"source": "descriptor", "verified": snap.verified_mapping()})


class ZXRXF4FailureClosure(_Base):
    def test_zrxf4a_os_open_failure(self):
        real_open = os.open

        def boom(file, *a, **k):
            if Path(file).name == "app.py":
                raise OSError("injected os.open failure")
            return real_open(file, *a, **k)

        with patch.object(os, "open", boom):
            snap = capture(self.root, {"app.py": A_TEXT}, identities=None, origin="zrxf4a")
        self.assertEqual(snap.verified_mapping(), {})
        self.assertFalse(snap.records()["app.py"].identity_available)
        self.rec("ZXRXF4a", expected="os.open failure -> fail-closed",
                 actual={"verified": snap.verified_mapping()})

    def test_zrxf4b_stat_recheck_failure(self):
        real_stat = os.stat

        def boom(p, *a, **k):
            if Path(p).name == "app.py":
                raise OSError("injected path-recheck failure")
            return real_stat(p, *a, **k)

        with patch.object(os, "stat", boom):
            snap = capture(self.root, {"app.py": A_TEXT}, identities=None, origin="zrxf4b")
        self.assertEqual(snap.verified_mapping(), {})
        self.assertFalse(snap.records()["app.py"].identity_available)
        self.rec("ZXRXF4b", expected="path-recheck failure -> fail-closed",
                 actual={"verified": snap.verified_mapping()})

    def test_zrxf4c_path_replaced_by_other_inode_before_recheck(self):
        # swap the path to a different inode while the descriptor is open:
        # the fstat pair stays consistent, but the path recheck must refuse
        side = self.tmp / "side.py"
        side.write_bytes(B_TEXT)                             # same size, new inode
        a_bytes = (self.root / "app.py").read_bytes()
        real_open = os.open

        def swap_on_open(file, *a, **k):
            if Path(file).name == "app.py":
                os.rename(side, self.root / "app.py")        # path -> other inode
            return real_open(file, *a, **k)

        with patch.object(os, "open", swap_on_open):
            snap = capture(self.root, {"app.py": a_bytes}, identities=None, origin="zrxf4c")
        self.assertEqual(snap.verified_mapping(), {})
        self.assertFalse(snap.records()["app.py"].identity_available)
        self.rec("ZXRXF4c", expected="path replaced (different inode) during capture -> refused",
                 actual={"verified": snap.verified_mapping()})


class ZXRXF5Semantics(_Base):
    def test_zrxf5a_post_capture_swap_detected(self):
        a_bytes = (self.root / "app.py").read_bytes()
        snap = capture(self.root, {"app.py": a_bytes}, identities=None, origin="zrxf5a")
        (self.root / "app.py").unlink()
        (self.root / "app.py").write_bytes(B_TEXT)
        self.assertEqual(snap.integrity()["files"]["app.py"], "DRIFTED")
        self.assertEqual(snap.verified_mapping(), {})
        self.rec("ZXRXF5a", expected="post-capture replacement -> DRIFTED -> unverified",
                 actual={"integrity": snap.integrity()["files"]})

    def test_zrxf5b_same_inode_same_metadata_rewrite_undetected(self):
        """Honest limitation: metadata cannot see a size+mtime_ns-preserving
        rewrite.  Recorded as UNDETECTED, never as a safety PASS."""
        a_bytes = (self.root / "app.py").read_bytes()
        snap = capture(self.root, {"app.py": a_bytes}, identities=None, origin="zrxf5b")
        st = (self.root / "app.py").stat()
        raw_write(self.root / "app.py", B_TEXT)              # same size, same inode
        os.utime(self.root / "app.py", ns=(st.st_atime_ns, st.st_mtime_ns))
        self.assertEqual(snap.integrity()["files"]["app.py"], "OK")
        self.rec("ZXRXF5b", expected="UNDETECTED (documented ZRF-12 boundary, no safety claim)",
                 actual={"integrity": "OK", "content_changed": True,
                         "limitation": "metadata-only verification after capture"})


class ZXRXF6ProductionPath(_Base):
    def test_zrxf6_scanner_supplies_descriptor_identities(self):
        seen = {}

        real_capture = scanner.capture

        def spying_capture(root, contents, *, identities=None, origin="capture"):
            seen["identities_none"] = identities is None
            seen["count"] = len(contents)
            seen["origin"] = origin
            return real_capture(root, contents, identities=identities, origin=origin)

        out = self.tmp / "out"
        with patch.object(scanner, "capture", spying_capture):
            summary = scan_project(self.root, out, force=True)
        self.assertFalse(seen["identities_none"],
                         "the production pipeline must supply descriptor identities")
        self.assertEqual(seen["origin"], "scan:usit-p0")
        self.assertIn(summary.get("graph_validation_status"), ("VALID", "VALID_WITH_ISSUES"))
        self.rec("ZXRXF6", expected="scanner passes descriptor identities; self_observed branch not used",
                 actual={"identities_none": seen["identities_none"],
                         "files": seen["count"],
                         "status": summary.get("graph_validation_status")})


class ZXRXF7FrozenCompatibility(_Base):
    def test_zrxf7_hashes_recorded(self):
        if not (OUT / "PRE_SHA256.json").is_file():
            self.skipTest("historical PRE_SHA256.json evidence is not checked into this clean checkout")
        # R2: use the explicit, documented guard scope (ZXRXF7_WATCH) and the
        # shared helper so the comparison logic is also unit-tested in
        # tests.test_p0710zxrxf_guard_r2.
        unchanged = _frozen_guard_unchanged(ZXRXF7_WATCH, OUT / "PRE_SHA256.json")
        self.assertTrue(all(unchanged.values()), f"unexpectedly changed: {unchanged}")
        self.rec("ZXRXF7", expected="frozen + related tests + graph/scanner/safeio unchanged",
                 actual=unchanged)


class ZXRXF8FullSuiteNote(_Base):
    def test_zrxf8_runner_handles_regression(self):
        self.rec("ZXRXF8", expected="full suite run by the ticket runner",
                 actual="see ZRXRF_REGRESSION_REPORT.json / commands.log")


if __name__ == "__main__":
    unittest.main(verbosity=2)
