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


    def test_cli_creates_registry_template_without_overwriting(self):
        from contextlib import redirect_stdout
        from io import StringIO
        from usit import cli

        with tempfile.TemporaryDirectory() as td:
            output_path = Path(td) / "registry.json"
            output = StringIO()
            with redirect_stdout(output):
                code = cli.main(["ai-init-registry", "--out", str(output_path)])
            self.assertEqual(code, 0)
            self.assertFalse(json.loads(output_path.read_text(encoding="utf-8"))["models"][0]["enabled"])
            with redirect_stdout(StringIO()):
                code = cli.main(["ai-init-registry", "--out", str(output_path)])
            self.assertEqual(code, 2)

    def test_cli_ai_ensemble_reports_partial_exit_status(self):
        from contextlib import redirect_stdout
        from io import StringIO
        from usit import cli

        with tempfile.TemporaryDirectory() as td:
            config_path = Path(td) / "registry.json"
            config_path.write_text(json.dumps(self.registry), encoding="utf-8")
            fake_result = {
                "schema": "usit-ai-ensemble-result-v1", "task": "architecture",
                "required_role": "architecture_analysis", "selected_model_count": 2,
                "succeeded_model_count": 1, "failed_model_count": 1,
                "status": "PARTIAL", "prompt_logged": False,
                "execution_claim": "MODEL_RESPONSES_ONLY_NOT_PROJECT_TEST_EXECUTION",
                "results": [],
            }
            output = StringIO()
            with patch.object(cli, "run_ensemble", return_value=fake_result):
                with redirect_stdout(output):
                    code = cli.main(["ai-ensemble", "--config", str(config_path),
                                     "--task", "architecture", "--prompt", "safe prompt"])
            self.assertEqual(code, 2)
            self.assertEqual(json.loads(output.getvalue())["status"], "PARTIAL")


if __name__ == "__main__":
    unittest.main()
