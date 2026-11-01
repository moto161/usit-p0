from __future__ import annotations
import json
import unittest
from usit.project_quality_assessment import assess_project_quality


class ProjectQualityAssessmentTests(unittest.TestCase):
    def setUp(self):
        self.semantics={"schema_version":"usit-business-semantics-v1",
            "features":[{"name":"上传","priority":"P0"},{"name":"分享","priority":"P1"}],
            "endpoint_contract_alignment":[{"status":"MATCHED"},{"status":"DOCUMENTED_NOT_FOUND_IN_CODE"}]}
        self.graph={"schema_version":"usit-graph-v1","business_understanding":{
            "documented_feature_linkage":[
                {"feature_name":"上传","linkage_status":"EXPLICIT_REVIEWED_LINK"},
                {"feature_name":"分享","linkage_status":"NOT_LINKED"}],
            "business_function_views":[
                {"business_function_id":"bf-upload","verification_status":"NOT_VERIFIED"},
                {"business_function_id":"bf-share","verification_status":"NOT_VERIFIED"},
                {"business_function_id":"bf-delete","verification_status":"NOT_VERIFIED"}]}}
        self.plan={"schema_version":"usit-business-test-plan-v1","candidates":[
            {"test_id":"t1","business_function_id":"bf-upload"},
            {"test_id":"t2","business_function_id":"bf-upload"},
            {"test_id":"t3","business_function_id":"bf-share"}]}
        self.matrix={"schema_version":"usit-business-evidence-verification-v1","business_function_evidence":[
            {"business_function_id":"bf-upload","test_execution_status":"PASSED","code_coverage_status":"COVERED","evidence_conflict":False},
            {"business_function_id":"bf-share","test_execution_status":"FAILED","code_coverage_status":"PARTIAL","evidence_conflict":False}]}
        self.gaps={"schema_version":"usit-verification-gap-backlog-v1","gaps":[
            {"gap_id":"g1","priority":"P0","gap_kind":"TEST_FAILURE_OBSERVED"},
            {"gap_id":"g2","priority":"P1","gap_kind":"NO_RUNTIME_TEST_EVIDENCE"}]}

    def test_reports_independent_dimensions_without_aggregate_score(self):
        result=assess_project_quality(self.semantics,self.graph,self.plan,self.matrix,self.gaps)
        self.assertEqual(result["assessment_mode"],"MULTIDIMENSIONAL_NO_AGGREGATE_SCORE")
        self.assertNotIn("overall_score",result)
        dims=result["dimensions"]
        self.assertEqual(dims["documented_feature_traceability"]["percent"],50.0)
        self.assertEqual(dims["business_function_test_design"]["percent"],66.67)
        self.assertEqual(dims["business_function_test_execution"]["percent"],66.67)
        self.assertEqual(dims["business_function_test_pass"]["percent"],50.0)
        self.assertEqual(dims["business_function_code_coverage_measured"]["percent"],66.67)

    def test_missing_evidence_is_not_counted_as_failed_or_zero_coverage(self):
        result=assess_project_quality({"features":[],"endpoint_contract_alignment":[]},self.graph)
        self.assertEqual(result["business_function_test_execution_status_counts"],{"NOT_MEASURED":3})
        self.assertEqual(result["business_function_code_coverage_status_counts"],{"NOT_MEASURED":3})
        self.assertIsNone(result["dimensions"]["documented_feature_traceability"]["percent"])
        self.assertEqual(result["dimensions"]["business_function_test_execution"]["percent"],0.0)

    def test_unknown_ids_and_conflicts_are_visible(self):
        matrix={"schema_version":"usit-business-evidence-verification-v1","business_function_evidence":[
            {"business_function_id":"bf-upload","test_execution_status":"UNKNOWN","code_coverage_status":"UNKNOWN","evidence_conflict":True},
            {"business_function_id":"bf-other","test_execution_status":"PASSED","code_coverage_status":"COVERED"}]}
        plan={"schema_version":"usit-business-test-plan-v1","candidates":[
            {"business_function_id":"bf-missing"}]}
        result=assess_project_quality(self.semantics,self.graph,plan,matrix)
        integrity=result["evidence_integrity"]
        self.assertEqual(integrity["business_function_evidence_conflict_ids"],["bf-upload"])
        self.assertEqual(integrity["unknown_test_design_business_function_ids"],["bf-missing"])
        self.assertEqual(integrity["unknown_runtime_evidence_business_function_ids"],["bf-other"])
        self.assertEqual(integrity["status"],"CONFLICTS_OR_UNLINKED_RECORDS_PRESENT")

    def test_invalid_status_and_source_hash_conflict_are_reported(self):
        matrix={"schema_version":"usit-business-evidence-verification-v1",
            "business_graph_sha256":"0"*64,
            "business_function_evidence":[
                {"business_function_id":"bf-upload","test_execution_status":"MAGIC","code_coverage_status":"COVERED"},
                {"business_function_id":"bf-share","test_execution_status":"PASSED","code_coverage_status":"INVALID"}]}
        result=assess_project_quality(self.semantics,self.graph,None,matrix)
        integrity=result["evidence_integrity"]
        self.assertEqual(integrity["invalid_runtime_status_business_function_ids"],["bf-share","bf-upload"])
        self.assertEqual(integrity["source_hash_conflicts"][0]["artifact"],"evidence_matrix")
        self.assertEqual(integrity["status"],"CONFLICTS_OR_UNLINKED_RECORDS_PRESENT")

    def test_empty_function_inventory_is_explicit_not_divided_by_zero(self):
        result=assess_project_quality({"features":[],"endpoint_contract_alignment":[]},
            {"schema_version":"usit-graph-v1","business_understanding":{"business_function_views":[]}})
        self.assertIsNone(result["dimensions"]["business_function_test_design"]["percent"])
        self.assertEqual(result["dimensions"]["business_function_test_design"]["status"],"NOT_APPLICABLE_OR_NOT_FOUND")

    def test_rejects_duplicate_function_ids_and_bad_schema(self):
        graph={"schema_version":"usit-graph-v1","business_understanding":{"business_function_views":[
            {"business_function_id":"same"},{"business_function_id":"same"}]}}
        with self.assertRaisesRegex(ValueError,"duplicate business_function_id"):
            assess_project_quality(self.semantics,graph)
        with self.assertRaisesRegex(ValueError,"usit-graph-v1"):
            assess_project_quality(self.semantics,{"schema_version":"wrong"})

    def test_cli_writes_parseable_report_with_hashes(self):
        import tempfile
        from contextlib import redirect_stdout
        from io import StringIO
        from pathlib import Path
        from usit.cli import main
        with tempfile.TemporaryDirectory() as td:
            base=Path(td)
            sem=base/"sem.json"; graph=base/"graph.json"; plan=base/"plan.json"; out=base/"quality.json"
            sem.write_text(json.dumps(self.semantics),encoding="utf-8")
            graph.write_text(json.dumps(self.graph),encoding="utf-8")
            plan.write_text(json.dumps(self.plan),encoding="utf-8")
            with redirect_stdout(StringIO()):
                code=main(["assess-quality","--semantics",str(sem),"--graph",str(graph),
                           "--test-plan",str(plan),"--out",str(out)])
            self.assertEqual(code,0)
            saved=json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(len(saved["input_hashes"]["business_graph_sha256"]),64)
            self.assertEqual(saved["assessment_mode"],"MULTIDIMENSIONAL_NO_AGGREGATE_SCORE")


if __name__=="__main__":
    unittest.main()
