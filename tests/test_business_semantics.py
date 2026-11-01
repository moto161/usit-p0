"""Tests for the business-semantics adapter (P0.8).

Every fixture is a synthetic project built inside ``tempfile``; no real business
project is read.  The adapter is exercised through its public ``analyze()``
entry point, and every assertion checks the *structured* output plus evidence,
never a hard-coded real-world path.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from usit.business_semantics import (
    BusinessSemanticsAdapter,
    SemanticsBlocked,
    CONF_VERIFIED,
    CONF_INFERRED,
)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class SyntheticProject:
    def __init__(self, case: unittest.TestCase):
        self._tmp = tempfile.TemporaryDirectory(prefix="usit-p08-")
        case.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def db_doc(self, text: str) -> None:
        _write(self.root / "数据库设计文档.md", text)

    def api_doc(self, text: str) -> None:
        _write(self.root / "API接口设计文档.md", text)

    def prd(self, text: str) -> None:
        _write(self.root / "家庭APP需求文档.md", text)

    def route(self, rel: str, text: str) -> None:
        _write(self.root / rel, text)


DB_DOC = """# 数据库设计

### 2.1 users - 用户表

| 字段 | 类型 | 说明 |
|------|------|------|
| id | BIGINT | 主键 |
| username | VARCHAR(50) | 用户名 |
| role | ENUM('admin','member') | 角色 |
| status | ENUM('active','disabled') | 状态 |

### 2.5 contents - 内容表

| 字段 | 类型 | 说明 |
|------|------|------|
| id | BIGINT | 主键 |
| is_secret | TINYINT(1) | 秘密 |
| is_only_self | TINYINT(1) | 仅自己可见 |
"""

API_DOC = """# API 接口

POST /api/auth/login
GET /api/contents?type=photo
POST /api/contents
"""


class EntityExtractionTests(unittest.TestCase):
    def test_extracts_table_names_and_fields(self):
        p = SyntheticProject(self)
        p.db_doc(DB_DOC)
        result = BusinessSemanticsAdapter(p.root).analyze()
        names = {e["name"] for e in result["entities"]}
        self.assertIn("users", names)
        self.assertIn("contents", names)
        contents = next(e for e in result["entities"] if e["name"] == "contents")
        field_names = {f["name"] for f in contents["fields"]}
        self.assertIn("is_secret", field_names)
        self.assertIn("is_only_self", field_names)
        self.assertEqual(contents["confidence"], CONF_VERIFIED)

    def test_empty_project_yields_no_entities(self):
        p = SyntheticProject(self)
        result = BusinessSemanticsAdapter(p.root).analyze()
        self.assertEqual(result["entity_count"], 0)


class EndpointAuthTests(unittest.TestCase):
    def test_module_level_router_use_applies_admin_auth(self):
        p = SyntheticProject(self)
        p.route("server/routes/admin.js", (
            "const router = require('express').Router();\n"
            "const { authRequired, adminRequired } = require('../middleware/auth');\n"
            "router.use(authRequired, adminRequired);\n"
            "router.get('/stats', async (req, res) => {});\n"
        ))
        result = BusinessSemanticsAdapter(p.root).analyze()
        ep = next(e for e in result["endpoints"] if e["path"] == "/stats")
        self.assertEqual(ep["code_auth"], "admin")

    def test_inline_auth_required_detected(self):
        p = SyntheticProject(self)
        p.route("server/routes/contents.js", (
            "const { authRequired } = require('../middleware/auth');\n"
            "router.get('/', authRequired, async (req, res) => {});\n"
        ))
        result = BusinessSemanticsAdapter(p.root).analyze()
        ep = next(e for e in result["endpoints"] if e["path"] == "/")
        self.assertEqual(ep["code_auth"], "auth")

    def test_public_login_is_not_flagged(self):
        p = SyntheticProject(self)
        p.route("server/routes/auth.js", (
            "router.post('/login', async (req, res) => {});\n"
        ))
        result = BusinessSemanticsAdapter(p.root).analyze()
        # login is public-by-design, so no missing_auth discrepancy.
        self.assertFalse(any(d["kind"] == "missing_auth" for d in result["discrepancies"]))


class PermissionFieldDetectionTests(unittest.TestCase):
    def test_documented_field_without_code_reference_is_flagged(self):
        p = SyntheticProject(self)
        p.db_doc(DB_DOC)
        # code references is_secret but never is_only_self
        p.route("server/routes/contents.js", (
            "router.get('/', authRequired, async (req, res) => {\n"
            "  const q = 'SELECT * FROM contents WHERE is_secret = 0';\n"
            "});\n"
        ))
        result = BusinessSemanticsAdapter(p.root).analyze()
        unflagged = [d for d in result["discrepancies"] if d["kind"] == "documented_field_unimplemented"]
        self.assertEqual(len(unflagged), 1)
        self.assertIn("is_only_self", unflagged[0]["message"])
        self.assertNotIn("is_secret", unflagged[0]["message"])
        self.assertEqual(unflagged[0]["confidence"], CONF_INFERRED)

    def test_field_present_in_code_is_not_flagged(self):
        p = SyntheticProject(self)
        p.db_doc(DB_DOC)
        p.route("server/routes/contents.js", (
            "router.get('/', authRequired, async (req, res) => {\n"
            "  const a = is_secret;\n"
            "  const b = is_only_self;\n"
            "});\n"
        ))
        result = BusinessSemanticsAdapter(p.root).analyze()
        self.assertFalse(any(d["kind"] == "documented_field_unimplemented" for d in result["discrepancies"]))


# PRD that marks the "仅自己" visibility feature as done (✅, P0).
PRD_ONLY_SELF_DONE = """# 家庭APP需求文档

## 〇、当前开发进度

| 功能 | 状态 | 优先级 |
|------|------|--------|
| 仅自己 | ✅ | P0 |
| 秘密文件 | ✅ | P0 |
"""


class FeatureDocumentedDoneTests(unittest.TestCase):
    def test_prd_done_but_field_unreferenced_is_conflict(self):
        p = SyntheticProject(self)
        p.prd(PRD_ONLY_SELF_DONE)
        # code references is_secret but never is_only_self
        p.route("server/routes/contents.js", (
            "router.get('/', authRequired, async (req, res) => {\n"
            "  const q = 'SELECT * FROM contents WHERE is_secret = 0';\n"
            "});\n"
        ))
        result = BusinessSemanticsAdapter(p.root).analyze()
        hits = [d for d in result["discrepancies"]
                if d["kind"] == "feature_documented_done_but_unimplemented"]
        self.assertEqual(len(hits), 1)
        self.assertIn("仅自己", hits[0]["message"])
        self.assertIn("is_only_self", hits[0]["message"])
        self.assertEqual(hits[0]["confidence"], "CONFLICT")
        ev = hits[0]["evidence"][0]
        self.assertEqual(ev["field"], "is_only_self")
        self.assertEqual(ev["status"], "DONE")

    def test_prd_done_with_field_referenced_is_not_flagged(self):
        p = SyntheticProject(self)
        p.prd(PRD_ONLY_SELF_DONE)
        p.route("server/routes/contents.js", (
            "router.get('/', authRequired, async (req, res) => {\n"
            "  const a = is_only_self;\n"
            "  const b = is_secret;\n"
            "});\n"
        ))
        result = BusinessSemanticsAdapter(p.root).analyze()
        self.assertFalse(any(d["kind"] == "feature_documented_done_but_unimplemented"
                             for d in result["discrepancies"]))

    def test_prd_pending_feature_is_not_flagged(self):
        p = SyntheticProject(self)
        p.prd("""# 家庭APP需求文档

| 功能 | 状态 | 优先级 |
|------|------|--------|
| 仅自己 | ⏳ | P0 |
""")
        p.route("server/routes/contents.js", "router.get('/', authRequired, async (req, res) => {});\n")
        result = BusinessSemanticsAdapter(p.root).analyze()
        # PENDING is not a "documented-done-but-missing" claim; nothing to flag.
        self.assertFalse(any(d["kind"] == "feature_documented_done_but_unimplemented"
                             for d in result["discrepancies"]))


class FieldGapLocatorTests(unittest.TestCase):
    def test_missing_field_located_at_sibling_reference(self):
        p = SyntheticProject(self)
        p.db_doc(DB_DOC)
        p.route("server/routes/contents.js", (
            "router.get('/', authRequired, async (req, res) => {\n"
            "  let where = 'WHERE user_id = ? AND is_secret = 1';\n"
            "});\n"
        ))
        result = BusinessSemanticsAdapter(p.root).analyze()
        gaps = result["field_gaps"]
        self.assertEqual(len(gaps), 1)
        gap = gaps[0]
        self.assertEqual(gap["field"], "is_only_self")
        self.assertEqual(gap["entity"], "contents")
        # The sibling is_secret reference line is the anchor.
        self.assertTrue(any(a["line"] == 2 and "is_secret" in a["context"]
                            for a in gap["anchors"]))

    def test_table_never_referenced_reports_absent_anchor(self):
        p = SyntheticProject(self)
        p.db_doc(DB_DOC)
        # No code files at all -> contents table never touched.
        result = BusinessSemanticsAdapter(p.root).analyze()
        gaps = result["field_gaps"]
        self.assertTrue(len(gaps) >= 2)  # is_only_self and is_secret both missing
        only_self = next(g for g in gaps if g["field"] == "is_only_self")
        self.assertTrue(any("never referenced" in a["reason"] for a in only_self["anchors"]))

    def test_no_gaps_when_all_fields_referenced(self):
        p = SyntheticProject(self)
        p.db_doc(DB_DOC)
        p.route("server/routes/contents.js", (
            "router.get('/', authRequired, async (req, res) => {\n"
            "  const a = is_secret;\n"
            "  const b = is_only_self;\n"
            "});\n"
        ))
        result = BusinessSemanticsAdapter(p.root).analyze()
        self.assertEqual(result["field_gap_count"], 0)


class ResourceSafetyTests(unittest.TestCase):

    def test_outside_project_file_is_rejected(self):
        p = SyntheticProject(self)
        outside = p.root.parent / (p.root.name + "-outside.txt")
        outside.write_text("OUTSIDE_MARKER", encoding="utf-8")
        self.addCleanup(lambda: outside.unlink(missing_ok=True))
        adapter = BusinessSemanticsAdapter(p.root)
        with self.assertRaises(SemanticsBlocked) as blocked:
            adapter._read_text(outside)
        self.assertEqual(blocked.exception.code, "PATH_TRAVERSAL_REJECTED")

    def test_symlinked_file_is_rejected_before_read(self):
        p = SyntheticProject(self)
        outside = p.root.parent / (p.root.name + "-outside-file.txt")
        outside.write_text("OUTSIDE_MARKER", encoding="utf-8")
        self.addCleanup(lambda: outside.unlink(missing_ok=True))
        link = p.root / "linked.txt"
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError, ValueError):
            self.skipTest("OS denied symlink creation")
        adapter = BusinessSemanticsAdapter(p.root)
        with self.assertRaises(SemanticsBlocked) as blocked:
            adapter._read_text(link)
        self.assertEqual(blocked.exception.code, "REPARSE_POINT_REJECTED")

    def test_symlinked_directory_is_rejected_during_discovery(self):
        p = SyntheticProject(self)
        outside = p.root.parent / (p.root.name + "-outside-dir")
        outside.mkdir()
        (outside / "secret.js").write_text("const secret = true;\\n", encoding="utf-8")
        self.addCleanup(lambda: (outside / "secret.js").unlink(missing_ok=True))
        self.addCleanup(lambda: outside.rmdir())
        link = p.root / "external"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError, ValueError):
            self.skipTest("OS denied directory symlink creation")
        adapter = BusinessSemanticsAdapter(p.root)
        with self.assertRaises(SemanticsBlocked) as blocked:
            adapter._project_files()
        self.assertEqual(blocked.exception.code, "REPARSE_POINT_REJECTED")
    def test_repeated_file_reads_use_cached_verified_bytes(self):
        p = SyntheticProject(self)
        doc = p.root / "数据库设计文档.md"
        _write(doc, DB_DOC)
        adapter = BusinessSemanticsAdapter(p.root)
        first = adapter._read_text(doc)
        second = adapter._read_text(doc)
        self.assertTrue(first["read_ok"])
        self.assertEqual(first, second)
        self.assertEqual(adapter._file_count, 1)

    def test_oversized_business_input_blocks_instead_of_silent_skip(self):
        p = SyntheticProject(self)
        _write(p.root / "数据库设计文档.md", "x" * (1024 * 1024 + 1))
        with self.assertRaises(SemanticsBlocked) as blocked:
            BusinessSemanticsAdapter(p.root).analyze()
        self.assertEqual(blocked.exception.code, "FILE_SIZE_LIMIT_EXCEEDED")

    def test_business_scan_time_budget_fails_closed(self):
        from unittest.mock import patch
        p = SyntheticProject(self)
        with patch("usit.business_semantics.MAX_SCAN_SECONDS", -1):
            with self.assertRaises(SemanticsBlocked) as blocked:
                BusinessSemanticsAdapter(p.root).analyze()
        self.assertEqual(blocked.exception.code, "SCAN_TIME_BUDGET_EXCEEDED")

    def test_business_discovery_budget_fails_closed(self):
        from unittest.mock import patch
        p = SyntheticProject(self)
        p.route("server/routes/a.js", "router.get('/a', authRequired, () => {});")
        p.route("server/routes/b.js", "router.get('/b', authRequired, () => {});")
        with patch("usit.business_semantics.MAX_FILES", 1):
            with self.assertRaises(SemanticsBlocked) as blocked:
                BusinessSemanticsAdapter(p.root).analyze()
        self.assertEqual(blocked.exception.code, "FILE_BUDGET_EXCEEDED")

    def test_skipped_dependency_directories_do_not_consume_scan_budget(self):
        from unittest.mock import patch
        p = SyntheticProject(self)
        _write(p.root / "node_modules" / "large" / "index.js", "x" * 20)
        with patch("usit.business_semantics.MAX_FILES", 1):
            result = BusinessSemanticsAdapter(p.root).analyze()
        self.assertEqual(result["entity_count"], 0)


if __name__ == "__main__":
    unittest.main()
