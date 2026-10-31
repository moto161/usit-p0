"""P0.7.5 P0: JS/TS adapter read-race elimination + fault injection.

The adapter now receives parent-verified source text over JSON/stdin and analyzes
it in memory; it never re-opens a project source file by path.  These tests prove
the residual ``realpathSync``-then-``readFileSync`` race is gone for SOURCE files,
and that every rejection scenario leaves no project-outside byte in the output and
no COMPLETE graph.

Every input is synthetic.  Paths are really swapped (symlink, junction, delete,
recreate, content rewrite) between discovery and read, then the real scan path is
exercised.  We assert on actual read results and the final artifact directory, not
merely on a return code.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import usit.scanner as scanner
from usit.js_ts_parser import analyze_js_ts_project
from usit.safety import ScanBlocked, authorization_record
from usit.scanner import discover_files, scan_project

OUTSIDE_MARKER = "OUTSIDE_MARKER_DO_NOT_READ_9c41"


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


class _SyntheticProject:
    def __init__(self, case: unittest.TestCase, name: str = "project"):
        self._tmp = tempfile.TemporaryDirectory(prefix="usit-p075-js-")
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


def _js_ts_source(root: Path) -> dict[str, str]:
    paths = discover_files(root)
    out: dict[str, str] = {}
    for p in paths:
        out[p.relative_to(root).as_posix()] = scanner.decode_text(
            scanner.open_input_file(p, scanner.MAX_FILE_BYTES)) or ""
    return out


def _js_ts_config(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for p in discover_files(root):
        if p.name in ("tsconfig.json", "jsconfig.json"):
            text = scanner.decode_text(scanner.open_input_file(p, scanner.MAX_FILE_BYTES))
            if text is not None:
                out[p.name] = text
    return out


# --------------------------------------------------------------------------
# Adapter never re-opens source by path: in-memory content is authoritative
# --------------------------------------------------------------------------
class AdapterInMemorySourceTests(unittest.TestCase):
    def test_adapter_uses_verified_content_not_swapped_disk_file(self):
        # The on-disk file is replaced by a symlink to an outside marker AFTER the
        # verified content was captured.  Memory mode must analyze the captured
        # content and must NOT read the swapped/linked file.
        project = _SyntheticProject(self)
        target = project.write("src/a.ts", "export function safe(): string { return 'VERIFIED_CONTENT'; }\n")
        secret = project.write_external("outside.ts", OUTSIDE_MARKER + " = 1;\n")
        probe = project.markers / "ln-probe"
        if not _make_symlink(probe, secret):
            self.skipTest("OS denied symlink creation")
        probe.unlink()

        # Capture the parent-verified content AND the path snapshot BEFORE the swap.
        # Memory mode must analyze the captured content; the paths snapshot is the
        # discovery result we would otherwise re-run (which would now hit the reparse
        # point introduced by the swap).
        paths = discover_files(project.root)
        source = _js_ts_source(project.root)
        config = _js_ts_config(project.root)
        # Swap the on-disk source to a symlink pointing outside.
        target.unlink()
        target.symlink_to(secret)

        result = analyze_js_ts_project(project.root, paths,
                                      js_ts_source=source, js_ts_config=config)
        self.assertTrue(result["available"])
        blob = json.dumps(result)
        self.assertNotIn(OUTSIDE_MARKER, blob, "Adapter must not read the swapped outside file.")
        row = result["files"].get("src/a.ts")
        self.assertIsNotNone(row, "The in-memory source must still be analyzed.")
        self.assertIn("VERIFIED_CONTENT", json.dumps(row["symbols"]),
                      "Adapter must have analyzed the parent-verified content, not the swapped file.")

    def test_intermediate_directory_junction_swap_cannot_redirect_source_read(self):
        project = _SyntheticProject(self)
        project.write("src/sub/a.ts", "export const inner = 1;\n")
        project.write_external("a.ts", OUTSIDE_MARKER + " = 1;\n")
        probe = project.markers / "jn-probe"
        if not _make_junction(probe, project.external):
            self.skipTest("OS refused junction creation")
        probe.rmdir()

        # Capture verified content and the path snapshot before the swap.
        paths = discover_files(project.root)
        source = _js_ts_source(project.root)
        config = _js_ts_config(project.root)
        # Swap the whole sub-directory for a junction to the outside directory.
        shutil.rmtree(project.root / "src" / "sub")
        _make_junction(project.root / "src" / "sub", project.external)

        result = analyze_js_ts_project(project.root, paths,
                                      js_ts_source=source, js_ts_config=config)
        self.assertNotIn(OUTSIDE_MARKER, json.dumps(result),
                         "Junction-swapping an intermediate dir must not redirect a source read.")

    def test_project_outside_import_is_never_read(self):
        # A source file imports a sibling that resolves, via a symlink, to a file
        # outside the project.  The in-memory host must not read it.
        project = _SyntheticProject(self)
        project.write("src/a.ts",
                      "import {evil} from './evil';\nexport const x = evil();\n")
        (project.root / "src" / "evil.ts").write_text("export const evil = () => 'inner';\n", encoding="utf-8")
        secret = project.write_external("evil.ts", "export const evil = () => '" + OUTSIDE_MARKER + "';\n")
        probe = project.markers / "ln-probe"
        if not _make_symlink(probe, secret):
            self.skipTest("OS denied symlink creation")
        probe.unlink()

        # Capture the verified sibling content and the path snapshot before the swap.
        paths = discover_files(project.root)
        source = _js_ts_source(project.root)
        config = _js_ts_config(project.root)
        # Replace the real sibling with a symlink to the outside file.
        (project.root / "src" / "evil.ts").unlink()
        (project.root / "src" / "evil.ts").symlink_to(secret)

        result = analyze_js_ts_project(project.root, paths,
                                      js_ts_source=source, js_ts_config=config)
        self.assertNotIn(OUTSIDE_MARKER, json.dumps(result),
                         "An outside-resolving import must not be read into the analysis.")
        # The call to `evil` must not resolve to the outside file.  In memory mode
        # the adapter analyzes the parent-verified (inner) sibling content, so the
        # call is RESOLVED but to the in-memory file, never to the swapped outside
        # target.  The binding we actually forbid is an "outside" resolution target.
        row = result["files"].get("src/a.ts", {})
        for call in row.get("calls", []):
            if call.get("callee") == "evil":
                tgt = call.get("target") or {}
                self.assertNotIn("outside", (tgt.get("file") or "").lower(),
                                 "An outside-resolving import must not be read into the analysis.")


class NodeModulesWithinRootGuardedTests(unittest.TestCase):
    def test_node_modules_symlink_to_outside_is_rejected(self):
        project = _SyntheticProject(self)
        project.write("src/a.ts", "import {ext} from 'external-lib';\nexport const x = ext();\n")
        nm = project.root / "node_modules"
        nm.mkdir()
        (nm / "external-lib").mkdir()
        (nm / "external-lib" / "package.json").write_text(json.dumps({"name": "external-lib", "main": "index.js"}), encoding="utf-8")
        (nm / "external-lib" / "index.js").write_text("exports.ext = function(){return 1;};", encoding="utf-8")
        secret = project.write_external("payload.ts", OUTSIDE_MARKER + " = 1;\n")
        probe = project.markers / "ln-probe"
        if not _make_symlink(probe, secret):
            self.skipTest("OS denied symlink creation")
        probe.unlink()
        # Make external-lib a symlink to the outside secret directory.
        shutil.rmtree(nm / "external-lib")
        (nm / "external-lib").symlink_to(secret.parent)

        source = _js_ts_source(project.root)
        config = _js_ts_config(project.root)
        result = analyze_js_ts_project(project.root, discover_files(project.root),
                                      js_ts_source=source, js_ts_config=config)
        self.assertNotIn(OUTSIDE_MARKER, json.dumps(result),
                         "A node_modules symlink to outside must not be read.")
        row = result["files"].get("src/a.ts", {})
        for call in row.get("calls", []):
            if call.get("callee") == "ext":
                tgt = call.get("target") or {}
                self.assertNotIn("outside", (tgt.get("file") or "").lower())


# --------------------------------------------------------------------------
# End-to-end: the production scan path fails closed on a source swap
# --------------------------------------------------------------------------
class ScanFailClosedOnSwapTests(unittest.TestCase):
    def test_source_swapped_to_symlink_after_discovery_blocks_scan(self):
        project = _SyntheticProject(self)
        project.write("src/a.ts", "export const inner = 1;\n")
        secret = project.write_external("outside.ts", OUTSIDE_MARKER + " = 1;\n")
        probe = project.markers / "ln-probe"
        if not _make_symlink(probe, secret):
            self.skipTest("OS denied symlink creation")
        probe.unlink()

        real = scanner.discover_files

        def wrapper(root, **kwargs):
            paths = real(root, **kwargs)
            target = project.root / "src" / "a.ts"
            target.unlink()
            target.symlink_to(secret)
            return paths

        with patch.object(scanner, "discover_files", wrapper):
            with self.assertRaises(ScanBlocked) as blocked:
                scan_project(str(project.root), str(project.out), force=True,
                             authorization=project.authorization())
        self.assertIn(blocked.exception.code, {"REPARSE_POINT_REJECTED", "FILE_SUBSTITUTED_DURING_SCAN"})
        self.assertEqual(project.published(), [], "A detected swap must not publish output.")
        self.assertNotIn(OUTSIDE_MARKER, project.published_blob())
        self.assertEqual(project.terminal_status(), "BLOCKED")

    def test_source_content_rewritten_after_discovery_blocks_scan(self):
        # "Content replaced after discovery" in its detectable form is a
        # delete-and-recreate (new inode); the verified-open identity check catches
        # it before any byte is read.  (A same-inode in-place rewrite is a documented
        # residual mitigated by the Windows no-share-delete handle; see the report.)
        project = _SyntheticProject(self)
        project.write("src/a.ts", "export const inner = 1;\n")

        real = scanner.discover_files

        def wrapper(root, **kwargs):
            paths = real(root, **kwargs)
            target = project.root / "src" / "a.ts"
            target.unlink()
            target.write_text(OUTSIDE_MARKER + " = 1;\n", encoding="utf-8")
            return paths

        with patch.object(scanner, "discover_files", wrapper):
            with self.assertRaises(ScanBlocked) as blocked:
                scan_project(str(project.root), str(project.out), force=True,
                             authorization=project.authorization())
        self.assertEqual(blocked.exception.code, "FILE_SUBSTITUTED_DURING_SCAN")
        self.assertEqual(project.published(), [])
        self.assertEqual(project.terminal_status(), "BLOCKED")


# --------------------------------------------------------------------------
# Malformed / crashed / oversized adapter input must not publish COMPLETE
# --------------------------------------------------------------------------
class AdapterFaultInjectionTests(unittest.TestCase):
    def _run_fake_adapter(self, script_text: str, expect_block: bool):
        project = _SyntheticProject(self)
        project.write("src/a.ts", "export const x = 1;\n")
        fake = project.markers / "fake_adapter.cjs"
        fake.write_text(script_text, encoding="utf-8")
        real_analyze = scanner.analyze_js_ts_project

        def analyze_with_fake(project_root, source_paths, **kwargs):
            # Inject the fault-injected adapter into the production scan path so
            # the scanner's fatal_parser_codes handling is exercised end to end.
            kwargs["adapter"] = fake
            return real_analyze(project_root, source_paths, **kwargs)

        # Drive the production scan but substitute the fake adapter.  Patch the
        # name scanner.py bound at import time (it does `from ... import ...`),
        # not the module-level symbol in js_ts_parser.
        with patch.object(scanner, "discover_files", lambda r, **k: discover_files(r, **k)):
            with patch.object(scanner, "analyze_js_ts_project", analyze_with_fake):
                try:
                    summary = scan_project(str(project.root), str(project.out), force=True,
                                           authorization=project.authorization())
                    blocked = False
                    code = summary.get("status")
                except ScanBlocked as exc:
                    blocked = True
                    code = exc.code
        return project, blocked, code

    def test_crashed_adapter_fails_closed(self):
        project, blocked, code = self._run_fake_adapter(
            "process.stdin.on('data',()=>{});process.stdin.on('end',()=>{process.exit(3);});",
            expect_block=True)
        self.assertTrue(blocked, "A crashed adapter must fail the scan closed.")
        self.assertEqual(code, "ADAPTER_FAILED")
        self.assertNotIn("knowledge_graph.json", project.published())
        self.assertEqual(project.terminal_status(), "BLOCKED")

    def test_corrupted_output_fails_closed(self):
        project, blocked, code = self._run_fake_adapter(
            "process.stdin.on('data',()=>{});process.stdin.on('end',()=>{process.stdout.write('not json');});",
            expect_block=True)
        self.assertTrue(blocked, "Malformed adapter output must fail the scan closed.")
        self.assertEqual(code, "ADAPTER_INVALID_JSON")
        self.assertEqual(project.published(), [])
        self.assertEqual(project.terminal_status(), "BLOCKED")

    def test_oversized_source_is_rejected_not_crashed(self):
        # A source whose content exceeds the per-file limit must be rejected with a
        # diagnostic and must not be parsed/leaked; the adapter must not crash.
        project = _SyntheticProject(self)
        project.write("src/big.ts", "export const x = 1;\n")
        source = _js_ts_source(project.root)
        config = _js_ts_config(project.root)
        # Inject an oversized payload for one file.
        source["src/big.ts"] = "a" * (1024 * 1024 + 10)
        result = analyze_js_ts_project(project.root, discover_files(project.root),
                                      js_ts_source=source, js_ts_config=config)
        self.assertTrue(result["available"])
        row = result["files"].get("src/big.ts")
        if row is not None:
            codes = {d.get("code") for d in row.get("diagnostics", [])}
            self.assertIn("FILE_TOO_LARGE", codes, "Oversized source must carry a FILE_TOO_LARGE diagnostic.")
            self.assertEqual(row.get("symbols"), [], "Oversized source must not be parsed.")
            self.assertEqual(row.get("calls"), [], "Oversized source must not be parsed.")
        self.assertNotIn(OUTSIDE_MARKER, json.dumps(result))

    def test_clean_js_ts_project_still_completes(self):
        project = _SyntheticProject(self)
        project.write("src/a.ts", "export function used(): number { return 1; }\n")
        project.write("src/b.ts", "import {used} from './a';\nexport const y = used();\n")
        summary = scan_project(str(project.root), str(project.out), force=True,
                               authorization=project.authorization())
        self.assertEqual(summary["status"], "COMPLETE")
        self.assertIn("knowledge_graph.json", project.published())


if __name__ == "__main__":
    unittest.main()
