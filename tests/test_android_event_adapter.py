from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from usit.android_event_adapter import adapt_android_jsonl
from usit.runtime_evidence import validate_runtime_evidence


class AndroidEventAdapterTests(unittest.TestCase):
    def setUp(self):
        self.graph = {"schema_version": "usit-graph-v1", "business_understanding": {
            "business_function_views": [{"business_function_id": "bf-photo-view"}]}}
        self.graph_bytes = json.dumps(self.graph, ensure_ascii=False).encode()
        self.graph_hash = hashlib.sha256(self.graph_bytes).hexdigest()
        self.records = [
            {"event_id": "a1", "timestamp": "2026-11-01T12:00:01+08:00",
             "event": "screen_opened", "correlation_id": "c1",
             "business_function_id": "bf-photo-view", "screen_id": "PhotoDetail",
             "app_state": "RESUMED"},
            {"event_id": "a2", "timestamp": "2026-11-01T12:00:02+08:00",
             "event": "ui_action", "correlation_id": "c1", "parent_event_id": "a1",
             "business_function_id": "bf-photo-view", "screen_id": "PhotoDetail",
             "control_id": "openButton", "action": "tap", "result": "accepted"},
            {"event_id": "a3", "timestamp": "2026-11-01T12:00:03+08:00",
             "event": "permission_result", "correlation_id": "c1", "parent_event_id": "a2",
             "permission": "android.permission.READ_MEDIA_IMAGES", "granted": True},
            {"event_id": "a4", "timestamp": "2026-11-01T12:00:04+08:00",
             "event": "http_request", "correlation_id": "c1", "parent_event_id": "a2",
             "http_method": "GET", "route_template": "/api/photos/{id}"},
            {"event_id": "a5", "timestamp": "2026-11-01T12:00:05+08:00",
             "event": "http_response", "correlation_id": "c1", "parent_event_id": "a4",
             "status_code": 200},
        ]

    def _raw(self):
        return ("ordinary log line\n" + "\n".join(
            "USIT_ANDROID_EVENT " + json.dumps(row, ensure_ascii=False) for row in self.records
        ) + "\n").encode()

    def test_adapts_android_events_to_valid_usit_runtime_schema(self):
        result = adapt_android_jsonl(self._raw(), self.graph, self.graph_hash,
                                     "android-session-01", "2026-11-01T12:00:00+08:00")
        self.assertEqual(result["schema_version"], "usit-runtime-evidence-v1")
        self.assertEqual(result["session"]["platform"], "ANDROID")
        self.assertEqual(len(result["events"]), 5)
        self.assertEqual(result["events"][1]["component"], "PhotoDetail/openButton")
        validated = validate_runtime_evidence(result, self.graph, self.graph_hash)
        self.assertEqual(validated["event_count"], 5)
        self.assertEqual(validated["assessment_status"], "VALIDATED_EVENT_STRUCTURE")

    def test_rejects_unknown_function_sensitive_fields_and_bad_prefix_records(self):
        bad = [dict(row) for row in self.records]
        bad[0]["business_function_id"] = "bf-unknown"
        raw = ("USIT_ANDROID_EVENT " + json.dumps(bad[0])).encode()
        with self.assertRaisesRegex(ValueError, "unknown business_function_id"):
            adapt_android_jsonl(raw, self.graph, self.graph_hash, "s1", "2026-11-01T12:00:00+08:00")
        bad[0] = dict(self.records[0], access_token="do-not-ingest")
        raw = ("USIT_ANDROID_EVENT " + json.dumps(bad[0])).encode()
        with self.assertRaisesRegex(ValueError, "unsupported fields"):
            adapt_android_jsonl(raw, self.graph, self.graph_hash, "s1", "2026-11-01T12:00:00+08:00")
        with self.assertRaisesRegex(ValueError, "no prefixed"):
            adapt_android_jsonl(b"ordinary logcat only\n", self.graph, self.graph_hash,
                                "s1", "2026-11-01T12:00:00+08:00")

    def test_rejects_unhashable_event_and_business_function_ids(self):
        for field, value, message in (
            ("event", [], "unsupported event type"),
            ("business_function_id", {}, "unknown business_function_id"),
        ):
            record = dict(self.records[0])
            record[field] = value
            raw = ("USIT_ANDROID_EVENT " + json.dumps(record)).encode()
            with self.assertRaisesRegex(ValueError, message):
                adapt_android_jsonl(raw, self.graph, self.graph_hash, "s1", "2026-11-01T12:00:00+08:00")

    def test_rejects_invalid_graph_shape_and_non_bytes_input(self):
        with self.assertRaisesRegex(ValueError, "usit-graph-v1"):
            adapt_android_jsonl(self._raw(), {"schema_version": "wrong"}, self.graph_hash,
                                "s1", "2026-11-01T12:00:00+08:00")
        with self.assertRaisesRegex(ValueError, "must be bytes"):
            adapt_android_jsonl("not-bytes", self.graph, self.graph_hash,
                                "s1", "2026-11-01T12:00:00+08:00")

    def test_rejects_duplicate_ids_and_timezone_less_timestamps(self):
        records = [dict(row) for row in self.records[:2]]
        records[1]["event_id"] = records[0]["event_id"]
        raw = ("\n".join("USIT_ANDROID_EVENT " + json.dumps(row) for row in records)).encode()
        with self.assertRaisesRegex(ValueError, "duplicate event_id"):
            adapt_android_jsonl(raw, self.graph, self.graph_hash, "s1", "2026-11-01T12:00:00+08:00")
        records = [dict(row) for row in self.records[:1]]
        records[0]["timestamp"] = "2026-11-01T12:00:01"
        raw = ("USIT_ANDROID_EVENT " + json.dumps(records[0])).encode()
        with self.assertRaisesRegex(ValueError, "requires timezone"):
            adapt_android_jsonl(raw, self.graph, self.graph_hash, "s1", "2026-11-01T12:00:00+08:00")

    def test_rejects_duplicate_json_keys_in_android_event_records(self):
        raw = (b'USIT_ANDROID_EVENT {"event_id":"dup-1","timestamp":"2026-11-01T12:00:01+08:00",'
               b'"event":"screen_opened","event":"database_change"}')
        with self.assertRaisesRegex(ValueError, "duplicate JSON key: event"):
            adapt_android_jsonl(raw, self.graph, self.graph_hash, "s1",
                                "2026-11-01T12:00:00+08:00")

    def test_file_adapter_rejects_duplicate_json_keys_in_business_graph(self):
        from usit.android_event_adapter import adapt_android_jsonl_file
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            event_path = base / "events.jsonl"
            event_path.write_bytes(self._raw())
            graph_path = base / "graph.json"
            graph_path.write_text(
                '{"schema_version":"usit-graph-v1","schema_version":"other",'
                '"business_understanding":{"business_function_views":[]}}',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "duplicate JSON key: schema_version"):
                adapt_android_jsonl_file(event_path, graph_path, "s1",
                                         "2026-11-01T12:00:00+08:00")

    def test_cli_writes_schema_file_and_never_overwrites(self):
        from usit.cli import main
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            graph_path, input_path, out_path = base / "graph.json", base / "android.jsonl", base / "runtime.json"
            graph_path.write_bytes(self.graph_bytes)
            input_path.write_bytes(self._raw())
            args = ["adapt-android-events", "--input", str(input_path), "--graph", str(graph_path),
                    "--session-id", "session-cli", "--captured-at", "2026-11-01T12:00:00+08:00",
                    "--out", str(out_path)]
            with redirect_stdout(StringIO()):
                code = main(args)
            self.assertEqual(code, 0)
            document = json.loads(out_path.read_text(encoding="utf-8"))
            self.assertEqual(len(document["events"]), 5)
            self.assertEqual(document["session"]["platform"], "ANDROID")
            validation_path = base / "validated.json"
            with redirect_stdout(StringIO()):
                validation_code = main(["validate-runtime-evidence", "--events", str(out_path),
                    "--graph", str(graph_path), "--out", str(validation_path)])
            self.assertEqual(validation_code, 0)
            validated = json.loads(validation_path.read_text(encoding="utf-8"))
            self.assertEqual(validated["event_count"], 5)
            with redirect_stdout(StringIO()):
                overwrite = main(args)
            self.assertEqual(overwrite, 2)

    def test_file_adapter_rejects_oversized_inputs_before_unbounded_read(self):
        from usit.android_event_adapter import MAX_INPUT_BYTES, _read
        with tempfile.TemporaryDirectory() as td:
            oversized = Path(td) / "oversized.jsonl"
            with oversized.open("wb") as stream:
                stream.truncate(MAX_INPUT_BYTES + 1)
            with self.assertRaisesRegex(ValueError, "exceeds size limit"):
                _read(oversized)

    def test_file_adapter_rejects_final_component_symlink(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            target = base / "events.jsonl"
            target.write_bytes(self._raw())
            link = base / "events-link.jsonl"
            try:
                link.symlink_to(target)
            except (OSError, NotImplementedError):
                self.skipTest("symlink creation is unavailable")
            from usit.android_event_adapter import _read
            with self.assertRaises((OSError, ValueError)):
                _read(link)

    def test_file_adapter_rejects_oversized_graph_before_unbounded_read(self):
        from usit.android_event_adapter import MAX_INPUT_BYTES, adapt_android_jsonl_file
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            event_path = base / "events.jsonl"
            event_path.write_bytes(self._raw())
            graph_path = base / "oversized-graph.json"
            with graph_path.open("wb") as stream:
                stream.truncate(MAX_INPUT_BYTES + 1)
            with self.assertRaisesRegex(ValueError, "exceeds size limit"):
                adapt_android_jsonl_file(event_path, graph_path, "s1", "2026-11-01T12:00:00+08:00")


if __name__ == "__main__":
    unittest.main()
