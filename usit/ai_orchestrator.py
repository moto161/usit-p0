"""Explicit, auditable multi-model task orchestration for local Agent2API."""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable

from .ai_gateway import Agent2APIClient, DEFAULT_BASE_URL, DEFAULT_API_KEY_ENV
from .safeio import read_bounded_no_follow

SCHEMA = "usit-ai-model-registry-v1"
MAX_CONFIG_BYTES = 1024 * 1024
MAX_MODELS = 8
TASK_ROLES = {
    "architecture": "architecture_analysis",
    "defect": "defect_analysis",
    "test_design": "test_design",
    "review": "independent_review",
    "general": "general_analysis",
}


def load_model_registry(path: str | Path) -> dict[str, Any]:
    source = Path(path).expanduser()
    try:
        raw = read_bounded_no_follow(source, MAX_CONFIG_BYTES)
    except ValueError as exc:
        if "exceeds size limit" in str(exc):
            raise ValueError("model registry exceeds 1 MiB") from None
        raise ValueError("model registry must not be a symbolic link and must be a regular file") from None
    except OSError:
        raise ValueError("model registry must not be a symbolic link and must be a regular file") from None
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("model registry must be valid UTF-8 JSON") from None
    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA:
        raise ValueError(f"model registry schema must be {SCHEMA}")
    rows = doc.get("models")
    if not isinstance(rows, list) or not rows or len(rows) > MAX_MODELS:
        raise ValueError(f"model registry must contain 1..{MAX_MODELS} models")
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("each model entry must be an object")
        model_id = row.get("model_id")
        roles = row.get("roles")
        if not isinstance(model_id, str) or not model_id.strip() or model_id in seen:
            raise ValueError("model_id must be non-empty and unique")
        if not isinstance(row.get("enabled"), bool):
            raise ValueError(f"{model_id}: enabled must be boolean")
        if not isinstance(roles, list) or not roles or any(
            role not in set(TASK_ROLES.values()) for role in roles
        ):
            raise ValueError(f"{model_id}: roles must use documented role identifiers")
        seen.add(model_id)
    return doc


def select_models(registry: dict[str, Any], task: str, limit: int = MAX_MODELS) -> list[dict[str, Any]]:
    if task not in TASK_ROLES:
        raise ValueError(f"unsupported task: {task}")
    if not 1 <= int(limit) <= MAX_MODELS:
        raise ValueError(f"model limit must be 1..{MAX_MODELS}")
    role = TASK_ROLES[task]
    selected = [
        {"model_id": row["model_id"], "roles": list(row["roles"])}
        for row in registry["models"]
        if row["enabled"] and role in row["roles"]
    ]
    return selected[:int(limit)]


def run_ensemble(
    registry: dict[str, Any],
    task: str,
    prompt: str,
    *,
    max_models: int = 3,
    timeout: float = 60.0,
    max_tokens: int = 2048,
    base_url: str = DEFAULT_BASE_URL,
    api_key_env: str = DEFAULT_API_KEY_ENV,
    client_factory: Callable[..., Agent2APIClient] = Agent2APIClient,
) -> dict[str, Any]:
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("prompt must be non-empty")
    if len(prompt) > 100_000:
        raise ValueError("prompt exceeds 100,000 characters")
    selected = select_models(registry, task, max_models)
    if not selected:
        raise ValueError(f"no enabled model is explicitly assigned to task role {TASK_ROLES[task]}")
    system = (
        "You are one analysis component in USIT. Analyze only the supplied material. "
        "Separate evidence-backed observations from hypotheses and unknowns. "
        "Do not claim tests or execution occurred unless explicit evidence is supplied. "
        "Return concise findings, evidence references, uncertainties, and recommended verification."
    )

    def invoke(entry: dict[str, Any]) -> dict[str, Any]:
        try:
            client = client_factory(base_url, api_key_env=api_key_env, timeout=timeout)
            result = client.chat(entry["model_id"], prompt, system, max_tokens)
            return {
                "model_id": entry["model_id"],
                "role": TASK_ROLES[task],
                "status": "SUCCEEDED",
                "content": result["content"],
                "finish_reason": result.get("finish_reason"),
                "usage": result.get("usage"),
                "request_id": result.get("request_id"),
            }
        except Exception as exc:
            # Error text from third-party gateways is not propagated; it may
            # contain request details or credentials.
            return {
                "model_id": entry["model_id"],
                "role": TASK_ROLES[task],
                "status": "FAILED",
                "error_type": type(exc).__name__,
                "content": None,
            }

    results = []
    with ThreadPoolExecutor(max_workers=min(len(selected), MAX_MODELS)) as pool:
        futures = {pool.submit(invoke, entry): entry for entry in selected}
        for future in as_completed(futures):
            results.append(future.result())
    order = {entry["model_id"]: i for i, entry in enumerate(selected)}
    results.sort(key=lambda item: order[item["model_id"]])
    successes = sum(item["status"] == "SUCCEEDED" for item in results)
    return {
        "schema": "usit-ai-ensemble-result-v1",
        "task": task,
        "required_role": TASK_ROLES[task],
        "selected_model_count": len(selected),
        "succeeded_model_count": successes,
        "failed_model_count": len(results) - successes,
        "status": "SUCCEEDED" if successes == len(results) else (
            "PARTIAL" if successes else "FAILED"
        ),
        "prompt_logged": False,
        "execution_claim": "MODEL_RESPONSES_ONLY_NOT_PROJECT_TEST_EXECUTION",
        "results": results,
    }


def registry_template() -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "description": "Assign roles only after manually validating each model.",
        "models": [
            {
                "model_id": "REPLACE_WITH_EXACT_MODEL_ID",
                "enabled": False,
                "roles": ["general_analysis", "independent_review"],
                "notes": "Capability assignment requires human verification.",
            }
        ],
    }
