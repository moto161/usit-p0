from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from usit.ai_gateway import Agent2APIClient, _normalise_base_url


class Agent2APITests(unittest.TestCase):
    def test_normalises_default_agent2api_url(self):
        self.assertEqual(_normalise_base_url("http://127.0.0.1:3065"),
                         "http://127.0.0.1:3065/v1")
        self.assertEqual(_normalise_base_url("http://localhost:3065/v1"),
                         "http://localhost:3065/v1")

    def test_rejects_remote_hosts_and_unsafe_url_parts(self):
        for url in ("https://127.0.0.1:3065/v1", "http://example.com/v1",
                    "http://192.168.1.20:3065/v1",
                    "http://user:pass@127.0.0.1:3065/v1",
                    "http://127.0.0.1:3065/v1?x=1"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                _normalise_base_url(url)

    def test_reads_key_from_environment_without_exposing_it(self):
        with patch.dict(os.environ, {"USIT_AGENT2API_API_KEY": "test-secret"}):
            client = Agent2APIClient()
        self.assertEqual(client.api_key, "test-secret")
        self.assertNotIn("test-secret", repr(client))

    def test_model_list_is_sorted_and_filters_invalid_rows(self):
        client = Agent2APIClient(api_key="")
        with patch.object(client, "_request", return_value={"data": [
            {"id": "z-model"}, {"id": "a-model", "owned_by": "provider"},
            {"name": "missing-id"}, "bad"
        ]}):
            self.assertEqual([x["id"] for x in client.list_models()], ["a-model", "z-model"])

    def test_chat_extracts_text_and_usage(self):
        client = Agent2APIClient(api_key="")
        with patch.object(client, "_request", return_value={
            "id": "req-1", "model": "model-a",
            "choices": [{"message": {"role": "assistant", "content": "分析完成"},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3}
        }) as request:
            result = client.chat("model-a", "只分析这段无敏感信息的文本")
        self.assertEqual(result["content"], "分析完成")
        self.assertEqual(result["usage"]["completion_tokens"], 3)
        payload = request.call_args.args[2]
        self.assertEqual(payload["stream"], False)
        self.assertEqual(payload["model"], "model-a")

    def test_chat_rejects_empty_prompt_and_invalid_token_limit(self):
        client = Agent2APIClient(api_key="")
        with self.assertRaises(ValueError):
            client.chat("model-a", " ")
        with self.assertRaises(ValueError):
            client.chat("model-a", "hello", max_tokens=9000)

    def test_invalid_models_shape_is_rejected(self):
        client = Agent2APIClient(api_key="")
        with patch.object(client, "_request", return_value={"items": []}):
            with self.assertRaisesRegex(RuntimeError, "data array"):
                client.list_models()


    def test_cli_ai_models_and_ai_chat_use_local_provider(self):
        from contextlib import redirect_stdout
        from io import StringIO
        import json
        from usit import cli

        models_client = Agent2APIClient(api_key="")
        with patch.object(cli, "Agent2APIClient", return_value=models_client), \
             patch.object(models_client, "list_models", return_value=[{"id": "model-a"}]):
            output = StringIO()
            with redirect_stdout(output):
                code = cli.main(["ai-models"])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(output.getvalue())["models"][0]["id"], "model-a")

        with patch.object(cli, "Agent2APIClient", return_value=models_client), \
             patch.object(models_client, "chat", return_value={
                 "model": "model-a", "content": "ok", "finish_reason": "stop",
                 "usage": None, "request_id": "r1"
             }) as chat:
            output = StringIO()
            with redirect_stdout(output):
                code = cli.main(["ai-chat", "--model", "model-a", "--prompt", "hello"])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(output.getvalue())["content"], "ok")
            chat.assert_called_once()

if __name__ == "__main__":
    unittest.main()
