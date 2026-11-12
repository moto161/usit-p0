from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from usit.fix_proposal import (
    apply_fix,
    propose_fixes,
    review_fix_proposals,
    rollback_fix,
    verify_fix,
)

DOC_TEXT = "# API\n\nGET /api/albums\nPOST /api/albums\n"
ROUTE_TEXT = "router.get('/api/photos', async (req, res) => {});\n"


def _gap(gap_id, kind, target, evidence=None):
    return {
        "gap_id": gap_id,
        "gap_kind": kind,
        "scope": "API_CONTRACT",
        "target": target,
        "priority": "P1",
        "reason": "reason",
        "action": "action",
        "evidence": evidence or [],
    }


def _semantics(rows):
    return {"endpoint_contract_alignment": rows}


def _exact_row(method, path, doc, line):
    return {
        "status": "EXACT_MATCH",
        "method": method,
        "path": path,
        "document_evidence": {"source": doc, "line": line},
        "code_evidence": [{"method": method, "path": path, "source": "server/routes/photos.js", "line": 1}],
        "confidence": "STATIC_INFERENCE",
        "verification_status": "NOT_VERIFIED",
    }


def _undocumented_row(method, path):
    return {
        "status": "CODE_ENDPOINT_NOT_DOCUMENTED",
        "method": method,
        "path": path,
        "document_evidence": None,
        "code_evidence": [{"method": method, "path": path, "source": "server/routes/photos.js", "line": 1}],
        "confidence": "STATIC_INFERENCE",
        "verification_status": "NOT_VERIFIED",
    }


class FixProposalTests(unittest.TestCase):
    def _project(self, base: Path, doc_text: str = DOC_TEXT) -> Path:
        project = base / "project"
        (project / "docs").mkdir(parents=True)
        (project / "server" / "routes").mkdir(parents=True)
        (project / "docs" / "api.md").write_text(doc_text, encoding="utf-8")
        (project / "server" / "routes" / "photos.js").write_text(ROUTE_TEXT, encoding="utf-8")
        return project

    def _semantics(self):
        return _semantics([
            _exact_row("GET", "/api/albums", "docs/api.md", 3),
            _exact_row("POST", "/api/albums", "docs/api.md", 4),
            _undocumented_row("GET", "/api/photos"),
        ])

    def _backlog(self, gaps):
        return {"schema_version": "usit-verification-gap-backlog-v1", "gaps": gaps}

    def _undocumented_gap(self, gap_id="g1", target="GET /api/photos"):
        return _gap(gap_id, "UNDOCUMENTED_CODE_ENDPOINT", target, [
            {"path": "server/routes/photos.js", "line": 1, "kind": "code_route"},
        ])

    def _approved(self, base: Path):
        """(project, proposals, manifest, proposal) for the happy-path fixture."""
        project = self._project(base)
        proposals = propose_fixes(self._backlog([self._undocumented_gap()]), self._semantics(), project)
        proposal = proposals["proposals"][0]
        manifest = review_fix_proposals(proposals, [{
            "proposal_id": proposal["proposal_id"],
            "decision": "APPROVED",
            "reviewed_by": "owner",
            "reviewed_at": "2026-11-12T00:00:00Z",
            "rationale": "internal endpoint, document it",
        }])
        return project, proposals, manifest, proposal

    def test_automatic_proposal_for_undocumented_endpoint(self):
        with tempfile.TemporaryDirectory(prefix="usit-fix-") as td:
            project = self._project(Path(td))
            result = propose_fixes(self._backlog([self._undocumented_gap()]), self._semantics(), project)
            self.assertEqual(result["proposal_count"], 1)
            proposal = result["proposals"][0]
            self.assertEqual(proposal["status"], "PROPOSED")
            change = proposal["change"]
            self.assertEqual(change["kind"], "insert_after_line")
            self.assertEqual(change["file"], "docs/api.md")
            self.assertEqual(change["anchor_line"], 4)
            self.assertEqual(change["anchor_text"], "POST /api/albums")
            self.assertEqual(change["new_text"], "GET /api/photos")

    def test_non_api_path_needs_human_decision(self):
        with tempfile.TemporaryDirectory(prefix="usit-fix-") as td:
            project = self._project(Path(td))
            result = propose_fixes(
                self._backlog([self._undocumented_gap(target="GET /photos")]),
                self._semantics(), project)
            proposal = result["proposals"][0]
            self.assertEqual(proposal["status"], "NEEDS_HUMAN_DECISION")
            self.assertIsNone(proposal["change"])

    def test_multiple_docs_need_human_decision(self):
        with tempfile.TemporaryDirectory(prefix="usit-fix-") as td:
            project = self._project(Path(td))
            semantics = _semantics([
                _exact_row("GET", "/api/albums", "docs/api.md", 3),
                _exact_row("POST", "/api/albums", "docs/other.md", 2),
                _undocumented_row("GET", "/api/photos"),
            ])
            result = propose_fixes(self._backlog([self._undocumented_gap()]), semantics, project)
            proposal = result["proposals"][0]
            self.assertEqual(proposal["status"], "NEEDS_HUMAN_DECISION")
            self.assertIsNone(proposal["change"])

    def test_documented_unmatched_needs_human_decision(self):
        with tempfile.TemporaryDirectory(prefix="usit-fix-") as td:
            project = self._project(Path(td))
            result = propose_fixes(
                self._backlog([_gap("g2", "DOCUMENTED_ENDPOINT_UNMATCHED", "GET /api/old")]),
                self._semantics(), project)
            proposal = result["proposals"][0]
            self.assertEqual(proposal["status"], "NEEDS_HUMAN_DECISION")
            self.assertIsNone(proposal["change"])

    def test_ambiguous_needs_human_decision(self):
        with tempfile.TemporaryDirectory(prefix="usit-fix-") as td:
            project = self._project(Path(td))
            result = propose_fixes(
                self._backlog([_gap("g3", "AMBIGUOUS_ENDPOINT_CONTRACT", "GET /api/albums")]),
                self._semantics(), project)
            proposal = result["proposals"][0]
            self.assertEqual(proposal["status"], "NEEDS_HUMAN_DECISION")
            self.assertIsNone(proposal["change"])

    def test_other_gap_kinds_skipped(self):
        with tempfile.TemporaryDirectory(prefix="usit-fix-") as td:
            project = self._project(Path(td))
            result = propose_fixes(
                self._backlog([_gap("g4", "CODE_COVERAGE_GAP", "some-function")]),
                self._semantics(), project)
            self.assertEqual(result["proposal_count"], 0)
            self.assertEqual(result["skipped_kinds"], ["CODE_COVERAGE_GAP"])

    def test_proposal_id_stable(self):
        with tempfile.TemporaryDirectory(prefix="usit-fix-") as td:
            project = self._project(Path(td))
            backlog = self._backlog([self._undocumented_gap()])
            first = propose_fixes(backlog, self._semantics(), project)
            second = propose_fixes(backlog, self._semantics(), project)
            self.assertEqual(first["proposals"][0]["proposal_id"], second["proposals"][0]["proposal_id"])
            other = propose_fixes(self._backlog([self._undocumented_gap(gap_id="g9")]), self._semantics(), project)
            self.assertNotEqual(first["proposals"][0]["proposal_id"], other["proposals"][0]["proposal_id"])

    def test_propose_does_not_write_project(self):
        with tempfile.TemporaryDirectory(prefix="usit-fix-") as td:
            project = self._project(Path(td))
            before = {p.relative_to(project).as_posix(): p.read_bytes() for p in project.rglob("*") if p.is_file()}
            propose_fixes(self._backlog([self._undocumented_gap()]), self._semantics(), project)
            after = {p.relative_to(project).as_posix(): p.read_bytes() for p in project.rglob("*") if p.is_file()}
            self.assertEqual(before, after)

    def test_unreviewed_proposal_cannot_apply(self):
        with tempfile.TemporaryDirectory(prefix="usit-fix-") as td:
            base = Path(td)
            project, proposals, _manifest, proposal = self._approved(base)
            empty_manifest = review_fix_proposals(proposals, [])
            with self.assertRaises(ValueError):
                apply_fix(proposals, empty_manifest, proposal["proposal_id"], project, base / "work")
            self.assertFalse((base / "work").exists())

    def test_rejected_proposal_cannot_apply(self):
        with tempfile.TemporaryDirectory(prefix="usit-fix-") as td:
            base = Path(td)
            project, proposals, _manifest, proposal = self._approved(base)
            manifest = review_fix_proposals(proposals, [{
                "proposal_id": proposal["proposal_id"],
                "decision": "REJECTED",
                "reviewed_by": "owner",
                "reviewed_at": "2026-11-12T00:00:00Z",
                "rationale": "not now",
            }])
            with self.assertRaises(ValueError):
                apply_fix(proposals, manifest, proposal["proposal_id"], project, base / "work")

    def test_tampered_proposals_manifest_mismatch(self):
        with tempfile.TemporaryDirectory(prefix="usit-fix-") as td:
            base = Path(td)
            project, proposals, manifest, proposal = self._approved(base)
            tampered = copy.deepcopy(proposals)
            tampered["proposals"][0]["target"] = "GET /api/other"
            with self.assertRaises(ValueError):
                apply_fix(tampered, manifest, proposal["proposal_id"], project, base / "work")

    def test_apply_and_rollback(self):
        with tempfile.TemporaryDirectory(prefix="usit-fix-") as td:
            base = Path(td)
            project, proposals, manifest, proposal = self._approved(base)
            work = base / "work"
            applied = apply_fix(proposals, manifest, proposal["proposal_id"], project, work)
            self.assertTrue(applied["applied"])
            work_doc = work / "docs" / "api.md"
            self.assertEqual(
                work_doc.read_text(encoding="utf-8"),
                "# API\n\nGET /api/albums\nPOST /api/albums\nGET /api/photos\n",
            )
            self.assertEqual((project / "docs" / "api.md").read_text(encoding="utf-8"), DOC_TEXT)
            # The snapshot must hold the file's exact original bytes, whatever
            # line endings the platform wrote (CRLF on Windows, LF elsewhere).
            original_bytes = (project / "docs" / "api.md").read_bytes()
            snapshot = Path(applied["snapshot_path"])
            self.assertEqual(snapshot.read_bytes(), original_bytes)
            self.assertEqual(applied["snapshot_sha256"], hashlib.sha256(original_bytes).hexdigest())
            rolled = rollback_fix(applied, work)
            self.assertTrue(rolled["rolled_back"])
            self.assertEqual(work_doc.read_text(encoding="utf-8"), DOC_TEXT)

    def test_apply_fails_closed_on_stale_anchor(self):
        with tempfile.TemporaryDirectory(prefix="usit-fix-") as td:
            base = Path(td)
            project, proposals, manifest, proposal = self._approved(base)
            (project / "docs" / "api.md").write_text(
                "# API\n\nGET /api/albums\nPOST /api/albums-changed\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                apply_fix(proposals, manifest, proposal["proposal_id"], project, base / "work")
            self.assertFalse((base / "work" / "docs" / "api.md").exists())

    def test_verify_fixed_doc(self):
        with tempfile.TemporaryDirectory(prefix="usit-fix-") as td:
            base = Path(td)
            project, proposals, manifest, proposal = self._approved(base)
            work = base / "work"
            apply_fix(proposals, manifest, proposal["proposal_id"], project, work)
            result = verify_fix(proposal, work, self._semantics())
            self.assertEqual(result["status"], "VERIFIED")
            self.assertTrue(result["gap_closed"])
            self.assertEqual(result["new_mismatches"], [])

    def test_apply_with_crlf_line_endings(self):
        # Regression for CI Run #1577: on Windows the fixture doc is written
        # with CRLF (write_text translates \n), while apply_fix decoded the
        # bytes directly, so the anchor line carried a trailing \r and the
        # exact-match check failed. CRLF bytes must apply and verify on
        # every platform, keeping the file's own line-ending style.
        with tempfile.TemporaryDirectory(prefix="usit-fix-") as td:
            base = Path(td)
            project = base / "project"
            (project / "docs").mkdir(parents=True)
            (project / "server" / "routes").mkdir(parents=True)
            (project / "docs" / "api.md").write_bytes(
                DOC_TEXT.replace("\n", "\r\n").encode("utf-8"))
            (project / "server" / "routes" / "photos.js").write_text(ROUTE_TEXT, encoding="utf-8")
            proposals = propose_fixes(self._backlog([self._undocumented_gap()]), self._semantics(), project)
            proposal = proposals["proposals"][0]
            self.assertEqual(proposal["status"], "PROPOSED")
            manifest = review_fix_proposals(proposals, [{
                "proposal_id": proposal["proposal_id"], "decision": "APPROVED",
                "reviewed_by": "owner", "reviewed_at": "2026-11-12T00:00:00Z",
                "rationale": "internal endpoint, document it",
            }])
            work = base / "work"
            applied = apply_fix(proposals, manifest, proposal["proposal_id"], project, work)
            self.assertTrue(applied["applied"])
            self.assertEqual(
                (work / "docs" / "api.md").read_bytes(),
                "# API\r\n\r\nGET /api/albums\r\nPOST /api/albums\r\nGET /api/photos\r\n".encode("utf-8"),
            )
            result = verify_fix(proposal, work, self._semantics())
            self.assertEqual(result["status"], "VERIFIED")

    def test_verify_unfixed_doc(self):
        with tempfile.TemporaryDirectory(prefix="usit-fix-") as td:
            base = Path(td)
            project, proposals, _manifest, proposal = self._approved(base)
            work = base / "work"
            (work / "docs").mkdir(parents=True)
            (work / "docs" / "api.md").write_text(DOC_TEXT, encoding="utf-8")
            result = verify_fix(proposal, work, self._semantics())
            self.assertEqual(result["status"], "NOT_VERIFIED")
            self.assertFalse(result["gap_closed"])


if __name__ == "__main__":
    unittest.main()
