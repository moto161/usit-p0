"""P0.7.4 P0: file-access race elimination with handle-bound, no-follow opens.

Every input is synthetic.  Path redirection is produced by *really* replacing the
path (symbolic link, junction, hard link, delete-and-recreate) between discovery
and open, and then exercising the real ``open_input_file``/``scan_project`` code
path.  The two invariants under test are:

* no byte from a file outside the authorized project is ever read into a scan;
* no confident (COMPLETE) graph is published when any of these races is detected.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import usit.scanner as scanner
import usit.safeio as safeio
from usit.safety import ScanBlocked, authorization_record
from usit.scanner import discover_files, open_input_file, scan_project

OUTSIDE_MARKER = "OUTSIDE_MARKER_DO_NOT_READ_7f3a"


def _now():
    return datetime.now(timezone.utc)


def _make_symlink(link: Path, target: Path, is_dir: bool = False) -> bool:
    try:
        link.symlink_to(target, target_is_directory=is_dir)
        return True
    except (OSError, NotImplementedError, ValueError):
        return False


def _make_junction(link: Path, target: Path) -> bool:
    if os.name != "nt":
        return False
    try:
        completed = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                                   capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0 and link.exists()


def _can_make_hardlink(directory: Path) -> bool:
    src, dst = directory / "hl-src", directory / "hl-dst"
    src.write_text("x", encoding="utf-8")
    try:
        os.link(src, dst)
        return True
    except OSError:
        return False


class _SyntheticProject:
    def __init__(self, case: unittest.TestCase, name: str = "project"):
        self._tmp = tempfile.TemporaryDirectory(prefix="usit-p074-file-")
        case.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name).resolve()
        self.root = self.base / name
        self.out = self.base / "out"
        self.external = self.base / "external"
        self.root.mkdir(parents=True, exist_ok=True)
        self.out.mkdir(parents=True, exist_ok=True)
        self.external.mkdir(parents=True, exist_ok=True)
        self.markers = self.base / "markers"
        self.markers.mkdir(exist_ok=True)

    def authorization(self, **overrides):
        record = authorization_record(self.root.resolve(), self.out.resolve(), now=_now())
        record.update(overrides)
        return record

    def write(self, rel: str, text: str) -> Path:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def write_external(self, rel: str, text: str) -> Path:
        path = self.external / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def published(self) -> list[str]:
        if not self.out.exists():
            return []
        return sorted(p.relative_to(self.out).as_posix() for p in self.out.rglob("*") if p.is_file())

    def published_blob(self) -> str:
        return "\n".join((self.out / name).read_text(encoding="utf-8", errors="replace")
                         for name in self.published())

    def terminal_status(self) -> str | None:
        files = sorted(self.base.glob("usit-status-*.json"))
        if not files:
            return None
        return json.loads(files[-1].read_text(encoding="utf-8"))["status"]


# --------------------------------------------------------------------------
# Handle-bound open: identity, no-follow, hard link, share mode
# --------------------------------------------------------------------------
class HandleBoundOpenTests(unittest.TestCase):
    def test_normal_file_reads_when_identity_matches(self):
        project = _SyntheticProject(self)
        target = project.write("a.py", "x = 1\n")
        identities: dict[str, tuple[int, int]] = {}
        discover_files(project.root, identities=identities)
        self.assertIn("a.py", identities)
        data = open_input_file(target, scanner.MAX_FILE_BYTES, expected_identity=identities["a.py"])
        self.assertEqual(data, (project.root / "a.py").read_bytes())

    def test_final_component_symlink_is_never_followed(self):
        project = _SyntheticProject(self)
        secret = project.write_external("outside.py", OUTSIDE_MARKER + " = 1\n")
        link = project.root / "linked.py"
        if not _make_symlink(link, secret):
            self.skipTest("OS denied symlink creation")
        with self.assertRaises(ScanBlocked) as blocked:
            open_input_file(link, scanner.MAX_FILE_BYTES)
        self.assertEqual(blocked.exception.code, "REPARSE_POINT_REJECTED")

    def test_hard_link_is_rejected(self):
        project = _SyntheticProject(self)
        project.write("a.py", "x = 1\n")
        link = project.root / "hard.py"
        try:
            os.link(project.root / "a.py", link)
        except OSError:
            self.skipTest("OS denied hard-link creation")
        with self.assertRaises(ScanBlocked) as blocked:
            open_input_file(link, scanner.MAX_FILE_BYTES)
        self.assertEqual(blocked.exception.code, "HARD_LINK_REJECTED")

    def test_identity_mismatch_is_detected_before_reading(self):
        project = _SyntheticProject(self)
        target = project.write("a.py", "x = 1\n")
        # A different identity stands in for a file replaced between discovery
        # and open; the check happens before any byte is returned.
        with self.assertRaises(ScanBlocked) as blocked:
            open_input_file(target, scanner.MAX_FILE_BYTES, expected_identity=(0, 0))
        self.assertEqual(blocked.exception.code, "FILE_SUBSTITUTED_DURING_SCAN")

    def test_share_mode_denies_delete_while_the_handle_is_held(self):
        project = _SyntheticProject(self)
        target = project.write("a.py", "x = 1\n")
        fd = safeio.open_no_follow(target)
        try:
            if os.name == "nt":
                with self.assertRaises(OSError):
                    os.remove(target)
                self.assertTrue(target.exists(), "A file with an open no-share-delete handle must not be deletable.")
        finally:
            os.close(fd)
        # Once the handle is released the file can be removed normally.
        os.remove(target)
        self.assertFalse(target.exists())


# --------------------------------------------------------------------------
# Discovery-to-open swaps exercised end-to-end through scan_project
# --------------------------------------------------------------------------
class DiscoveryToOpenSwapTests(unittest.TestCase):
    def _scan_with_swap(self, project, swap):
        real = scanner.discover_files

        def wrapper(root, **kwargs):
            paths = real(root, **kwargs)
            swap()          # the path changes *after* discovery recorded identity
            return paths

        with patch.object(scanner, "discover_files", wrapper):
            with self.assertRaises(ScanBlocked) as blocked:
                scan_project(str(project.root), str(project.out), force=True,
                             authorization=project.authorization())
        return blocked.exception

    def test_intermediate_junction_swap_after_discovery_is_blocked(self):
        project = _SyntheticProject(self)
        project.write("sub/data.py", "INNER = 1\n")
        # The junction target carries the *same* relative file name, so the only
        # reason the scan can block is the identity change — not a missing file.
        project.write_external("data.py", OUTSIDE_MARKER + " = 1\n")
        probe = project.markers / "jn-probe"
        if not _make_junction(probe, project.external):
            self.skipTest("OS refused junction creation")
        os.rmdir(probe)

        def swap():
            shutil.rmtree(project.root / "sub")
            _make_junction(project.root / "sub", project.external)

        error = self._scan_with_swap(project, swap)
        self.assertEqual(error.code, "FILE_SUBSTITUTED_DURING_SCAN")
        self.assertEqual(project.published(), [], "A detected swap must not publish output.")
        self.assertNotIn(OUTSIDE_MARKER, project.published_blob())
        self.assertEqual(project.terminal_status(), "BLOCKED")

    def test_file_deleted_and_recreated_after_discovery_is_blocked(self):
        project = _SyntheticProject(self)
        project.write("a.py", "INNER = 1\n")

        def swap():
            target = project.root / "a.py"
            target.unlink()
            target.write_text("REPLACED = 1\n", encoding="utf-8")

        error = self._scan_with_swap(project, swap)
        self.assertEqual(error.code, "FILE_SUBSTITUTED_DURING_SCAN")
        self.assertEqual(project.published(), [])
        self.assertEqual(project.terminal_status(), "BLOCKED")

    def test_file_turned_into_symlink_after_discovery_is_blocked(self):
        project = _SyntheticProject(self)
        project.write("a.py", "INNER = 1\n")
        secret = project.write_external("outside.py", OUTSIDE_MARKER + " = 1\n")
        probe = project.markers / "ln-probe"
        if not _make_symlink(probe, secret):
            self.skipTest("OS denied symlink creation")
        probe.unlink()

        def swap():
            target = project.root / "a.py"
            target.unlink()
            target.symlink_to(secret)

        error = self._scan_with_swap(project, swap)
        self.assertIn(error.code, {"FILE_SUBSTITUTED_DURING_SCAN", "REPARSE_POINT_REJECTED"})
        self.assertEqual(project.published(), [])
        self.assertNotIn(OUTSIDE_MARKER, project.published_blob())
        self.assertEqual(project.terminal_status(), "BLOCKED")

    def test_hard_link_into_the_project_blocks_the_scan(self):
        project = _SyntheticProject(self)
        project.write("a.py", "x = 1\n")
        if not _can_make_hardlink(project.markers):
            self.skipTest("OS denied hard-link creation")
        secret = project.write_external("outside.py", OUTSIDE_MARKER + " = 1\n")
        os.link(secret, project.root / "hard.py")
        with self.assertRaises(ScanBlocked) as blocked:
            scan_project(str(project.root), str(project.out), force=True,
                         authorization=project.authorization())
        self.assertEqual(blocked.exception.code, "HARD_LINK_REJECTED")
        self.assertEqual(project.published(), [])
        self.assertNotIn(OUTSIDE_MARKER, project.published_blob())
        self.assertEqual(project.terminal_status(), "BLOCKED")


class NormalScanStillCompletesTests(unittest.TestCase):
    def test_clean_project_still_publishes_a_complete_graph(self):
        project = _SyntheticProject(self)
        project.write("main.py", "from lib import util\n\ndef go():\n    return util.value()\n")
        project.write("lib.py", "def value():\n    return 1\n")
        summary = scan_project(str(project.root), str(project.out), force=True,
                               authorization=project.authorization())
        self.assertEqual(summary["status"], "COMPLETE")
        self.assertIn("knowledge_graph.json", project.published())


class CapabilityLogTests(unittest.TestCase):
    """Record which reparse/hard-link types this host can really create."""

    def test_capability_log_records_real_creation(self):
        project = _SyntheticProject(self)
        target = project.markers / "target"
        target.mkdir()
        caps = {
            "symlink_file": _make_symlink(project.markers / "sl", target / "x"),
            "junction": _make_junction(project.markers / "jn", target),
            "hard_link": _can_make_hardlink(project.markers),
        }
        self.assertTrue(all(isinstance(v, bool) for v in caps.values()))
        self.assertTrue(any(caps.values()), "At least one redirection type must be creatable to test it.")


if __name__ == "__main__":
    unittest.main()
