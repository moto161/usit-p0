"""USIT-S1-C IMPLEMENT-02B: scanner wiring tests for the file-level understanding layer.

Every input is a synthetic project inside ``tempfile`` or a direct call to the
scanner assembly function ``_build_file_understanding_text``. No real business
project is read, built, executed or contacted; no network, no external AI.

Coverage (per USIT-S1-C-IMPLEMENT-02B-DESIGN-01/02):
- I-12..I-15  publish consistency (published_files <-> file existence <-> status)
- B-01..B-04  output budget (exact fit, +1 byte, large evidence_refs, serialisation failure)
- K1..K8      summary identities on the real pipeline
- zero-record file / empty-path project diagnostic / unmatched record / projection degradation
- S-01..S-04  scrub boundary (credential value, sensitive path, diagnostic text, exception message)
- determinism across repeated runs; CLI exit codes
"""
from __future__ import annotations

import contextlib
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import usit.scanner as scanner
import usit.understanding_projection as understanding_projection
from usit.cli import main as cli_main
from usit.limits import ParserTaskSupervisor
from usit.safety import ScanBlocked, authorization_record
from usit.scanner import scan_project


def _frozen_supervisor(*args, **kwargs):
    """ParserTaskSupervisor with a frozen clock.

    ``report.json`` carries ``parser_task_audit.elapsed_seconds`` (wall-clock
    timings); their digit count varies between otherwise-identical runs, which
    would make the staged byte total -- and therefore an exact-fit budget
    assertion -- flaky.  Freezing the clock leaves every other output untouched
    and makes the total deterministic.  (The understanding payload itself is
    already deterministic; see DeterminismAndCliTests.)
    """
    kwargs["clock"] = lambda: 1000.0
    return ParserTaskSupervisor(*args, **kwargs)


class _SyntheticProject:
    """A disposable project built inside the OS temp directory."""

    def __init__(self, case: unittest.TestCase, name: str = "project"):
        self._tmp = tempfile.TemporaryDirectory(prefix="usit-s1c-wiring-")
        case.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self.root = self.base / name
        self.out = self.base / "out"
        self.root.mkdir(parents=True, exist_ok=True)

    def authorization(self, out: Path | None = None) -> dict:
        return authorization_record(self.root.resolve(), (out or self.out).resolve())

    def write(self, rel: str, text: str) -> Path:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def published(self) -> list[str]:
        if not self.out.exists():
            return []
        return sorted(p.relative_to(self.out).as_posix() for p in self.out.rglob("*") if p.is_file())

    def staged_dirs(self) -> list[Path]:
        return list(self.base.glob(".usit-stage-*"))

    def status_markers(self) -> list[Path]:
        return sorted(self.base.glob("usit-status-*.json"))

    def terminal_status(self) -> str | None:
        markers = self.status_markers()
        if not markers:
            return None
        return json.loads(markers[-1].read_text(encoding="utf-8"))["status"]

    def read_output(self, name: str) -> dict:
        return json.loads((self.out / name).read_text(encoding="utf-8"))

    def understanding(self) -> dict:
        return self.read_output("understanding.json")


def _record(path: str, **overrides) -> dict:
    rec = {
        "language": "Python", "file_path": path, "parser_name": "p", "parser_version": "v",
        "node_type": "function", "qualified_name": "pkg.mod.alpha",
        "source_location": {"file_path": path, "line": 12, "column": 4},
        "relation_type": None, "resolution_status": "RESOLVED", "confidence": 0.9,
        "evidence": {"locator": path + ":12", "kind": "definition"}, "diagnostics": [],
    }
    rec.update(overrides)
    return rec


def _manifest_entry(path: str, **overrides) -> dict:
    entry = {"path": path, "size_bytes": 100, "readable_text": True, "symbols": [{"name": "f"}],
             "analysis_degradation": {"degraded": False}}
    entry.update(overrides)
    return entry


def _project_diagnostic(code: str = "JS_TS_PROJECT_DIAGNOSTIC") -> dict:
    return {
        "language": "JavaScript/TypeScript", "file_path": "", "parser_name": "tsc", "parser_version": "6",
        "node_type": "diagnostic", "qualified_name": code,
        "source_location": {"file_path": "", "line": None, "column": None}, "relation_type": None,
        "resolution_status": "UNKNOWN", "confidence": 0.0,
        "evidence": {"diagnostic": code}, "diagnostics": [{"code": code}],
    }


def _assemble(manifest_entries: list[dict], records: list):
    """Drive the scanner assembly function directly (pure, no scan)."""
    text, status = scanner._build_file_understanding_text(
        {"files": manifest_entries}, {"records": records})
    return (json.loads(text) if text is not None else None), status


# ---------------------------------------------------------------------------
# I-12..I-15 publish consistency
# ---------------------------------------------------------------------------
class PublishConsistencyTests(unittest.TestCase):
    def test_i12_published_files_file_existence_and_status_agree(self):
        project = _SyntheticProject(self)
        project.write("a.py", "def alpha():\n    return 1\n")
        summary = scan_project(str(project.root), str(project.out), force=True,
                               authorization=project.authorization())
        status_complete = summary["understanding_status"]["status"] == "COMPLETE"
        exists = (project.out / "understanding.json").is_file()
        published = "understanding.json" in project.published()
        self.assertTrue(status_complete)
        self.assertTrue(exists)
        self.assertTrue(published)
        # iff chain: file existence <-> published_files <-> COMPLETE status
        self.assertEqual(exists, published)
        self.assertEqual(exists, status_complete)

    def test_i13_build_failure_leaves_file_absent_status_failed_others_published(self):
        project = _SyntheticProject(self)
        project.write("a.py", "def alpha():\n    return 1\n")
        with patch.object(scanner, "build_file_understanding",
                          side_effect=ValueError("synthetic build failure")):
            summary = scan_project(str(project.root), str(project.out), force=True,
                                   authorization=project.authorization())  # must NOT raise
        self.assertEqual(summary["status"], "COMPLETE")
        status = summary["understanding_status"]
        self.assertEqual(status["status"], "FAILED")
        self.assertEqual(status["error_type"], "ValueError")
        self.assertIn("synthetic build failure", status["error_message"])
        self.assertFalse((project.out / "understanding.json").exists())
        self.assertNotIn("understanding.json", project.published())
        # every other output keeps its existing semantics
        for name in ("report.json", "scan_manifest.json", "knowledge_graph.json",
                     "parser_contract.json", "scan_status.json"):
            self.assertIn(name, project.published())
        self.assertEqual(project.terminal_status(), None)  # no blocked/incomplete marker

    def test_i14_budget_exceeded_blocks_before_publish_and_cleans_stage(self):
        project = _SyntheticProject(self)
        project.write("a.py", "def alpha():\n    return 1\n")
        with patch.object(scanner, "MAX_OUTPUT_BYTES", 1):
            with self.assertRaises(ScanBlocked) as blocked:
                scan_project(str(project.root), str(project.out),
                             authorization=project.authorization())
        self.assertEqual(blocked.exception.code, "MAX_OUTPUT_BYTES_EXCEEDED")
        self.assertEqual(project.published(), [])
        self.assertFalse((project.out / "understanding.json").exists())
        self.assertEqual(project.staged_dirs(), [])
        self.assertEqual(project.terminal_status(), "BLOCKED")

    def test_i15_write_failure_is_incomplete_and_cleans_stage(self):
        project = _SyntheticProject(self)
        project.write("a.py", "def alpha():\n    return 1\n")
        with patch.object(Path, "write_bytes", side_effect=OSError("synthetic disk error")):
            with self.assertRaises(OSError):
                scan_project(str(project.root), str(project.out),
                             authorization=project.authorization())
        self.assertEqual(project.published(), [])
        self.assertFalse((project.out / "understanding.json").exists())
        self.assertEqual(project.staged_dirs(), [])
        self.assertEqual(project.terminal_status(), "INCOMPLETE")


# ---------------------------------------------------------------------------
# B-01..B-04 output budget (UTF-8 serialised bytes, no exemption)
# ---------------------------------------------------------------------------
class OutputBudgetTests(unittest.TestCase):
    @staticmethod
    def _staged_total(project: _SyntheticProject) -> int:
        return sum(p.stat().st_size for p in project.out.rglob("*") if p.is_file())

    def _reference_total(self, project: _SyntheticProject) -> int:
        """Staged byte total of an unpatched run (frozen clock => deterministic).

        ``report.json`` embeds ``runtime_limits.max_output_bytes`` (9 digits
        unpatched), so a run patched with a 5-digit limit writes exactly 4 fewer
        bytes; the exact-fit value below compensates for that self-reference and
        the caller then proves the fit with ``staged_total == limit``.
        """
        with patch.object(scanner, "ParserTaskSupervisor", _frozen_supervisor):
            summary = scan_project(str(project.root), str(project.out), force=True,
                                   authorization=project.authorization())
        self.assertEqual(summary["understanding_status"]["status"], "COMPLETE")
        return self._staged_total(project)

    def test_b01_budget_exactly_at_limit_publishes(self):
        project = _SyntheticProject(self)
        project.write("a.py", "def alpha():\n    return beta()\ndef beta():\n    return 1\n")
        exact_fit = self._reference_total(project) - 4
        shutil.rmtree(project.out)  # fresh output dir => deterministic staged bytes
        with patch.object(scanner, "ParserTaskSupervisor", _frozen_supervisor), \
             patch.object(scanner, "MAX_OUTPUT_BYTES", exact_fit):
            summary = scan_project(str(project.root), str(project.out), force=True,
                                   authorization=project.authorization())
        self.assertEqual(summary["status"], "COMPLETE")
        self.assertIn("understanding.json", project.published())
        # the staged total equals the limit exactly: the boundary is inclusive (>)
        self.assertEqual(self._staged_total(project), exact_fit)

    def test_b02_budget_one_byte_over_blocks(self):
        project = _SyntheticProject(self)
        project.write("a.py", "def alpha():\n    return beta()\ndef beta():\n    return 1\n")
        exact_fit = self._reference_total(project) - 4
        shutil.rmtree(project.out)
        # guard the self-reference compensation: the exact-fit run must publish
        # with a staged total that equals the patched limit
        with patch.object(scanner, "ParserTaskSupervisor", _frozen_supervisor), \
             patch.object(scanner, "MAX_OUTPUT_BYTES", exact_fit):
            scan_project(str(project.root), str(project.out), force=True,
                         authorization=project.authorization())
        self.assertEqual(self._staged_total(project), exact_fit)
        shutil.rmtree(project.out)
        with patch.object(scanner, "ParserTaskSupervisor", _frozen_supervisor), \
             patch.object(scanner, "MAX_OUTPUT_BYTES", exact_fit - 1):
            with self.assertRaises(ScanBlocked) as blocked:
                scan_project(str(project.root), str(project.out), force=True,
                             authorization=project.authorization())
        self.assertEqual(blocked.exception.code, "MAX_OUTPUT_BYTES_EXCEEDED")
        self.assertEqual(project.published(), [])
        self.assertEqual(project.staged_dirs(), [])
        self.assertEqual(project.terminal_status(), "BLOCKED")

    def test_b03_large_evidence_refs_participate_in_budget(self):
        project = _SyntheticProject(self)
        for i in range(150):
            project.write("mod%d.py" % i, "def f%d():\n    return %d\n" % (i, i))
        summary = scan_project(str(project.root), str(project.out), force=True,
                               authorization=project.authorization())
        status = summary["understanding_status"]
        self.assertEqual(status["status"], "COMPLETE")
        # reported bytes == on-disk UTF-8 size of the emitted file
        self.assertEqual((project.out / "understanding.json").stat().st_size, status["bytes"])
        understanding = project.understanding()
        self.assertGreater(status["bytes"], 5000)
        self.assertGreater(sum(len(f["evidence_refs"]) for f in understanding["files"]), 100)
        # the same project under a tiny budget is blocked BEFORE publish (no exemption)
        shutil.rmtree(project.out)
        with patch.object(scanner, "MAX_OUTPUT_BYTES", 4096):
            with self.assertRaises(ScanBlocked) as blocked:
                scan_project(str(project.root), str(project.out), force=True,
                             authorization=project.authorization())
        self.assertEqual(blocked.exception.code, "MAX_OUTPUT_BYTES_EXCEEDED")
        self.assertEqual(project.published(), [])

    def test_b04_serialisation_failure_degrades_to_failed(self):
        project = _SyntheticProject(self)
        project.write("a.py", "def alpha():\n    return 1\n")
        unserialisable = {"files": [], "totals": {}, "project_diagnostics": [],
                          "unmatched_records": [], "payload": object()}
        with patch.object(scanner, "build_file_understanding", return_value=unserialisable), \
             patch.object(scanner, "verify_understanding_identities"):
            summary = scan_project(str(project.root), str(project.out), force=True,
                                   authorization=project.authorization())  # must NOT raise
        self.assertEqual(summary["status"], "COMPLETE")
        status = summary["understanding_status"]
        self.assertEqual(status["status"], "FAILED")
        self.assertEqual(status["error_type"], "TypeError")
        self.assertFalse((project.out / "understanding.json").exists())
        self.assertNotIn("understanding.json", project.published())


# ---------------------------------------------------------------------------
# K1..K8 summary identities + record attribution edge cases
# ---------------------------------------------------------------------------
class SummaryIdentityTests(unittest.TestCase):
    def _scan_mixed(self) -> tuple[_SyntheticProject, dict, dict]:
        project = _SyntheticProject(self)
        project.write("a.py", "def alpha():\n    return beta()\ndef beta():\n    return 1\n")
        project.write("empty.py", "")
        project.write("bad.py", "def broken(:\n    pass\n")
        summary = scan_project(str(project.root), str(project.out), force=True,
                               authorization=project.authorization())
        return project, summary, project.understanding()

    def test_k1_to_k8_hold_on_real_pipeline(self):
        project, summary, understanding = self._scan_mixed()
        parser_contract = project.read_output("parser_contract.json")
        manifest = project.read_output("scan_manifest.json")
        totals = understanding["totals"]
        excluded = summary["understanding_status"]["sensitive_records_excluded"]
        # K1: every raw input record is attributed exactly once
        self.assertEqual(
            totals["n_records_total"] + totals["project_diagnostic_total"] + totals["unmatched_total"],
            len(parser_contract["records"]) - excluded)
        # K2
        self.assertEqual(totals["n_records_total"],
                         sum(f["n_records"] for f in understanding["files"]))
        # K3 / K4
        self.assertEqual(totals["unmatched_total"], len(understanding["unmatched_records"]))
        self.assertEqual(totals["project_diagnostic_total"], len(understanding["project_diagnostics"]))
        # K5: manifest-driven coverage
        self.assertEqual(totals["files"], len(understanding["files"]))
        self.assertEqual(totals["files"], len(manifest["files"]))
        # K6
        self.assertEqual(sum(totals["by_conclusion"].values()), totals["files"])
        # K7: each conflict group counted once
        self.assertEqual(totals["conflict_total"],
                         sum(f["conflict_count"] for f in understanding["files"]))
        # K8 (observable projection-level part: Nr == N_raw; projected_items == Nr + cf
        # is verified by the projection suite and by the in-assembly identity check,
        # whose success is implied by understanding_status == COMPLETE)
        self.assertEqual(summary["understanding_status"]["status"], "COMPLETE")
        for f in understanding["files"]:
            self.assertEqual(f["counts"]["Nr"], f["counts"]["N_raw"])
            self.assertEqual(f["n_records"], f["counts"]["N_raw"])
        # no VERIFIED_FACT anywhere in the payload
        self.assertNotIn("VERIFIED_FACT", json.dumps(understanding))

    def test_zero_record_manifest_file_gets_no_records_conclusion(self):
        understanding, status = _assemble([_manifest_entry("a.py", symbols=[])], [])
        self.assertEqual(status["status"], "COMPLETE")
        self.assertEqual(len(understanding["files"]), 1)
        f = understanding["files"][0]
        self.assertEqual(f["file_conclusion"], "NO_RECORDS")
        self.assertEqual(f["branch"], 1)
        self.assertEqual(f["n_records"], 0)
        self.assertEqual(understanding["totals"]["n_records_total"], 0)

    def test_empty_path_project_diagnostic_is_isolated(self):
        understanding, status = _assemble(
            [_manifest_entry("a.py", symbols=[])], [_project_diagnostic("JS_X")])
        self.assertEqual(status["status"], "COMPLETE")
        self.assertEqual(len(understanding["project_diagnostics"]), 1)
        diag = understanding["project_diagnostics"][0]
        self.assertEqual(diag["file_path"], "")
        self.assertEqual(diag["diagnostic_code"], "JS_X")
        self.assertEqual(understanding["totals"]["project_diagnostic_total"], 1)
        self.assertEqual(understanding["totals"]["n_records_total"], 0)
        self.assertEqual(understanding["files"][0]["file_conclusion"], "NO_RECORDS")

    def test_unmatched_record_is_preserved_with_reason(self):
        # a.py carries a matching record (so the batch is not a systemic
        # mismatch); ghost.py has no manifest entry -> unmatched, never dropped.
        understanding, status = _assemble(
            [_manifest_entry("a.py", symbols=[])], [_record("a.py"), _record("ghost.py")])
        self.assertEqual(status["status"], "COMPLETE")
        self.assertEqual(understanding["totals"]["unmatched_total"], 1)
        self.assertEqual(understanding["totals"]["n_records_total"], 1)
        entry = understanding["unmatched_records"][0]
        self.assertEqual(entry["reason"], "no_matching_manifest_file")
        self.assertEqual(entry["file_path"], "ghost.py")
        self.assertIsNotNone(entry["projection"])  # per-record traceability preserved

    def test_real_pipeline_project_diagnostics_have_empty_path_only(self):
        project, _, understanding = self._scan_mixed()
        for diag in understanding["project_diagnostics"]:
            self.assertEqual(diag["file_path"], "")
            self.assertTrue(diag["diagnostic_code"])


# ---------------------------------------------------------------------------
# projection failure degradation (raw counts never reduced)
# ---------------------------------------------------------------------------
class ProjectionFailureTests(unittest.TestCase):
    def test_partial_projection_failure_still_counts_raw_records(self):
        project = _SyntheticProject(self)
        project.write("a.py", "def alpha():\n    return 1\ndef target():\n    return 2\n")
        real = understanding_projection.project_record

        def flaky(record):
            if isinstance(record, dict) and record.get("node_type") == "function":
                raise ValueError("synthetic projection failure")
            return real(record)

        with patch.object(understanding_projection, "project_record", side_effect=flaky):
            summary = scan_project(str(project.root), str(project.out), force=True,
                                   authorization=project.authorization())
        understanding = project.understanding()
        f = next(x for x in understanding["files"] if x["file_path"] == "a.py")
        self.assertEqual(summary["understanding_status"]["status"], "COMPLETE")
        self.assertGreaterEqual(f["counts"]["N_construct_fail"], 1)
        # raw record count comes from the pre-projection grouping, never reduced
        self.assertEqual(f["n_records"], f["counts"]["N_raw"])
        self.assertGreaterEqual(f["n_records"], 2)
        # K1 still holds on the degraded pipeline
        totals = understanding["totals"]
        self.assertEqual(
            totals["n_records_total"] + totals["project_diagnostic_total"] + totals["unmatched_total"],
            len(project.read_output("parser_contract.json")["records"]))

    def test_total_projection_failure_still_counts_raw_records(self):
        project = _SyntheticProject(self)
        project.write("a.py", "def alpha():\n    return 1\n")

        def boom(record):
            raise RuntimeError("synthetic total failure")

        with patch.object(understanding_projection, "project_record", side_effect=boom):
            summary = scan_project(str(project.root), str(project.out), force=True,
                                   authorization=project.authorization())
        understanding = project.understanding()
        f = next(x for x in understanding["files"] if x["file_path"] == "a.py")
        self.assertEqual(summary["understanding_status"]["status"], "COMPLETE")
        self.assertEqual(f["n_records"], f["counts"]["N_raw"])
        self.assertEqual(
            f["counts"]["N_construct_fail"] + f["counts"]["N_unexpected_error"], f["counts"]["N_raw"])
        self.assertFalse(any(x["file_conclusion"] == "VERIFIED_FACT" for x in understanding["files"]))


# ---------------------------------------------------------------------------
# S-01..S-04 scrub boundary
# ---------------------------------------------------------------------------
class ScrubBoundaryTests(unittest.TestCase):
    def test_is_sensitive_positive_examples_match_real_rules(self):
        for rel in ("credentials.py", "server.pem", ".env.production", "secrets/token.txt",
                    "api_key.py", "密码清单.md", "id_rsa"):
            self.assertTrue(scanner.is_sensitive(rel), rel)

    def test_is_sensitive_negative_examples_match_real_rules(self):
        for rel in ("app.py", "api.py", "pkg/mod.py", "README.md", "main.go", "tests/test_x.py"):
            self.assertFalse(scanner.is_sensitive(rel), rel)

    def test_s01_credential_value_in_normal_source_is_not_emitted(self):
        project = _SyntheticProject(self)
        project.write("a.py",
                      'def alpha():\n    API_KEY = "sk-live-SECRETVALUE123"\n    return API_KEY\n')
        summary = scan_project(str(project.root), str(project.out), force=True,
                               authorization=project.authorization())
        self.assertEqual(summary["understanding_status"]["status"], "COMPLETE")
        blob = (project.out / "understanding.json").read_text(encoding="utf-8")
        self.assertNotIn("sk-live-SECRETVALUE123", blob)

    def test_s02_sensitive_path_records_excluded_with_count_never_in_output(self):
        sensitive = [_record("credentials.py"), _record("server.pem"),
                     _record("secrets/token.txt"), _record(".env.production"),
                     _record("api_key.py")]
        understanding, status = _assemble([_manifest_entry("a.py", symbols=[])],
                                          [_record("a.py")] + sensitive)
        self.assertEqual(status["status"], "COMPLETE")
        self.assertEqual(status["sensitive_records_excluded"], 5)
        # excluded records are NOT unmatched and NOT counted as raw records
        self.assertEqual(understanding["totals"]["unmatched_total"], 0)
        self.assertEqual(understanding["totals"]["n_records_total"], 1)
        blob = json.dumps(understanding)
        for rec in sensitive:
            self.assertNotIn(rec["file_path"], blob)

    def test_s02b_normal_records_are_not_dropped_by_the_prefilter(self):
        normal = [_record("a.py"), _record("api.py"), _record("pkg/mod.py"), _record("README.md")]
        _, status = _assemble([_manifest_entry("a.py", symbols=[])], normal)
        self.assertEqual(status["sensitive_records_excluded"], 0)

    def test_s03_diagnostic_and_source_text_not_emitted(self):
        project = _SyntheticProject(self)
        project.write("bad.py", "def broken(:\n    UNIQUE_SOURCE_LINE = 1\n")
        summary = scan_project(str(project.root), str(project.out), force=True,
                               authorization=project.authorization())
        self.assertEqual(summary["understanding_status"]["status"], "COMPLETE")
        blob = (project.out / "understanding.json").read_text(encoding="utf-8")
        self.assertNotIn("def broken(:", blob)
        self.assertNotIn("UNIQUE_SOURCE_LINE", blob)

    def test_s04_exception_message_not_emitted(self):
        project = _SyntheticProject(self)
        project.write("a.py", "def alpha():\n    return 1\n")

        def boom(record):
            raise ValueError("SECRET_DEBUG_INFO")

        with patch.object(understanding_projection, "project_record", side_effect=boom):
            summary = scan_project(str(project.root), str(project.out), force=True,
                                   authorization=project.authorization())
        self.assertEqual(summary["understanding_status"]["status"], "COMPLETE")
        blob = (project.out / "understanding.json").read_text(encoding="utf-8")
        self.assertNotIn("SECRET_DEBUG_INFO", blob)


# ---------------------------------------------------------------------------
# determinism + CLI exit codes
# ---------------------------------------------------------------------------
class DeterminismAndCliTests(unittest.TestCase):
    def test_repeated_runs_produce_identical_understanding(self):
        project = _SyntheticProject(self)
        project.write("a.py", "def alpha():\n    return beta()\ndef beta():\n    return 1\n")
        project.write("pkg/mod.py", "class Service:\n    def run(self):\n        return 1\n")
        scan_project(str(project.root), str(project.out), force=True,
                     authorization=project.authorization())
        first = (project.out / "understanding.json").read_bytes()
        scan_project(str(project.root), str(project.out), force=True,
                     authorization=project.authorization())  # retry over existing output
        second = (project.out / "understanding.json").read_bytes()
        self.assertEqual(first, second)

    def test_c01_cli_scan_exit_zero_reports_understanding_status(self):
        project = _SyntheticProject(self)
        project.write("a.py", "def alpha():\n    return 1\n")
        auth_path = project.base / "auth.json"
        auth_path.write_text(json.dumps(project.authorization()), encoding="utf-8")
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = cli_main(["scan", str(project.root), "--out", str(project.out),
                             "--authorization-file", str(auth_path)])
        self.assertEqual(code, 0)
        summary = json.loads(buffer.getvalue())
        self.assertEqual(summary["understanding_status"]["status"], "COMPLETE")
        self.assertIn("understanding.json", project.published())

    def test_c02_cli_missing_authorization_exits_two_without_understanding(self):
        project = _SyntheticProject(self)
        project.write("a.py", "def alpha():\n    return 1\n")
        out2 = project.base / "out2"
        auth = authorization_record(project.root.resolve(), out2.resolve())
        auth_path = project.base / "auth2.json"
        auth_path.write_text(json.dumps(auth), encoding="utf-8")
        auth_path.unlink()  # missing authorization file
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = cli_main(["scan", str(project.root), "--out", str(out2),
                             "--authorization-file", str(auth_path)])
        self.assertEqual(code, 2)
        self.assertFalse((out2 / "understanding.json").exists())


if __name__ == "__main__":
    unittest.main()
