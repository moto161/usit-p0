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
            auth_path = base / "authorization.json"
            auth = authorization_record(project, output)
            auth_path.write_text(json.dumps(auth), encoding="utf-8")

            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = main([
                    "plan-business-tests", str(project),
                    "--out", str(output),
                    "--authorization-file", str(auth_path),
                ])

            self.assertEqual(exit_code, 0)
            summary = json.loads(stdout.getvalue())
            self.assertEqual(summary["execution_status"], "NOT_EXECUTED")
            semantics_path = output / "business_semantics.json"
            plan_path = output / "business_test_plan.json"
            self.assertTrue(semantics_path.is_file())
            self.assertTrue(plan_path.is_file())
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


if __name__ == "__main__":
    unittest.main()
