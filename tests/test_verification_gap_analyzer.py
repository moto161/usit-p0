from __future__ import annotations
import json
import unittest
from usit.verification_gap_analyzer import build_verification_gap_backlog


class VerificationGapAnalyzerTests(unittest.TestCase):
    def setUp(self):
        self.semantics={
            "schema_version":"usit-business-semantics-v1",
            "features":[{"name":"照片上传","priority":"P0","source":"docs/PRD.md","line":8}],
            "endpoint_contract_alignment":[
                {"status":"DOCUMENTED_NOT_FOUND_IN_CODE","method":"POST","path":"/api/missing",
                 "document_evidence":{"source":"docs/api.md","line":12},"code_evidence":[]},
                {"status":"CODE_ENDPOINT_NOT_DOCUMENTED","method":"GET","path":"/internal",
                 "document_evidence":None,"code_evidence":[{"source":"server/routes.py","line":9}]},
                {"status":"MULTIPLE_CODE_MATCHES","method":"GET","path":"/duplicate",
                 "document_evidence":{"source":"docs/api.md","line":20},
                 "code_evidence":[{"source":"a.py","line":1},{"source":"b.py","line":2}]}
            ]}
        self.graph={"schema_version":"usit-graph-v1","business_understanding":{
            "documented_feature_linkage":[{"feature_name":"照片上传","linkage_status":"NOT_LINKED_NO_EXPLICIT_FEATURE_ENDPOINT_RELATION"}],
            "business_function_views":[
                {"business_function_id":"bf-upload","endpoint_id":"ep-upload","source":{"path":"routes.py","line":1}},
                {"business_function_id":"bf-delete","endpoint_id":"ep-delete","source":{"path":"routes.py","line":20}},
                {"business_function_id":"bf-share","endpoint_id":"ep-share","source":{"path":"routes.py","line":30}}]}}
        self.plan={"schema_version":"usit-business-test-plan-v1","candidates":[
            {"test_id":"t-upload","business_function_id":"bf-upload","execution_status":"NOT_EXECUTED"}]}
        self.matrix={"schema_version":"usit-business-evidence-verification-v1","business_function_evidence":[
            {"business_function_id":"bf-upload","test_execution_status":"PASSED",
             "code_coverage_status":"COVERED","verification_conclusion":"TEST_PASS_AND_CODE_LINES_COVERED_UNAUTHENTICATED",
             "evidence_conflict":False},
            {"business_function_id":"bf-delete","test_execution_status":"FAILED",
             "code_coverage_status":"PARTIAL","verification_conclusion":"TEST_FAILURE_OBSERVED",
             "evidence_conflict":False}]}

    def test_builds_prioritized_requirement_api_and_function_gaps(self):
        result=build_verification_gap_backlog(self.semantics,self.graph,self.plan,self.matrix)
        kinds={x["gap_kind"] for x in result["gaps"]}
        self.assertIn("REQUIREMENT_TRACEABILITY_GAP",kinds)
        feature_gap=next(x for x in result["gaps"] if x["gap_kind"]=="REQUIREMENT_TRACEABILITY_GAP")
        self.assertEqual(feature_gap["priority"],"P0")
        self.assertIn("DOCUMENTED_ENDPOINT_UNMATCHED",kinds)
        self.assertIn("UNDOCUMENTED_CODE_ENDPOINT",kinds)
        self.assertIn("AMBIGUOUS_ENDPOINT_CONTRACT",kinds)
        self.assertIn("TEST_FAILURE_OBSERVED",kinds)
        self.assertIn("CODE_COVERAGE_GAP",kinds)
        self.assertIn("NO_LINKED_TEST_DESIGN",kinds)
        self.assertIn("NO_RUNTIME_TEST_EVIDENCE",kinds)
        self.assertTrue(all(x["status"]=="REVIEW_REQUIRED" and x["defect_confirmed"] is False for x in result["gaps"]))
        upload=[x for x in result["gaps"] if x.get("business_function_id")=="bf-upload"]
        self.assertFalse(any(x["gap_kind"] in {"NO_LINKED_TEST_DESIGN","NO_RUNTIME_TEST_EVIDENCE","CODE_COVERAGE_NOT_MEASURED"} for x in upload))

    def test_does_not_fuzzy_link_feature_name_to_endpoint(self):
        graph={"schema_version":"usit-graph-v1","business_understanding":{
            "documented_feature_linkage":[{"feature_name":"照片上传","linkage_status":"NOT_LINKED"}],
            "business_function_views":[]}}
        result=build_verification_gap_backlog(self.semantics,graph)
        feature_gap=next(x for x in result["gaps"] if x["gap_kind"]=="REQUIREMENT_TRACEABILITY_GAP")
        self.assertEqual(feature_gap["target"],"照片上传")
        self.assertIsNone(feature_gap["business_function_id"])

    def test_same_name_requirements_are_matched_by_source_and_line(self):
        semantics = {"features": [
            {"name": "照片上传", "source": "docs/admin.md", "line": 8, "priority": "P1"},
            {"name": "照片上传", "source": "docs/member.md", "line": 20, "priority": "P1"},
        ], "endpoint_contract_alignment": []}
        graph = {"schema_version": "usit-graph-v1", "business_understanding": {
            "documented_feature_linkage": [
                {"feature_name": "照片上传", "source": "docs/admin.md", "line": 8,
                 "linkage_status": "EXPLICIT_REVIEWED_LINK"},
                {"feature_name": "照片上传", "source": "docs/member.md", "line": 20,
                 "linkage_status": "NOT_LINKED_NO_EXPLICIT_FEATURE_ENDPOINT_RELATION"},
            ],
            "business_function_views": [],
        }}
        result = build_verification_gap_backlog(semantics, graph)
        feature_gaps = [item for item in result["gaps"]
                        if item["gap_kind"] == "REQUIREMENT_TRACEABILITY_GAP"]
        self.assertEqual(len(feature_gaps), 1)
        self.assertEqual(feature_gaps[0]["evidence_refs"][0]["path"], "docs/member.md")
        self.assertEqual(feature_gaps[0]["evidence_refs"][0]["line"], 20)

    def test_approved_graph_bound_traceability_closes_exact_requirement_gap(self):
        graph_hash = "a" * 64
        traceability = {
            "schema_version": "usit-business-traceability-matrix-v1",
            "source_graph_sha256": graph_hash,
            "rows": [{
                "feature_name": "照片上传",
                "feature_source": "docs/PRD.md",
                "feature_line": 8,
                "mapping_decision": "APPROVED_LINK",
                "business_function_id": "bf-upload",
                "traceability_status": "TRACEABLE_TO_STATIC_FUNCTION_VIEW",
            }],
        }
        result = build_verification_gap_backlog(
            self.semantics, self.graph, self.plan, self.matrix,
            {"business_graph_sha256": graph_hash}, traceability,
        )
        self.assertFalse(any(item["gap_kind"] == "REQUIREMENT_TRACEABILITY_GAP"
                             for item in result["gaps"]))

    def test_traceability_matrix_with_stale_graph_hash_is_rejected(self):
        traceability = {
            "schema_version": "usit-business-traceability-matrix-v1",
            "source_graph_sha256": "b" * 64,
            "rows": [],
        }
        with self.assertRaisesRegex(ValueError, "does not match"):
            build_verification_gap_backlog(
                self.semantics, self.graph, input_hashes={"business_graph_sha256": "a" * 64},
                traceability_matrix=traceability,
            )

    def test_approved_traceability_to_unknown_function_is_rejected(self):
        traceability = {
            "schema_version": "usit-business-traceability-matrix-v1",
            "source_graph_sha256": "a" * 64,
            "rows": [{
                "feature_name": "照片上传", "feature_source": "docs/PRD.md",
                "feature_line": 8, "mapping_decision": "APPROVED_LINK",
                "business_function_id": "bf-does-not-exist",
            }],
        }
        with self.assertRaisesRegex(ValueError, "unknown business_function_id"):
            build_verification_gap_backlog(
                self.semantics, self.graph, input_hashes={"business_graph_sha256": "a" * 64},
                traceability_matrix=traceability,
            )

    def test_unapproved_or_targetless_traceability_does_not_close_requirement_gap(self):
        graph_hash = "a" * 64
        for decision, function_id in (("NEEDS_REVIEW", None), ("APPROVED_LINK", None)):
            traceability = {
                "schema_version": "usit-business-traceability-matrix-v1",
                "source_graph_sha256": graph_hash,
                "rows": [{
                    "feature_name": "照片上传", "feature_source": "docs/PRD.md",
                    "feature_line": 8, "mapping_decision": decision,
                    "business_function_id": function_id,
                }],
            }
            result = build_verification_gap_backlog(
                self.semantics, self.graph, input_hashes={"business_graph_sha256": graph_hash},
                traceability_matrix=traceability,
            )
            self.assertTrue(any(item["gap_kind"] == "REQUIREMENT_TRACEABILITY_GAP"
                                for item in result["gaps"]))

    def test_unlinked_function_without_runtime_and_coverage_is_unknown_gap(self):
        result=build_verification_gap_backlog({"features":[],"endpoint_contract_alignment":[]},
            {"schema_version":"usit-graph-v1","business_understanding":{"business_function_views":[
                {"business_function_id":"bf-one","source":{"path":"a.py","line":2}}]}})
        kinds={x["gap_kind"] for x in result["gaps"]}
        self.assertTrue({"NO_LINKED_TEST_DESIGN","NO_RUNTIME_TEST_EVIDENCE","CODE_COVERAGE_NOT_MEASURED"}.issubset(kinds))

    def test_conflict_has_p0_priority_and_is_not_called_a_defect(self):
        matrix={"schema_version":"usit-business-evidence-verification-v1","business_function_evidence":[
            {"business_function_id":"bf-upload","evidence_conflict":True,"verification_conclusion":"EVIDENCE_CONFLICT"}]}
        result=build_verification_gap_backlog({"features":[],"endpoint_contract_alignment":[]},
            {"schema_version":"usit-graph-v1","business_understanding":{"business_function_views":[
                {"business_function_id":"bf-upload"}]}},None,matrix)
        conflict=next(x for x in result["gaps"] if x["gap_kind"]=="EVIDENCE_CONFLICT")
        self.assertEqual(conflict["priority"],"P0")
        self.assertFalse(conflict["defect_confirmed"])

    def test_deterministic_and_rejects_duplicate_function_ids(self):
        a=build_verification_gap_backlog(self.semantics,self.graph,self.plan,self.matrix)
        b=build_verification_gap_backlog(self.semantics,self.graph,self.plan,self.matrix)
        self.assertEqual(a,b)
        graph={"schema_version":"usit-graph-v1","business_understanding":{"business_function_views":[
            {"business_function_id":"same"},{"business_function_id":"same"}]}}
        with self.assertRaisesRegex(ValueError,"duplicate business_function_id"):
            build_verification_gap_backlog({"features":[],"endpoint_contract_alignment":[]},graph)

    def test_cli_writes_parseable_gap_backlog_with_reviewed_traceability(self):
        import hashlib
        import tempfile
        from contextlib import redirect_stdout
        from io import StringIO
        from pathlib import Path
        from usit.cli import main
        with tempfile.TemporaryDirectory() as td:
            base=Path(td)
            semantics=base/"semantics.json"; graph=base/"graph.json"; plan=base/"plan.json"
            traceability=base/"traceability.json"; out=base/"gaps.json"
            semantics.write_text(json.dumps(self.semantics),encoding="utf-8")
            graph.write_text(json.dumps(self.graph,ensure_ascii=False,indent=2),encoding="utf-8")
            graph_hash=hashlib.sha256(graph.read_bytes()).hexdigest()
            plan.write_text(json.dumps(self.plan),encoding="utf-8")
            traceability.write_text(json.dumps({
                "schema_version":"usit-business-traceability-matrix-v1",
                "source_graph_sha256":graph_hash,
                "rows":[{
                    "feature_name":"照片上传","feature_source":"docs/PRD.md","feature_line":8,
                    "mapping_decision":"APPROVED_LINK","business_function_id":"bf-upload",
                    "traceability_status":"TRACEABLE_TO_STATIC_FUNCTION_VIEW",
                }],
            }),encoding="utf-8")
            with redirect_stdout(StringIO()):
                code=main(["analyze-gaps","--semantics",str(semantics),"--graph",str(graph),
                           "--test-plan",str(plan),"--traceability",str(traceability),"--out",str(out)])
            self.assertEqual(code,0)
            saved=json.loads(out.read_text(encoding="utf-8"))
            self.assertGreater(saved["gap_count"],0)
            self.assertEqual(saved["input_hashes"]["business_graph_sha256"],graph_hash)
            self.assertIn("traceability_matrix_sha256",saved["input_hashes"])
            self.assertFalse(any(item["gap_kind"]=="REQUIREMENT_TRACEABILITY_GAP"
                                 for item in saved["gaps"]))

    def test_rejects_invalid_artifact_schemas(self):
        with self.assertRaisesRegex(ValueError,"usit-graph-v1"):
            build_verification_gap_backlog(self.semantics,{"schema_version":"bad"})
        with self.assertRaisesRegex(ValueError,"usit-business-test-plan-v1"):
            build_verification_gap_backlog(self.semantics,self.graph,{"schema_version":"bad"})


    def test_missing_or_invalid_runtime_test_status_is_inconclusive(self):
        for status in (None, "INVALID", "CANCELLED"):
            with self.subTest(status=status):
                row = {"business_function_id": "bf-upload"}
                if status is not None:
                    row["test_execution_status"] = status
                matrix = {
                    "schema_version": "usit-business-evidence-verification-v1",
                    "business_function_evidence": [row],
                }
                result = build_verification_gap_backlog(
                    {"features": [], "endpoint_contract_alignment": []},
                    {"schema_version": "usit-graph-v1", "business_understanding": {
                        "business_function_views": [{"business_function_id": "bf-upload"}],
                    }},
                    evidence_matrix=matrix,
                )
                kinds = {item["gap_kind"] for item in result["gaps"]}
                self.assertIn("INCONCLUSIVE_TEST_EVIDENCE", kinds)
                self.assertNotIn("NO_RUNTIME_TEST_EVIDENCE", kinds)


if __name__=="__main__":
    unittest.main()
