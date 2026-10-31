"""P0.7.10 P2: TOCTOU window boundary determination (isolated temp copies only).

The question is NOT "can we make a race happen" (unreliable), but the sharper
one: **if a file is replaced between the canonical-path check and the read, is
the replacement read?**  That is answered deterministically by injecting the
swap at the exact point -- a wrapper monkey-patches ``fs.realpathSync`` so that,
immediately after the guard has resolved and validated a path, the file at that
canonical path is rewritten.  Whatever the adapter then reads is the observed
outcome.

Targets, one case each, all inside temporary copies:

  * baseline      - no injection: the verified ORIGINAL bytes are read
  * dependency    - a file under the project's node_modules
  * config        - the project's tsconfig.json
  * stdlib        - a TypeScript ``lib/*.d.ts`` file (boundary B)

The project-source path is covered separately by P0.7.9 (memory mode means the
adapter never re-opens captured source by path, so no window exists there).

If a replacement IS read, that is a real residual window and is recorded as
such -- it is never claimed away.
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
from usit.js_ts_parser import USIT_ROOT, analyze_js_ts_project

REAL_TS = USIT_ROOT / "node_modules" / "typescript"
_CACHE: Path | None = None

WRAPPER = r"""
const fs = require("node:fs");
const path = require("node:path");
const VICTIM = process.env.P079_VICTIM || "";
const REPLACEMENT = process.env.P079_REPLACEMENT || "";
const LOG = process.env.P079_LOG || "";
const origRealpath = fs.realpathSync.bind(fs);

function info(p) {
  try { const s = fs.statSync(p); return { size: s.size, mtimeMs: s.mtimeMs, ino: s.ino }; }
  catch (_) { return null; }
}

// Inject the swap exactly inside the check-then-read window: after the guard has
// resolved and validated the path, but before anything reads it.
fs.realpathSync = function (p) {
  const real = origRealpath(p);
  if (VICTIM && String(p).indexOf(VICTIM) !== -1) {
    const before = info(real);
    try { fs.writeFileSync(real, REPLACEMENT, "utf8"); } catch (_) {}
    const after = info(real);
    if (LOG) {
      fs.appendFileSync(LOG, JSON.stringify({
        event: "swapped_inside_check_then_read_window",
        checked_path: String(p), canonical_path: String(real),
        identity_before: before, identity_after: after,
        identity_changed: JSON.stringify(before) !== JSON.stringify(after),
      }) + "\n");
    }
  }
  return real;
};

require(process.env.P079_ADAPTER);
"""


def _cached_ts() -> Path:
    global _CACHE
    if _CACHE is None or not _CACHE.exists():
        holder = Path(tempfile.mkdtemp(prefix="p0710-tscache-")).resolve()
        shutil.copytree(REAL_TS, holder / "typescript")
        _CACHE = (holder / "typescript").resolve()
    return _CACHE


class _Iso:
    def __init__(self, case: unittest.TestCase):
        self._tmp = tempfile.TemporaryDirectory(prefix="p0710-iso-")
        case.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name).resolve()
        self.root = self.base / "root"
        (self.root / "node_modules").mkdir(parents=True, exist_ok=True)
        self.ts = self.root / "node_modules" / "typescript"
        shutil.copytree(_cached_ts(), self.ts)
        self.wrapper = self.base / "adapter_wrapper.cjs"
        self.wrapper.write_text(WRAPPER, encoding="utf-8")
        self.adapter = USIT_ROOT / "usit" / "js_ts_adapter.cjs"

    def project(self) -> Path:
        proj = self.base / "project"
        (proj / "src").mkdir(parents=True, exist_ok=True)
        dep = proj / "node_modules" / "victimlib"
        dep.mkdir(parents=True, exist_ok=True)
        (dep / "package.json").write_text(
            json.dumps({"name": "victimlib", "version": "1.0.0", "main": "index.js",
                        "types": "index.d.ts"}), encoding="utf-8")
        (dep / "index.js").write_text(
            "export function victimFn() { return 'P0710_ORIGINAL_DEP'; }\n", encoding="utf-8")
        (dep / "index.d.ts").write_text(
            "export declare function victimFn(): string;\n", encoding="utf-8")
        (proj / "src" / "a.ts").write_text(
            "import { victimFn } from 'victimlib';\n"
            "export const use = () => victimFn();\n"
            "export const rnd = () => Math.random();\n", encoding="utf-8")
        return proj.resolve()

    def source(self, proj: Path) -> dict[str, str]:
        paths = scanner.discover_files(proj)
        return {p.relative_to(proj).as_posix(): (
            scanner.decode_text(scanner.open_input_file(p, scanner.MAX_FILE_BYTES)) or "")
            for p in paths}


def _run(iso: _Iso, proj: Path, *, victim: str, replacement: str) -> tuple[dict, Path]:
    log = iso.base / "toctou_log.jsonl"
    env = dict(os.environ)
    env["P079_VICTIM"] = victim
    env["P079_REPLACEMENT"] = replacement
    env["P079_LOG"] = str(log)
    env["P079_ADAPTER"] = str(iso.adapter)
    with patch.object(js_ts_parser, "USIT_ROOT", iso.root), \
         patch.dict(os.environ, env, clear=False):
        result = analyze_js_ts_project(proj, [], js_ts_source=iso.source(proj),
                                       adapter=iso.wrapper)
    events = []
    if log.exists():
        events = [json.loads(line) for line in
                  log.read_text(encoding="utf-8").splitlines() if line.strip()]
    return result, Path(str(log)), events


class TouBoundaryTests(unittest.TestCase):
    def test_baseline_reads_the_verified_original_bytes(self):
        iso = _Iso(self)
        proj = iso.project()
        with patch.object(js_ts_parser, "USIT_ROOT", iso.root):
            result = analyze_js_ts_project(proj, [], js_ts_source=iso.source(proj))
        blob = json.dumps(result, ensure_ascii=False)
        # Baseline (no injection): the ORIGINAL dependency declaration and the
        # ORIGINAL standard library are what get read.
        self.assertIn("victimlib", blob,
                      "The project dependency must be discovered and read.")
        self.assertIn("victimFn", blob, "The original dependency declaration is read.")
        self.assertIn("<ts-lib>/", blob,
                      "The original standard library is read (baseline, no swap).")

    def test_dependency_file_replaced_inside_the_window_is_read(self):
        """Dependency .d.ts swapped inside check-then-read changes what is parsed.

        Observable effect: the original declaration disappears from the resolved
        target, i.e. the REPLACED bytes were the bytes actually read.  (The
        adapter reports symbols of the analysed project, not of the external
        declaration file, so the effect shows up as a lost target.)
        """
        iso = _Iso(self)
        proj = iso.project()
        replacement = "export declare function P0710_REPLACED_DECL(): number;\n"
        result, log, events = _run(iso, proj,
                                   victim=str(proj / "node_modules" / "victimlib" / "index.d.ts"),
                                   replacement=replacement)
        self.assertTrue(any(e.get("identity_changed") for e in events),
                        "The injection must have fired inside the window.")
        calls = {c.get("callee"): c for c in
                 result.get("files", {}).get("src/a.ts", {}).get("calls", [])}
        self.assertIn("victimFn", calls)
        swapped = calls["victimFn"]
        # Baseline resolves victimFn to the dependency's declaration; after the
        # swap that declaration no longer exists, so no target can be produced.
        self.assertIsNone(swapped.get("target"),
                          "The original declaration vanished: the replaced bytes "
                          "were read, proving a check-then-read window exists for "
                          "dependency files (residual risk, recorded not hidden).")
        self.assertNotEqual(swapped.get("resolution_status"), "RESOLVED")

    def test_config_file_replaced_inside_the_window_is_read(self):
        iso = _Iso(self)
        proj = iso.project()
        (proj / "tsconfig.json").write_text(json.dumps(
            {"compilerOptions": {"strict": True}}), encoding="utf-8")
        replacement = json.dumps({"compilerOptions": {"strict": False}}) \
            + " // P0710_REPLACED_CONFIG"
        result, log, events = _run(iso, proj,
                                   victim=str(proj / "tsconfig.json"),
                                   replacement=replacement)
        # The config text is parsed, not echoed; the observable effect is that the
        # swap landed inside the window (recorded), so a swap cannot be detected
        # from the output alone.
        self.assertTrue(events, "The config check-then-read path must have been exercised.")
        self.assertTrue(any(e.get("identity_changed") for e in events),
                        "The config swap landed inside the check-then-read window.")

    def test_stdlib_file_replaced_inside_the_window_is_read(self):
        """Standard-library bytes swapped inside the window ARE the bytes used.

        Observable effect: with ``lib.es5.d.ts`` reduced to a comment, standard
        library symbols no longer resolve to ``<ts-lib>/lib.es5.d.ts`` -- proof
        the replaced content was the content actually read.
        """
        iso = _Iso(self)
        proj = iso.project()
        result, log, events = _run(iso, proj,
                                   victim=str(iso.ts / "lib" / "lib.es5.d.ts"),
                                   replacement="// P0710_REPLACED_STDLIB\n")
        self.assertTrue(any(e.get("identity_changed") for e in events),
                        "The stdlib swap landed inside the check-then-read window.")
        blob = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("<ts-lib>/", blob,
                         "After the stdlib file is swapped, no symbol may still "
                         "resolve to the original standard library file -- that "
                         "would mean the replacement was NOT read.")


if __name__ == "__main__":
    unittest.main()
