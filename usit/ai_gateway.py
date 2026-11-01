"""Safe client for a local Agent2API OpenAI-compatible gateway.

Only loopback HTTP endpoints are accepted. Credentials are read from the
process environment and are never included in results, logs, or exceptions.
"""
from __future__ import annotations

import json
import os
import ipaddress
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


DEFAULT_BASE_URL = "http://127.0.0.1:3065/v1"
DEFAULT_API_KEY_ENV = "USIT_AGENT2API_API_KEY"
MAX_RESPONSE_BYTES = 4 * 1024 * 1024


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _normalise_base_url(base_url: str) -> str:
    parsed = urllib.parse.urlsplit(base_url.strip())
    if parsed.scheme != "http" or not parsed.hostname:
        raise ValueError("gateway URL must use plain HTTP on loopback")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("gateway URL must not contain credentials, query, or fragment")
    host = parsed.hostname.lower()
    try:
        is_loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        is_loopback = host == "localhost"
    if not is_loopback:
        raise ValueError("gateway URL must resolve to localhost/loopback; remote hosts are blocked")
    path = parsed.path.rstrip("/")
    if path.endswith("/v1"):
        versioned = path
    else:
        versioned = path + "/v1"
    return urllib.parse.urlunsplit(("http", parsed.netloc, versioned, "", ""))


class Agent2APIClient:
    """Minimal non-streaming Agent2API client using only the Python stdlib."""

    def __init__(self, base_url: str = DEFAULT_BASE_URL, api_key: str | None = None,
                 api_key_env: str = DEFAULT_API_KEY_ENV, timeout: float = 20.0,
                 max_response_bytes: int = MAX_RESPONSE_BYTES):
        self.base_url = _normalise_base_url(base_url)
        self.api_key = api_key if api_key is not None else os.environ.get(api_key_env, "")
        self.timeout = max(1.0, min(float(timeout), 120.0))
        self.max_response_bytes = max(1024, min(int(max_response_bytes), MAX_RESPONSE_BYTES))
        self._opener = urllib.request.build_opener(_NoRedirect())

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        url = self.base_url + path
        headers = {"Accept": "application/json", "User-Agent": "USIT-Agent2API/0.1"}
        data = None
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if self.api_key:
            headers["Authorization"] = "Bearer " + self.api_key
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                raw = response.read(self.max_response_bytes + 1)
        except urllib.error.HTTPError as exc:
            # Never echo response bodies: gateways may include sensitive diagnostics.
            raise RuntimeError(f"Agent2API returned HTTP {exc.code} for {path}") from None
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", None)
            reason_name = type(reason).__name__ if reason is not None else "ConnectionError"
            raise RuntimeError(f"Cannot reach local Agent2API ({reason_name})") from None
        except TimeoutError:
            raise RuntimeError("Agent2API request timed out") from None
        if len(raw) > self.max_response_bytes:
            raise RuntimeError("Agent2API response exceeds configured size limit")
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise RuntimeError("Agent2API returned a non-JSON or invalid UTF-8 response") from None
        if not isinstance(value, dict):
            raise RuntimeError("Agent2API returned an unexpected JSON shape")
        return value

    def list_models(self) -> list[dict[str, Any]]:
        document = self._request("GET", "/models")
        rows = document.get("data")
        if not isinstance(rows, list):
            raise RuntimeError("Agent2API /v1/models response has no data array")
        models = []
        for row in rows:
            if isinstance(row, dict) and isinstance(row.get("id"), str) and row["id"]:
                models.append({"id": row["id"], "object": row.get("object", "model"),
                               "owned_by": row.get("owned_by")})
        return sorted(models, key=lambda item: item["id"])

    def chat(self, model: str, prompt: str, system: str | None = None,
             max_tokens: int = 1024) -> dict[str, Any]:
        if not isinstance(model, str) or not model.strip():
            raise ValueError("model must be a non-empty model ID")
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("prompt must be non-empty")
        if len(prompt) > 100_000 or (system is not None and len(system) > 20_000):
            raise ValueError("prompt exceeds the local client size limit")
        if not 1 <= int(max_tokens) <= 8192:
            raise ValueError("max_tokens must be between 1 and 8192")
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        document = self._request("POST", "/chat/completions", {
            "model": model, "messages": messages, "stream": False,
            "max_tokens": int(max_tokens),
        })
        choices = document.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise RuntimeError("Agent2API chat response contains no choices")
        message = choices[0].get("message")
        if not isinstance(message, dict) or not isinstance(message.get("content"), str):
            raise RuntimeError("Agent2API chat response contains no text content")
        usage = document.get("usage")
        return {
            "model": document.get("model", model),
            "content": message["content"],
            "finish_reason": choices[0].get("finish_reason"),
            "usage": usage if isinstance(usage, dict) else None,
            "request_id": document.get("id"),
        }
