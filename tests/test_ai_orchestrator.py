from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from usit.ai_orchestrator import (
    SCHEMA, load_model_registry, select_models, run_ensemble, registry_template
)


class AIOrchestratorTests(unittest.TestCase):
    def setUp(self):
        self.registry = {
            "schema": SCHEMA,
            "models": [
                {"model_id": "model-a", "enabled": True, "roles": ["architecture_analysis", "general_analysis"]},
                {"model_id": "model-b", "enabled": True, "roles": ["architecture_analysis", "independent_review"]},
                {"model_id": "model-c", "enabled": False, "roles": ["architecture_analysis"]},
                {"model_id": "model-d", "enabled": True, "roles": ["test_design"]},
            ],
        }

    def test_selects_only_enabled_explicitly_assigned_models(self):
        selected = select_models(self.registry, "architecture")
        self.assertEqual([x["model_id"] for x in selected], ["model-a", "model-b"])

    def test_no_implicit_role_guessing(self):
        self.assertEqual(select_models(self.registry, "defect"), [])

    def test_rejects_unknown_task_and_invalid_limit(self):
        with self.assertRaises(ValueError):
            select_models(self.registry, "magic")
        with self.assertRaises(ValueError):
            select_models(self.registry, "general", 0)

    def test_loads_registry_and_rejects_duplicate_ids(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "models.json"
            p.write_text(json.dumps(self.registry), encoding="utf-8")
            self.assertEqual(len(load_model_registry(p)["models"]), 4)
            duplicate = dict(self.registry)
            duplicate["models"] = [self.registry["models"][0], self.registry["models"][0]]
            p.write_text(json.dumps(duplicate), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "unique"):
                load_model_registry(p)

    def test_rejects_wrong_schema_and_symlink(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "models.json"
            p.write_text('{"schema":"wrong","models":[]}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "schema"):
                load_model_registry(p)
            target = Path(td) / "target.json"
            target.write_text(json.dumps(self.registry), encoding="utf-8")
            link = Path(td) / "link.json"
            try:
                link.symlink_to(target)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks unavailable on this runner")
            with self.assertRaisesRegex(ValueError, "symbolic link"):
                load_model_registry(link)

    def test_ensemble_returns_partial_when_one_model_fails(self):
        class FakeClient:
            def __init__(self, *args, **kwargs):
                pass
            def chat(self, model, prompt, system, max_tokens):
                if model == "model-b":
                    raise RuntimeError("sensitive gateway details")
                return {"content": "analysis", "finish_reason": "stop",
                        "usage": {"completion_tokens": 4}, "request_id": "r1"}
        result = run_ensemble(self.registry, "architecture", "safe prompt",
                              client_factory=FakeClient)
        self.assertEqual(result["status"], "PARTIAL")
        self.assertEqual(result["succeeded_model_count"], 1)
        self.assertEqual(result["failed_model_count"], 1)
        self.assertNotIn("sensitive gateway details", json.dumps(result))

    def test_ensemble_fails_closed_without_assigned_models(self):
        with self.assertRaisesRegex(ValueError, "no enabled model"):
            run_ensemble(self.registry, "defect", "safe prompt")

    def test_rejects_empty_or_oversized_prompt(self):
        with self.assertRaises(ValueError):
            run_ensemble(self.registry, "architecture", " ")
        with self.assertRaises(ValueError):
            run_ensemble(self.registry, "architecture", "x" * 100001)

    def test_registry_template_is_disabled_by_default(self):
        self.assertFalse(registry_template()["models"][0]["enabled"])


if __name__ == "__main__":
    unittest.main()
