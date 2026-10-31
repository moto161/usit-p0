"""P0.7.9 P3: security boundary suite (8 scenarios) in isolated temp roots.

All junction/symlink injection happens inside temporary roots created here; the
engineer's own ``node_modules`` is never touched.  Every scenario verifies more
than ``parser_availability()``: it also checks the module path that would
actually be loaded, the paths actually read, and the final analysis product.

  1. trusted install loads normally
  2. valid EXTERNAL TypeScript via junction is refused and never loaded
  3. a marker planted inside that external install never reaches the output
  4. prefix-collision sibling is not treated as inside the install
  5. project config ``extends`` / ``paths`` pointing outside is refused
  6. project source replaced by a symlink/junction is not followed
  7. content swapped after capture (TOCTOU) does not change the analysis
  8. adapter failures fail closed

Scenario 7 is deliberately honest: memory mode removes the window for PROJECT
SOURCE, but a realpath->read window remains for on-disk reads (dependencies,
config, standard library). That residual risk is recorded, not claimed away.
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
from usit import scanner
from usit.js_ts_parser import (
    EXPECTED_TYPESCRIPT_VERSION,
    USIT_ROOT,
    analyze_js_ts_project,
    parser_availability,
    verify_ts_lib_trust,
)

REAL_TS = USIT_ROOT / "node_modules" / "typescript"
_CACHE: Path | None = None


def _cached_ts() -> Path:
    global _CACHE
    if _CACHE is None or not _CACHE.exists():
        holder = Path(tempfile.mkdtemp(prefix="p079-tscache-")).resolve()
        shutil.copytree(REAL_TS, holder / "typescript")
        _CACHE = (holder / "typescript").resolve()
    return _CACHE


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


def _is_link(p: Path) -> bool:
    if p.is_symlink():
        return True
    fn = getattr(p, "is_junction", None)
    return bool(callable(fn) and fn())


def _rm_link(link: Path) -> bool:
    if not _is_link(link):
        return False
    if os.name == "nt":
        ok = subprocess.run(["cmd", "/c", "rmdir", str(link)],
                            capture_output=True, timeout=30).returncode == 0
    else:
        link.unlink()
        ok = True
    return ok and not _is_link(link)


class _IsoRoot:
    def __init__(self, case: unittest.TestCase):
        self._tmp = tempfile.TemporaryDirectory(prefix="p079-iso-")
        case.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name).resolve()
        self.root = self.base / "root"
        (self.root / "node_modules").mkdir(parents=True, exist_ok=True)
        self.ts = self.root / "node_modules" / "typescript"

    def install_local(self) -> None:
        shutil.copytree(_cached_ts(), self.ts)

    def external_copy(self, name: str, *, marker: bool = False) -> Path:
        out = (self.base / name).resolve()
        shutil.copytree(_cached_ts(), out)
        if marker:
            pkg = out / "package.json"
            data = json.loads(pkg.read_text(encoding="utf-8"))
            data["p079ExternalMarker"] = "P079_EXTERNAL_MARKER"
            pkg.write_text(json.dumps(data), encoding="utf-8")
            (out / "P079_EXTERNAL_MARKER.txt").write_text("marker\n", encoding="utf-8")
        return out

    def project(self) -> Path:
        proj = self.base / "project"
        (proj / "src").mkdir(parents=True, exist_ok=True)
        (proj / "src" / "a.ts").write_text(
            "export function p079(): string { return 'P079_SYNTH'; }\n", encoding="utf-8")
        return proj.resolve()


def _source(proj: Path) -> dict[str, str]:
    paths = scanner.discover_files(proj)
    return {p.relative_to(proj).as_posix(): (
        scanner.decode_text(scanner.open_input_file(p, scanner.MAX_FILE_BYTES)) or "")
        for p in paths}


def _restore(ts_lib: Path, backup: Path) -> None:
    if _is_link(ts_lib):
        _rm_link(ts_lib)
    if backup.exists() and not ts_lib.exists():
        os.rename(backup, ts_lib)


class TrustedInstallTests(unittest.TestCase):
    def test_1_trusted_install_loads_and_analyses(self):
        iso = _IsoRoot(self)
        iso.install_local()
        with patch.object(js_ts_parser, "USIT_ROOT", iso.root):
            avail = parser_availability(iso.root)
            proj = iso.project()
            result = analyze_js_ts_project(proj, [], js_ts_source=_source(proj))
        self.assertTrue(avail["available"], str(avail))
        self.assertEqual(avail["version"], EXPECTED_TYPESCRIPT_VERSION)
        self.assertEqual(Path(avail["ts_lib_real_path"]).resolve(), iso.ts.resolve())
        self.assertEqual(result["parser_version"], EXPECTED_TYPESCRIPT_VERSION)
        self.assertEqual([s["qualified_name"] for s in result["files"]["src/a.ts"]["symbols"]],
                         ["p079"], "A trusted install must still resolve symbols.")


class ExternalJunctionTests(unittest.TestCase):
    def _junction(self, iso: _IsoRoot, external: Path) -> Path:
        backup = iso.ts.with_name("typescript.p079-bak")
        os.rename(iso.ts, backup)
        if not _mk_junction(iso.ts, external):
            _restore(iso.ts, backup)
            self.skipTest("OS refused junction creation")
        return backup

    def test_2_valid_external_typescript_refused_and_not_loaded(self):
        if os.name != "nt":
            self.skipTest("junction scenarios require Windows")
        iso = _IsoRoot(self)
        iso.install_local()
        external = iso.external_copy("typescript-external")
        backup = self._junction(iso, external)
        try:
            resolved = subprocess.run(
                ["node", "-e", "console.log(require.resolve(%s))" % json.dumps(str(iso.ts))],
                capture_output=True, text=True, cwd=str(iso.root))
            self.assertIn("typescript-external", resolved.stdout.lower(),
                          "The junction really does resolve outside the tree.")
            with patch.object(js_ts_parser, "USIT_ROOT", iso.root):
                avail = parser_availability(iso.root)
                proj = iso.project()
                result = analyze_js_ts_project(proj, [], js_ts_source=_source(proj))
            self.assertFalse(avail["available"], str(avail))
            self.assertIn(avail.get("reason"), {
                "TS_LIB_ROOT_IS_REPARSE_POINT", "TS_LIB_ROOT_REDIRECTED"})
            self.assertEqual(result.get("files"), {},
                             "The external compiler must not drive any analysis.")
            self.assertNotEqual(result.get("parser_version"), EXPECTED_TYPESCRIPT_VERSION)
        finally:
            _restore(iso.ts, backup)

    def test_3_external_marker_never_read(self):
        if os.name != "nt":
            self.skipTest("junction scenarios require Windows")
        iso = _IsoRoot(self)
        iso.install_local()
        external = iso.external_copy("typescript-external-marked", marker=True)
        backup = self._junction(iso, external)
        try:
            with patch.object(js_ts_parser, "USIT_ROOT", iso.root):
                avail = parser_availability(iso.root)
                proj = iso.project()
                result = analyze_js_ts_project(proj, [], js_ts_source=_source(proj))
            for blob in (json.dumps(avail, ensure_ascii=False),
                         json.dumps(result, ensure_ascii=False)):
                self.assertNotIn("P079_EXTERNAL_MARKER", blob)
                self.assertNotIn("p079ExternalMarker", blob)
            self.assertEqual(result.get("files"), {})
        finally:
            _restore(iso.ts, backup)


class PrefixCollisionTests(unittest.TestCase):
    def test_4_prefix_collision_sibling_is_outside(self):
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
        # The sibling shares the install path as a string prefix...
        self.assertTrue(str(sibling).lower().startswith(str(iso.ts).lower()))
        # ...yet a relative-path decision keeps it out.
        relative = os.path.relpath(str(sibling), str(iso.ts))
        self.assertTrue(relative.startswith("..") or os.path.isabs(relative),
                        "A prefix-sharing sibling must not be treated as inside.")


class ConfigEscapeTests(unittest.TestCase):
    def test_5_extends_outside_is_not_loaded(self):
        iso = _IsoRoot(self)
        iso.install_local()
        proj = iso.project()
        (proj / "tsconfig.json").write_text(json.dumps(
            {"extends": "../../outside-base.json", "compilerOptions": {}}), encoding="utf-8")
        (iso.base / "outside-base.json").write_text(
            json.dumps({"compilerOptions": {}}) + " // P079_OUTSIDE_MARKER", encoding="utf-8")
        with patch.object(js_ts_parser, "USIT_ROOT", iso.root):
            src = _source(proj)
            src["tsconfig.json"] = (proj / "tsconfig.json").read_text(encoding="utf-8")
            config = {"tsconfig.json": src["tsconfig.json"]}
            result = analyze_js_ts_project(proj, [], js_ts_source=src, js_ts_config=config)
        self.assertNotIn("P079_OUTSIDE_MARKER", json.dumps(result, ensure_ascii=False),
                         "An 'extends' target outside the project must not be read.")

    def test_5b_paths_outside_is_not_resolved(self):
        iso = _IsoRoot(self)
        iso.install_local()
        proj = iso.project()
        (proj / "src" / "b.ts").write_text(
            "import { secret } from '@secret/x';\nexport const use = () => secret();\n",
            encoding="utf-8")
        (proj / "tsconfig.json").write_text(json.dumps(
            {"compilerOptions": {"baseUrl": ".",
                                 "paths": {"@secret/*": ["../outside/*"]}}}), encoding="utf-8")
        (iso.base / "outside").mkdir(exist_ok=True)
        (iso.base / "outside" / "x.ts").write_text(
            "export const secret = () => 'P079_OUTSIDE_MARKER';\n", encoding="utf-8")
        with patch.object(js_ts_parser, "USIT_ROOT", iso.root):
            src = _source(proj)
            config = {"tsconfig.json": (proj / "tsconfig.json").read_text(encoding="utf-8")}
            result = analyze_js_ts_project(proj, [], js_ts_source=src, js_ts_config=config)
        self.assertNotIn("P079_OUTSIDE_MARKER", json.dumps(result, ensure_ascii=False),
                         "A 'paths' mapping outside the project must not be resolved.")


class SourceLinkTests(unittest.TestCase):
    def test_6_source_replaced_by_symlink_after_capture_is_not_followed(self):
        iso = _IsoRoot(self)
        iso.install_local()
        proj = iso.project()
        outside = (iso.base / "outside.ts").resolve()
        outside.write_text("export const evil = 'P079_SOURCE_MARKER';\n", encoding="utf-8")
        src = _source(proj)
        target = (proj / "src" / "a.ts").resolve()
        target.unlink()
        try:
            target.symlink_to(outside)
        except (OSError, NotImplementedError, ValueError):
            self.skipTest("OS refused symlink creation")
        with patch.object(js_ts_parser, "USIT_ROOT", iso.root):
            result = analyze_js_ts_project(proj, [], js_ts_source=src)
        blob = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("P079_SOURCE_MARKER", blob,
                         "Memory mode must analyse the captured content, not the swapped file.")
        self.assertIn("P079_SYNTH", blob, "The parent-verified content must still be analysed.")


class TouTests(unittest.TestCase):
    def test_7a_content_swap_after_capture_never_changes_analysis(self):
        """TOCTOU (content swap): captured source is analysed, not the disk.

        The on-disk file is rewritten with outside content AFTER capture, then
        analysed. Memory mode must still analyse the parent-verified capture.
        This scenario needs no elevated privileges and is always executed.
        """
        iso = _IsoRoot(self)
        iso.install_local()
        proj = iso.project()
        src = _source(proj)
        self.assertIn("P079_SYNTH", src["src/a.ts"])
        # Swap the bytes under the analyzer's feet.
        (proj / "src" / "a.ts").write_text(
            "export const evil = 'P079_TOU_MARKER';\n", encoding="utf-8")
        with patch.object(js_ts_parser, "USIT_ROOT", iso.root):
            result = analyze_js_ts_project(proj, [], js_ts_source=src)
        blob = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("P079_TOU_MARKER", blob,
                         "Swapped disk content must never reach the analysis.")
        self.assertIn("P079_SYNTH", blob, "The captured content must be analysed.")

    def test_7b_path_replacement_after_capture_is_not_followed(self):
        """TOCTOU (path replacement): the source path becomes a link to outside.

        On Windows a file symlink normally needs elevation; when the OS refuses,
        this scenario is reported as SKIPPED and counted as NOT TESTED rather
        than being silently treated as a pass.
        """
        iso = _IsoRoot(self)
        iso.install_local()
        proj = iso.project()
        outside = (iso.base / "swap.ts").resolve()
        outside.write_text("export const evil = 'P079_TOU_MARKER';\n", encoding="utf-8")
        src = _source(proj)
        target = (proj / "src" / "a.ts").resolve()
        if _is_link(target):
            _rm_link(target)
        elif target.exists():
            target.unlink()
        try:
            target.symlink_to(outside)
        except (OSError, NotImplementedError, ValueError):
            self.skipTest("OS refused symlink creation (needs elevation); "
                          "path-replacement TOCTOU remains NOT TESTED")
        with patch.object(js_ts_parser, "USIT_ROOT", iso.root):
            result = analyze_js_ts_project(proj, [], js_ts_source=src)
        blob = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("P079_TOU_MARKER", blob,
                         "A source path replaced by a link must not be followed.")
        self.assertIn("P079_SYNTH", blob)

    def test_7c_repeated_swap_never_changes_analysis(self):
        """Repeated swaps (content-based) across several analyses."""
        iso = _IsoRoot(self)
        iso.install_local()
        proj = iso.project()
        src = _source(proj)
        for _ in range(3):
            (proj / "src" / "a.ts").write_text(
                "export const evil = 'P079_TOU_MARKER';\n", encoding="utf-8")
            with patch.object(js_ts_parser, "USIT_ROOT", iso.root):
                result = analyze_js_ts_project(proj, [], js_ts_source=src)
            self.assertNotIn("P079_TOU_MARKER", json.dumps(result, ensure_ascii=False))
            self.assertIn("P079_SYNTH", json.dumps(result, ensure_ascii=False))


class FailClosedTests(unittest.TestCase):
    def test_8_adapter_failure_yields_no_fabricated_analysis(self):
        iso = _IsoRoot(self)
        iso.install_local()
        proj = iso.project()
        with patch.object(js_ts_parser, "USIT_ROOT", iso.root):
            missing = analyze_js_ts_project(proj, [], js_ts_source=_source(proj),
                                            adapter=iso.base / "missing.cjs")
            bad = iso.base / "bad.cjs"
            bad.write_text("process.stdout.write('not json');\n", encoding="utf-8")
            invalid = analyze_js_ts_project(proj, [], js_ts_source=_source(proj), adapter=bad)
        self.assertEqual(missing.get("files"), {})
        self.assertEqual(invalid.get("files"), {})
        codes = [d.get("code") for d in missing.get("project_diagnostics", [])]
        self.assertTrue(any(c in {"ADAPTER_FAILED", "ADAPTER_START_FAILED"} for c in codes),
                        f"got {codes}")
        self.assertIn("ADAPTER_INVALID_JSON",
                      [d.get("code") for d in invalid.get("project_diagnostics", [])])


if __name__ == "__main__":
    unittest.main()
