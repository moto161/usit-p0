"""P0.7.3 P1: independent authorization tests.

P0.7.2 marked several authorization branches as NOT TESTED; this module covers
them explicitly: oversized authorization files, authorization files stored
inside the project, missing authorization and malformed fields.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from usit.safety import (
    ScanAuthorization, ScanBlocked, authorization_record,
    canonical_project, load_authorization_file,
)
from usit.scanner import scan_project

FORBIDDEN = sorted({
    "execute_project_code", "build_or_test_project", "install_dependencies",
    "network_or_service_access", "database_access", "read_credentials",
    "modify_project_files",
})


def _now():
    return datetime.now(timezone.utc)


class _SyntheticProject:
    def __init__(self, case: unittest.TestCase):
        self._tmp = tempfile.TemporaryDirectory(prefix="usit-p073-authz-")
        case.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self.root = self.base / "project"
        self.out = self.base / "out"
        self.root.mkdir(parents=True, exist_ok=True)
        self.out.mkdir(parents=True, exist_ok=True)
        (self.root / "a.py").write_text("def run():\n    return 1\n", encoding="utf-8")

    def record(self, **overrides):
        data = authorization_record(self.root.resolve(), self.out.resolve(), now=_now())
        data.update(overrides)
        return data

    def write_auth(self, data, name: str = "auth.json", directory: Path | None = None) -> Path:
        target = (directory or self.base) / name
        target.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return target


class AuthorizationFileTests(unittest.TestCase):
    def test_authorization_file_larger_than_16kib_is_blocked(self):
        project = _SyntheticProject(self)
        data = project.record()
        data["padding"] = "x" * 20_000
        path = project.write_auth(data)
        self.assertGreater(path.stat().st_size, 16_384)
        with self.assertRaises(ScanBlocked) as blocked:
            load_authorization_file(path, project.root, project.out)
        self.assertEqual(blocked.exception.code, "AUTHORIZATION_FILE_TOO_LARGE")
        with self.assertRaises(ScanBlocked) as scan_blocked:
            scan_project(str(project.root), str(project.out), authorization=data)
        self.assertFalse((project.out / "knowledge_graph.json").exists())

    def test_authorization_file_inside_the_project_is_blocked(self):
        project = _SyntheticProject(self)
        data = project.record()
        path = project.write_auth(data, directory=project.root)
        self.assertTrue(str(path.resolve()).startswith(str(project.root.resolve())))
        with self.assertRaises(ScanBlocked) as blocked:
            load_authorization_file(path, project.root, project.out)
        self.assertEqual(blocked.exception.code, "AUTHORIZATION_FILE_IN_PROJECT")

    def test_relative_authorization_file_path_is_blocked(self):
        project = _SyntheticProject(self)
        with self.assertRaises(ScanBlocked) as blocked:
            load_authorization_file("relative/auth.json", project.root, project.out)
        self.assertEqual(blocked.exception.code, "AUTHORIZATION_FILE_MUST_BE_ABSOLUTE")

    def test_missing_authorization_file_is_blocked(self):
        project = _SyntheticProject(self)
        missing = project.base / "does-not-exist.json"
        with self.assertRaises(ScanBlocked) as blocked:
            load_authorization_file(missing, project.root, project.out)
        self.assertEqual(blocked.exception.code, "AUTHORIZATION_FILE_INVALID")

    def test_malformed_authorization_json_is_blocked(self):
        project = _SyntheticProject(self)
        path = project.base / "auth.json"
        path.write_text("{not valid json", encoding="utf-8")
        with self.assertRaises(ScanBlocked) as blocked:
            load_authorization_file(path, project.root, project.out)
        self.assertEqual(blocked.exception.code, "AUTHORIZATION_FILE_INVALID")


class AuthorizationFieldTests(unittest.TestCase):
    def test_missing_authorization_is_blocked(self):
        project = _SyntheticProject(self)
        with self.assertRaises(ScanBlocked) as blocked:
            scan_project(str(project.root), str(project.out))
        self.assertEqual(blocked.exception.code, "AUTHORIZATION_MISSING")

    def test_non_mapping_authorization_is_blocked(self):
        project = _SyntheticProject(self)
        with self.assertRaises(ScanBlocked) as blocked:
            scan_project(str(project.root), str(project.out), authorization="not-a-record")
        self.assertEqual(blocked.exception.code, "AUTHORIZATION_MISSING")

    def test_missing_required_field_is_blocked(self):
        project = _SyntheticProject(self)
        for field in ("project_root", "output_dir", "allowed_actions", "forbidden_actions",
                      "authorized_at", "expires_at"):
            with self.subTest(field=field):
                data = project.record()
                del data[field]
                with self.assertRaises(ScanBlocked) as blocked:
                    ScanAuthorization.from_mapping(data)
                self.assertEqual(blocked.exception.code, "AUTHORIZATION_INVALID")

    def test_wrong_action_types_are_blocked(self):
        project = _SyntheticProject(self)
        data = project.record(allowed_actions="static_read_only_scan")
        with self.assertRaises(ScanBlocked) as blocked:
            ScanAuthorization.from_mapping(data)
        self.assertEqual(blocked.exception.code, "AUTHORIZATION_INVALID")

    def test_bad_timestamp_fields_are_blocked(self):
        project = _SyntheticProject(self)
        for field in ("authorized_at", "expires_at"):
            with self.subTest(field=field):
                data = project.record()
                data[field] = "not-a-timestamp"
                with self.assertRaises(ScanBlocked) as blocked:
                    ScanAuthorization.from_mapping(data)
                self.assertEqual(blocked.exception.code, "AUTHORIZATION_INVALID")

    def test_timestamp_without_timezone_is_blocked(self):
        project = _SyntheticProject(self)
        naive = datetime.now().replace(tzinfo=None).isoformat()
        data = project.record(authorized_at=naive)
        with self.assertRaises(ScanBlocked) as blocked:
            ScanAuthorization.from_mapping(data)
        self.assertEqual(blocked.exception.code, "AUTHORIZATION_INVALID")

    def test_expired_and_not_yet_valid_authorization_is_blocked(self):
        project = _SyntheticProject(self)
        data = project.record()
        data["authorized_at"] = (_now() - timedelta(hours=3)).isoformat()
        data["expires_at"] = (_now() - timedelta(hours=2)).isoformat()
        with self.assertRaises(ScanBlocked) as blocked:
            scan_project(str(project.root), str(project.out), authorization=data)
        self.assertEqual(blocked.exception.code, "AUTHORIZATION_EXPIRED")

    def test_project_scope_mismatch_is_blocked(self):
        project = _SyntheticProject(self)
        other = project.base / "other-project"
        other.mkdir()
        data = project.record()
        data["project_root"] = str(other.resolve())
        with self.assertRaises(ScanBlocked) as blocked:
            scan_project(str(project.root), str(project.out), authorization=data)
        self.assertIn(blocked.exception.code, {"PROJECT_SCOPE_MISMATCH", "PROJECT_PATH_INVALID"})

    def test_output_scope_mismatch_is_blocked(self):
        project = _SyntheticProject(self)
        sibling = project.base / "sibling-out"
        sibling.mkdir()
        data = project.record()
        with self.assertRaises(ScanBlocked) as blocked:
            scan_project(str(project.root), str(sibling), authorization=data)
        self.assertEqual(blocked.exception.code, "OUTPUT_SCOPE_MISMATCH")

    def test_valid_authorization_publishes_a_complete_scan(self):
        project = _SyntheticProject(self)
        summary = scan_project(str(project.root), str(project.out), force=True,
                               authorization=project.record())
        self.assertEqual(summary["status"], "COMPLETE")
        self.assertIn("scan_status.json", sorted(p.name for p in project.out.rglob("*") if p.is_file()))


if __name__ == "__main__":
    unittest.main()
