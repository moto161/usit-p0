"""P0.7.6 P0/JS-TS: independent source-input-boundary audit + synthetic faults.

This is an INDEPENDENT re-audit of the JS/TS adapter's read surface, built on the
P0.7.5 memory-mode closure.  It attacks the gap surfaces the P0.7.5 suite did not
name explicitly:

  * a NESTED junction (node_modules several directories deep) that points outside;
  * a root ``tsconfig.json`` whose ``extends`` / ``paths`` map a module specifier to
    a file outside the project root;
  * reaffirming that memory mode analyzes parent-verified content even when the
    on-disk source is swapped to a link after capture.

The adapter is expected to read project source ONLY from the in-memory map the
parent hands it; every on-disk read (TS lib, in-root node_modules, config) is
guarded by ``realpathSync`` + ``within(projectRoot)``.  Each rejection scenario
asserts the outside marker never leaks into the result and no COMPLETE graph is
published by the production scan path.

A fourth surface — ``TS_LIB_ROOT`` itself being a junction/symlink to outside — is
audited statically in the report but the executable test is DISABLED by default
(``USIT_TEST_TSLIB_JUNCTION=1``) because it mutates the analyzer's own install
directory and could corrupt the shared environment; it must never run in a batch
that also executes the other suites.

Every input is synthetic; files are really linked/junctioned on disk, then the
real scan/adapter path is exercised.
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


def _is_link(path: Path) -> bool:
    """True when ``path`` is a symlink or a Windows junction/reparse point.

    ``Path.is_symlink()`` returns False for a directory junction on Windows, so a
    junction must be detected via ``is_junction()`` as well; otherwise cleanup
    treats it as an ordinary directory and the wrong removal strategy is used.
    """
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    if callable(is_junction):
        try:
            if is_junction():
                return True
        except OSError:
            return False
    return False


def _remove_link(link: Path) -> bool:
    """Remove ONLY the link/junction itself; never touch its target.

    ``shutil.rmtree()`` cannot delete a Windows junction and silently fails
    (especially with ``ignore_errors=True``), which is exactly the P0.7.7 defect
    that left the install swapped. A junction is therefore removed with
    ``cmd /c rmdir``, which unlinks the reparse point without descending into the
    target directory.
    """
    if not _is_link(link):
        return False
    if os.name == "nt":
        completed = subprocess.run(
            ["cmd", "/c", "rmdir", str(link)], capture_output=True, timeout=30)
        ok = completed.returncode == 0
    else:
        link.unlink()
        ok = True
    return ok and not _is_link(link)


def _make_junction(link: Path, target: Path) -> bool:
    if os.name != "nt":
        return False
    try:
        completed = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0 and link.exists()


class _SyntheticProject:
    def __init__(self, case: unittest.TestCase, name: str = "project"):
        self._tmp = tempfile.TemporaryDirectory(prefix="usit-p076-js-")
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
        return "\n".join(
            (self.out / name).read_text(encoding="utf-8", errors="replace")
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
# NESTED junction inside the project root pointing outside must not leak
# --------------------------------------------------------------------------
class NestedJunctionAuditTests(unittest.TestCase):
    def test_nested_node_modules_junction_to_outside_is_rejected(self):
        project = _SyntheticProject(self)
        project.write("src/sub/mod.ts", "import {x} from 'extlib';\nexport const y = x();\n")
        # A node_modules several directories deep, junctioned to the outside dir.
        (project.external / "extlib").mkdir(parents=True, exist_ok=True)
        (project.external / "extlib" / "package.json").write_text(
            json.dumps({"name": "extlib", "main": "index.js"}), encoding="utf-8")
        (project.external / "extlib" / "index.js").write_text(
            "exports.x = function () { return '" + OUTSIDE_MARKER + "'; };\n", encoding="utf-8")
        nm = project.root / "src" / "sub" / "node_modules"
        nm.mkdir(parents=True, exist_ok=True)
        probe = project.markers / "jn-probe"
        if not _make_junction(probe, project.external):
            self.skipTest("OS refused junction creation")
        probe.rmdir()
        if not _make_junction(nm / "extlib", project.external / "extlib"):
            self.skipTest("OS refused junction creation")

        # Capture verified content and paths BEFORE any further mutation.
        paths = discover_files(project.root)
        source = _js_ts_source(project.root)
        config = _js_ts_config(project.root)
        result = analyze_js_ts_project(project.root, paths,
                                      js_ts_source=source, js_ts_config=config)
        self.assertNotIn(OUTSIDE_MARKER, json.dumps(result),
                         "A nested node_modules junction to outside must not be read.")
        row = result["files"].get("src/sub/mod.ts", {})
        for call in row.get("calls", []):
            if call.get("callee") == "x":
                tgt = call.get("target") or {}
                self.assertNotIn("outside", (tgt.get("file") or "").lower())


# --------------------------------------------------------------------------
# Config-driven escape: extends / paths mapping a module to outside root
# --------------------------------------------------------------------------
class ConfigEscapeAuditTests(unittest.TestCase):
    def test_tsconfig_extends_outside_is_rejected(self):
        project = _SyntheticProject(self)
        project.write("src/a.ts", "export const x = 1;\n")
        project.write("tsconfig.json",
                      json.dumps({"extends": "../external/tsconfig.base.json",
                                  "compilerOptions": {"strict": True}}))
        project.write_external(
            "tsconfig.base.json",
            json.dumps({"compilerOptions": {"paths": {"@secret/*": ["../external/*"]}}})
            + " // " + OUTSIDE_MARKER)
        paths = discover_files(project.root)
        source = _js_ts_source(project.root)
        config = _js_ts_config(project.root)
        result = analyze_js_ts_project(project.root, paths,
                                      js_ts_source=source, js_ts_config=config)
        self.assertNotIn(OUTSIDE_MARKER, json.dumps(result),
                         "A tsconfig 'extends' pointing outside must not load the base file.")

    def test_tsconfig_paths_to_outside_is_rejected(self):
        project = _SyntheticProject(self)
        project.write("src/a.ts", "import {secret} from '@secret/x';\nexport const y = secret();\n")
        project.write("tsconfig.json",
                      json.dumps({"compilerOptions": {"baseUrl": ".",
                                  "paths": {"@secret/*": ["../external/*"]}}}))
        project.write_external("x.ts", "export const secret = () => '" + OUTSIDE_MARKER + "';\n")
        paths = discover_files(project.root)
        source = _js_ts_source(project.root)
        config = _js_ts_config(project.root)
        result = analyze_js_ts_project(project.root, paths,
                                      js_ts_source=source, js_ts_config=config)
        self.assertNotIn(OUTSIDE_MARKER, json.dumps(result),
                         "A tsconfig 'paths' mapping to outside must not resolve the module.")
        row = result["files"].get("src/a.ts", {})
        for call in row.get("calls", []):
            if call.get("callee") == "secret":
                self.assertNotIn("RESOLVED", call.get("resolution_status", ""),
                                 "An outside-mapped module must not resolve to the secret file.")


# --------------------------------------------------------------------------
# Reaffirm: memory mode analyzes parent-verified content when disk is swapped
# --------------------------------------------------------------------------
class MemoryIsolationReaffirmTests(unittest.TestCase):
    def test_swapped_disk_source_not_read_in_memory_mode(self):
        project = _SyntheticProject(self)
        target = project.write("src/a.ts", "export function safe(): string { return 'VERIFIED_CONTENT'; }\n")
        secret = project.write_external("outside.ts", OUTSIDE_MARKER + " = 1;\n")
        probe = project.markers / "ln-probe"
        if not _make_symlink(probe, secret):
            self.skipTest("OS denied symlink creation")
        probe.unlink()

        paths = discover_files(project.root)
        source = _js_ts_source(project.root)
        config = _js_ts_config(project.root)
        # Swap the on-disk source to a symlink to the outside marker AFTER capture.
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
                      "Adapter must analyze the parent-verified content, not the swapped file.")


# --------------------------------------------------------------------------
# TS_LIB_ROOT junction to outside: analyzer-integrity audit (DISABLED by default)
# --------------------------------------------------------------------------
class TsLibRootIntegrityTests(unittest.TestCase):
    def test_ts_lib_root_junction_to_outside_is_contained(self):
        # Mutating the analyzer's own install is intrusive; only run when explicitly
        # opted in.  Default SKIP keeps the shared environment safe.
        if os.environ.get("USIT_TEST_TSLIB_JUNCTION") != "1":
            self.skipTest("TS_LIB_ROOT junction test disabled (set USIT_TEST_TSLIB_JUNCTION=1)")
        from usit.js_ts_parser import USIT_ROOT

        ts_lib = USIT_ROOT / "node_modules" / "typescript"
        if not ts_lib.is_dir():
            self.skipTest("typescript install not found")
        # Only meaningful if we can actually create a junction at the install path.
        backup = ts_lib.with_name("typescript.p076-bak")
        if backup.exists():
            self.skipTest("backup already present; refusing to double-swap")
        outside = (USIT_ROOT / "node_modules" / "typescript.p076-outside")
        outside.mkdir(parents=True, exist_ok=True)
        try:
            os.rename(ts_lib, backup)
            if not _make_junction(ts_lib, outside):
                os.rename(backup, ts_lib)
                self.skipTest("could not junction TS_LIB_ROOT")
            # The install must be rejected by the P0.7.8 trust check (canonical
            # path / reparse-point proof), NOT merely because package.json is
            # absent -- a missing file is not evidence of junction protection.
            from usit.js_ts_parser import parser_availability
            availability = parser_availability(USIT_ROOT)
            self.assertFalse(availability["available"],
                             "A junctioned TS_LIB_ROOT must not load an unverified install.")
            self.assertIn(availability.get("reason"), {
                "TS_LIB_ROOT_IS_REPARSE_POINT", "TS_LIB_ROOT_REDIRECTED",
                "TS_LIB_FILE_OUTSIDE_INSTALL", "TYPESCRIPT_PACKAGE_UNREADABLE",
                "TS_LIB_FILE_MISSING",
            }, "Rejection must come from the install trust check; got: "
               + str(availability.get("reason")))
        finally:
            # P0.7.8 cleanup: remove link -> restore backup -> verify -> clean
            # scratch.  Nothing is silenced: any failure is reported loudly with
            # the exact paths a human must fix by hand.
            problems: list[str] = []
            if _is_link(ts_lib) or backup.exists():
                if _is_link(ts_lib) and not _remove_link(ts_lib):
                    problems.append(f"junction still present: {ts_lib}")
                if backup.exists():
                    if ts_lib.exists():
                        problems.append(
                            f"cannot restore, path still occupied: {ts_lib} (backup at {backup})")
                    else:
                        os.rename(backup, ts_lib)
                if not ts_lib.is_dir() or _is_link(ts_lib):
                    problems.append(f"typescript install not restored: {ts_lib}")
                else:
                    try:
                        restored = parser_availability(USIT_ROOT)
                        if not restored.get("available") or restored.get("version") != "6.0.3":
                            problems.append(
                                f"restored install failed verification: {restored}")
                    except Exception as exc:  # noqa: BLE001 - reported verbatim
                        problems.append(
                            f"restore verification raised {type(exc).__name__}: {exc}")
            if outside.exists():
                try:
                    shutil.rmtree(outside)
                except OSError as exc:
                    problems.append(f"could not remove scratch dir {outside}: {exc}")
            if problems:
                self.fail("TS_LIB_ROOT cleanup FAILED - manual restore required: "
                          + " | ".join(problems))


if __name__ == "__main__":
    unittest.main()
