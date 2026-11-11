from __future__ import annotations
import unittest
from usit.business_traceability import build_business_traceability
from usit.business_traceability_report import render_business_traceability_markdown


class BusinessTraceabilityTests(unittest.TestCase):
    def setUp(self):
        self.graph_hash = "a" * 64
        self.semantics = {"schema_version": "usit-business-semantics-v1", "features": [
            {"name": "查看照片", "source": "docs/requirements.md", "line": 12},
            {"name": "删除照片", "source": "docs/requirements.md", "line": 20},
        ]}
        self.graph = {"schema_version": "usit-graph-v1", "business_understanding": {
            "business_function_views": [{
                "business_function_id": "bf-list", "endpoint_id": "ep-list", "method": "GET",
                "endpoint": "/api/photos", "frontend_entrypoint_count": 1,
                "frontend_entrypoints": [{"source_file": "web/Photos.tsx", "event_line": 4,
                    "api_call_file": "web/api.ts", "api_call_line": 18}],
                "source": {"path": "server/routes.js", "line": 8},
                "handler": {"path": "server/photos.js", "line": 22},
                "call_chain": [{"call_site": {"path": "server/photos.js", "line": 25}}],
                "database_accesses": [{"path": "server/db.js", "line": 9}],
                "authorization": {"evidence": [{"path": "server/auth.js", "line": 3}]},
                "identity_resource_flow": [{"path": "server/db.js", "line": 9}],
                "stage_status": {"handler": "RESOLVED", "calls": "RESOLVED_STATIC_CHAIN",
                    "database": "LINKED", "authorization": "EVIDENCE_FOUND", "identity_resource": "EVIDENCE_FOUND"},
            }],
        }}
        self.mapping = {"schema_version": "usit-feature-function-mapping-v1",
            "source_graph_sha256": self.graph_hash, "mappings": [{
                "feature_name": "查看照片", "source_path": "docs/requirements.md", "source_line": 12,
                "decision": "APPROVED_LINK", "business_function_id": "bf-list",
                "reviewed_by": "reviewer-1", "reviewed_at": "2026-11-01T12:00:00+08:00",
                "rationale": "需求验收范围明确对应照片列表接口及页面入口。",
            }, {
                "feature_name": "删除照片", "source_path": "docs/requirements.md", "source_line": 20,
                "decision": "NEEDS_REVIEW", "reviewed_by": "reviewer-1",
                "reviewed_at": "2026-11-01T12:00:00+08:00", "rationale": "尚未确认业务入口。",
            }]}
        self.hashes = {"business_graph_sha256": self.graph_hash}

    def test_only_approved_exact_mapping_builds_cross_layer_traceability(self):
        plan = {"schema_version": "usit-business-test-plan-v1", "candidates": [
            {"test_id": "test-photo-list", "business_function_id": "bf-list"}]}
        evidence = {"schema_version": "usit-business-evidence-verification-v1",
            "business_function_evidence": [{"business_function_id": "bf-list",
                "test_execution_status": "PASSED", "code_coverage_status": "PARTIAL"}]}
        result = build_business_traceability(self.semantics, self.graph, self.mapping, plan, evidence, self.hashes)
        first = next(row for row in result["rows"] if row["feature_name"] == "查看照片")
        second = next(row for row in result["rows"] if row["feature_name"] == "删除照片")
        self.assertEqual(first["business_function_id"], "bf-list")
        self.assertEqual(first["traceability_status"], "TRACEABLE_WITH_RUNTIME_EVIDENCE")
        self.assertEqual(first["stage_status"]["business_function_to_frontend_entrypoint"], "LINKED")
        self.assertEqual(first["stage_status"]["api_to_backend_handler"], "RESOLVED")
        self.assertEqual(first["stage_status"]["test_execution"], "PASSED")
        self.assertEqual(first["test_candidate_ids"], ["test-photo-list"])
        self.assertEqual(second["traceability_status"], "REVIEW_REQUIRED")
        self.assertIsNone(second["business_function_id"])

    def test_structured_test_candidate_is_carried_into_requirement_traceability(self):
        plan = {
            "schema_version": "usit-business-test-plan-v1",
            "candidates": [{
                "test_id": "bt-api-123",
                "business_function_id": "bf-list",
                "category": "api_contract_verification",
                "target": "GET /api/photos",
                "purpose": "验证照片列表接口",
                "design_status": "CANDIDATE_NEEDS_REQUIREMENTS",
                "execution_status": "NOT_EXECUTED",
                "preconditions": ["隔离测试环境", "已批准验收标准"],
                "actions": [{
                    "step": 1,
                    "action": "发起已批准的列表请求",
                    "expected_result": "由负责人确认的结果",
                    "evidence_to_collect": ["请求/响应", "服务端日志"],
                }],
                "expected_result": "由负责人确认的结果",
                "expected_results_status": "OWNER_CONFIRMATION_REQUIRED",
                "evidence_requirements": [{"evidence_type": "RUNTIME_TEST_RESULT", "requirement": "保留原始报告"}],
                "evidence_refs": [{"path": "server/photos.py", "line": 10, "evidence_kind": "resolved_handler"}],
                "limitations": ["静态分析不证明运行时成功"],
            }],
        }
        result = build_business_traceability(
            self.semantics, self.graph, self.mapping,
            test_plan=plan, input_hashes=self.hashes,
        )
        linked = next(row for row in result["rows"] if row["feature_name"] == "查看照片")
        self.assertEqual(linked["test_candidate_ids"], ["bt-api-123"])
        self.assertEqual(linked["test_candidates"][0]["execution_status"], "NOT_EXECUTED")
        self.assertEqual(linked["test_candidates"][0]["expected_results_status"], "OWNER_CONFIRMATION_REQUIRED")
        markdown = render_business_traceability_markdown(result)
        self.assertIn("发起已批准的列表请求", markdown)
        self.assertIn("保留原始报告", markdown)
        self.assertIn("OWNER_CONFIRMATION_REQUIRED", markdown)

    def test_security_candidates_flow_into_explicitly_approved_requirement_trace(self):
        security_plan = {"schema_version": "usit-security-test-candidates-v1", "source_business_graph_sha256": self.graph_hash, "candidate_count": 1,
            "candidates": [{
                "test_id": "sec-cross-user-001", "business_function_id": "bf-list",
                "category": "security_cross_user_resource_isolation", "target": "GET /api/photos",
                "purpose": "验证跨用户资源隔离", "basis": "STATIC_INFERENCE",
                "design_status": "CANDIDATE_NEEDS_REQUIREMENTS", "execution_status": "NOT_EXECUTED",
                "preconditions": ["隔离测试环境"],
                "actions": [{"step": 1, "action": "使用用户 A 访问用户 B 资源",
                    "expected_result": "OWNER_CONFIRMATION_REQUIRED", "evidence_to_collect": ["响应", "审计日志"]}],
                "expected_results_status": "OWNER_CONFIRMATION_REQUIRED",
                "evidence_requirements": [{"evidence_type": "AUTHORIZATION_DECISION"}],
                "authorization_evidence": {"status": "DETECTED"},
                "database_access_evidence": [{"operation": "SELECT", "path": "db.py"}],
                "identity_dataflow_evidence": [{"kind": "request_identity"}],
                "call_chain_evidence": [{"steps": []}], "limitations": ["静态证据不是漏洞结论"],
            }]}
        result = build_business_traceability(self.semantics, self.graph, self.mapping,
            security_test_plan=security_plan, input_hashes=self.hashes)
        linked = next(row for row in result["rows"] if row["feature_name"] == "查看照片")
        self.assertEqual(linked["test_candidate_ids"], ["sec-cross-user-001"])
        self.assertEqual(linked["test_candidates"][0]["category"], "security_cross_user_resource_isolation")
        self.assertEqual(linked["test_candidates"][0]["execution_status"], "NOT_EXECUTED")
        self.assertEqual(linked["test_candidates"][0]["authorization_evidence"]["status"], "DETECTED")
        self.assertEqual(linked["test_candidates"][0]["expected_results_status"], "OWNER_CONFIRMATION_REQUIRED")
        self.assertEqual(result["unlinked_security_test_candidate_ids"], [])
        markdown = render_business_traceability_markdown(result)
        self.assertIn("验证跨用户资源隔离", markdown)
        self.assertIn("静态证据不是漏洞结论", markdown)

    def test_rejects_security_candidates_from_a_stale_graph(self):
        security_plan = {"schema_version": "usit-security-test-candidates-v1",
            "source_business_graph_sha256": "b" * 64, "candidates": []}
        with self.assertRaisesRegex(ValueError, "source_business_graph_sha256"):
            build_business_traceability(self.semantics, self.graph, self.mapping,
                security_test_plan=security_plan, input_hashes=self.hashes)

    def test_unlinked_security_candidate_is_reported_not_guessed(self):
        security_plan = {"schema_version": "usit-security-test-candidates-v1",
            "source_business_graph_sha256": self.graph_hash,
            "candidates": [{"test_id": "sec-unlinked", "business_function_id": None}]}
        result = build_business_traceability(self.semantics, self.graph, self.mapping,
            security_test_plan=security_plan, input_hashes=self.hashes)
        self.assertEqual(result["unlinked_security_test_candidate_ids"], ["sec-unlinked"])
        self.assertNotIn("sec-unlinked", [item for row in result["rows"] for item in row["test_candidate_ids"]])

    def test_security_candidate_with_unknown_function_id_is_reported_unlinked(self):
        security_plan = {"schema_version": "usit-security-test-candidates-v1",
            "source_business_graph_sha256": self.graph_hash,
            "candidates": [{"test_id": "sec-unknown-function", "business_function_id": "bf-missing"}]}
        result = build_business_traceability(self.semantics, self.graph, self.mapping,
            security_test_plan=security_plan, input_hashes=self.hashes)
        self.assertEqual(result["unlinked_security_test_candidate_ids"], ["sec-unknown-function"])
        self.assertNotIn("sec-unknown-function", [item for row in result["rows"] for item in row["test_candidate_ids"]])

    def test_no_mapping_never_uses_feature_name_or_module_to_guess(self):
        result = build_business_traceability(self.semantics, self.graph,
            {"schema_version": "usit-feature-function-mapping-v1", "source_graph_sha256": self.graph_hash, "mappings": []},
            input_hashes=self.hashes)
        self.assertEqual(result["unmapped_feature_count"], 2)
        self.assertTrue(all(row["business_function_id"] is None for row in result["rows"]))

    def test_rejects_stale_graph_hash_and_unknown_feature(self):
        stale = dict(self.mapping, source_graph_sha256="b" * 64)
        with self.assertRaisesRegex(ValueError, "does not match"):
            build_business_traceability(self.semantics, self.graph, stale, input_hashes=self.hashes)
        unknown = {"schema_version": "usit-feature-function-mapping-v1",
            "source_graph_sha256": self.graph_hash, "mappings": [dict(self.mapping["mappings"][0],
                feature_name="not in requirements")]}
        with self.assertRaisesRegex(ValueError, "unknown feature identity"):
            build_business_traceability(self.semantics, self.graph, unknown, input_hashes=self.hashes)

    def test_rejects_unreviewed_or_invalid_approved_mapping(self):
        invalid = {"schema_version": "usit-feature-function-mapping-v1",
            "source_graph_sha256": self.graph_hash, "mappings": [dict(self.mapping["mappings"][0], reviewed_by=" ")]}
        with self.assertRaisesRegex(ValueError, "reviewed_by"):
            build_business_traceability(self.semantics, self.graph, invalid, input_hashes=self.hashes)
        invalid["mappings"][0]["reviewed_by"] = "reviewer"
        invalid["mappings"][0]["business_function_id"] = ""
        with self.assertRaisesRegex(ValueError, "requires business_function_id"):
            build_business_traceability(self.semantics, self.graph, invalid, input_hashes=self.hashes)

    def test_reports_missing_function_target_and_unlinked_test_ids(self):
        mapping = {"schema_version": "usit-feature-function-mapping-v1",
            "source_graph_sha256": self.graph_hash, "mappings": [dict(self.mapping["mappings"][0],
                business_function_id="missing-function")]}
        plan = {"schema_version": "usit-business-test-plan-v1", "candidates": [
            {"test_id": "test-unknown", "business_function_id": "missing-function"}]}
        result = build_business_traceability(self.semantics, self.graph, mapping, plan, input_hashes=self.hashes)
        mapped_row = next(row for row in result["rows"] if row["feature_name"] == "查看照片")
        self.assertEqual(mapped_row["traceability_status"], "MAPPING_TARGET_NOT_FOUND_IN_GRAPH")
        self.assertEqual(result["unlinked_test_design_function_ids"], ["missing-function"])

    def test_rejects_duplicate_feature_identity_and_duplicate_mapping(self):
        duplicate_semantics = {"features": self.semantics["features"] + [dict(self.semantics["features"][0])]}
        with self.assertRaisesRegex(ValueError, "ambiguous duplicate"):
            build_business_traceability(duplicate_semantics, self.graph, self.mapping, input_hashes=self.hashes)
        duplicate_mapping = dict(self.mapping, mappings=self.mapping["mappings"] + [dict(self.mapping["mappings"][0])])
        with self.assertRaisesRegex(ValueError, "duplicate mapping"):
            build_business_traceability(self.semantics, self.graph, duplicate_mapping, input_hashes=self.hashes)

    def test_cli_writes_parseable_traceability_matrix_and_hashes(self):
        import hashlib
        import json
        import tempfile
        from contextlib import redirect_stdout
        from io import StringIO
        from pathlib import Path
        from usit.cli import main
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            sem_path, graph_path = base / "sem.json", base / "graph.json"
            mapping_path, out_path, md_path, runtime_path, security_path = (
                base / "mapping.json", base / "trace.json", base / "trace.md",
                base / "runtime.json", base / "security.json"
            )
            sem_path.write_text(json.dumps(self.semantics), encoding="utf-8")
            graph_bytes = json.dumps(self.graph).encode("utf-8")
            graph_path.write_bytes(graph_bytes)
            graph_hash = hashlib.sha256(graph_bytes).hexdigest()
            mapping = dict(self.mapping, source_graph_sha256=graph_hash)
            mapping_path.write_text(json.dumps(mapping), encoding="utf-8")
            security_path.write_text(json.dumps({"schema_version": "usit-security-test-candidates-v1",
                "source_business_graph_sha256": graph_hash,
                "candidates": [{"test_id": "sec-cli-1", "business_function_id": "bf-list",
                    "category": "security_cross_user_resource_isolation", "purpose": "验证跨用户资源隔离",
                    "design_status": "CANDIDATE_NEEDS_REQUIREMENTS", "execution_status": "NOT_EXECUTED",
                    "expected_results_status": "OWNER_CONFIRMATION_REQUIRED",
                    "authorization_evidence": {"status": "DETECTED"}}]}), encoding="utf-8")
            runtime_path.write_text(json.dumps({
                "schema_version": "usit-runtime-evidence-validation-v1",
                "assessment_status": "VALIDATED_EVENT_STRUCTURE",
                "source_hashes": {"business_graph_sha256": graph_hash, "runtime_evidence_sha256": "c" * 64},
                "business_function_ids_observed": ["bf-list"],
                "correlations": [{"correlation_id": "corr-cli", "business_function_ids": ["bf-list"]}],
            }), encoding="utf-8")
            with redirect_stdout(StringIO()):
                code = main(["trace-business", "--semantics", str(sem_path), "--graph", str(graph_path),
                    "--mapping", str(mapping_path), "--runtime-evidence", str(runtime_path),
                    "--security-test-plan", str(security_path),
                    "--out", str(out_path), "--out-md", str(md_path)])
            self.assertEqual(code, 0)
            report = json.loads(out_path.read_text(encoding="utf-8"))
            self.assertEqual(report["schema_version"], "usit-business-traceability-matrix-v1")
            self.assertEqual(report["input_hashes"]["business_graph_sha256"], hashlib.sha256(graph_bytes).hexdigest())
            self.assertEqual(report["reviewed_mapping_count"], 2)
            self.assertEqual(report["input_hashes"]["runtime_evidence_validation_sha256"], hashlib.sha256(runtime_path.read_bytes()).hexdigest())
            self.assertEqual(report["input_hashes"]["security_test_plan_sha256"], hashlib.sha256(security_path.read_bytes()).hexdigest())
            linked = next(row for row in report["rows"] if row["feature_name"] == "查看照片")
            self.assertIn("sec-cli-1", linked["test_candidate_ids"])
            self.assertEqual(next(row for row in report["rows"] if row["feature_name"] == "查看照片")["stage_status"]["runtime_observation"], "RUNTIME_OBSERVED")
            markdown = md_path.read_text(encoding="utf-8")
            self.assertIn("# USIT 业务需求跨层追溯报告", markdown)
            self.assertIn("查看照片", markdown)
            self.assertIn("不证明运行时页面可达", markdown)

    def test_rejects_invalid_review_timestamp_and_reports_invalid_runtime_state(self):
        invalid = dict(self.mapping, mappings=[dict(self.mapping["mappings"][0], reviewed_at="yesterday")])
        with self.assertRaisesRegex(ValueError, "ISO-8601"):
            build_business_traceability(self.semantics, self.graph, invalid, input_hashes=self.hashes)
        evidence = {"schema_version": "usit-business-evidence-verification-v1",
            "business_function_evidence": [{"business_function_id": "bf-list",
                "test_execution_status": "MAYBE", "code_coverage_status": "COVERED"}]}
        result = build_business_traceability(self.semantics, self.graph, self.mapping,
            evidence_matrix=evidence, input_hashes=self.hashes)
        mapped_row = next(row for row in result["rows"] if row["feature_name"] == "查看照片")
        self.assertEqual(mapped_row["stage_status"]["test_execution"], "INVALID")
        self.assertEqual(result["invalid_runtime_status_function_ids"], ["bf-list"])

    def test_incomplete_test_report_is_distinguished_from_verified_execution(self):
        evidence = {
            "schema_version": "usit-business-evidence-verification-v1",
            "business_function_evidence": [{
                "business_function_id": "bf-list",
                "test_execution_status": "UNKNOWN",
                "code_coverage_status": "NOT_MEASURED",
                "verification_conclusion": "TEST_EVIDENCE_INCONCLUSIVE",
                "evidence_conflict": False,
                "test_execution_detail": {
                    "execution_status_reason": "MAPPED_TEST_CASES_MISSING_FROM_REPORT",
                    "expected_mapped_test_case_count": 2,
                    "reported_mapped_test_case_count": 1,
                    "mapped_case_completeness": "INCOMPLETE",
                    "mapped_cases_missing_from_report": [
                        "tests.PhotoTests::test_upload_permissions"
                    ],
                    "mapped_test_cases": [{
                        "test_case_id": "tests.PhotoTests::test_upload",
                        "status": "PASSED",
                    }],
                },
            }],
        }
        report = build_business_traceability(
            self.semantics, self.graph, self.mapping,
            evidence_matrix=evidence, input_hashes=self.hashes,
        )
        linked = next(row for row in report["rows"] if row["feature_name"] == "查看照片")
        self.assertEqual(linked["stage_status"]["test_execution"], "UNKNOWN")
        self.assertEqual(linked["stage_status"]["test_evidence_integrity"], "INCOMPLETE")
        self.assertEqual(linked["traceability_status"], "TRACEABLE_WITH_INCONCLUSIVE_TEST_EVIDENCE")
        markdown = render_business_traceability_markdown(report)
        self.assertIn("tests.PhotoTests::test_upload", markdown)
        self.assertIn("tests.PhotoTests::test_upload_permissions", markdown)

    def test_test_source_binding_and_runner_locator_stages_reach_traceability(self):
        evidence = {
            "schema_version": "usit-business-evidence-verification-v1",
            "business_function_evidence": [{
                "business_function_id": "bf-list",
                "test_execution_status": "PASSED",
                "code_coverage_status": "NOT_MEASURED",
                "test_source_binding_status": "SOURCE_BINDING_NOT_VERIFIED",
                "test_source_snapshot_binding_status": "ALL_SNAPSHOT_ENTRIES_MATCHED_UNAUTHENTICATED",
                "test_source_report_locator_status": "ALL_REPORT_LOCATORS_MATCHED_UNAUTHENTICATED",
                "test_execution_detail": {
                    "mapped_case_completeness": "COMPLETE",
                    "mapped_test_cases": [{
                        "test_case_id": "tests/test_photos.py::test_list",
                        "status": "PASSED",
                        "test_source_binding_status": "TEST_SOURCE_BINDING_NOT_CHECKED",
                        "test_source_snapshot_binding_status": "TEST_SOURCE_SNAPSHOT_ENTRY_MATCHED",
                        "test_source_report_locator_status": "REPORT_SOURCE_LOCATOR_MATCHED_UNAUTHENTICATED",
                    }],
                },
            }],
        }
        report = build_business_traceability(
            self.semantics, self.graph, self.mapping,
            evidence_matrix=evidence, input_hashes=self.hashes,
        )
        linked = next(row for row in report["rows"] if row["feature_name"] == "查看照片")
        self.assertEqual(linked["stage_status"]["test_execution"], "PASSED")
        self.assertEqual(linked["stage_status"]["test_source_binding"], "SOURCE_BINDING_NOT_VERIFIED")
        self.assertEqual(
            linked["stage_status"]["test_source_snapshot_binding"],
            "ALL_SNAPSHOT_ENTRIES_MATCHED_UNAUTHENTICATED",
        )
        self.assertEqual(
            linked["stage_status"]["test_source_report_locator"],
            "ALL_REPORT_LOCATORS_MATCHED_UNAUTHENTICATED",
        )
        markdown = render_business_traceability_markdown(report)
        self.assertIn("test_source_binding", markdown)
        self.assertIn("SOURCE_BINDING_NOT_VERIFIED", markdown)

    def test_testcase_evidence_and_integrity_conflicts_reach_traceability_report(self):
        evidence = {
            "schema_version": "usit-business-evidence-verification-v1",
            "business_function_evidence": [{
                "business_function_id": "bf-list",
                "test_execution_status": "UNKNOWN",
                "reported_test_execution_status": "PASSED",
                "code_coverage_status": "NOT_MEASURED",
                "verification_conclusion": "EVIDENCE_CONFLICT",
                "evidence_conflict": True,
                "test_execution_detail": {
                    "execution_status_reason": "MAPPED_TEST_CASES_MISSING_FROM_REPORT",
                    "expected_mapped_test_case_count": 2,
                    "reported_mapped_test_case_count": 1,
                    "mapped_case_completeness": "INCOMPLETE",
                    "mapped_cases_missing_from_report": [
                        "tests.PhotoTests::test_upload_permissions"
                    ],
                    "mapped_test_cases": [{
                        "test_case_id": "tests.PhotoTests::test_upload",
                        "status": "PASSED",
                    }],
                },
            }],
        }
        report = build_business_traceability(
            self.semantics, self.graph, self.mapping,
            evidence_matrix=evidence, input_hashes=self.hashes,
        )
        linked = next(row for row in report["rows"] if row["feature_name"] == "查看照片")
        self.assertEqual(linked["stage_status"]["test_execution"], "UNKNOWN")
        self.assertEqual(linked["stage_status"]["test_evidence_integrity"], "EVIDENCE_CONFLICT")
        self.assertEqual(linked["traceability_status"], "EVIDENCE_CONFLICT")
        markdown = render_business_traceability_markdown(report)
        self.assertIn("tests.PhotoTests::test_upload", markdown)
        self.assertIn("tests.PhotoTests::test_upload_permissions", markdown)
        self.assertIn("MAPPED_TEST_CASES_MISSING_FROM_REPORT", markdown)

    def test_markdown_renderer_tolerates_malformed_optional_candidate_collections(self):
        report = build_business_traceability(
            self.semantics, self.graph,
            {"schema_version": "usit-feature-function-mapping-v1",
             "source_graph_sha256": self.graph_hash, "mappings": []},
            input_hashes=self.hashes,
        )
        report["rows"][0]["test_candidates"] = [{
            "test_id": "malformed-candidate",
            "preconditions": None,
            "actions": "not-a-list",
            "evidence_requirements": None,
        }]
        report["rows"][0]["test_candidate_ids"] = None
        markdown = render_business_traceability_markdown(report)
        self.assertIn("malformed-candidate", markdown)
        self.assertIn("测试设计候选详情", markdown)

    def test_markdown_renderer_rejects_wrong_schema_and_escapes_cells(self):
        report = build_business_traceability(self.semantics, self.graph,
            {"schema_version": "usit-feature-function-mapping-v1",
             "source_graph_sha256": self.graph_hash, "mappings": []},
            input_hashes=self.hashes)
        report["rows"][0]["feature_name"] = "A|B"
        markdown = render_business_traceability_markdown(report)
        self.assertIn(r"A\|B", markdown)
        with self.assertRaisesRegex(ValueError, "usit-business-traceability-matrix-v1"):
            render_business_traceability_markdown({"schema_version": "wrong"})

    def test_runtime_observation_is_separate_from_test_execution_and_requires_explicit_function_id(self):
        runtime = {
            "schema_version": "usit-runtime-evidence-validation-v1",
            "assessment_status": "VALIDATED_EVENT_STRUCTURE",
            "source_hashes": {"business_graph_sha256": self.graph_hash, "runtime_evidence_sha256": "b" * 64},
            "business_function_ids_observed": ["bf-list"],
            "correlations": [{"correlation_id": "corr-1", "business_function_ids": ["bf-list"],
                "runtime_observation_status": "RUNTIME_OBSERVED"}],
        }
        result = build_business_traceability(self.semantics, self.graph, self.mapping,
            input_hashes=self.hashes, runtime_evidence_validation=runtime)
        linked = next(row for row in result["rows"] if row["feature_name"] == "查看照片")
        unreviewed = next(row for row in result["rows"] if row["feature_name"] == "删除照片")
        self.assertEqual(linked["stage_status"]["runtime_observation"], "RUNTIME_OBSERVED")
        self.assertEqual(linked["stage_status"]["test_execution"], "NOT_MEASURED")
        self.assertEqual(linked["traceability_status"], "TRACEABLE_WITH_RUNTIME_OBSERVATION")
        self.assertEqual(linked["runtime_observation"]["correlation_ids"], ["corr-1"])
        self.assertEqual(linked["runtime_observation"]["correlations"][0]["parent_topology_status"], "NOT_PROVIDED")
        self.assertEqual(unreviewed["stage_status"]["runtime_observation"], "NOT_OBSERVED")
        self.assertIsNone(unreviewed["business_function_id"])

    def test_runtime_correlation_topology_is_carried_by_explicit_function_id_only(self):
        runtime = {
            "schema_version": "usit-runtime-evidence-validation-v1",
            "assessment_status": "VALIDATED_EVENT_STRUCTURE",
            "source_hashes": {"business_graph_sha256": self.graph_hash, "runtime_evidence_sha256": "b" * 64},
            "business_function_ids_observed": ["bf-list"],
            "correlations": [{
                "correlation_id": "corr-chain",
                "business_function_ids": ["bf-list"],
                "event_count": 3,
                "observed_stages": ["UI_ACTION", "HTTP_REQUEST", "HTTP_RESPONSE"],
                "observed_event_ids_by_timestamp": ["evt-1", "evt-2", "evt-3"],
                "explicit_parent_edges": [
                    {"parent_event_id": "evt-1", "child_event_id": "evt-2"},
                    {"parent_event_id": "evt-2", "child_event_id": "evt-3"},
                ],
                "parent_topology_status": "LINEAR_CHAIN",
                "parent_link_coverage_ratio": 1.0,
                "root_event_ids": ["evt-1"],
                "leaf_event_ids": ["evt-3"],
                "branching_parent_event_ids": [],
                "isolated_event_ids": [],
                "cycle_event_ids": [],
            }],
        }
        result = build_business_traceability(
            self.semantics, self.graph, self.mapping,
            input_hashes=self.hashes, runtime_evidence_validation=runtime,
        )
        linked = next(row for row in result["rows"] if row["feature_name"] == "查看照片")
        chain = linked["runtime_observation"]["correlations"][0]
        self.assertEqual(chain["correlation_id"], "corr-chain")
        self.assertEqual(chain["parent_topology_status"], "LINEAR_CHAIN")
        self.assertEqual(chain["explicit_parent_edges"][0],
                         {"parent_event_id": "evt-1", "child_event_id": "evt-2"})
        self.assertEqual(chain["observed_event_ids_by_timestamp"], ["evt-1", "evt-2", "evt-3"])
        self.assertIn("时间排序不代表因果", chain["interpretation"])
        markdown = render_business_traceability_markdown(result)
        self.assertIn("LINEAR_CHAIN", markdown)
        self.assertIn("事件审阅顺序（非因果）", markdown)
        self.assertIn("采集端声明父子边：evt-1 → evt-2", markdown)

    def test_shared_correlation_does_not_attribute_all_stages_to_each_function(self):
        graph = {
            **self.graph,
            "business_understanding": {
                **self.graph["business_understanding"],
                "business_function_views": [
                    *self.graph["business_understanding"]["business_function_views"],
                    {"business_function_id": "bf-delete"},
                ],
            },
        }
        runtime = {
            "schema_version": "usit-runtime-evidence-validation-v1",
            "assessment_status": "VALIDATED_EVENT_STRUCTURE",
            "source_hashes": {"business_graph_sha256": self.graph_hash},
            "business_function_ids_observed": ["bf-list", "bf-delete"],
            "correlations": [{
                "correlation_id": "corr-shared",
                "business_function_ids": ["bf-list", "bf-delete"],
                "event_count": 3,
                "observed_stages": ["ui", "http_request", "backend"],
                "observed_event_ids_by_timestamp": ["evt-1", "evt-2", "evt-3"],
                "explicit_parent_edges": [
                    {"parent_event_id": "evt-1", "child_event_id": "evt-2"},
                    {"parent_event_id": "evt-2", "child_event_id": "evt-3"},
                ],
                "parent_topology_status": "LINEAR_CHAIN",
                "parent_link_coverage_ratio": 1.0,
                "root_event_ids": ["evt-1"],
                "leaf_event_ids": ["evt-3"],
                "branching_parent_event_ids": [],
                "isolated_event_ids": [],
                "cycle_event_ids": [],
            }],
        }
        result = build_business_traceability(
            self.semantics, graph, self.mapping,
            input_hashes=self.hashes, runtime_evidence_validation=runtime,
        )
        linked = next(row for row in result["rows"] if row["feature_name"] == "查看照片")
        shared = linked["runtime_observation"]["correlations"][0]
        self.assertEqual(shared["function_attribution_status"],
                         "SHARED_CORRELATION_EVENT_OWNERSHIP_UNRESOLVED")
        self.assertEqual(shared["function_ids_in_correlation"], ["bf-list", "bf-delete"])
        self.assertEqual(shared["observed_stages"], [])
        self.assertEqual(shared["observed_event_ids_by_timestamp"], [])
        self.assertEqual(shared["explicit_parent_edges"], [])
        self.assertEqual(shared["parent_topology_status"], "NOT_ATTRIBUTABLE_ACROSS_FUNCTIONS")
        self.assertIn("没有逐事件功能归属", shared["interpretation"])
        markdown = render_business_traceability_markdown(result)
        self.assertIn("SHARED_CORRELATION_EVENT_OWNERSHIP_UNRESOLVED", markdown)
        self.assertIn("bf-list, bf-delete", markdown)

    def test_rejects_malformed_runtime_correlation_parent_edges(self):
        runtime = {
            "schema_version": "usit-runtime-evidence-validation-v1",
            "assessment_status": "VALIDATED_EVENT_STRUCTURE",
            "source_hashes": {"business_graph_sha256": self.graph_hash},
            "business_function_ids_observed": ["bf-list"],
            "correlations": [{
                "correlation_id": "corr-invalid",
                "business_function_ids": ["bf-list"],
                "explicit_parent_edges": [{"parent_event_id": "evt-1"}],
            }],
        }
        with self.assertRaisesRegex(ValueError, "malformed explicit_parent_edges"):
            build_business_traceability(
                self.semantics, self.graph, self.mapping,
                input_hashes=self.hashes, runtime_evidence_validation=runtime,
            )

    def test_global_runtime_conflict_is_not_reported_as_no_observation(self):
        runtime = {
            "schema_version": "usit-runtime-evidence-validation-v1",
            "assessment_status": "EVIDENCE_CONFLICT",
            "source_hashes": {"business_graph_sha256": self.graph_hash},
            "business_function_ids_observed": [],
            "correlations": [],
        }
        result = build_business_traceability(self.semantics, self.graph, self.mapping,
            input_hashes=self.hashes, runtime_evidence_validation=runtime)
        linked = next(row for row in result["rows"] if row["feature_name"] == "查看照片")
        self.assertEqual(linked["stage_status"]["runtime_observation"], "EVIDENCE_CONFLICT")
        self.assertEqual(linked["traceability_status"], "EVIDENCE_CONFLICT")

    def test_runtime_observed_function_ids_must_match_explicit_correlation_links(self):
        runtime = {
            "schema_version": "usit-runtime-evidence-validation-v1",
            "assessment_status": "VALIDATED_EVENT_STRUCTURE",
            "source_hashes": {"business_graph_sha256": self.graph_hash},
            "business_function_ids_observed": ["bf-list"],
            "correlations": [{"correlation_id": "corr-no-function", "business_function_ids": []}],
        }
        with self.assertRaisesRegex(ValueError, "does not match.*correlations"):
            build_business_traceability(
                self.semantics, self.graph, self.mapping,
                input_hashes=self.hashes, runtime_evidence_validation=runtime,
            )

    def test_runtime_observed_function_ids_reject_duplicates_and_unknown_ids(self):
        base = {
            "schema_version": "usit-runtime-evidence-validation-v1",
            "assessment_status": "VALIDATED_EVENT_STRUCTURE",
            "source_hashes": {"business_graph_sha256": self.graph_hash},
            "correlations": [{"correlation_id": "corr-list", "business_function_ids": ["bf-list"]}],
        }
        for ids, message in (
            (["bf-list", "bf-list"], "duplicate business_function_ids_observed"),
            (["bf-missing"], "unknown graph function IDs"),
        ):
            runtime = dict(base, business_function_ids_observed=ids)
            with self.subTest(ids=ids):
                with self.assertRaisesRegex(ValueError, message):
                    build_business_traceability(
                        self.semantics, self.graph, self.mapping,
                        input_hashes=self.hashes, runtime_evidence_validation=runtime,
                    )

    def test_runtime_evidence_graph_hash_mismatch_is_rejected(self):
        runtime = {
            "schema_version": "usit-runtime-evidence-validation-v1",
            "source_hashes": {"business_graph_sha256": "0" * 64},
            "business_function_ids_observed": [],
            "correlations": [],
        }
        with self.assertRaisesRegex(ValueError, "does not match"):
            build_business_traceability(self.semantics, self.graph, self.mapping,
                input_hashes=self.hashes, runtime_evidence_validation=runtime)

    def test_rejects_invalid_runtime_evidence_shape(self):
        evidence = {"schema_version": "usit-business-evidence-verification-v1",
            "business_function_evidence": [{"business_function_id": "bf-list"}, {"business_function_id": "bf-list"}]}
        with self.assertRaisesRegex(ValueError, "duplicate runtime evidence"):
            build_business_traceability(self.semantics, self.graph, self.mapping, evidence_matrix=evidence,
                input_hashes=self.hashes)

    def test_runtime_unknown_function_conflict_is_preserved_in_traceability_and_markdown(self):
        runtime = {
            "schema_version": "usit-runtime-evidence-validation-v1",
            "assessment_status": "EVIDENCE_CONFLICT",
            "source_hashes": {"business_graph_sha256": self.graph_hash},
            "business_function_ids_observed": [],
            "correlations": [{"correlation_id": "corr-unknown", "business_function_ids": ["bf-missing"],
                "observed_event_ids_by_timestamp": ["evt-unknown"]}],
            "evidence_conflicts": [{"kind": "UNKNOWN_BUSINESS_FUNCTION", "event_id": "evt-unknown",
                "business_function_id": "bf-missing"}],
        }
        result = build_business_traceability(self.semantics, self.graph, self.mapping,
            input_hashes=self.hashes, runtime_evidence_validation=runtime)
        self.assertEqual(result["runtime_evidence_conflict_count"], 1)
        self.assertEqual(result["unknown_runtime_function_ids"], ["bf-missing"])
        self.assertEqual(result["runtime_evidence_conflicts"][0]["event_id"], "evt-unknown")
        self.assertEqual(result["runtime_evidence_conflicts"][0]["business_function_id"], "bf-missing")
        markdown = render_business_traceability_markdown(result)
        self.assertIn("UNKNOWN_BUSINESS_FUNCTION", markdown)
        self.assertIn("evt-unknown", markdown)
        self.assertIn("bf-missing", markdown)

    def test_runtime_evidence_conflicts_default_to_empty_and_malformed_conflicts_rejected(self):
        runtime = {
            "schema_version": "usit-runtime-evidence-validation-v1",
            "assessment_status": "VALIDATED_EVENT_STRUCTURE",
            "source_hashes": {"business_graph_sha256": self.graph_hash},
            "business_function_ids_observed": ["bf-list"],
            "correlations": [{"correlation_id": "corr-clean", "business_function_ids": ["bf-list"]}],
        }
        result = build_business_traceability(self.semantics, self.graph, self.mapping,
            input_hashes=self.hashes, runtime_evidence_validation=runtime)
        self.assertEqual(result["runtime_evidence_conflicts"], [])
        self.assertEqual(result["runtime_evidence_conflict_count"], 0)
        malformed = dict(runtime, evidence_conflicts=[{"kind": 7}])
        with self.assertRaisesRegex(ValueError, "evidence_conflicts\\[0\\] is malformed"):
            build_business_traceability(self.semantics, self.graph, self.mapping,
                input_hashes=self.hashes, runtime_evidence_validation=malformed)

    # ---- B1: G3 shared-correlation row cap ----
    def test_shared_correlation_caps_row_below_runtime_evidence(self):
        graph = {
            **self.graph,
            "business_understanding": {
                **self.graph["business_understanding"],
                "business_function_views": [
                    *self.graph["business_understanding"]["business_function_views"],
                    {"business_function_id": "bf-delete"},
                ],
            },
        }
        runtime = {
            "schema_version": "usit-runtime-evidence-validation-v1",
            "assessment_status": "VALIDATED_EVENT_STRUCTURE",
            "source_hashes": {"business_graph_sha256": self.graph_hash},
            "business_function_ids_observed": ["bf-list", "bf-delete"],
            "correlations": [{
                "correlation_id": "corr-shared",
                "business_function_ids": ["bf-list", "bf-delete"],
                "event_count": 3,
                "observed_stages": ["ui", "http_request", "backend"],
                "observed_event_ids_by_timestamp": ["evt-1", "evt-2", "evt-3"],
                "explicit_parent_edges": [
                    {"parent_event_id": "evt-1", "child_event_id": "evt-2"},
                    {"parent_event_id": "evt-2", "child_event_id": "evt-3"},
                ],
                "parent_topology_status": "LINEAR_CHAIN",
                "parent_link_coverage_ratio": 1.0,
                "root_event_ids": ["evt-1"],
                "leaf_event_ids": ["evt-3"],
                "branching_parent_event_ids": [],
                "isolated_event_ids": [],
                "cycle_event_ids": [],
            }],
        }
        evidence = {"schema_version": "usit-business-evidence-verification-v1",
            "business_function_evidence": [{"business_function_id": "bf-list",
                "test_execution_status": "PASSED", "code_coverage_status": "PARTIAL"}]}
        result = build_business_traceability(
            self.semantics, graph, self.mapping, evidence_matrix=evidence,
            input_hashes=self.hashes, runtime_evidence_validation=runtime)
        linked = next(row for row in result["rows"] if row["feature_name"] == "查看照片")
        # The shared correlation's observation cannot be attributed to
        # this function alone, so the row must not rise to the runtime
        # evidence state even though its tests passed.
        self.assertEqual(linked["stage_status"]["runtime_observation"],
                         "SHARED_CORRELATION_EVENT_OWNERSHIP_UNRESOLVED")
        self.assertEqual(linked["traceability_status"],
                         "TRACEABLE_WITH_INCONCLUSIVE_TEST_EVIDENCE")
        self.assertNotEqual(linked["traceability_status"],
                            "TRACEABLE_WITH_RUNTIME_EVIDENCE")
        markdown = render_business_traceability_markdown(result)
        self.assertIn("SHARED_CORRELATION_EVENT_OWNERSHIP_UNRESOLVED", markdown)

    # ---- B1: G2 verifier consistency passes through to traceability ----
    def test_runtime_static_mismatch_reaches_traceability_and_markdown(self):
        runtime = {
            "schema_version": "usit-runtime-evidence-validation-v1",
            "assessment_status": "VALIDATED_EVENT_STRUCTURE",
            "source_hashes": {"business_graph_sha256": self.graph_hash,
                              "runtime_evidence_sha256": "b" * 64},
            "business_function_ids_observed": ["bf-list"],
            "correlations": [{"correlation_id": "corr-1", "business_function_ids": ["bf-list"],
                "runtime_observation_status": "RUNTIME_OBSERVED"}],
        }
        evidence = {"schema_version": "usit-business-evidence-verification-v1",
            "business_function_evidence": [{
                "business_function_id": "bf-list",
                "test_execution_status": "PASSED", "code_coverage_status": "PARTIAL",
                "verification_conclusion": "EVIDENCE_CONFLICT", "evidence_conflict": True,
                "runtime_consistency_status": "MISMATCH",
                "runtime_consistency_detail": {
                    "compared_event_count": 1, "matched_event_count": 0,
                    "mismatches": [{"event_id": "evt-9", "field": "route_template",
                                    "expected": "/api/photos", "actual": "/api/photos/123"}]},
            }]}
        result = build_business_traceability(
            self.semantics, self.graph, self.mapping, evidence_matrix=evidence,
            input_hashes=self.hashes, runtime_evidence_validation=runtime)
        linked = next(row for row in result["rows"] if row["feature_name"] == "查看照片")
        self.assertEqual(linked["stage_status"]["runtime_observation"], "EVIDENCE_CONFLICT")
        self.assertEqual(linked["runtime_observation"]["consistency"], "MISMATCH")
        self.assertEqual(linked["traceability_status"], "EVIDENCE_CONFLICT")
        markdown = render_business_traceability_markdown(result)
        self.assertIn("evt-9", markdown)
        self.assertIn("/api/photos/123", markdown)

    # ---- B1: G6 parser scope annotation + drift test ----
    def test_every_row_carries_stage_parser_scope(self):
        result = build_business_traceability(self.semantics, self.graph, self.mapping,
            input_hashes=self.hashes)
        self.assertTrue(result["rows"])
        for row in result["rows"]:
            scope = row["stage_parser_scope"]
            self.assertEqual(scope["business_function_to_frontend_entrypoint"], ["JSX_TSX_ONLY"])
            self.assertEqual(scope["business_function_to_api_endpoint"], ["CALL_GRAPH_DEPTH_8"])
            self.assertEqual(scope["api_to_backend_handler"], ["HANDLER_UNIQUE_SYMBOL_ONLY"])
            self.assertEqual(scope["backend_call_chain"], ["CALL_GRAPH_DEPTH_8"])
            self.assertEqual(scope["backend_to_database"], ["SQL_SINGLE_LINE_LITERAL_ONLY"])
            self.assertEqual(scope["authorization_evidence"], ["AUTHORIZATION_LIMITED_PATTERNS_ONLY"])
            self.assertEqual(scope["identity_resource_evidence"], ["SAME_LINE_IDENTITY_ONLY"])
        markdown = render_business_traceability_markdown(result)
        self.assertIn("解析口径", markdown)
        self.assertIn("JSX_TSX_ONLY", markdown)

    def test_parser_scope_limitation_text_matches_real_graph_limitations(self):
        from usit.business_graph import project_business_graph
        from usit.business_traceability import PARSER_SCOPE_LIMITATION_TEXT, STAGE_PARSER_SCOPE
        graph = {"schema_version": "usit-graph-v1", "nodes": [
            {"id": "file:docs/requirements.md", "type": "file",
             "label": "docs/requirements.md", "evidence": {"path": "docs/requirements.md"}},
        ], "edges": []}
        semantics = {"features": [{"name": "查看照片", "source": "docs/requirements.md", "line": 12}]}
        projected = project_business_graph(graph, semantics)
        limitations = projected["business_understanding"]["limitations"]
        for code, texts in PARSER_SCOPE_LIMITATION_TEXT.items():
            for text in texts:
                self.assertIn(text, limitations,
                              f"parser scope {code} text drifted from graph limitations")
        for stage, codes in STAGE_PARSER_SCOPE.items():
            for code in codes:
                self.assertIn(code, PARSER_SCOPE_LIMITATION_TEXT,
                              f"stage {stage} code {code} has no limitation text mapping")


if __name__ == "__main__":
    unittest.main()
