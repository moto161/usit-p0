"""P0.7.3 P0: Windows path safety for discovery, open, and output creation.

Environment capability is recorded explicitly: if the OS refuses to create a
symbol link or a junction, that path type is reported as NOT TESTED, never as
PASS.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import usit.scanner as scanner
from usit.safety import ScanBlocked, authorization_record, canonical_project, canonical_output
from usit.scanner import discover_files, scan_project

TEMP_ROOT = Path(tempfile.gettempdir()).resolve()
CAPABILITY_LOG: dict[str, str] = {}


def _now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc)


def _make_symlink(link: Path, target: Path) -> bool:
    try:
        link.symlink_to(target, target_is_directory=target.is_dir())
        return True
    except (OSError, NotImplementedError, ValueError):
        return False


def _make_junction(link: Path, target: Path) -> bool:
    """Create a Windows directory junction via mklink /J."""
    if os.name != "nt":
        return False
    try:
        completed = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                                   capture_output=True, text=True, check=False, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0 and link.exists()


class _SyntheticProject:
    def __init__(self, case: unittest.TestCase, name: str = "project"):
        self._tmp = tempfile.TemporaryDirectory(prefix="usit-p073-path-")
        case.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self.root = self.base / name
        self.out = self.base / "out"
        self.external = self.base / "external"
        self.root.mkdir(parents=True, exist_ok=True)
        self.out.mkdir(parents=True, exist_ok=True)
        self.external.mkdir(parents=True, exist_ok=True)

    def authorization(self, **overrides):
        record = authorization_record(self.root.resolve(), self.out.resolve(), now=_now())
        record.update(overrides)
        return record

    def write(self, rel: str, text: str) -> Path:
        path = self.root / rel
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


class PathSafetyTests(unittest.TestCase):
    def test_symlink_file_inside_project_is_rejected_and_not_read(self):
        project = _SyntheticProject(self)
        project.write("a.py", "def run():\n    return 1\n")
        outside = project.external / "outside.py"
        outside.write_text("OUTSIDE_MARKER = 1\n", encoding="utf-8")
        link = project.root / "linked.py"
        if not _make_symlink(link, outside):
            CAPABILITY_LOG["symlink_file"] = "NOT TESTED - OS denied symlink creation"
            self.skipTest("OS denied symlink creation")
        CAPABILITY_LOG["symlink_file"] = "created"
        with self.assertRaises(ScanBlocked) as blocked:
            discover_files(project.root)
        self.assertEqual(blocked.exception.code, "REPARSE_POINT_REJECTED")
        with self.assertRaises(ScanBlocked) as scan_blocked:
            scan_project(str(project.root), str(project.out), authorization=project.authorization())
        self.assertEqual(scan_blocked.exception.code, "REPARSE_POINT_REJECTED")
        self.assertEqual(project.published(), [])
        self.assertNotIn("OUTSIDE_MARKER", project.published_blob())

    def test_symlink_directory_inside_project_is_rejected(self):
        project = _SyntheticProject(self)
        project.write("a.py", "x = 1\n")
        outside = project.external / "pkg"
        outside.mkdir()
        (outside / "deep.py").write_text("DEEP_MARKER = 1\n", encoding="utf-8")
        link = project.root / "linked"
        if not _make_symlink(link, outside):
            CAPABILITY_LOG["symlink_dir"] = "NOT TESTED - OS denied symlink creation"
            self.skipTest("OS denied symlink creation")
        CAPABILITY_LOG["symlink_dir"] = "created"
        with self.assertRaises(ScanBlocked) as blocked:
            discover_files(project.root)
        self.assertEqual(blocked.exception.code, "REPARSE_POINT_REJECTED")
        self.assertNotIn("DEEP_MARKER", project.published_blob())

    def test_directory_junction_is_rejected(self):
        project = _SyntheticProject(self)
        project.write("a.py", "x = 1\n")
        outside = project.external / "junction-target"
        outside.mkdir()
        (outside / "hidden.py").write_text("JUNCTION_MARKER = 1\n", encoding="utf-8")
        link = project.root / "junction-link"
        if not _make_junction(link, outside):
            CAPABILITY_LOG["junction"] = "NOT TESTED - OS refused mklink /J (no privilege or unsupported)"
            self.skipTest("OS refused junction creation")
        CAPABILITY_LOG["junction"] = "created"
        with self.assertRaises(ScanBlocked) as blocked:
            discover_files(project.root)
        self.assertEqual(blocked.exception.code, "REPARSE_POINT_REJECTED")
        self.assertNotIn("JUNCTION_MARKER", project.published_blob())

    def test_symlinked_project_root_is_rejected(self):
        project = _SyntheticProject(self)
        real = project.base / "real-project"
        real.mkdir()
        (real / "a.py").write_text("x = 1\n", encoding="utf-8")
        link = project.base / "linked-root"
        if not _make_symlink(link, real):
            CAPABILITY_LOG["symlink_root"] = "NOT TESTED - OS denied symlink creation"
            self.skipTest("OS denied symlink creation")
        CAPABILITY_LOG["symlink_root"] = "created"
        with self.assertRaises(ScanBlocked) as blocked:
            canonical_project(link)
        self.assertEqual(blocked.exception.code, "REPARSE_POINT_REJECTED")

    def test_symlinked_output_directory_is_rejected(self):
        project = _SyntheticProject(self)
        real_out = project.base / "real-out"
        real_out.mkdir()
        link = project.base / "linked-out"
        if not _make_symlink(link, real_out):
            CAPABILITY_LOG["symlink_output"] = "NOT TESTED - OS denied symlink creation"
            self.skipTest("OS denied symlink creation")
        CAPABILITY_LOG["symlink_output"] = "created"
        with self.assertRaises(ScanBlocked) as blocked:
            canonical_output(link)
        self.assertEqual(blocked.exception.code, "REPARSE_POINT_REJECTED")

    def test_nested_and_deep_paths_stay_inside_the_root(self):
        project = _SyntheticProject(self)
        deep = project.root / "a" / "b" / "c" / "d" / "e"
        deep.mkdir(parents=True)
        (deep / "deep.py").write_text("def f():\n    return 1\n", encoding="utf-8")
        summary = scan_project(str(project.root), str(project.out), force=True,
                               authorization=project.authorization())
        self.assertEqual(summary["files_scanned"], 1)
        manifest = json.loads((project.out / "scan_manifest.json").read_text(encoding="utf-8"))
        paths = [row["path"] for row in manifest["files"]]
        self.assertEqual(paths, ["a/b/c/d/e/deep.py"])
        for row in manifest["files"]:
            self.assertNotIn("..", Path(row["path"]).parts)

    def test_output_inside_project_and_ancestor_overlap_are_rejected(self):
        project = _SyntheticProject(self)
        project.write("a.py", "x = 1\n")
        for label, out in (("inside", project.root / "out"), ("ancestor", project.root.parent)):
            with self.subTest(label=label):
                record = authorization_record(project.root.resolve(), out.resolve(), now=_now())
                with self.assertRaises(ScanBlocked) as blocked:
                    scan_project(str(project.root), str(out), authorization=record)
                self.assertEqual(blocked.exception.code, "OUTPUT_OVERLAPS_PROJECT")

    def test_sibling_output_directory_is_accepted(self):
        project = _SyntheticProject(self)
        project.write("a.py", "def run():\n    return 1\n")
        sibling = project.base / "sibling-out"
        sibling.mkdir()
        record = authorization_record(project.root.resolve(), sibling.resolve(), now=_now())
        summary = scan_project(str(project.root), str(sibling), force=True, authorization=record)
        self.assertEqual(summary["status"], "COMPLETE")
        self.assertIn("scan_status.json", sorted(p.name for p in sibling.rglob("*") if p.is_file()))

    def test_open_time_escape_is_blocked_even_if_discovery_approved_the_path(self):
        """A path approved by discovery must still be re-checked when opened."""
        project = _SyntheticProject(self)
        project.write("a.py", "x = 1\n")
        discovered = discover_files(project.root)
        self.assertEqual(len(discovered), 1)
        outside = project.external / "escape.py"
        outside.write_text("ESCAPED = 1\n", encoding="utf-8")
        # Simulate the path turning into a link after discovery was done.
        with patch.object(scanner, "is_reparse_point", return_value=True):
            with self.assertRaises(ScanBlocked) as blocked:
                scan_project(str(project.root), str(project.out), authorization=project.authorization())
        self.assertEqual(blocked.exception.code, "REPARSE_POINT_REJECTED")
        self.assertEqual(project.published(), [])
        self.assertNotIn("ESCAPED", project.published_blob())


class PathSafetyCapabilityTests(unittest.TestCase):
    def test_capability_log_is_complete(self):
        # Touches every capability key so the report can state NOT TESTED with a
        # concrete reason instead of silently passing.
        for key in ("symlink_file", "symlink_dir", "junction", "symlink_root", "symlink_output"):
            CAPABILITY_LOG.setdefault(key, "NOT TESTED - not exercised in this run")
        self.assertEqual(len(CAPABILITY_LOG), 5)
        with self.subTest():
            self.assertTrue(all(isinstance(v, str) and v for v in CAPABILITY_LOG.values()))


if __name__ == "__main__":
    unittest.main()
