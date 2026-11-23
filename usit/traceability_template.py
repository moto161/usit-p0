"""Create a blank, graph-version-bound feature-to-function review manifest."""
from __future__ import annotations
from typing import Any


def create_feature_mapping_template(semantics: dict[str, Any], graph_sha256: str) -> dict[str, Any]:
    if not isinstance(semantics, dict) or not isinstance(semantics.get("features", []), list):
        raise ValueError("semantics.features must be a list")
    if not isinstance(graph_sha256, str) or len(graph_sha256) != 64 or any(c not in "0123456789abcdef" for c in graph_sha256.lower()):
        raise ValueError("graph_sha256 must be a 64-character hexadecimal SHA-256")
    rows = []
    seen = set()
    for index, feature in enumerate(semantics["features"]):
        if not isinstance(feature, dict) or not isinstance(feature.get("name"), str) or not feature["name"]:
            continue
        source = feature.get("source") if isinstance(feature.get("source"), str) else ""
        line = feature.get("line")
        if line is not None and (not isinstance(line, int) or isinstance(line, bool) or line < 1):
            line = None
        key = (feature["name"], source, line)
        if key in seen:
            raise ValueError(f"ambiguous duplicate requirement feature identity: {key}")
        seen.add(key)
        rows.append({
            "feature_name": feature["name"],
            "source_path": source,
            "source_line": line,
            "decision": "NEEDS_REVIEW",
            "business_function_id": None,
            "reviewed_by": "",
            "reviewed_at": "",
            "rationale": "",
            "template_note": "填写真实审阅结论；不得按名称或模块猜测 business_function_id。",
        })
    rows.sort(key=lambda item: (item["source_path"], item["source_line"] or 0, item["feature_name"]))
    return {
        "schema_version": "usit-feature-function-mapping-v1",
        "template_status": "INCOMPLETE_REVIEW_REQUIRED",
        "source_graph_sha256": graph_sha256.lower(),
        "mapping_count": len(rows),
        "review_instructions": [
            "逐项核对需求原文和业务功能视图的证据后再填写审阅结论。",
            "decision 只能为 APPROVED_LINK、REJECTED_LINK 或 NEEDS_REVIEW。",
            "只有 APPROVED_LINK 可以填写 business_function_id，且必须是图谱中精确存在的 ID。",
            "reviewed_by、reviewed_at（ISO-8601）和 rationale 必须由实际审阅者填写；不得伪造或留空。",
            "图谱 SHA-256 发生变化时重新生成模板并重新审阅，不迁移旧映射。",
        ],
        "mappings": rows,
    }
