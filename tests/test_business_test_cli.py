from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from usit.cli import main
from usit.safety import authorization_record


class BusinessTestCliTests(unittest.TestCase):
    def test_command_writes_semantics_and_candidates_outside_project(self):
        with tempfile.TemporaryDirectory(prefix="usit-business-cli-") as td:
            base = Path(td).resolve()
            project = base / "project"
            output = base / "reports" / "run-001"
            reports = output.parent
            project.mkdir()
            reports.mkdir()
            (project / "docs").mkdir()
            (project / "server" / "routes").mkdir(parents=True)
            (project / "docs" / "PRD.md").write_text(
                "# 媒体模块\n\n| 功能 | 状态 | 优先级 |\n|---|---|---|\n| 照片上传 | ✅ | P0 |\n",
                encoding="utf-8",
            )
            (project / "server" / "routes" / "photos.js").write_text(
                "router.post('/photos', authRequired, async (req, res) => {});\n",
                encoding="utf-8",
            )
            before = {
                p.relative_to(project).as_posix(): p.read_bytes()
                for p in project.rglob("*") if p.is_file()
            }
            graph_input = base / "scan" / "knowledge_graph.json"
            graph_input.parent.mkdir(parents=True)
            graph_input.write_text(json.dumps({
                "schema_version": "usit-graph-v1",
                "nodes": [
                    {"id": "file:docs/PRD.md", "type": "file", "label": "docs/PRD.md",
                     "evidence": {"path": "docs/PRD.md"}},
                    {"id": "file:server/routes/photos.js", "type": "file",
                     "label": "server/routes/photos.js",
                     "evidence": {"path": "server/routes/photos.js"}},
                ],
                "edges": [],
            }), encoding="utf-8")
            auth_path = base / "authorization.json"
            auth = authorization_record(project, output)
            auth_path.write_text(json.dumps(auth), encoding="utf-8")

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = main([
                    "plan-business-tests", str(project),
                    "--out", str(output),
                    "--authorization-file", str(auth_path),
                    "--graph", str(graph_input),
                ])

            self.assertEqual(exit_code, 0)
            summary = json.loads(stdout.getvalue())
            self.assertEqual(summary["execution_status"], "NOT_EXECUTED")
            semantics_path = output / "business_semantics.json"
            plan_path = output / "business_test_plan.json"
            self.assertTrue(semantics_path.is_file())
            self.assertTrue(plan_path.is_file())
            business_graph_path = output / "business_knowledge_graph.json"
            self.assertTrue(business_graph_path.is_file())
            security_path = output / "security_test_candidates.json"
            self.assertTrue(security_path.is_file())
            security_plan = json.loads(security_path.read_text(encoding="utf-8"))
            import hashlib
            self.assertEqual(
                security_plan["source_business_graph_sha256"],
                hashlib.sha256(business_graph_path.read_bytes()).hexdigest(),
            )
            self.assertEqual(summary["security_test_candidate_count"], security_plan["candidate_count"])
            self.assertGreaterEqual(security_plan["candidate_count"], 1)
            self.assertTrue(all(item["execution_status"] == "NOT_EXECUTED"
                                for item in security_plan["candidates"]))
            business_graph = json.loads(business_graph_path.read_text(encoding="utf-8"))
            self.assertEqual(business_graph["business_understanding"]["linked_records"], 2)
            self.assertEqual(len(business_graph["business_understanding"]["unlinked_records"]), 0)
            self.assertTrue(all(
                edge.get("verified") is False
                for edge in business_graph["edges"]
                if edge.get("method") == "business-semantics-source-link"
            ))
            self.assertEqual(summary["business_graph_linked_records"], 2)
            endpoint_edges = [
                edge for edge in business_graph["edges"]
                if edge.get("method") == "business-semantics-source-link"
                and edge.get("evidence", {}).get("path") == "server/routes/photos.js"
            ]
            self.assertEqual(len(endpoint_edges), 1)
            self.assertEqual(endpoint_edges[0]["evidence"].get("line"), 1)
            semantics = json.loads(semantics_path.read_text(encoding="utf-8"))
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            self.assertGreaterEqual(semantics["feature_count"], 1)
            self.assertGreaterEqual(plan["candidate_count"], 1)
            self.assertTrue(all(x["execution_status"] == "NOT_EXECUTED" for x in plan["candidates"]))
            after = {
                p.relative_to(project).as_posix(): p.read_bytes()
                for p in project.rglob("*") if p.is_file()
            }
            self.assertEqual(before, after)

    def test_existing_output_is_not_overwritten(self):
        with tempfile.TemporaryDirectory(prefix="usit-business-cli-") as td:
            base = Path(td).resolve()
            project = base / "project"
            output = base / "reports" / "existing"
            output.mkdir(parents=True)
            project.mkdir()
            marker = output / "keep.txt"
            marker.write_text("preserve", encoding="utf-8")
            auth_path = base / "authorization.json"
            auth_path.write_text(
                json.dumps(authorization_record(project, output)), encoding="utf-8"
            )
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                exit_code = main([
                    "plan-business-tests", str(project),
                    "--out", str(output),
                    "--authorization-file", str(auth_path),
                ])
            self.assertEqual(exit_code, 2)
            self.assertIn("output already exists", stderr.getvalue())
            self.assertEqual(marker.read_text(encoding="utf-8"), "preserve")

    def test_propose_fixes_writes_proposals_outside_project(self):
        with tempfile.TemporaryDirectory(prefix="usit-business-cli-") as td:
            base = Path(td).resolve()
            project = base / "project"
            (project / "docs").mkdir(parents=True)
            (project / "server" / "routes").mkdir(parents=True)
            (project / "docs" / "api.md").write_text(
                "# API\n\nGET /api/albums\nPOST /api/albums\n", encoding="utf-8")
            (project / "server" / "routes" / "photos.js").write_text(
                "router.get('/api/photos', async (req, res) => {});\n", encoding="utf-8")
            before = {
                p.relative_to(project).as_posix(): p.read_bytes()
                for p in project.rglob("*") if p.is_file()
            }
            gaps_path = base / "gaps.json"
            gaps_path.write_text(json.dumps({
                "schema_version": "usit-verification-gap-backlog-v1",
                "gaps": [{
                    "gap_id": "g1", "gap_kind": "UNDOCUMENTED_CODE_ENDPOINT",
                    "scope": "API_CONTRACT", "target": "GET /api/photos",
                    "priority": "P1", "reason": "reason", "action": "action",
                    "evidence": [{"path": "server/routes/photos.js", "line": 1, "kind": "code_route"}],
                }],
            }), encoding="utf-8")
            sem_path = base / "semantics.json"
            sem_path.write_text(json.dumps({
                "endpoint_contract_alignment": [
                    {"status": "EXACT_MATCH", "method": "GET", "path": "/api/albums",
                     "document_evidence": {"source": "docs/api.md", "line": 3},
                     "code_evidence": [{"method": "GET", "path": "/api/albums", "source": "server/routes/photos.js", "line": 1}]},
                    {"status": "EXACT_MATCH", "method": "POST", "path": "/api/albums",
                     "document_evidence": {"source": "docs/api.md", "line": 4},
                     "code_evidence": [{"method": "POST", "path": "/api/albums", "source": "server/routes/photos.js", "line": 1}]},
                    {"status": "CODE_ENDPOINT_NOT_DOCUMENTED", "method": "GET", "path": "/api/photos",
                     "document_evidence": None,
                     "code_evidence": [{"method": "GET", "path": "/api/photos", "source": "server/routes/photos.js", "line": 1}]},
                ],
            }), encoding="utf-8")
            out_path = base / "fix-proposals.json"
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = main([
                    "propose-fixes",
                    "--gap-backlog", str(gaps_path),
                    "--semantics", str(sem_path),
                    "--project", str(project),
                    "--out", str(out_path),
                ])
            self.assertEqual(exit_code, 0)
            proposals = json.loads(out_path.read_text(encoding="utf-8"))
            self.assertEqual(proposals["proposal_count"], 1)
            self.assertEqual(proposals["proposals"][0]["status"], "PROPOSED")
            self.assertEqual(proposals["proposals"][0]["change"]["new_text"], "GET /api/photos")
            after = {
                p.relative_to(project).as_posix(): p.read_bytes()
                for p in project.rglob("*") if p.is_file()
            }
            self.assertEqual(before, after)

    def test_apply_fix_writes_work_copy_and_verifies(self):
        from usit.fix_proposal import propose_fixes, review_fix_proposals

        with tempfile.TemporaryDirectory(prefix="usit-business-cli-") as td:
            base = Path(td).resolve()
            project = base / "project"
            (project / "docs").mkdir(parents=True)
            (project / "server" / "routes").mkdir(parents=True)
            (project / "docs" / "api.md").write_text(
                "# API\n\nGET /api/albums\nPOST /api/albums\n", encoding="utf-8")
            (project / "server" / "routes" / "photos.js").write_text(
                "router.get('/api/photos', async (req, res) => {});\n", encoding="utf-8")
            gaps = {"schema_version": "usit-verification-gap-backlog-v1", "gaps": [{
                "gap_id": "g1", "gap_kind": "UNDOCUMENTED_CODE_ENDPOINT",
                "scope": "API_CONTRACT", "target": "GET /api/photos",
                "priority": "P1", "reason": "reason", "action": "action",
                "evidence": [{"path": "server/routes/photos.js", "line": 1, "kind": "code_route"}],
            }]}
            semantics = {"endpoint_contract_alignment": [
                {"status": "EXACT_MATCH", "method": "GET", "path": "/api/albums",
                 "document_evidence": {"source": "docs/api.md", "line": 3},
                 "code_evidence": [{"method": "GET", "path": "/api/albums", "source": "server/routes/photos.js", "line": 1}]},
                {"status": "EXACT_MATCH", "method": "POST", "path": "/api/albums",
                 "document_evidence": {"source": "docs/api.md", "line": 4},
                 "code_evidence": [{"method": "POST", "path": "/api/albums", "source": "server/routes/photos.js", "line": 1}]},
                {"status": "CODE_ENDPOINT_NOT_DOCUMENTED", "method": "GET", "path": "/api/photos",
                 "document_evidence": None,
                 "code_evidence": [{"method": "GET", "path": "/api/photos", "source": "server/routes/photos.js", "line": 1}]},
            ]}
            proposals = propose_fixes(gaps, semantics, project)
            proposal_id = proposals["proposals"][0]["proposal_id"]
            manifest = review_fix_proposals(proposals, [{
                "proposal_id": proposal_id, "decision": "APPROVED",
                "reviewed_by": "owner", "reviewed_at": "2026-11-12T00:00:00Z",
                "rationale": "internal endpoint, document it",
            }])
            proposals_path = base / "fix-proposals.json"
            proposals_path.write_text(json.dumps(proposals), encoding="utf-8")
            manifest_path = base / "fix-approval.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            sem_path = base / "semantics.json"
            sem_path.write_text(json.dumps(semantics), encoding="utf-8")
            out = base / "fix-work"
            auth_path = base / "authorization.json"
            auth_path.write_text(json.dumps(authorization_record(project, out)), encoding="utf-8")
            before = {
                p.relative_to(project).as_posix(): p.read_bytes()
                for p in project.rglob("*") if p.is_file()
            }
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = main([
                    "apply-fix",
                    "--proposals", str(proposals_path),
                    "--approval-manifest", str(manifest_path),
                    "--proposal-id", proposal_id,
                    "--project", str(project),
                    "--out", str(out),
                    "--authorization-file", str(auth_path),
                    "--semantics", str(sem_path),
                ])
            self.assertEqual(exit_code, 0)
            summary = json.loads(stdout.getvalue())
            self.assertEqual(summary["verification"], "VERIFIED")
            self.assertTrue(summary["gap_closed"])
            self.assertEqual(
                (out / "docs" / "api.md").read_text(encoding="utf-8"),
                "# API\n\nGET /api/albums\nPOST /api/albums\nGET /api/photos\n")
            after = {
                p.relative_to(project).as_posix(): p.read_bytes()
                for p in project.rglob("*") if p.is_file()
            }
            self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
