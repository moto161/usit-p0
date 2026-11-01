"""Evidence-linked business test design — static candidates only.

This module converts BusinessSemantics output into reviewable test scenarios.
It does not infer undocumented business rules, execute tests, or claim coverage.
Every scenario remains NOT_EXECUTED and carries the source evidence that caused
it to be proposed.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

SCHEMA_VERSION = "usit-business-test-plan-v1"
_PLAN_LIMIT = 10_000


def _stable_id(kind: str, payload: Mapping[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return f"bt-{kind}-{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:20]}"


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _source_ref(source: Any, line: Any = None, **extra: Any) -> dict[str, Any] | None:
    path = _text(source)
    if not path:
        return None
    ref: dict[str, Any] = {"path": path, "confidence": "STATIC_INFERENCE"}
    if isinstance(line, int) and not isinstance(line, bool) and line > 0:
        ref["line"] = line
    ref.update({k: v for k, v in extra.items() if v is not None and v != ""})
    return ref


def _candidate(kind: str, target: str, purpose: str, evidence: list[dict[str, Any]],
               steps: list[str], limitations: list[str], priority: str = "") -> dict[str, Any]:
    identity = {
        "kind": kind, "target": target, "purpose": purpose,
        "evidence": evidence, "steps": steps,
    }
    return {
        "test_id": _stable_id(kind, identity),
        "category": kind,
        "target": target,
        "purpose": purpose,
        "priority": priority or "UNSPECIFIED",
        "basis": "STATIC_INFERENCE",
        "evidence_refs": evidence,
        "preconditions": [
            "由项目负责人提供并批准的隔离测试环境",
            "明确的需求/验收标准及测试账号、测试数据",
        ],
        "steps": steps,
        "expected_result": (
            "必须与经确认的需求/验收标准一致；当前静态证据不足以确定具体结果，"
            "不得据此判定 PASS。"
        ),
        "design_status": "CANDIDATE_NEEDS_REQUIREMENTS",
        "execution_status": "NOT_EXECUTED",
        "limitations": limitations,
    }


def plan_business_tests(semantics: Mapping[str, Any]) -> dict[str, Any]:
    """Create deterministic test candidates from a BusinessSemantics result."""
    if not isinstance(semantics, Mapping):
        raise TypeError("semantics must be a mapping")
    for key in ("entities", "features", "endpoints", "discrepancies"):
        if not isinstance(semantics.get(key, []), list):
            raise ValueError(f"semantics.{key} must be a list")

    candidates: list[dict[str, Any]] = []
    warnings: list[str] = []

    for feature in semantics.get("features", []):
        if not isinstance(feature, Mapping):
            warnings.append("ignored malformed feature record")
            continue
        name = _text(feature.get("name"))
        if not name:
            warnings.append("feature without a name was not converted to a test")
            continue
        ref = _source_ref(feature.get("source"), feature.get("line"), feature= name)
        evidence = [ref] if ref else []
        candidates.append(_candidate(
            "feature_behavior", name,
            f"验证功能「{name}」是否按需求实现",
            evidence,
            [
                "依据需求文档确认该功能的前置条件、角色、输入和验收标准",
                "在隔离环境中执行正常流程并记录实际结果",
                "执行至少一个边界/异常场景，并核对数据与界面结果",
            ],
            ["当前提取结果没有结构化验收标准；不得把 PRD 的 DONE 标记当作测试通过。"],
            _text(feature.get("priority")),
        ))

    for endpoint in semantics.get("endpoints", []):
        if not isinstance(endpoint, Mapping):
            warnings.append("ignored malformed endpoint record")
            continue
        method, path = _text(endpoint.get("method")).upper(), _text(endpoint.get("path"))
        if not method or not path:
            warnings.append("endpoint missing method/path; no test candidate emitted")
            continue
        auth = _text(endpoint.get("code_auth")) or "UNKNOWN"
        source = _text(endpoint.get("source"))
        line = endpoint.get("line")
        ref = _source_ref(source.split(":")[0] if source else None, line,
                          http_method=method, endpoint=path, code_auth=auth)
        evidence = [ref] if ref else []
        candidates.append(_candidate(
            "endpoint_access", f"{method} {path}",
            "验证接口可访问性、身份认证与授权边界",
            evidence,
            [
                "在隔离环境中分别使用未登录、普通账号及适用的高权限账号请求接口",
                "记录响应状态、响应体、服务端审计记录及数据副作用",
                "将每种身份的实际权限与已批准的接口访问策略逐项比较",
            ],
            [
                f"静态代码认证信号为 {auth}，不等于运行时访问控制已生效。",
                "未从代码推断具体 HTTP 状态码或业务响应。",
            ],
        ))

    for entity in semantics.get("entities", []):
        if not isinstance(entity, Mapping):
            warnings.append("ignored malformed entity record")
            continue
        name = _text(entity.get("name"))
        fields = entity.get("fields", [])
        if not name:
            continue
        if not isinstance(fields, list):
            warnings.append(f"entity {name} has malformed fields")
            continue
        field_names = sorted({
            _text(item.get("name")) for item in fields
            if isinstance(item, Mapping) and _text(item.get("name"))
        })
        if not field_names:
            continue
        ref = _source_ref(entity.get("source"), entity=name, fields=field_names)
        evidence = [ref] if ref else []
        candidates.append(_candidate(
            "data_entity_integrity", name,
            f"验证数据实体 {name} 的字段映射、读写一致性与约束",
            evidence,
            [
                "将文档字段与实际数据库 schema/migration 逐项比对",
                "在隔离数据库中执行受控新增、读取、更新和删除场景",
                "核对持久化值、关联记录、权限过滤及异常回滚结果",
            ],
            [
                "当前实体字段来自静态文档提取，未连接数据库；schema 一致性尚未验证。",
                "字段约束、默认值和关系规则未被当前输入完整描述。",
            ],
        ))

    for discrepancy in semantics.get("discrepancies", []):
        if not isinstance(discrepancy, Mapping):
            warnings.append("ignored malformed discrepancy record")
            continue
        kind = _text(discrepancy.get("kind")) or "unknown_discrepancy"
        message = _text(discrepancy.get("message"))
        if not message:
            continue
        refs: list[dict[str, Any]] = []
        raw_evidence = discrepancy.get("evidence", [])
        if isinstance(raw_evidence, list):
            for item in raw_evidence:
                if not isinstance(item, Mapping):
                    continue
                ref = _source_ref(
                    item.get("rel_path") or item.get("source"),
                    item.get("line"),
                    field=item.get("field"), feature=item.get("feature"),
                    entity=item.get("entity"),
                )
                if ref:
                    refs.append(ref)
        candidates.append(_candidate(
            "discrepancy_verification", kind,
            f"独立验证静态差异：{message}",
            refs,
            [
                "复核引用的需求/数据库文档与代码证据是否对应同一功能和版本",
                "设计能区分该差异成立与不成立的正向、反向测试",
                "在授权隔离环境执行并保存请求、响应、数据状态和日志证据",
            ],
            [
                f"原始差异置信类型：{_text(discrepancy.get('confidence')) or 'UNKNOWN'}。",
                "静态差异只是测试线索，不是已确认缺陷。",
            ],
        ))

    if len(candidates) > _PLAN_LIMIT:
        raise ValueError(f"candidate limit exceeded: {len(candidates)} > {_PLAN_LIMIT}")

    candidates.sort(key=lambda item: (item["category"], item["target"], item["test_id"]))
    counts: dict[str, int] = {}
    for item in candidates:
        counts[item["category"]] = counts.get(item["category"], 0) + 1
    return {
        "schema_version": SCHEMA_VERSION,
        "model": "BusinessTestPlanner",
        "source_schema": _text(semantics.get("schema_version")) or "UNKNOWN",
        "project_root": _text(semantics.get("project_root")) or None,
        "candidate_count": len(candidates),
        "category_counts": dict(sorted(counts.items())),
        "execution_status": "NOT_EXECUTED",
        "design_note": "候选测试需经需求确认和用户授权后才能执行；本模块不执行命令。",
        "warnings": warnings,
        "candidates": candidates,
    }
