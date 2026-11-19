"""End-to-end business-evidence-loop demonstration (B1).

One synthetic project exercises the whole closed chain
(requirement -> UI entrypoint -> API -> handler -> DB -> runtime event
-> test execution -> coverage -> real source location) and three
counter-examples that must each downgrade correctly:

* bf-closed      -- every ring closed, tests pass, hashes match.
* bf-diverged    -- G1: test snapshot and coverage disagree on a source hash.
* bf-mismatch    -- G2: a runtime event's route does not match the static view.
* bf-shared-a/b  -- G3: one correlation observed across two functions,
                    so the observation belongs to neither alone.
"""
from __future__ import annotations

import hashlib
import json
import unittest

from usit.business_evidence_verifier import verify_business_function_evidence
from usit.business_traceability import build_business_traceability
from usit.business_traceability_report import render_business_traceability_markdown


def _sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def _view(function_id, method, endpoint, tables, entrypoint="web/Photos.tsx"):
    return {
        "business_function_id": function_id, "endpoint_id": "ep-" + function_id,
        "method": method, "endpoint": endpoint,
        "frontend_entrypoints": [{"source_file": entrypoint, "event_line": 4}],
        "database_accesses": [{"operation": "SELECT", "tables": tables}],
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
    graph = {"schema_version": "usit-graph-v1", "business_understanding": {
        "business_function_views": [
            _view("bf-closed", "GET", "/api/photos", ["photos"]),
            _view("bf-diverged", "GET", "/api/photos", ["photos"]),
            _view("bf-mismatch", "DELETE", "/api/photos/{id}", ["photos"]),
            _view("bf-shared-a", "GET", "/api/photos", ["photos"]),
            _view("bf-shared-b", "GET", "/api/photos", ["photos"]),
        ]}}
    graph_hash = hashlib.sha256(
        json.dumps(graph, ensure_ascii=False, indent=2).encode()).hexdigest()
    return graph, graph_hash


def _tests(graph_hash, manifest):
    provenance = {"execution_id": "run-1", "pytest_exit_code": 0,
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
                for fid in ("bf-closed", "bf-diverged", "bf-mismatch",
                            "bf-shared-a", "bf-shared-b")]}


def _coverage(graph_hash, source_files):
    def bound(source_file, digest):
        return {"source_file": source_file,
                "source_binding_status": "SOURCE_BYTES_MATCHED",
                "expected_source_file_sha256": digest,
                "actual_source_file_sha256": digest,
                "source_line_count": 5, "line_start": 1, "line_end": 5}
    rows = []
    for fid, source_file, digest in (
            ("bf-closed", "src/closed.py", "a" * 64),
            ("bf-diverged", "src/diverged.py", "b" * 64),
            ("bf-mismatch", "src/mismatch.py", "c" * 64),
            ("bf-shared-a", "src/shared_a.py", "d" * 64),
            ("bf-shared-b", "src/shared_b.py", "e" * 64)):
        rows.append({"business_function_id": fid, "code_coverage_status": "COVERED",
                     "line_coverage_percent": 100.0,
                     "source_binding_evidence": [bound(source_file, digest)],
                     "mapping_ranges": [{"source_file": source_file,
                                         "line_start": 1, "line_end": 5}]})
    return {"schema_version": "usit-code-coverage-results-v1", "execution_id": "run-1",
            "source_hashes": {"coverage_report_sha256": "3" * 64,
                              "mapping_json_sha256": "4" * 64,
                              "business_graph_sha256": graph_hash,
                              "source_files": source_files},
            "business_function_coverage": rows}


def _events():
    return [
        {"event_id": "evt-closed-http", "event_type": "HTTP_REQUEST",
         "correlation_id": "corr-closed", "business_function_id": "bf-closed",
         "http_method": "GET", "route_template": "/api/photos"},
        {"event_id": "evt-closed-db", "event_type": "DATABASE_CHANGE",
         "correlation_id": "corr-closed", "business_function_id": "bf-closed",
         "db_operation": "SELECT", "table_or_collection": "photos"},
        {"event_id": "evt-mismatch-http", "event_type": "HTTP_REQUEST",
         "correlation_id": "corr-mismatch", "business_function_id": "bf-mismatch",
         "http_method": "DELETE", "route_template": "/api/photos/123"},
    ]


class BusinessEvidenceLoopTests(unittest.TestCase):
    def test_closed_chain_passes_and_each_counterexample_downgrades(self):
        graph, graph_hash = _graph()
        manifest = {"src/closed.py": "a" * 64, "src/diverged.py": "b" * 64,
                    "src/mismatch.py": "c" * 64, "src/shared_a.py": "d" * 64,
                    "src/shared_b.py": "e" * 64}
        # G1 counter-example: the coverage side saw a different
        # src/diverged.py than the test snapshot did.
        coverage_files = dict(manifest)
        coverage_files["src/diverged.py"] = "f" * 64
        result = verify_business_function_evidence(
            graph, _tests(graph_hash, manifest),
            _coverage(graph_hash, coverage_files), graph_hash,
            runtime_events=_events())
        by_id = {x["business_function_id"]: x
                 for x in result["business_function_evidence"]}

        closed = by_id["bf-closed"]
        self.assertEqual(closed["verification_conclusion"],
                         "TEST_PASS_AND_CODE_LINES_COVERED_UNAUTHENTICATED")
        self.assertEqual(closed["runtime_consistency_status"], "MATCHED")
        self.assertEqual(closed["source_version_binding_status"], "MATCHED")
        self.assertFalse(closed["evidence_conflict"])
        self.assertEqual(closed["claim_vs_observed"]["observed_by_parent"],
                         {"pytest_exit_code": 0, "pytest_exit_status": "EXIT_OK"})

        diverged = by_id["bf-diverged"]
        self.assertEqual(diverged["source_version_binding_status"], "DIVERGENT")
        self.assertEqual(diverged["verification_conclusion"], "EVIDENCE_CONFLICT")
        self.assertIn("TEST_COVERAGE_SOURCE_VERSION_DIVERGENCE",
                      {item["kind"] for item in result["evidence_conflicts"]})

        mismatch = by_id["bf-mismatch"]
        self.assertEqual(mismatch["runtime_consistency_status"], "MISMATCH")
        self.assertEqual(mismatch["verification_conclusion"], "EVIDENCE_CONFLICT")
        self.assertIn("RUNTIME_STATIC_MISMATCH",
                      {item["kind"] for item in result["evidence_conflicts"]})
        self.assertEqual(
            mismatch["runtime_consistency_detail"]["mismatches"][0]["field"],
            "route_template")

        # The shared pair is untouched by G1/G2 here; their downgrade
        # is a traceability-stage decision (G3), asserted below.
        for fid in ("bf-shared-a", "bf-shared-b"):
            self.assertEqual(by_id[fid]["verification_conclusion"],
                             "TEST_PASS_AND_CODE_LINES_COVERED_UNAUTHENTICATED")

    def test_traceability_rows_reflect_closed_and_downgraded_states(self):
        graph, graph_hash = _graph()
        manifest = {"src/closed.py": "a" * 64, "src/diverged.py": "b" * 64,
                    "src/mismatch.py": "c" * 64, "src/shared_a.py": "d" * 64,
                    "src/shared_b.py": "e" * 64}
        coverage_files = dict(manifest)
        coverage_files["src/diverged.py"] = "f" * 64
        evidence = verify_business_function_evidence(
            graph, _tests(graph_hash, manifest),
            _coverage(graph_hash, coverage_files), graph_hash,
            runtime_events=_events())
        names = {"bf-closed": "查看照片", "bf-diverged": "导出照片",
                 "bf-mismatch": "删除照片", "bf-shared-a": "分享照片",
                 "bf-shared-b": "收藏照片"}
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
        runtime = {
            "schema_version": "usit-runtime-evidence-validation-v1",
            "assessment_status": "VALIDATED_EVENT_STRUCTURE",
            "source_hashes": {"business_graph_sha256": graph_hash,
                              "runtime_evidence_sha256": "b" * 64},
            "business_function_ids_observed": ["bf-closed", "bf-shared-a", "bf-shared-b"],
            "correlations": [
                {"correlation_id": "corr-closed", "business_function_ids": ["bf-closed"],
                 "runtime_observation_status": "RUNTIME_OBSERVED"},
                {"correlation_id": "corr-shared",
                 "business_function_ids": ["bf-shared-a", "bf-shared-b"],
                 "event_count": 3,
                 "observed_stages": ["ui", "http_request", "backend"],
                 "observed_event_ids_by_timestamp": ["evt-1", "evt-2", "evt-3"],
                 "explicit_parent_edges": [
                     {"parent_event_id": "evt-1", "child_event_id": "evt-2"},
                     {"parent_event_id": "evt-2", "child_event_id": "evt-3"},
                 ],
                 "parent_topology_status": "LINEAR_CHAIN",
                 "parent_link_coverage_ratio": 1.0,
                 "root_event_ids": ["evt-1"], "leaf_event_ids": ["evt-3"],
                 "branching_parent_event_ids": [], "isolated_event_ids": [],
                 "cycle_event_ids": []},
            ],
        }
        result = build_business_traceability(
            semantics, graph, mapping, evidence_matrix=evidence,
            input_hashes={"business_graph_sha256": graph_hash},
            runtime_evidence_validation=runtime)
        rows = {row["feature_name"]: row for row in result["rows"]}

        self.assertEqual(rows["查看照片"]["traceability_status"],
                         "TRACEABLE_WITH_RUNTIME_EVIDENCE")
        self.assertEqual(rows["导出照片"]["traceability_status"], "EVIDENCE_CONFLICT")
        self.assertEqual(rows["删除照片"]["traceability_status"], "EVIDENCE_CONFLICT")
        self.assertEqual(rows["删除照片"]["stage_status"]["runtime_observation"],
                         "EVIDENCE_CONFLICT")
        self.assertEqual(rows["删除照片"]["runtime_observation"]["consistency"], "MISMATCH")
        for name in ("分享照片", "收藏照片"):
            self.assertEqual(rows[name]["traceability_status"],
                             "TRACEABLE_WITH_INCONCLUSIVE_TEST_EVIDENCE")
            self.assertEqual(rows[name]["stage_status"]["runtime_observation"],
                             "SHARED_CORRELATION_EVENT_OWNERSHIP_UNRESOLVED")
        # Every row annotates the parser scope of each static stage.
        for row in result["rows"]:
            self.assertEqual(row["stage_parser_scope"]
                             ["business_function_to_frontend_entrypoint"],
                             ["JSX_TSX_ONLY"])
        markdown = render_business_traceability_markdown(result)
        self.assertIn("SHARED_CORRELATION_EVENT_OWNERSHIP_UNRESOLVED", markdown)
        self.assertIn("evt-mismatch-http", markdown)
        self.assertIn("/api/photos/123", markdown)


if __name__ == "__main__":
    unittest.main()
