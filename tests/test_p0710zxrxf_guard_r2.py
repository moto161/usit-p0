"""USIT-P0.7.13-F1-R2: directed tests for the ZXRXF7 frozen-hash guard.

These tests exercise the shared guard helper ``_frozen_guard_unchanged`` in
isolation (temp dirs, never the real project or historical artifacts) to prove:

  1. NEGATIVE: an unauthorized change to a protected production source is
     detected (the guard returns False for that file).
  2. POSITIVE: when nothing is changed, the guard passes for every watched file.
  3. AUTHORIZED MAINTENANCE: on the real repo, the regression harness that R1
     legitimately edited is excluded from ZXRXF7_WATCH, so the guard passes for
     the remaining protected set -- while the old "test guards its own test"
     circular dependency is demonstrably resolved (the harness still drifts from
     PRE, but is no longer in scope).

Nothing here writes to outputs/ or touches any historical evidence file.
"""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from tests.test_p0710zxrxf_trust_boundary import (  # noqa: E402
    ZXRXF7_WATCH,
    ZXRXF7_HISTORICAL_WATCH,
    ZXRXF7_MAINTENANCE_WATCH,
    MAINTENANCE_BASELINE,
    _frozen_guard_unchanged,
    _maintenance_guard_unchanged,
    _git_blob_sha1,
    OUT,
)

_PROJ = Path(__file__).resolve().parents[1]


class FrozenHashGuardR2Tests(unittest.TestCase):
    # ---- 1. NEGATIVE: unauthorized change to protected source is detected ---
    def test_guard_negative_unauthorized_change_detected(self):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "usit").mkdir()
        f = tmp / "usit" / "graph.py"
        f.write_bytes(b"unauthorized content that differs from baseline")
        real = hashlib.sha256(f.read_bytes()).hexdigest()
        baseline = tmp / "PRE_SHA256.json"
        # baseline records a WRONG hash for graph.py -> guard must flag it
        baseline.write_text(json.dumps({"usit/graph.py": "0" * 64}), encoding="utf-8")
        res = _frozen_guard_unchanged(["usit/graph.py"], str(baseline), base_dir=tmp)
        self.assertFalse(res["usit/graph.py"],
                         "guard must detect an unauthorized change to protected source")
        # and with the CORRECT hash it would pass (sanity of the helper)
        baseline.write_text(json.dumps({"usit/graph.py": real}), encoding="utf-8")
        res_ok = _frozen_guard_unchanged(["usit/graph.py"], str(baseline), base_dir=tmp)
        self.assertTrue(res_ok["usit/graph.py"])

    # ---- 2. POSITIVE: no change -> guard passes for every watched file -------
    def test_guard_positive_no_change_passes(self):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "usit").mkdir()
        f = tmp / "usit" / "graph.py"
        f.write_bytes(b"stable, unchanged content")
        real = hashlib.sha256(f.read_bytes()).hexdigest()
        baseline = tmp / "PRE_SHA256.json"
        baseline.write_text(json.dumps({"usit/graph.py": real}), encoding="utf-8")
        res = _frozen_guard_unchanged(["usit/graph.py"], str(baseline), base_dir=tmp)
        self.assertTrue(all(res.values()), f"no-change guard must pass: {res}")

    # ---- 3. AUTHORIZED MAINTENANCE: versioned successor baseline ----------
    def test_guard_real_repo_authorized_maintenance_tolerated(self):
        # The original PRE manifest remains immutable.  Files intentionally
        # maintained since PRE are covered by a separate, explicit successor
        # baseline with per-file reasons and canonical Git blob IDs.
        self.assertNotIn("tests/test_p0710zrfx_trust_boundary.py", ZXRXF7_WATCH)
        historical = _frozen_guard_unchanged(
            ZXRXF7_HISTORICAL_WATCH, OUT / "PRE_SHA256.json")
        maintained = _maintenance_guard_unchanged(
            ZXRXF7_MAINTENANCE_WATCH, MAINTENANCE_BASELINE)
        combined = {**historical, **maintained}
        self.assertTrue(all(combined.values()),
                        f"historical or authorized baseline mismatch: {combined}")

        manifest = json.loads(MAINTENANCE_BASELINE.read_text(encoding="utf-8"))
        self.assertEqual(set(manifest.get("files", {})), set(ZXRXF7_MAINTENANCE_WATCH))
        self.assertEqual(
            _git_blob_sha1("outputs/p0710zxrxf/PRE_SHA256.json"),
            manifest.get("historical_manifest_git_blob_sha1"),
            "the original PRE evidence manifest must remain byte-identical",
        )
        self.assertTrue(all(
            isinstance(item.get("reason"), str) and item["reason"].strip()
            for item in manifest["files"].values()
        ), "every maintained file requires a non-empty authorization rationale")

        # The actively-maintained ZRFX harness remains outside both baselines.
        with_harness = list(ZXRXF7_HISTORICAL_WATCH) + [
            "tests/test_p0710zrfx_trust_boundary.py"
        ]
        res_h = _frozen_guard_unchanged(with_harness, OUT / "PRE_SHA256.json")
        self.assertFalse(res_h["tests/test_p0710zrfx_trust_boundary.py"],
                         "the R1-edited harness must remain outside PRE (expected)")
        self.assertTrue(all(res_h[k] for k in ZXRXF7_HISTORICAL_WATCH),
                        "all still-frozen historical files must match PRE")



if __name__ == "__main__":
    unittest.main(verbosity=2)
