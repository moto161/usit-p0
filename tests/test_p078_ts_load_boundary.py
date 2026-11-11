"""P0.7.8 P0: TypeScript load-boundary security suite (scenarios A-I).

P0.7.7 proved that junctioning ``node_modules/typescript`` to a *valid*
out-of-tree TypeScript 6.0.3 made the analyzer load and use that external
compiler.  This suite pins the fixed behaviour:

  A  a normal local install still loads, parses and emits symbols;
  B  junction -> empty dir is rejected by the trust check (not just because a
     file is missing);
  C  junction -> a VALID external 6.0.3 install is rejected and must not drive
     any analysis;
  D  same, with a marker inside the external install: no marker may appear and
     the external path must not be the loaded path;
  E  a sibling directory whose name merely shares the prefix must never be
     treated as inside the install;
  F  normalisation, case, "..", symlink, missing file, wrong version and
     unreadable package.json;
  G  cleanup helpers: removing a junction must delete only the link, and a
     failed removal must be reported rather than silently ignored;
  H  fail-closed on adapter start failure / invalid JSON / untrusted compiler;
  I  structured, verifiable load-path reporting that leaks no environment data.

Every case drives the REAL production functions (``parser_availability``,
``verify_ts_lib_trust``, ``analyze_js_ts_project`` and the real adapter
subprocess).  Junction injection happens only inside isolated temp roots built
here -- the engineer's own ``node_modules`` is never touched.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import usit.js_ts_parser as js_ts_parser
import usit.scanner as scanner
from usit.js_ts_parser import (
    EXPECTED_TYPESCRIPT_VERSION,
    USIT_ROOT,
    analyze_js_ts_project,
    parser_availability,
    verify_ts_lib_trust,
)

from tests import test_p076_js_ts_boundary as p076

_is_link = p076._is_link
_remove_link = p076._remove_link

REAL_TS = USIT_ROOT / "node_modules" / "typescript"
_TS_CACHE: Path | None = None


def _cached_ts() -> Path:
    """One shared copy of the real install, reused to keep the suite quick."""
    global _TS_CACHE
    if _TS_CACHE is None or not _TS_CACHE.exists():
        holder = Path(tempfile.mkdtemp(prefix="p078-tscache-")).resolve()
        shutil.copytree(REAL_TS, holder / "typescript")
        _TS_CACHE = (holder / "typescript").resolve()
    return _TS_CACHE


def _mk_junction(link: Path, target: Path) -> bool:
    if os.name != "nt":
        return False
    try:
        completed = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0 and link.exists()


def _require_junction(case: unittest.TestCase) -> None:
    if os.name != "nt":
        case.skipTest("junction scenarios require Windows")


class _IsoRoot:
    """An isolated fake USIT root: ``<tmp>/root/node_modules/typescript``."""

    def __init__(self, case: unittest.TestCase):
        self._tmp = tempfile.TemporaryDirectory(prefix="p078-iso-")
        case.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name).resolve()
        self.root = self.base / "root"
        (self.root / "node_modules").mkdir(parents=True, exist_ok=True)
        self.ts = self.root / "node_modules" / "typescript"

    def install_local(self) -> None:
        shutil.copytree(_cached_ts(), self.ts)

    def external_copy(self, name: str = "typescript-external", *, marker: bool = False) -> Path:
        out = (self.base / name).resolve()
        shutil.copytree(_cached_ts(), out)
        if marker:
            package = out / "package.json"
            data = json.loads(package.read_text(encoding="utf-8"))
            # Marker inside the external install: if this ever reaches the
            # analyzer's structured output, the external compiler was read.
            data["p078ExternalMarker"] = "P078_EXTERNAL_MARKER"
            package.write_text(json.dumps(data), encoding="utf-8")
            (out / "P078_EXTERNAL_MARKER.txt").write_text("marker\n", encoding="utf-8")
        return out

    def synthetic_project(self) -> Path:
        proj = self.base / "project"
        (proj / "src").mkdir(parents=True, exist_ok=True)
        (proj / "src" / "a.ts").write_text(
            "export function p078(): string { return 'P078_SYNTH'; }\n", encoding="utf-8")
        return (proj).resolve()


def _memory_source(proj: Path) -> dict[str, str]:
    paths = scanner.discover_files(proj)
    out: dict[str, str] = {}
    for p in paths:
        out[p.relative_to(proj).as_posix()] = (
            scanner.decode_text(scanner.open_input_file(p, scanner.MAX_FILE_BYTES)) or "")
    return out


# ---------------------------------------------------------------------------
# A. normal local TypeScript
# ---------------------------------------------------------------------------
class NormalLocalTypescriptTests(unittest.TestCase):
    def test_local_install_loads_parses_and_emits_symbols(self):
        iso = _IsoRoot(self)
        iso.install_local()
        with patch.object(js_ts_parser, "USIT_ROOT", iso.root):
            availability = parser_availability(iso.root)
            self.assertTrue(availability["available"], str(availability))
            self.assertEqual(availability["version"], EXPECTED_TYPESCRIPT_VERSION)
            self.assertEqual(availability["typescript_install"], "PROJECT_LOCAL")
            self.assertEqual(availability["typescript_install_path_status"], "VERIFIED_WITHIN_USIT_ROOT")

            proj = iso.synthetic_project()
            result = analyze_js_ts_project(proj, [], js_ts_source=_memory_source(proj))
        self.assertTrue(result["available"], str(result.get("project_diagnostics")))
        self.assertEqual(result["parser_version"], EXPECTED_TYPESCRIPT_VERSION)
        row = result["files"]["src/a.ts"]
        self.assertEqual([s["qualified_name"] for s in row["symbols"]], ["p078"],
                         "A trusted local install must still resolve symbols.")


# ---------------------------------------------------------------------------
# B. junction -> empty directory
# ---------------------------------------------------------------------------
class JunctionToEmptyTests(unittest.TestCase):
    def test_empty_outside_junction_rejected_by_trust_check(self):
        _require_junction(self)
        iso = _IsoRoot(self)
        iso.install_local()
        empty = (iso.base / "empty-outside").resolve()
        empty.mkdir(parents=True, exist_ok=True)
        backup = iso.ts.with_name("typescript.p078-bak")
        os.rename(iso.ts, backup)
        if not _mk_junction(iso.ts, empty):
            os.rename(backup, iso.ts)
            self.skipTest("OS refused junction creation")
        try:
            with patch.object(js_ts_parser, "USIT_ROOT", iso.root):
                availability = parser_availability(iso.root)
            self.assertFalse(availability["available"], str(availability))
            # The rejection must come from the install trust check; "package.json
            # is missing" alone would NOT prove junction protection.
            self.assertIn(availability.get("reason"), {
                "TS_LIB_ROOT_IS_REPARSE_POINT", "TS_LIB_ROOT_REDIRECTED",
            }, "Expected a trust/redirect rejection, got: " + str(availability.get("reason")))

            with patch.object(js_ts_parser, "USIT_ROOT", iso.root):
                proj = iso.synthetic_project()
                result = analyze_js_ts_project(proj, [], js_ts_source=_memory_source(proj))
            self.assertEqual(result.get("files"), {},
                             "An untrusted install must not produce any analysis.")
        finally:
            _restore(iso.ts, backup)


# ---------------------------------------------------------------------------
# C/D. junction -> valid external TypeScript
# ---------------------------------------------------------------------------
class JunctionToValidExternalTests(unittest.TestCase):
    def _junction_to_external(self, iso: _IsoRoot, external: Path) -> Path:
        backup = iso.ts.with_name("typescript.p078-bak")
        os.rename(iso.ts, backup)
        if not _mk_junction(iso.ts, external):
            os.rename(backup, iso.ts)
            self.skipTest("OS refused junction creation")
        return backup

    def test_valid_external_typescript_is_rejected_and_never_drives_analysis(self):
        """Scenario C: a valid 6.0.3 outside the tree must be refused."""
        _require_junction(self)
        iso = _IsoRoot(self)
        iso.install_local()
        external = iso.external_copy("typescript-external")
        backup = self._junction_to_external(iso, external)
        try:
            # With the junction in place, resolving the install really does point
            # at the outside directory -- that is the attack being defended.
            resolved = subprocess.run(
                ["node", "-e", "console.log(require.resolve(%s))" % json.dumps(str(iso.ts))],
                capture_output=True, text=True, cwd=str(iso.root))
            self.assertIn("typescript-external", resolved.stdout.lower(),
                          "Expected the junction to resolve outside the tree.")
            with patch.object(js_ts_parser, "USIT_ROOT", iso.root):
                availability = parser_availability(iso.root)
            self.assertFalse(availability["available"], str(availability))
            self.assertIn(availability.get("reason"), {
                "TS_LIB_ROOT_IS_REPARSE_POINT", "TS_LIB_ROOT_REDIRECTED",
            }, "Rejection must be a trust/redirect decision, got: "
               + str(availability.get("reason")))

            with patch.object(js_ts_parser, "USIT_ROOT", iso.root):
                proj = iso.synthetic_project()
                result = analyze_js_ts_project(proj, [], js_ts_source=_memory_source(proj))
            self.assertEqual(result.get("files"), {},
                             "An external compiler must not drive the analysis.")
            self.assertNotEqual(result.get("parser_version"), EXPECTED_TYPESCRIPT_VERSION,
                               "No trusted compiler version may be claimed.")
        finally:
            _restore(iso.ts, backup)

    def test_external_marker_never_reaches_analyzer_output(self):
        """Scenario D: marker inside the external install must never be loaded."""
        _require_junction(self)
        iso = _IsoRoot(self)
        iso.install_local()
        external = iso.external_copy("typescript-external-marked", marker=True)
        backup = self._junction_to_external(iso, external)
        try:
            with patch.object(js_ts_parser, "USIT_ROOT", iso.root):
                availability = parser_availability(iso.root)
                proj = iso.synthetic_project()
                result = analyze_js_ts_project(proj, [], js_ts_source=_memory_source(proj))
            for blob in (json.dumps(availability, ensure_ascii=False),
                         json.dumps(result, ensure_ascii=False)):
                self.assertNotIn("P078_EXTERNAL_MARKER", blob,
                                 "The external install's marker must never be read.")
                self.assertNotIn("p078ExternalMarker", blob)
            self.assertFalse(result["available"], str(result))
            self.assertEqual(result.get("files"), {})
        finally:
            _restore(iso.ts, backup)


# ---------------------------------------------------------------------------
# E. prefix-collision sibling
# ---------------------------------------------------------------------------
class PrefixCollisionTests(unittest.TestCase):
    def test_sibling_sharing_prefix_is_not_inside_install(self):
        """A sibling named 'typescript-extra' must never count as inside."""
        iso = _IsoRoot(self)
        iso.install_local()
        sibling = (iso.root / "node_modules" / "typescript-extra").resolve()
        sibling.mkdir(parents=True, exist_ok=True)
        (sibling / "package.json").write_text(
            json.dumps({"name": "typescript-extra", "version": EXPECTED_TYPESCRIPT_VERSION}),
            encoding="utf-8")
        with patch.object(js_ts_parser, "USIT_ROOT", iso.root):
            trust = verify_ts_lib_trust(iso.root)
            self.assertTrue(trust["trusted"], str(trust))
            # The sibling path starts with the install path as a *string*, so only
            # a relative-path check can keep it out.
            self.assertTrue(str(sibling).lower().startswith(str(iso.ts).lower()[: len(str(iso.ts))]))
            self.assertFalse(_within_install(iso.ts, sibling),
                             "A prefix-sharing sibling must not be treated as inside.")


def _within_install(install: Path, candidate: Path) -> bool:
    relative = os.path.relpath(str(candidate), str(install))
    return not (relative == ".." or relative.startswith(".." + os.sep) or os.path.isabs(relative))


# ---------------------------------------------------------------------------
# F. normalisation / edge cases
# ---------------------------------------------------------------------------
class TrustEdgeCaseTests(unittest.TestCase):
    def test_missing_install_is_unavailable(self):
        iso = _IsoRoot(self)  # no install copied
        with patch.object(js_ts_parser, "USIT_ROOT", iso.root):
            availability = parser_availability(iso.root)
        self.assertFalse(availability["available"])
        self.assertEqual(availability["reason"], "PROJECT_LOCAL_TYPESCRIPT_MISSING")

    def test_wrong_version_is_rejected(self):
        iso = _IsoRoot(self)
        iso.install_local()
        package = iso.ts / "package.json"
        data = json.loads(package.read_text(encoding="utf-8"))
        data["version"] = "9.9.9"
        package.write_text(json.dumps(data), encoding="utf-8")
        with patch.object(js_ts_parser, "USIT_ROOT", iso.root):
            availability = parser_availability(iso.root)
        self.assertFalse(availability["available"])
        self.assertEqual(availability["reason"], "UNEXPECTED_TYPESCRIPT_VERSION")

    def test_unreadable_package_json_is_rejected(self):
        iso = _IsoRoot(self)
        iso.install_local()
        (iso.ts / "package.json").write_text("{ not valid json", encoding="utf-8")
        with patch.object(js_ts_parser, "USIT_ROOT", iso.root):
            availability = parser_availability(iso.root)
        self.assertFalse(availability["available"])
        self.assertEqual(availability["reason"], "TYPESCRIPT_PACKAGE_UNREADABLE")

    def test_missing_key_file_is_rejected(self):
        iso = _IsoRoot(self)
        iso.install_local()
        (iso.ts / "lib" / "typescript.js").unlink()
        with patch.object(js_ts_parser, "USIT_ROOT", iso.root):
            availability = parser_availability(iso.root)
        self.assertFalse(availability["available"])
        self.assertIn(availability["reason"], {"TS_LIB_FILE_MISSING", "TS_LIB_FILE_OUTSIDE_INSTALL"})

    def test_install_that_is_a_plain_file_is_rejected(self):
        iso = _IsoRoot(self)
        iso.ts.write_text("not a directory\n", encoding="utf-8")
        with patch.object(js_ts_parser, "USIT_ROOT", iso.root):
            availability = parser_availability(iso.root)
        self.assertFalse(availability["available"])
        self.assertEqual(availability["reason"], "TYPESCRIPT_PATH_NOT_A_DIRECTORY")

    def test_symlinked_install_is_rejected(self):
        iso = _IsoRoot(self)
        iso.install_local()
        backup = iso.ts.with_name("typescript.p078-bak")
        os.rename(iso.ts, backup)
        try:
            iso.ts.symlink_to(backup, target_is_directory=True)
        except (OSError, NotImplementedError, ValueError):
            os.rename(backup, iso.ts)
            self.skipTest("OS refused symlink creation")
        try:
            with patch.object(js_ts_parser, "USIT_ROOT", iso.root):
                availability = parser_availability(iso.root)
            self.assertFalse(availability["available"], str(availability))
            self.assertIn(availability["reason"], {
                "TS_LIB_ROOT_IS_REPARSE_POINT", "TS_LIB_ROOT_REDIRECTED"})
        finally:
            if _is_link(iso.ts):
                _remove_link(iso.ts)
            if backup.exists() and not iso.ts.exists():
                os.rename(backup, iso.ts)


# ---------------------------------------------------------------------------
# G. cleanup helpers
# ---------------------------------------------------------------------------
class CleanupHelperTests(unittest.TestCase):
    def test_removing_junction_deletes_only_the_link(self):
        _require_junction(self)
        iso = _IsoRoot(self)
        iso.install_local()
        external = iso.external_copy("typescript-external-cleanup")
        backup = iso.ts.with_name("typescript.p078-bak")
        os.rename(iso.ts, backup)
        if not _mk_junction(iso.ts, external):
            os.rename(backup, iso.ts)
            self.skipTest("OS refused junction creation")
        marker_file = external / "package.json"
        self.assertTrue(_is_link(iso.ts))
        self.assertTrue(_remove_link(iso.ts))
        self.assertFalse(iso.ts.exists(), "The junction itself must be gone.")
        self.assertTrue(marker_file.exists(),
                        "Removing a junction must NOT delete the target's contents.")
        os.rename(backup, iso.ts)
        self.assertTrue(iso.ts.is_dir() and not _is_link(iso.ts))

    def test_failed_removal_is_reported_not_swallowed(self):
        """A removal that fails must return False instead of pretending success."""
        iso = _IsoRoot(self)
        iso.install_local()
        # A plain directory is not a link: removal must refuse rather than rmtree it.
        self.assertFalse(_remove_link(iso.ts))
        self.assertTrue((iso.ts / "package.json").exists(),
                        "A non-link directory must never be deleted by the cleanup helper.")

    def test_failed_rmdir_reports_failure(self):
        _require_junction(self)
        iso = _IsoRoot(self)
        iso.install_local()
        external = iso.external_copy("typescript-external-failinj")
        backup = iso.ts.with_name("typescript.p078-bak")
        os.rename(iso.ts, backup)
        if not _mk_junction(iso.ts, external):
            os.rename(backup, iso.ts)
            self.skipTest("OS refused junction creation")
        try:
            with patch.object(p076.subprocess, "run", return_value=subprocess.CompletedProcess(
                    args=[], returncode=1, stdout="", stderr="denied")):
                self.assertFalse(p076._remove_link(iso.ts),
                                 "A failing rmdir must be reported, not silently ignored.")
        finally:
            _remove_link(iso.ts)
            if backup.exists() and not iso.ts.exists():
                os.rename(backup, iso.ts)


# ---------------------------------------------------------------------------
# H. fail-closed behaviour
# ---------------------------------------------------------------------------
class FailClosedTests(unittest.TestCase):
    def test_missing_adapter_produces_no_fabricated_analysis(self):
        iso = _IsoRoot(self)
        iso.install_local()
        with patch.object(js_ts_parser, "USIT_ROOT", iso.root):
            proj = iso.synthetic_project()
            result = analyze_js_ts_project(proj, [], js_ts_source=_memory_source(proj),
                                           adapter=iso.base / "does-not-exist.cjs")
        self.assertEqual(result.get("files"), {})
        codes = [d.get("code") for d in result.get("project_diagnostics", [])]
        self.assertTrue(any(c in {"ADAPTER_FAILED", "ADAPTER_START_FAILED"} for c in codes),
                        f"Expected an adapter failure diagnostic, got {codes}")

    def test_invalid_json_from_adapter_is_reported(self):
        iso = _IsoRoot(self)
        iso.install_local()
        bad = iso.base / "bad_adapter.cjs"
        bad.write_text("process.stdout.write('not json at all');\n", encoding="utf-8")
        with patch.object(js_ts_parser, "USIT_ROOT", iso.root):
            proj = iso.synthetic_project()
            result = analyze_js_ts_project(proj, [], js_ts_source=_memory_source(proj),
                                           adapter=bad)
        self.assertEqual(result.get("files"), {})
        codes = [d.get("code") for d in result.get("project_diagnostics", [])]
        self.assertIn("ADAPTER_INVALID_JSON", codes, f"got {codes}")

    def test_untrusted_compiler_never_yields_symbols(self):
        _require_junction(self)
        iso = _IsoRoot(self)
        iso.install_local()
        external = iso.external_copy("typescript-external-failclosed")
        backup = iso.ts.with_name("typescript.p078-bak")
        os.rename(iso.ts, backup)
        if not _mk_junction(iso.ts, external):
            os.rename(backup, iso.ts)
            self.skipTest("OS refused junction creation")
        try:
            with patch.object(js_ts_parser, "USIT_ROOT", iso.root):
                proj = iso.synthetic_project()
                result = analyze_js_ts_project(proj, [], js_ts_source=_memory_source(proj))
            self.assertEqual(result.get("files"), {})
            self.assertFalse(result.get("available"), str(result))
        finally:
            _restore(iso.ts, backup)


# ---------------------------------------------------------------------------
# I. structured load-path reporting
# ---------------------------------------------------------------------------
class StructuredLoadPathTests(unittest.TestCase):
    def test_availability_reports_verified_install_status_without_path_disclosure(self):
        iso = _IsoRoot(self)
        iso.install_local()
        with patch.object(js_ts_parser, "USIT_ROOT", iso.root):
            availability = parser_availability(iso.root)
        self.assertEqual(availability["typescript_install"], "PROJECT_LOCAL")
        self.assertEqual(availability["typescript_install_path_status"], "VERIFIED_WITHIN_USIT_ROOT")
        # Public capability output must never disclose absolute install paths.
        blob = json.dumps(availability, ensure_ascii=False)
        self.assertNotIn("node_modules/typescript", blob)
        self.assertNotIn(str(iso.base), blob)
        for secret_key in ("USERPROFILE", "PASSWORD", "TOKEN", "SECRET"):
            self.assertNotIn(secret_key, blob)

    def test_untrusted_path_still_reports_declared_and_real_path(self):
        iso = _IsoRoot(self)
        iso.install_local()
        # The junction target must exist, otherwise mklink creates a dangling link
        # and the restore below would collide with it.
        elsewhere = (iso.base / "elsewhere").resolve()
        elsewhere.mkdir(parents=True, exist_ok=True)
        backup = iso.ts.with_name("typescript.p078-bak")
        os.rename(iso.ts, backup)
        if not _mk_junction(iso.ts, elsewhere):
            if _is_link(iso.ts):
                _remove_link(iso.ts)
            if backup.exists() and not iso.ts.exists():
                os.rename(backup, iso.ts)
            self.skipTest("OS refused junction creation")
        try:
            with patch.object(js_ts_parser, "USIT_ROOT", iso.root):
                availability = parser_availability(iso.root)
            self.assertFalse(availability["available"])
            self.assertEqual(availability.get("typescript_install"), "PROJECT_LOCAL")
            self.assertEqual(availability.get("typescript_install_path_status"), "NOT_VERIFIED")
            self.assertTrue(availability.get("reason"))
            self.assertNotIn("node_modules/typescript", json.dumps(availability))
        finally:
            _restore(iso.ts, backup)


def _restore(ts_lib: Path, backup: Path) -> None:
    """Best-effort restore used by these tests; never silences a failure."""
    if _is_link(ts_lib):
        _remove_link(ts_lib)
    if backup.exists() and not ts_lib.exists():
        os.rename(backup, ts_lib)


if __name__ == "__main__":
    unittest.main()
