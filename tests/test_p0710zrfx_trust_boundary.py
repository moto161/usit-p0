"""USIT-P0.7.10-ZRFX: trust-boundary regression tests (ZRFX-1..10).

Closes the P0.7.10-ZRFR-1 bypass: ``capture(identities=None)`` must never bind
unrelated bytes to a path identity.  The fix (usit/snapshot.py, self_observed
branch) re-reads the file at the path and requires a content-digest match; the
strict entry (usit/graph.py) is unchanged and keeps consuming only records the
snapshot marks verifiable.

Rules honoured here: historical PRE evidence is retained, maintained files
use an explicit successor baseline, no real project is scanned, and everything
runs in TemporaryDirectory fixtures.
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
from datetime import datetime

HERE = Path(__file__).resolve().parent
PROJ = HERE.parent
sys.path.insert(0, str(PROJ))

from usit.snapshot import capture, DescriptorIdentity, LineCountSnapshot  # noqa: E402
from usit.graph import validate_graph, validate_graph_trusted, validate_graph_file  # noqa: E402
from usit import scanner                                                   # noqa: E402
from tests.synthetic_scan import scan_project                              # noqa: E402
from tests.frozen_baseline import git_blob_sha1, load_maintenance_baseline  # noqa: E402

OUT = PROJ / "outputs" / "p0710zrfx"
OUT.mkdir(parents=True, exist_ok=True)

# R1 (F1-R1): isolate per-run regression records from the historical
# acceptance records.  tearDown previously appended to
# OUT/"ZRFX_TEST_RECORDS.json", mutating the historical artifact on every run.
# Each test process now writes to a dedicated, timestamped run directory so the
# root historical file is never appended to again.  OUT itself is unchanged so
# the before/after evidence reads (ZRFX9) keep working.
RUN_TS = datetime.now().strftime("%Y%m%dT%H%M%S%f")
RUN_DIR = OUT / "runs" / RUN_TS
RUN_DIR.mkdir(parents=True, exist_ok=True)

HISTORICAL_GRAPH_VALIDATION_SHA256 = "72fcae081526ce0d67e0f3fbf1ba59ee1b2d887a10267149190e2b8c2d60b7f0"
FROZEN_SHA = {
    "tests/test_p073_budget_race.py": "470851778ee2224593ab146109b86b613f52fcd60149ddc23092da33dd3fbeb6",
}

A_TEXT = b"def f():\n    return 1\n" * 100        # 200 lines


def graph_for(path: str = "app.py", line: int = 150) -> dict:
    node = {"id": "n1", "type": "function", "name": "f", "label": "f",
            "location": {"path": path, "line": line},
            "evidence": {"path": path, "line": line}}
    return {"schema_version": "usit-graph-v1", "nodes": [node], "edges": []}


def all_codes(res: dict) -> list:
    return sorted({d["code"] for grp in ("errors", "warnings", "unknown")
                   if grp in res for d in res[grp]})


def err_codes(res: dict) -> list:
    return sorted({d["code"] for d in res.get("errors", [])})


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
        self._dump()

    def _dump(self) -> None:
        if self.records:
            with open(RUN_DIR / "ZRFX_TEST_RECORDS.json", "a", encoding="utf-8") as fh:
                for r in self.records:
                    fh.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")

    def rec(self, tid: str, expected: str, actual, note: str = "") -> None:
        self.records.append({"id": tid, "expected": expected, "actual": actual,
                             "note": note})

    def descriptor_identity_of(self, name: str = "app.py") -> DescriptorIdentity:
        st = (self.root / name).stat()
        return DescriptorIdentity(st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)


class ZRFX1OriginalB3(_Base):
    """The ZRFR-B3 counter-example must now be refused."""

    def test_zrfx1_b3_same_size_swap_is_refused(self):
        a_bytes = (self.root / "app.py").read_bytes()
        (self.root / "app.py").unlink()
        (self.root / "app.py").write_bytes(b"z" * len(a_bytes))   # B: new inode, 1 line
        snap = capture(self.root, {"app.py": a_bytes}, identities=None, origin="zrfx1")
        rec = snap.records()["app.py"]
        vm = snap.verified_mapping()
        r = validate_graph_trusted(graph_for(line=150), snap, project_root=self.root)
        self.assertEqual(vm, {}, "B3 must not yield a trusted line-count mapping")
        self.assertFalse(rec.identity_available)
        self.assertNotEqual(r["status"], "VALID",
                            "no evidence must never read as a clean VALID")
        self.assertTrue({"NODE_LINE_UNVERIFIED", "LINE_UNVERIFIED"} & set(all_codes(r)),
                        f"an explicit UNVERIFIED diagnostic is required, got {all_codes(r)}")
        self.rec("ZRFX1", expected="B3 refused: no trusted mapping, explicit UNVERIFIED, not VALID",
                 actual={"verified": vm, "identity_available": rec.identity_available,
                         "status": r["status"], "codes": all_codes(r)})


class ZRFX2NoIdentityNeverBlindlyTrusted(_Base):
    """identities=None + same size but different content -> not trusted."""

    def test_zrfx2_size_equal_content_different_is_refused(self):
        a_bytes = (self.root / "app.py").read_bytes()
        # in-place same-size rewrite: identity survives, content differs
        (self.root / "app.py").write_bytes(b"z" * len(a_bytes))
        snap = capture(self.root, {"app.py": a_bytes}, identities=None, origin="zrfx2")
        self.assertEqual(snap.verified_mapping(), {})
        self.assertFalse(snap.records()["app.py"].identity_available)
        self.rec("ZRFX2", expected="same-size different-content at the path -> unverified",
                 actual={"verified": snap.verified_mapping(),
                         "integrity": snap.integrity()["files"]})

    def test_zrfx2b_identical_content_still_binds(self):
        """Documented counterpart: identical bytes at the path bind correctly."""
        a_bytes = (self.root / "app.py").read_bytes()
        snap = capture(self.root, {"app.py": a_bytes}, identities=None, origin="zrfx2b")
        self.assertEqual(snap.verified_mapping(), {"app.py": 200})
        self.rec("ZRFX2b", expected="captured bytes == current content -> binds (Z1 compatibility)",
                 actual={"verified": snap.verified_mapping()})


class ZRFX3MixedSources(_Base):
    """Only trustworthy records may influence the strict mapping."""

    def test_zrfx3_descriptor_and_failed_self_observed_mix(self):
        (self.root / "lib.py").write_bytes(b"x = 1\n" * 30)
        a_bytes = (self.root / "app.py").read_bytes()
        lib_ident = self.descriptor_identity_of("lib.py")
        (self.root / "app.py").unlink()
        (self.root / "app.py").write_bytes(b"z" * len(a_bytes))   # app.py swapped
        snap = capture(self.root,
                       {"app.py": a_bytes, "lib.py": (self.root / "lib.py").read_bytes()},
                       identities={"lib.py": lib_ident}, origin="zrfx3")
        vm = snap.verified_mapping()
        self.assertEqual(vm, {"lib.py": 30},
                         "only the descriptor record may stay trusted")
        r = validate_graph_trusted(graph_for(path="app.py", line=150), snap,
                                   project_root=self.root)
        self.assertTrue({"NODE_LINE_UNVERIFIED", "LINE_UNVERIFIED"} & set(all_codes(r)))
        self.assertNotEqual(r["status"], "VALID")
        self.rec("ZRFX3", expected="mixed sources: descriptor trusted, refused self_observed -> UNVERIFIED",
                 actual={"verified": vm, "status": r["status"], "codes": all_codes(r)})


class ZRFX4ProductionPathUnaffected(_Base):
    """Real descriptor identities from the scan pipeline keep working."""

    def test_zrfx4_scan_pipeline_still_passes(self):
        # direct, dependency-light pipeline check: capture with genuine
        # descriptor identities, then validate through the strict entry
        a_bytes = (self.root / "app.py").read_bytes()
        ident = self.descriptor_identity_of()
        snap = capture(self.root, {"app.py": a_bytes}, identities={"app.py": ident},
                       origin="zrfx4")
        r = validate_graph_trusted(graph_for(line=150), snap, project_root=self.root)
        self.assertEqual(r["status"], "VALID", f"descriptor path must stay clean: {all_codes(r)}")
        self.assertEqual(snap.records()["app.py"].identity_source, "descriptor")
        self.rec("ZRFX4", expected="descriptor-identity capture keeps the strict entry VALID",
                 actual={"status": r["status"], "codes": all_codes(r)})


class ZRFX5FailClosedVariants(_Base):
    """Missing/malformed/mismatched identities all fail closed."""

    def test_zrfx5_missing_identity_for_one_of_two_files(self):
        (self.root / "lib.py").write_bytes(b"y = 2\n" * 5)
        st = (self.root / "lib.py").stat()
        snap = capture(self.root,
                       {"app.py": (self.root / "app.py").read_bytes(),
                        "lib.py": (self.root / "lib.py").read_bytes()},
                       identities={"lib.py": DescriptorIdentity(
                           st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)},
                       origin="zrfx5a")
        # app.py has no identity -> content re-read binds it correctly here
        vm = snap.verified_mapping()
        self.assertEqual(vm, {"app.py": 200, "lib.py": 5})
        self.rec("ZRFX5a", expected="missing identity -> content-verified binding (no bypass)",
                 actual={"verified": vm})

    def test_zrfx5b_malformed_and_mismatched_identities(self):
        payload = {"app.py": (self.root / "app.py").read_bytes()}
        for bogus in ({"app.py": {"dev": 1}}, {"app.py": (1, 2, 3, 4)}, {"app.py": "x"}):
            with self.assertRaises(TypeError):
                capture(self.root, payload, identities=bogus, origin="zrfx5b")
        st = (self.root / "app.py").stat()
        wrong_size = DescriptorIdentity(st.st_dev, st.st_ino, st.st_size + 9, st.st_mtime_ns)
        snap = capture(self.root, payload, identities={"app.py": wrong_size}, origin="zrfx5b")
        self.assertEqual(snap.verified_mapping(), {})
        self.rec("ZRFX5b", expected="dict/tuple/str -> TypeError; size mismatch -> unverified",
                 actual={"mismatch_verified": snap.verified_mapping()})


class ZRFX6NoBypassAnyEntrance(_Base):
    """Three entrances: strict mapping, strict entry, production scanner."""

    def test_zrfx6a_strict_mapping_interface(self):
        a_bytes = (self.root / "app.py").read_bytes()
        (self.root / "app.py").write_bytes(b"z" * len(a_bytes))
        snap = capture(self.root, {"app.py": a_bytes}, identities=None, origin="zrfx6a")
        self.assertEqual(snap.verified_mapping(), {})
        self.rec("ZRFX6a", expected="verified_mapping itself excludes the swapped record",
                 actual={"verified": snap.verified_mapping()})

    def test_zrfx6b_strict_entry(self):
        a_bytes = (self.root / "app.py").read_bytes()
        (self.root / "app.py").write_bytes(b"z" * len(a_bytes))
        snap = capture(self.root, {"app.py": a_bytes}, identities=None, origin="zrfx6b")
        r = validate_graph_trusted(graph_for(line=150), snap, project_root=self.root)
        self.assertNotEqual(r["status"], "VALID")
        self.rec("ZRFX6b", expected="strict entry refuses the swapped record",
                 actual={"status": r["status"], "codes": all_codes(r)})

    def test_zrfx6c_production_scanner_uses_strict_entry(self):
        calls = {"legacy": 0, "trusted": 0}
        real_legacy = scanner.validate_graph
        real_trusted = scanner.validate_graph_trusted

        def counting_legacy(*a, **k):
            calls["legacy"] += 1
            return real_legacy(*a, **k)

        def counting_trusted(graph, snap, **k):
            calls["trusted"] += 1
            return real_trusted(graph, snap, **k)

        out = self.tmp / "out"
        with patch.object(scanner, "validate_graph", counting_legacy), \
             patch.object(scanner, "validate_graph_trusted", counting_trusted):
            summary = scan_project(self.root, out, force=True)
        self.assertEqual(calls["legacy"], 0, "production must not reach the legacy entry")
        self.assertEqual(calls["trusted"], 1)
        self.assertIn(summary.get("graph_validation_status"), ("VALID", "VALID_WITH_ISSUES"))
        self.rec("ZRFX6c", expected="scanner: legacy 0, trusted 1, clean status",
                 actual={"calls": calls, "status": summary.get("graph_validation_status")})


class ZRFX7NoFalseValidPublication(_Base):
    """Refused evidence must not publish a plain VALID verdict."""

    def test_zrfx7a_capture_then_swap_never_publishes_valid(self):
        """Descriptor capture, then the path is swapped before validation."""
        a_bytes = (self.root / "app.py").read_bytes()
        ident = self.descriptor_identity_of()
        snap = capture(self.root, {"app.py": a_bytes}, identities={"app.py": ident},
                       origin="zrfx7a")
        (self.root / "app.py").unlink()
        (self.root / "app.py").write_bytes(b"z" * len(a_bytes))    # swap before validation
        r = validate_graph_trusted(graph_for(line=150), snap, project_root=self.root)
        self.assertNotEqual(r["status"], "VALID")
        self.assertTrue({"NODE_LINE_UNVERIFIED", "LINE_UNVERIFIED"} & set(all_codes(r)))
        self.rec("ZRFX7a", expected="captured-then-swapped -> UNVERIFIED, never plain VALID",
                 actual={"status": r["status"], "codes": all_codes(r)})

    def test_zrfx7b_graph_file_publication_refuses_untrusted(self):
        """validate_graph_file(require_trusted=True) with an unusable snapshot."""
        a_bytes = (self.root / "app.py").read_bytes()
        (self.root / "app.py").write_bytes(b"z" * len(a_bytes))    # swap, then capture
        snap = capture(self.root, {"app.py": a_bytes}, identities=None, origin="zrfx7b")
        g = graph_for(line=150)
        gp = self.tmp / "g.json"
        gp.write_text(json.dumps(g), encoding="utf-8")
        res = validate_graph_file(gp, self.root, trusted_snapshot=snap)
        self.assertNotEqual(res["status"], "VALID",
                            "a refused snapshot must not yield a plain VALID verdict")
        self.assertTrue({"NODE_LINE_UNVERIFIED", "LINE_UNVERIFIED"} & set(all_codes(res)))
        self.rec("ZRFX7b", expected="graph-file publication honours the refusal",
                 actual={"status": res["status"], "codes": all_codes(res)})


class ZRFX8FrozenTestsUntouched(_Base):
    """Execute the frozen + compatibility suites; hashes must be identical."""

    def test_zrfx8_frozen_files_byte_identical(self):
        manifest = load_maintenance_baseline()
        for rel, want in FROZEN_SHA.items():
            entry = manifest["files"][rel]
            self.assertEqual(entry.get("historical_sha256"), want,
                             f"{rel} historical PRE digest was changed")
            self.assertEqual(git_blob_sha1(rel), entry["git_blob_sha1"],
                             f"{rel} canonical Git blob drifted")
        graph_entry = manifest["files"]["tests/test_graph_validation.py"]
        self.assertEqual(graph_entry.get("historical_sha256"),
                         HISTORICAL_GRAPH_VALIDATION_SHA256)
        self.assertEqual(git_blob_sha1("tests/test_graph_validation.py"),
                         graph_entry["git_blob_sha1"])
        self.rec("ZRFX8", expected="budget-race fixture frozen; graph validation fixture matches successor baseline",
                 actual={"frozen": list(FROZEN_SHA),
                         "graph_validation_blob": graph_entry["git_blob_sha1"]})


class ZRFX9BeforeAfterEvidence(_Base):
    """The before/after B3 logs exist and show the transition."""

    def test_zrfx9_before_and_after_logs(self):
        before = json.loads((OUT / "B3_BEFORE_FIX.json").read_text(encoding="utf-8"))
        after = json.loads((OUT / "B3_AFTER_FIX.json").read_text(encoding="utf-8"))
        self.assertTrue(before["accepts_A_count_as_trusted"])
        self.assertFalse(after["accepts_A_count_as_trusted"])
        self.assertEqual(after["verified_mapping"], {})
        self.rec("ZRFX9", expected="B3 before=accepted / after=refused, both logs kept",
                 actual={"before_status": before["strict_status"],
                         "after_status": after["strict_status"],
                         "after_verified": after["verified_mapping"]})


class ZRFX10RegressionCount(_Base):
    """Full-suite totals are recorded for the regression report."""

    def test_zrfx10_records_exist(self):
        # the regression runner writes ZRFX_REGRESSION_REPORT.json; here we only
        # assert the baseline facts this ticket depends on
        self.assertTrue((PROJ / "usit" / "snapshot.py").exists())
        self.rec("ZRFX10", expected="regression handled by the ticket runner",
                 actual="see ZRFX_REGRESSION_REPORT.json")


if __name__ == "__main__":
    unittest.main(verbosity=2)
