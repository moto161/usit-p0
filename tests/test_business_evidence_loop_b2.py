"""End-to-end business-evidence-loop demonstration (B2).

One synthetic project exercises the B2 additions on top of B1:

* bf-attr-a / bf-attr-b -- G3(a): one correlation shared by two
  functions, with a collector declaration and per-event attribution,
  so each function is observed through its own declared events only.
* bf-outside           -- G3(a) counter-example: an event claims a
  function outside its correlation's declaration.
* bf-mismatch-attr     -- the declared function of that correlation;
  it gets no observation from it and is NOT downgraded by the conflict.
* bf-unconfirmed       -- G4 counter-example: tests pass but no linked
  candidate's expected result was confirmed by the business owner.
* bf-ghost             -- G3(a) counter-example: a declaration naming a
  correlation with no events at all; it must not poison other rows.

The runtime evidence goes through the real validator (not a hand-built
validation document): events -> validate_runtime_evidence ->
traceability -> gap backlog.
"""
from __future__ import annotations

import hashlib
import json
import unittest

from usit.business_evidence_verifier import verify_business_function_evidence
from usit.business_traceability import build_business_traceability
from usit.business_traceability_report import render_business_traceability_markdown
from usit.runtime_evidence import validate_runtime_evidence
from usit.verification_gap_analyzer import build_verification_gap_backlog


def _view(function_id):
    return {
        "business_function_id": function_id, "endpoint_id": "ep-" + function_id,
        "method": "GET", "endpoint": "/api/photos",
        "frontend_entrypoints": [{"source_file": "web/Photos.tsx", "event_line": 4}],
        "database_accesses": [{"operation": "SELECT", "tables": ["photos"]}],
        "source": {"path": "server/routes.js", "line": 8},
        "handler": {"path": "server/photos.js", "line": 22},
        "call_chain": [{"call_site": {"path": "server/photos.js", "line": 25}}],
        "authorization": {"evidence": [{"path": "server/auth.js", "line": 3}]},
        "identity_resource_flow": [{"path": "server/db.js", "line": 9}],
        "stage_status": {"handler": "RESOLVED", "calls": "RESOLVED_STATIC_CHAIN",
                         "database": "LINKED", "authorization": "EVIDENCE_FOUND",
                         "identity_resource": "EVIDENCE_FOUND"},
    }


def _graph():
    functions = ("bf-attr-a", "bf-attr-b", "bf-outside",
                 "bf-mismatch-attr", "bf-unconfirmed", "bf-ghost")
    graph = {"schema_version": "usit-graph-v1", "business_understanding": {
        "business_function_views": [_view(fid) for fid in functions]}}
    graph_hash = hashlib.sha256(
        json.dumps(graph, ensure_ascii=False, indent=2).encode()).hexdigest()
    return graph, graph_hash, functions


def _event(event_id, event_type, correlation_id, function_id, timestamp, **extra):
    event = {"event_id": event_id, "timestamp": timestamp,
             "event_type": event_type, "correlation_id": correlation_id,
             "business_function_id": function_id, "parent_event_id": None}
    event.update(extra)
    return event


def _events():
    base = "2026-11-11T12:00:%02d+08:00"
    return [
        _event("evt-a1", "UI_ACTION", "corr-attr", "bf-attr-a", base % 1,
               component="PhotoDetail", operation="tap_open"),
        _event("evt-a2", "HTTP_REQUEST", "corr-attr", "bf-attr-a", base % 2,
               http_method="GET", route_template="/api/photos",
               parent_event_id="evt-a1"),
        _event("evt-b1", "HTTP_REQUEST", "corr-attr", "bf-attr-b", base % 3,
               http_method="GET", route_template="/api/photos"),
        _event("evt-b2", "DATABASE_CHANGE", "corr-attr", "bf-attr-b", base % 4,
               db_operation="SELECT", table_or_collection="photos",
               parent_event_id="evt-b1"),
        # Claims bf-outside, but corr-mismatch declares bf-mismatch-attr.
        _event("evt-m1", "HTTP_REQUEST", "corr-mismatch", "bf-outside", base % 5,
               http_method="GET", route_template="/api/photos"),
        _event("evt-u1", "UI_ACTION", "corr-unconf", "bf-unconfirmed", base % 6,
               component="PhotoDetail", operation="tap_open"),
        _event("evt-u2", "HTTP_REQUEST", "corr-unconf", "bf-unconfirmed", base % 7,
               http_method="GET", route_template="/api/photos",
               parent_event_id="evt-u1"),
    ]


def _runtime_doc(graph_hash, events):
    return {
        "schema_version": "usit-runtime-evidence-v1",
        "source_graph_sha256": graph_hash,
        "session": {"session_id": "session-b2", "platform": "ANDROID",
                    "captured_at": "2026-11-11T12:00:00+08:00"},
        "correlation_function_declarations": {
            "corr-attr": ["bf-attr-a", "bf-attr-b"],
            "corr-mismatch": ["bf-mismatch-attr"],
            "corr-ghost": ["bf-ghost"],
        },
        "events": events,
    }


def _tests(graph_hash, manifest, functions):
    provenance = {"execution_id": "run-b2", "pytest_exit_code": 0,
                  "pytest_exit_status": "EXIT_OK",
                  "source_manifest_file_hashes": manifest}
    return {"schema_version": "usit-test-execution-results-v1",
            "source_hashes": {"test_report_sha256": "1" * 64,
                              "mapping_json_sha256": "2" * 64,
                              "business_graph_sha256": graph_hash},
            "execution_provenance": provenance,
            "business_function_results": [
                {"business_function_id": fid, "execution_status": "PASSED",
                 "mapped_test_case_count": 1, "failed": 0, "skipped": 0}
                for fid in functions]}


def _coverage(graph_hash, manifest, functions):
    rows = []
    for fid in functions:
        source_file = f"src/{fid}.py"
        digest = manifest[source_file]
        rows.append({"business_function_id": fid, "code_coverage_status": "COVERED",
                     "line_coverage_percent": 100.0,
                     "source_binding_evidence": [{
                         "source_file": source_file,
                         "source_binding_status": "SOURCE_BYTES_MATCHED",
                         "expected_source_file_sha256": digest,
                         "actual_source_file_sha256": digest,
                         "source_line_count": 5, "line_start": 1, "line_end": 5}],
                     "mapping_ranges": [{"source_file": source_file,
                                         "line_start": 1, "line_end": 5}]})
    return {"schema_version": "usit-code-coverage-results-v1", "execution_id": "run-b2",
            "source_hashes": {"coverage_report_sha256": "3" * 64,
                              "mapping_json_sha256": "4" * 64,
                              "business_graph_sha256": graph_hash,
                              "source_files": manifest},
            "business_function_coverage": rows}


def _confirmation():
    return {"reviewed_by": "业务负责人",
            "reviewed_at": "2026-11-11T10:00:00+08:00",
            "rationale": "验收标准已批准，预期结果已确认。"}


class BusinessEvidenceLoopB2Tests(unittest.TestCase):
    def _pipeline(self):
        graph, graph_hash, functions = _graph()
        events = _events()
        validation = validate_runtime_evidence(
            _runtime_doc(graph_hash, events), graph, graph_hash)
        manifest = {f"src/{fid}.py": hashlib.sha256(fid.encode()).hexdigest()
                    for fid in functions}
        evidence = verify_business_function_evidence(
            graph, _tests(graph_hash, manifest, functions),
            _coverage(graph_hash, manifest, functions), graph_hash,
            runtime_events=events)
        names = {"bf-attr-a": "查看照片", "bf-attr-b": "分享照片",
                 "bf-outside": "删除照片", "bf-mismatch-attr": "导出照片",
                 "bf-unconfirmed": "收藏照片", "bf-ghost": "搜索照片"}
        semantics = {"schema_version": "usit-business-semantics-v1", "features": [
            {"name": name, "source": "docs/requirements.md", "line": 10 + index}
            for index, name in enumerate(names.values())]}
        mapping = {"schema_version": "usit-feature-function-mapping-v1",
                   "source_graph_sha256": graph_hash, "mappings": [
                       {"feature_name": name, "source_path": "docs/requirements.md",
                        "source_line": 10 + index, "decision": "APPROVED_LINK",
                        "business_function_id": fid, "reviewed_by": "reviewer-1",
                        "reviewed_at": "2026-11-01T12:00:00+08:00",
                        "rationale": "合成演示映射。"}
                       for index, (fid, name) in enumerate(names.items())]}
        plan = {"schema_version": "usit-business-test-plan-v1", "candidates": [
            {"test_id": "bt-b2-" + fid, "business_function_id": fid,
             **({} if fid == "bf-unconfirmed" else
                {"expected_result_confirmation": _confirmation()})}
            for fid in functions]}
        result = build_business_traceability(
            semantics, graph, mapping, plan, evidence,
            {"business_graph_sha256": graph_hash},
            runtime_evidence_validation=validation)
        return graph, graph_hash, functions, semantics, validation, evidence, result

    def test_attributed_shared_correlation_closes_both_functions(self):
        _, _, _, _, validation, _, result = self._pipeline()
        kinds = {x["kind"] for x in validation["evidence_conflicts"]}
        self.assertEqual(kinds, {"EVENT_FUNCTION_NOT_IN_CORRELATION_DECLARATION",
                                 "CORRELATION_DECLARATION_WITHOUT_EVENTS"})
        attr = next(c for c in validation["correlations"]
                    if c["correlation_id"] == "corr-attr")
        self.assertEqual(attr["attribution_status"], "PER_EVENT_COLLECTOR_DECLARED")
        self.assertEqual(attr["declared_function_ids"], ["bf-attr-a", "bf-attr-b"])
        rows = {row["feature_name"]: row for row in result["rows"]}
        for name, stages, event_ids in (
                ("查看照片", ["http_request", "ui"], ["evt-a1", "evt-a2"]),
                ("分享照片", ["database", "http_request"], ["evt-b1", "evt-b2"])):
            row = rows[name]
            self.assertEqual(row["traceability_status"], "TRACEABLE_WITH_RUNTIME_EVIDENCE")
            self.assertEqual(row["stage_status"]["runtime_observation"], "RUNTIME_OBSERVED")
            self.assertEqual(row["expected_result_confirmation_status"], "CONFIRMED")
            corr = row["runtime_observation"]["correlations"][0]
            self.assertEqual(corr["function_attribution_status"], "PER_EVENT_COLLECTOR_DECLARED")
            self.assertEqual(corr["observed_stages"], stages)
            self.assertEqual(corr["observed_event_ids_by_timestamp"], event_ids)

    def test_each_counterexample_downgrades_or_is_scoped(self):
        _, _, _, _, _, _, result = self._pipeline()
        rows = {row["feature_name"]: row for row in result["rows"]}
        # G3(a): the claiming function is downgraded to a conflict.
        outside = rows["删除照片"]
        self.assertEqual(outside["traceability_status"], "EVIDENCE_CONFLICT")
        self.assertEqual(outside["stage_status"]["runtime_observation"], "EVIDENCE_CONFLICT")
        # The declared function of that correlation gets no observation
        # from it, but the conflict is not smeared onto it.
        mismatch_attr = rows["导出照片"]
        self.assertNotEqual(mismatch_attr["traceability_status"], "EVIDENCE_CONFLICT")
        self.assertEqual(mismatch_attr["stage_status"]["runtime_observation"], "NOT_OBSERVED")
        # G4: tests pass, but the oracle was never confirmed.
        unconfirmed = rows["收藏照片"]
        self.assertEqual(unconfirmed["expected_result_confirmation_status"], "UNCONFIRMED")
        self.assertEqual(unconfirmed["stage_status"]["test_execution"], "UNKNOWN")
        self.assertEqual(unconfirmed["traceability_status"],
                         "TRACEABLE_WITH_INCONCLUSIVE_TEST_EVIDENCE")
        # The ghost declaration must not poison an unrelated row.
        ghost = rows["搜索照片"]
        self.assertEqual(ghost["traceability_status"], "TRACEABLE_WITH_RUNTIME_EVIDENCE")
        markdown = render_business_traceability_markdown(result)
        self.assertIn("PER_EVENT_COLLECTOR_DECLARED", markdown)
        self.assertIn("预期结果确认（业务负责人）：UNCONFIRMED", markdown)

    def test_gap_backlog_names_all_three_b2_causes(self):
        graph, graph_hash, functions, semantics, _, evidence, result = self._pipeline()
        backlog = build_verification_gap_backlog(
            semantics, graph, None, evidence,
            {"business_graph_sha256": graph_hash}, result)
        kinds = {x["gap_kind"] for x in backlog["gaps"]}
        self.assertIn("EVENT_FUNCTION_NOT_IN_CORRELATION_DECLARATION", kinds)
        self.assertIn("CORRELATION_DECLARATION_WITHOUT_EVENTS", kinds)
        self.assertIn("EXPECTED_RESULT_UNCONFIRMED", kinds)
        for kind in ("EVENT_FUNCTION_NOT_IN_CORRELATION_DECLARATION",
                     "CORRELATION_DECLARATION_WITHOUT_EVENTS",
                     "EXPECTED_RESULT_UNCONFIRMED"):
            gap = next(x for x in backlog["gaps"] if x["gap_kind"] == kind)
            self.assertEqual(gap["priority"], "P1")
            self.assertFalse(gap["defect_confirmed"])
        ghost = next(x for x in backlog["gaps"]
                     if x["gap_kind"] == "CORRELATION_DECLARATION_WITHOUT_EVENTS")
        self.assertEqual(ghost["target"], "corr-ghost")


if __name__ == "__main__":
    unittest.main()
