"""USIT-P0.7.10-ZRF12-IMPL: targeted adversarial tests for verify_current_content.

These tests exercise the real implementation of
``LineCountSnapshot.verify_current_content`` (added to ``usit/snapshot.py`` in
this ticket).  They run entirely against throw-away fixtures under a temporary
directory; no real business project, production file, or historical artifact is
ever touched.

The new method is the opt-in "scheme B" content check: it re-reads the current
file through a no-follow descriptor (reusing ``usit.safeio``) and compares the
fresh SHA-256 with ``FileRecord.digest`` captured at capture time.  It closes the
ZRF-12 blind spot that the metadata-only ``verified_mapping``/``integrity`` leave
open: a same-inode / same-size / same-mtime_ns in-place rewrite is invisible to
metadata checks but is caught here.

Nothing here modifies a frozen test, ``scanner.py``, ``graph.py`` or any
historical evidence. Privilege-dependent junction cases remain explicitly
skipped when the operating system refuses their creation. The mid-read race is
injected at the OS read boundary without changing the production API.
"""
from __future__ import annotations

import hashlib
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from usit import safeio
from usit.snapshot import (LineCountSnapshot, ContentVerifyResult, FileRecord,
                           DescriptorIdentity, capture, _CONSTRUCTION_TOKEN)
from usit.graph import validate_graph_trusted

A = b"def f():\n    return 1\n"            # 6 lines
B_SAME_LEN = b"def g():\n    return 2\n"   # 6 lines, same byte length as A
B_DIFF_LEN = b"def h():\n    return 3\n\n"  # 7 lines, different length
C = b"x = 1\ny = 2\nz = 3\n"               # unrelated content


def write_file(root: Path, rel: str, data: bytes) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


def build_descriptor_snapshot(root: Path, content: dict[str, bytes]) -> LineCountSnapshot:
    """Capture with real descriptor identities (production pipeline path)."""
    contents: dict[str, bytes] = {}
    identities: dict[str, DescriptorIdentity] = {}
    for rel, data in content.items():
        p = write_file(root, rel, data)
        fd = safeio.open_no_follow(p)
        try:
            st = os.fstat(fd)
            ident = DescriptorIdentity(int(st.st_dev), int(st.st_ino),
                                      int(st.st_size), int(st.st_mtime_ns))
        finally:
            os.close(fd)
        contents[rel] = data
        identities[rel] = ident
    return capture(str(root), contents, identities=identities)


def build_self_observed_snapshot(root: Path, content: dict[str, bytes]) -> LineCountSnapshot:
    """Capture without identities (compatible self_observed path)."""
    contents = {rel: write_file(root, rel, data) and data
                for rel, data in content.items()}
    return capture(str(root), contents)


def rewrite_in_place_same_length(path: Path, new_data: bytes,
                                 captured_mtime_ns: int) -> None:
    """Overwrite equal-length content in place and restore the captured mtime."""
    with open(path, "r+b") as fh:
        fh.write(new_data)
    st = os.stat(path, follow_symlinks=False)
    os.utime(path, ns=(st.st_atime_ns, captured_mtime_ns))


def rewrite_truncate_restore_mtime(path: Path, new_data: bytes) -> None:
    """Rewrite content (truncating, may change size), then restore mtime."""
    with open(path, "wb") as fh:
        fh.write(new_data)
    st = os.stat(path, follow_symlinks=False)
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))


def replace_file(path: Path, new_data: bytes) -> None:
    """Swap in a different inode via a temp file + atomic replace."""
    tmp = path.with_suffix(path.suffix + ".zrftmp")
    tmp.write_bytes(new_data)
    os.replace(tmp, path)


def manual_snapshot(root: Path, rel: str, digest: str,
                   identity_available: bool = True) -> LineCountSnapshot:
    """Build a snapshot from a hand-made record (for malformed-digest guards)."""
    rec = FileRecord(rel, 1, 1, None, 0, 0, digest, identity_available,
                     "self_observed")
    return LineCountSnapshot(_CONSTRUCTION_TOKEN, root=str(root),
                            root_identity=None, files={rel: rec},
                            origin="test", version=1)


class ReadCounter:
    """Count ``os.read`` calls (used to prove on-demand read behaviour)."""

    def __init__(self) -> None:
        self.reads = 0
        self._orig = os.read

    def __enter__(self) -> "ReadCounter":
        counter = self

        def wrapper(fd, n):
            counter.reads += 1
            return counter._orig(fd, n)

        self._patch = patch.object(os, "read", wrapper)
        self._patch.start()
        return self

    def __exit__(self, *exc) -> bool:
        self._patch.stop()
        return False


class VerifyCurrentContentTests(unittest.TestCase):

    # ---- ZB-1: unchanged file -> MATCH --------------------------------
    def test_zb1_unchanged_match_descriptor(self):
        root = Path(tempfile.mkdtemp())
        snap = build_descriptor_snapshot(root, {"app.py": A})
        r = snap.verify_current_content("app.py")
        self.assertEqual(r.status, "MATCH")
        self.assertEqual(r.current_digest, r.captured_digest)

    def test_zb1_unchanged_match_self_observed(self):
        root = Path(tempfile.mkdtemp())
        snap = build_self_observed_snapshot(root, {"app.py": A})
        r = snap.verify_current_content("app.py")
        self.assertEqual(r.status, "MATCH")

    # ---- ZB-2: same inode/size/mtime in-place rewrite -> CHANGED -------
    def test_zb2_inplace_rewrite_changed(self):
        root = Path(tempfile.mkdtemp())
        p = write_file(root, "app.py", A)
        snap = build_descriptor_snapshot(root, {"app.py": A})
        rewrite_in_place_same_length(p, B_SAME_LEN, snap.records()["app.py"].mtime_ns)
        r = snap.verify_current_content("app.py")
        self.assertEqual(r.status, "CHANGED")
        # The metadata-only classifier still reports OK -- that is the blind spot
        # this method is supposed to close.
        self.assertEqual(snap._classify("app.py", str(root))[0], "OK")

    def test_zb2_self_observed_path(self):
        root = Path(tempfile.mkdtemp())
        p = write_file(root, "app.py", A)
        snap = build_self_observed_snapshot(root, {"app.py": A})
        rewrite_in_place_same_length(p, B_SAME_LEN, snap.records()["app.py"].mtime_ns)
        r = snap.verify_current_content("app.py")
        self.assertEqual(r.status, "CHANGED")
        self.assertEqual(snap._classify("app.py", str(root))[0], "OK")

    # ---- ZB-3: content changed, mtime restored -> CHANGED -------------
    def test_zb3_content_changed_mtime_restored(self):
        root = Path(tempfile.mkdtemp())
        p = write_file(root, "app.py", A)
        snap = build_descriptor_snapshot(root, {"app.py": A})
        rewrite_truncate_restore_mtime(p, B_DIFF_LEN)  # size differs, mtime forced back
        r = snap.verify_current_content("app.py")
        self.assertEqual(r.status, "CHANGED")

    # ---- ZB-4: different inode replacement -> not MATCH ----------------
    def test_zb4_path_replacement_not_match(self):
        root = Path(tempfile.mkdtemp())
        p = write_file(root, "app.py", A)
        snap = build_descriptor_snapshot(root, {"app.py": A})
        replace_file(p, C)  # new inode, new content
        r = snap.verify_current_content("app.py")
        self.assertNotEqual(r.status, "MATCH")
        self.assertIn(r.status, ("CHANGED", "UNAVAILABLE"))

    # ---- ZB-5: deletion -> UNAVAILABLE --------------------------------
    def test_zb5_deleted_unavailable(self):
        root = Path(tempfile.mkdtemp())
        p = write_file(root, "app.py", A)
        snap = build_descriptor_snapshot(root, {"app.py": A})
        os.remove(p)
        r = snap.verify_current_content("app.py")
        self.assertEqual(r.status, "UNAVAILABLE")
        self.assertIsNone(r.current_digest)

    def test_zb5_permission_denied_not_tested(self):
        # Read denial is not reliably reproducible as Administrator on Windows
        # (the OS overrides DACL for the owner).  We attempt it; if the open
        # still succeeds, this sub-case is reported as NOT TESTED rather than
        # faked green.
        root = Path(tempfile.mkdtemp())
        p = write_file(root, "app.py", A)
        snap = build_descriptor_snapshot(root, {"app.py": A})
        tried = False
        try:
            os.chmod(p, 0o000)
            tried = True
            # Check whether the OS actually denies the owner's read.  As
            # Administrator on Windows the DACL is often overridden, so the
            # denial is not enforceable and the scenario is reported NOT TESTED.
            try:
                with open(p, "rb"):
                    pass
                self.skipTest("permission-denied not enforceable on this host (NOT TESTED)")
            except PermissionError:
                pass
            r = snap.verify_current_content("app.py")
            self.assertEqual(r.status, "UNAVAILABLE")
        except (PermissionError, OSError):
            self.skipTest("permission-denied scenario not reproducible on this host")
        finally:
            try:
                os.chmod(p, 0o644)
            except OSError:
                pass
        if not tried:  # pragma: no cover
            self.skipTest("permission-denied scenario not reproducible on this host")

    # ---- ZB-6: missing/invalid digest -> never MATCH -------------------
    def test_zb6_malformed_digest_unavailable(self):
        root = Path(tempfile.mkdtemp())
        snap = manual_snapshot(root, "app.py", digest="not-a-valid-hex")
        r = snap.verify_current_content("app.py")
        self.assertEqual(r.status, "UNAVAILABLE")
        self.assertEqual(r.reason, "captured digest missing or malformed")

    def test_zb6_empty_digest_unavailable(self):
        root = Path(tempfile.mkdtemp())
        snap = manual_snapshot(root, "app.py", digest="")
        r = snap.verify_current_content("app.py")
        self.assertEqual(r.status, "UNAVAILABLE")

    def test_zb6_path_not_in_capture_unavailable(self):
        root = Path(tempfile.mkdtemp())
        snap = build_descriptor_snapshot(root, {"app.py": A})
        r = snap.verify_current_content("other.py")
        self.assertEqual(r.status, "UNAVAILABLE")
        self.assertEqual(r.reason, "path not part of this capture")

    # ---- ZB-7: escape / symlink / reparse -> fail-closed --------------
    def test_zb7_path_escape_valueerror(self):
        root = Path(tempfile.mkdtemp())
        snap = build_descriptor_snapshot(root, {"app.py": A})
        with self.assertRaises(ValueError):
            snap.verify_current_content("../escape.py")
        with self.assertRaises(ValueError):
            snap.verify_current_content("/abs/escape.py")

    def test_zb7_symlink_reparse_unavailable(self):
        root = Path(tempfile.mkdtemp())
        outside = Path(tempfile.mkdtemp()) / "secret.txt"
        outside.write_bytes(b"top secret")
        link = root / "link.py"
        try:
            os.symlink(outside, link)
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation not permitted on this host (NOT TESTED)")
        # The rel exists in a hand-made record so the path check is reached.
        snap = manual_snapshot(root, "link.py", digest="0" * 64)
        r = snap.verify_current_content("link.py")
        # POSIX: O_NOFOLLOW -> ELOOP; Windows: reparse point opened -> rejected.
        self.assertEqual(r.status, "UNAVAILABLE")

    # ---- ZB-8: re-verify reflects current state -----------------------
    def test_zb8_reverify_reflects_state(self):
        root = Path(tempfile.mkdtemp())
        p = write_file(root, "app.py", A)
        snap = build_descriptor_snapshot(root, {"app.py": A})
        self.assertEqual(snap.verify_current_content("app.py").status, "MATCH")
        rewrite_truncate_restore_mtime(p, B_DIFF_LEN)
        self.assertEqual(snap.verify_current_content("app.py").status, "CHANGED")
        rewrite_truncate_restore_mtime(p, A)  # back to original content
        self.assertEqual(snap.verify_current_content("app.py").status, "MATCH")

    # ---- ZB-9: concurrent / replacement contract ----------------------
    def test_zb9_replacement_not_match(self):
        # Deterministic part of ZB-9: under a path replacement the method must
        # never claim MATCH. The separate mid-read race test injects a
        # same-size rewrite between the descriptor read and post-read fstat.
        root = Path(tempfile.mkdtemp())
        p = write_file(root, "app.py", A)
        snap = build_descriptor_snapshot(root, {"app.py": A})
        replace_file(p, C)
        r = snap.verify_current_content("app.py")
        self.assertNotEqual(r.status, "MATCH")

    def test_zb9_mid_read_race_never_yields_false_match(self):
        root = Path(tempfile.mkdtemp())
        p = write_file(root, "app.py", A)
        snap = build_descriptor_snapshot(root, {"app.py": A})
        before = os.stat(p, follow_symlinks=False)
        real_read = os.read
        real_open = safeio.open_no_follow
        target_fd = None
        triggered = False
        write_blocked = False

        def recording_open(path):
            nonlocal target_fd
            fd = real_open(path)
            target_fd = fd
            return fd

        def racing_read(fd, size):
            nonlocal triggered, write_blocked
            data = real_read(fd, size)
            if fd == target_fd and not triggered:
                triggered = True
                time.sleep(0.01)
                try:
                    rewrite_in_place_same_length(p, B_SAME_LEN, before.st_mtime_ns)
                except OSError:
                    # Windows opens the descriptor with FILE_SHARE_READ only;
                    # a concurrent writer must be denied while it is held.
                    write_blocked = True
            return data

        with patch("usit.snapshot.safeio.open_no_follow", side_effect=recording_open):
            with patch("usit.snapshot.os.read", side_effect=racing_read):
                result = snap.verify_current_content("app.py")

        self.assertTrue(triggered, "the rewrite attempt must occur during descriptor reading")
        if os.name == "nt":
            self.assertTrue(write_blocked, "Windows descriptor sharing must block concurrent writes")
            self.assertEqual(result.status, "MATCH")
            self.assertEqual(p.read_bytes(), A)
        else:
            self.assertFalse(write_blocked)
            self.assertEqual(result.status, "UNAVAILABLE")
            after = os.stat(p, follow_symlinks=False)
            self.assertEqual(after.st_mtime_ns, before.st_mtime_ns,
                             "the test must restore mtime to exercise ctime stability")
            self.assertNotEqual(after.st_ctime_ns, before.st_ctime_ns,
                                "the in-place rewrite must change POSIX ctime")
            self.assertEqual(p.read_bytes(), B_SAME_LEN)

    # ---- ZB-10: verify must not mutate snapshot/source ----------------
    def test_zb10_no_mutation(self):
        root = Path(tempfile.mkdtemp())
        p = write_file(root, "app.py", A)
        snap = build_descriptor_snapshot(root, {"app.py": A})
        original_digest = snap.records()["app.py"].digest
        original_state = snap.state
        original_len = len(snap)
        original_bytes = p.read_bytes()
        for _ in range(3):
            snap.verify_current_content("app.py")
        self.assertEqual(snap.records()["app.py"].digest, original_digest)
        self.assertEqual(snap.state, original_state)
        self.assertEqual(len(snap), original_len)
        self.assertEqual(p.read_bytes(), original_bytes)
        # The captured digest must NOT be overwritten with the current one.
        self.assertEqual(snap.records()["app.py"].digest, hashlib.sha256(A).hexdigest())

    # ---- ZA-1: documentation / status names ---------------------------
    def test_za1_status_names(self):
        method = LineCountSnapshot.verify_current_content
        doc = method.__doc__ or ""
        self.assertIn("MATCH", doc)
        self.assertIn("UNAVAILABLE", doc)
        # The docstring must make explicit that MATCH is not a permanent promise.
        self.assertIn("never", doc.lower())
        # The three statuses are the only values the implementation returns.
        snap = build_descriptor_snapshot(Path(tempfile.mkdtemp()), {"app.py": A})
        r = snap.verify_current_content("app.py")
        self.assertIn(r.status, ("MATCH", "CHANGED", "UNAVAILABLE"))

    # ---- ZP-1: graph validation + verified_mapping unchanged ----------
    def test_zp1_verified_mapping_and_graph_unchanged(self):
        # A == b"def f():\n    return 1\n" -> 2 lines (splitlines, trailing nl dropped).
        root = Path(tempfile.mkdtemp())
        snap = build_descriptor_snapshot(root, {"app.py": A})
        # verified_mapping still returns the still-verifiable subset.
        self.assertEqual(snap.verified_mapping(str(root)), {"app.py": 2})
        # A missing file is omitted (drift degrades to UNVERIFIED, no fallback).
        os.remove(root / "app.py")
        self.assertEqual(snap.verified_mapping(str(root)), {})
        # validate_graph_trusted still works on an intact snapshot (same root).
        root2 = Path(tempfile.mkdtemp())
        snap2 = build_descriptor_snapshot(root2, {"app.py": A})
        graph = {"schema_version": "usit-graph-v1",
                 "nodes": [{"id": "file:app.py", "type": "file", "label": "app.py",
                            "evidence": {"path": "app.py"}},
                           {"id": "symbol:app.py#f:1", "type": "function", "label": "f",
                            "evidence": {"path": "app.py", "line": 1}}],
                 "edges": [{"id": "e1", "source": "file:app.py",
                            "target": "symbol:app.py#f:1", "type": "defines",
                            "evidence": {"path": "app.py", "line": 2}}]}
        result = validate_graph_trusted(graph, snap2, project_root=str(root2))
        self.assertIn("status", result)

    # ---- ZP-2: read-count behaviour -----------------------------------
    def test_zp2_read_counts(self):
        # Static guarantee: production code never invokes the new method.
        scanner_src = (Path(__file__).resolve().parent.parent / "usit" / "scanner.py").read_text()
        graph_src = (Path(__file__).resolve().parent.parent / "usit" / "graph.py").read_text()
        self.assertNotIn("verify_current_content", scanner_src)
        self.assertNotIn("verify_current_content", graph_src)
        # Behavioural: metadata validation + graph validation do 0 os.read of files.
        root = Path(tempfile.mkdtemp())
        snap = build_descriptor_snapshot(root, {"app.py": A})
        graph = {"schema_version": "usit-graph-v1",
                 "nodes": [{"id": "file:app.py", "type": "file", "label": "app.py",
                            "evidence": {"path": "app.py"}}],
                 "edges": []}
        with ReadCounter() as rc:
            snap.verified_mapping(str(root))
            validate_graph_trusted(graph, snap, project_root=str(root))
        self.assertEqual(rc.reads, 0)
        # Only the explicit call produces an on-demand read.
        with ReadCounter() as rc2:
            r = snap.verify_current_content("app.py")
        self.assertGreaterEqual(rc2.reads, 1)
        self.assertEqual(r.status, "MATCH")

    def test_zp2_pipeline_still_runs(self):
        # End-to-end regression: the unchanged scanner still produces a capture.
        from tests.synthetic_scan import scan_project
        proj = Path(tempfile.mkdtemp()) / "proj"
        (proj / "pkg").mkdir(parents=True, exist_ok=True)
        (proj / "pkg" / "__init__.py").write_text("", encoding="utf-8")
        (proj / "pkg" / "lib.py").write_text("def h():\n    return 1\n", encoding="utf-8")
        (proj / "app.py").write_text(A.decode("utf-8"), encoding="utf-8")
        out = Path(tempfile.mkdtemp()) / "scan_out"  # must not pre-exist
        result = scan_project(str(proj), str(out))
        self.assertIsInstance(result, dict)

    # ---- ZP-3: both capture paths compatible --------------------------
    def test_zp3_both_capture_paths_detect_rewrite(self):
        for builder in (build_descriptor_snapshot, build_self_observed_snapshot):
            with self.subTest(builder=builder.__name__):
                root = Path(tempfile.mkdtemp())
                p = write_file(root, "app.py", A)
                snap = builder(root, {"app.py": A})
                # Both modes: unchanged -> MATCH, in-place rewrite -> CHANGED.
                self.assertEqual(snap.verify_current_content("app.py").status, "MATCH")
                rewrite_in_place_same_length(p, B_SAME_LEN, snap.records()["app.py"].mtime_ns)
                self.assertEqual(snap.verify_current_content("app.py").status, "CHANGED")
                # Deletion -> UNAVAILABLE on both modes (no bypass of fail-closed).
                os.remove(p)
                self.assertEqual(snap.verify_current_content("app.py").status, "UNAVAILABLE")


if __name__ == "__main__":
    unittest.main(verbosity=2)
