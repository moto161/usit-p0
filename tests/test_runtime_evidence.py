from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from usit.runtime_evidence import validate_runtime_evidence


class RuntimeEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.graph = {"schema_version": "usit-graph-v1", "business_understanding": {
            "business_function_views": [
                {"business_function_id": "bf-photo-view"},
                {"business_function_id": "bf-photo-delete"},
            ]}}
        self.graph_bytes = json.dumps(self.graph, ensure_ascii=False, indent=2).encode()
        self.graph_hash = hashlib.sha256(self.graph_bytes).hexdigest()
        self.doc = {
            "schema_version": "usit-runtime-evidence-v1",
            "source_graph_sha256": self.graph_hash,
            "session": {"session_id": "session-001", "platform": "ANDROID",
                        "captured_at": "2026-11-01T12:00:00+08:00"},
            "events": [
                {"event_id": "e1", "timestamp": "2026-11-01T12:00:01+08:00",
                 "event_type": "UI_ACTION", "correlation_id": "c1",
                 "business_function_id": "bf-photo-view", "component": "PhotoDetail",
                 "operation": "tap_open", "parent_event_id": None},
                {"event_id": "e2", "timestamp": "2026-11-01T12:00:02+08:00",
                 "event_type": "HTTP_REQUEST", "correlation_id": "c1",
                 "business_function_id": "bf-photo-view", "http_method": "GET",
                 "route_template": "/api/photos/{id}", "parent_event_id": "e1"},
                {"event_id": "e3", "timestamp": "2026-11-01T12:00:03+08:00",
                 "event_type": "HTTP_RESPONSE", "correlation_id": "c1",
                 "business_function_id": "bf-photo-view", "status_code": 200,
                 "parent_event_id": "e2"},
                {"event_id": "e4", "timestamp": "2026-11-01T12:00:04+08:00",
                 "event_type": "BACKEND_SPAN", "correlation_id": "c1",
                 "business_function_id": "bf-photo-view", "operation": "load_photo",
                 "parent_event_id": "e3"},
            ],
        }

    def test_validates_explicit_runtime_observations_without_claiming_correctness(self):
        result = validate_runtime_evidence(self.doc, self.graph, self.graph_hash, "a" * 64)
        self.assertEqual(result["assessment_status"], "VALIDATED_EVENT_STRUCTURE")
        self.assertEqual(result["event_count"], 4)
        self.assertEqual(result["business_function_observation_count"], 1)
        chain = result["correlations"][0]
        self.assertEqual(chain["runtime_observation_status"], "RUNTIME_OBSERVED")
        self.assertEqual(chain["correlation_status"], "PARENT_LINKED_EVENTS_OBSERVED")
        self.assertTrue(chain["has_ui_action"])
        self.assertTrue(chain["has_http_exchange"])
        self.assertTrue(chain["has_backend_span"])
        self.assertFalse(chain["has_database_change"])
        self.assertEqual(chain["observed_event_ids_by_timestamp"], ["e1", "e2", "e3", "e4"])
        self.assertEqual(chain["explicit_parent_edges"], [
            {"parent_event_id": "e1", "child_event_id": "e2"},
            {"parent_event_id": "e2", "child_event_id": "e3"},
            {"parent_event_id": "e3", "child_event_id": "e4"},
        ])
        self.assertEqual(chain["parent_linked_event_count"], 4)
        self.assertEqual(chain["root_event_ids"], ["e1"])
        self.assertEqual(chain["unlinked_event_ids"], [])
        self.assertEqual(chain["leaf_event_ids"], ["e4"])
        self.assertEqual(chain["branching_parent_event_ids"], [])
        self.assertEqual(chain["isolated_event_ids"], [])
        self.assertEqual(chain["cycle_event_ids"], [])
        self.assertEqual(chain["parent_link_coverage_ratio"], 1.0)
        self.assertEqual(chain["parent_topology_status"], "LINEAR_CHAIN")
        self.assertTrue(any("不证明功能正确" in x for x in result["limitations"]))

    def test_reports_branched_and_disconnected_event_topology(self):
        doc = json.loads(json.dumps(self.doc))
        doc["events"] = [
            doc["events"][0],
            {**doc["events"][1], "event_id": "e2", "parent_event_id": "e1"},
            {**doc["events"][2], "event_id": "e3", "parent_event_id": "e1"},
            {**doc["events"][3], "event_id": "e4", "parent_event_id": None},
        ]
        result = validate_runtime_evidence(doc, self.graph, self.graph_hash)
        chain = result["correlations"][0]
        self.assertEqual(chain["parent_topology_status"], "BRANCHED")
        self.assertEqual(chain["branching_parent_event_ids"], ["e1"])
        self.assertEqual(chain["root_event_ids"], ["e1", "e4"])
        self.assertEqual(chain["leaf_event_ids"], ["e2", "e3", "e4"])
        self.assertEqual(chain["isolated_event_ids"], ["e4"])
        self.assertEqual(chain["parent_link_coverage_ratio"], 0.75)

    def test_reports_no_explicit_links_without_inventing_roots_as_a_chain(self):
        doc = json.loads(json.dumps(self.doc))
        for event in doc["events"]:
            event["parent_event_id"] = None
        result = validate_runtime_evidence(doc, self.graph, self.graph_hash)
        chain = result["correlations"][0]
        self.assertEqual(chain["parent_topology_status"], "NO_EXPLICIT_LINKS")
        self.assertEqual(chain["root_event_ids"], ["e1", "e2", "e3", "e4"])
        self.assertEqual(chain["leaf_event_ids"], ["e1", "e2", "e3", "e4"])
        self.assertEqual(chain["isolated_event_ids"], ["e1", "e2", "e3", "e4"])
        self.assertEqual(chain["parent_link_coverage_ratio"], 0.0)

    def test_graph_hash_and_unknown_function_are_conflicts(self):
        doc = json.loads(json.dumps(self.doc))
        doc["source_graph_sha256"] = "0" * 64
        doc["events"][0]["business_function_id"] = "bf-not-in-graph"
        result = validate_runtime_evidence(doc, self.graph, self.graph_hash)
        self.assertEqual(result["assessment_status"], "EVIDENCE_CONFLICT")
        self.assertEqual({x["kind"] for x in result["evidence_conflicts"]},
                         {"GRAPH_HASH_MISMATCH", "UNKNOWN_BUSINESS_FUNCTION"})

    def test_rejects_sensitive_fields_and_unrecognised_metadata(self):
        doc = json.loads(json.dumps(self.doc))
        doc["events"][0]["request_body"] = "private payload"
        with self.assertRaisesRegex(ValueError, "potentially sensitive fields"):
            validate_runtime_evidence(doc, self.graph, self.graph_hash)
        doc = json.loads(json.dumps(self.doc))
        doc["session"]["user_email"] = "private@example.invalid"
        with self.assertRaisesRegex(ValueError, "unsupported fields"):
            validate_runtime_evidence(doc, self.graph, self.graph_hash)

    def test_rejects_oversized_identifiers_and_malformed_runtime_digest(self):
        doc = json.loads(json.dumps(self.doc))
        doc["session"]["session_id"] = "s" * 129
        with self.assertRaisesRegex(ValueError, "session_id"):
            validate_runtime_evidence(doc, self.graph, self.graph_hash)
        doc = json.loads(json.dumps(self.doc))
        doc["events"][0]["event_id"] = "e" * 257
        with self.assertRaisesRegex(ValueError, "event_id"):
            validate_runtime_evidence(doc, self.graph, self.graph_hash)
        doc = json.loads(json.dumps(self.doc))
        doc["events"][0]["correlation_id"] = "c" * 257
        with self.assertRaisesRegex(ValueError, "correlation_id"):
            validate_runtime_evidence(doc, self.graph, self.graph_hash)
        with self.assertRaisesRegex(ValueError, "runtime_evidence_sha256"):
            validate_runtime_evidence(self.doc, self.graph, self.graph_hash, "not-a-digest")

    def test_rejects_duplicate_ids_bad_parent_and_timezone_less_timestamp(self):
        doc = json.loads(json.dumps(self.doc))
        doc["events"][1]["event_id"] = "e1"
        with self.assertRaisesRegex(ValueError, "duplicate runtime event_id"):
            validate_runtime_evidence(doc, self.graph, self.graph_hash)
        doc = json.loads(json.dumps(self.doc))
        doc["events"][1]["parent_event_id"] = "missing"
        result = validate_runtime_evidence(doc, self.graph, self.graph_hash)
        self.assertIn("MISSING_PARENT_EVENT", {x["kind"] for x in result["evidence_conflicts"]})
        doc = json.loads(json.dumps(self.doc))
        doc["events"][0]["timestamp"] = "2026-11-01T12:00:01"
        with self.assertRaisesRegex(ValueError, "include a timezone"):
            validate_runtime_evidence(doc, self.graph, self.graph_hash)
        doc = json.loads(json.dumps(self.doc))
        doc["events"][1]["parent_event_id"] = "p" * 257
        with self.assertRaisesRegex(ValueError, "parent_event_id"):
            validate_runtime_evidence(doc, self.graph, self.graph_hash)
        doc = json.loads(json.dumps(self.doc))
        doc["events"][1]["parent_event_id"] = 123
        with self.assertRaisesRegex(ValueError, "parent_event_id"):
            validate_runtime_evidence(doc, self.graph, self.graph_hash)

    def test_parent_cycle_is_reported_even_when_timestamps_are_equal(self):
        doc = json.loads(json.dumps(self.doc))
        for event in doc["events"]:
            event["timestamp"] = "2026-11-01T12:00:01+08:00"
        doc["events"][0]["parent_event_id"] = "e4"
        result = validate_runtime_evidence(doc, self.graph, self.graph_hash)
        self.assertIn("PARENT_EVENT_CYCLE", {x["kind"] for x in result["evidence_conflicts"]})
        self.assertEqual(result["assessment_status"], "EVIDENCE_CONFLICT")

    def test_cli_writes_validation_report_and_returns_conflict_exit_code(self):
        from usit.cli import main
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            graph_path, event_path, out_path = base / "graph.json", base / "events.json", base / "validated.json"
            graph_path.write_bytes(self.graph_bytes)
            event_path.write_text(json.dumps(self.doc), encoding="utf-8")
            with redirect_stdout(StringIO()):
                code = main(["validate-runtime-evidence", "--events", str(event_path),
                             "--graph", str(graph_path), "--out", str(out_path)])
            self.assertEqual(code, 0)
            report = json.loads(out_path.read_text(encoding="utf-8"))
            self.assertEqual(report["event_count"], 4)
            self.assertEqual(report["source_hashes"]["runtime_evidence_sha256"],
                             hashlib.sha256(event_path.read_bytes()).hexdigest())
            bad_path = base / "bad-events.json"
            bad_doc = json.loads(json.dumps(self.doc))
            bad_doc["source_graph_sha256"] = "f" * 64
            bad_path.write_text(json.dumps(bad_doc, ensure_ascii=False), encoding="utf-8")
            bad_out = base / "bad-report.json"
            with redirect_stdout(StringIO()):
                bad_code = main(["validate-runtime-evidence", "--events", str(bad_path),
                                 "--graph", str(graph_path), "--out", str(bad_out)])
            self.assertEqual(bad_code, 1)
            self.assertEqual(json.loads(bad_out.read_text(encoding="utf-8"))["assessment_status"], "EVIDENCE_CONFLICT")
            with redirect_stdout(StringIO()):
                overwrite_code = main(["validate-runtime-evidence", "--events", str(event_path),
                                       "--graph", str(graph_path), "--out", str(out_path)])
            self.assertEqual(overwrite_code, 2)

    def test_cli_rejects_oversized_runtime_evidence_before_reading(self):
        from usit.runtime_evidence import MAX_EVENT_BYTES
        from usit.cli import main
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            event_path = base / "oversized.json"
            with event_path.open("wb") as stream:
                stream.truncate(MAX_EVENT_BYTES + 1)
            graph_path = base / "graph.json"
            graph_path.write_bytes(self.graph_bytes)
            out_path = base / "report.json"
            with redirect_stdout(StringIO()):
                code = main(["validate-runtime-evidence", "--events", str(event_path),
                             "--graph", str(graph_path), "--out", str(out_path)])
            self.assertEqual(code, 2)
            self.assertFalse(out_path.exists())

    def test_cli_rejects_final_component_symlink_without_following_it(self):
        from usit.cli import main
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            target = base / "events.json"
            target.write_text(json.dumps(self.doc), encoding="utf-8")
            link = base / "events-link.json"
            try:
                link.symlink_to(target)
            except (OSError, NotImplementedError):
                self.skipTest("symlink creation is unavailable")
            graph_path = base / "graph.json"
            graph_path.write_bytes(self.graph_bytes)
            out_path = base / "report.json"
            with redirect_stdout(StringIO()):
                code = main(["validate-runtime-evidence", "--events", str(link),
                             "--graph", str(graph_path), "--out", str(out_path)])
            self.assertEqual(code, 2)
            self.assertFalse(out_path.exists())


if __name__ == "__main__":
    unittest.main()
