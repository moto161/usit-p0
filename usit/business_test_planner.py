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


def plan_business_tests(semantics: Mapping[str, Any], business_graph: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Create deterministic candidates from semantics and optional evidence-linked graph."""
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

    # S2.12: turn exact API-contract alignment into reviewable functional candidates.
    # The optional graph contributes only its already-linked endpoint execution paths.
    understanding = business_graph.get("business_understanding", {}) if isinstance(business_graph, Mapping) else {}
    execution_paths = understanding.get("endpoint_execution_paths", []) if isinstance(understanding, Mapping) else []
    if not isinstance(execution_paths, list):
        execution_paths = []
        warnings.append("business_graph.endpoint_execution_paths is malformed; graph evidence omitted")

    def _path_for(method: str, endpoint_path: str) -> Mapping[str, Any] | None:
        matches = [item for item in execution_paths if isinstance(item, Mapping)
                   and _text(item.get("method")).upper() == method
                   and _text(item.get("endpoint")) == endpoint_path]
        return matches[0] if len(matches) == 1 else None

    def _api_refs(alignment: Mapping[str, Any], flow: Mapping[str, Any] | None) -> list[dict[str, Any]]:
        refs: list[dict[str, Any]] = []
        doc = alignment.get("document_evidence")
        if isinstance(doc, Mapping):
            ref = _source_ref(doc.get("source"), doc.get("line"), evidence_kind="documented_api_contract")
            if ref:
                refs.append(ref)
        code_items = alignment.get("code_evidence")
        if isinstance(code_items, list):
            for item in code_items:
                if isinstance(item, Mapping):
                    ref = _source_ref(item.get("source"), item.get("line"),
                                      evidence_kind="code_route", http_method=method,
                                      endpoint=endpoint_path)
                    if ref:
                        refs.append(ref)
        if isinstance(flow, Mapping):
            source = flow.get("source")
            if isinstance(source, Mapping):
                ref = _source_ref(source.get("path"), source.get("line"), evidence_kind="endpoint_execution_path")
                if ref:
                    refs.append(ref)
            handler = flow.get("handler")
            if isinstance(handler, Mapping):
                ref = _source_ref(handler.get("path"), handler.get("line"),
                                  evidence_kind="resolved_handler", symbol_id=handler.get("symbol_id"))
                if ref:
                    refs.append(ref)
            for key, kind in (("call_chain", "call_chain"), ("database_accesses", "database_access"),
                              ("authorization", "authorization"), ("identity_resource_flow", "identity_resource")):
                values = flow.get(key)
                if key == "authorization" and isinstance(values, Mapping):
                    values = values.get("evidence", [])
                if not isinstance(values, list):
                    continue
                for item in values:
                    if not isinstance(item, Mapping):
                        continue
                    location = item.get("call_site") if kind == "call_chain" else item
                    location = location if isinstance(location, Mapping) else {}
                    ref = _source_ref(location.get("path"), location.get("line"),
                                      evidence_kind=kind,
                                      evidence_id=(item.get("database_access_id") or item.get("authorization_check_id")
                                                   or item.get("identity_evidence_id") or item.get("target_id")))
                    if ref:
                        refs.append(ref)
        # Stable de-duplication; no inferred links are added here.
        unique: dict[str, dict[str, Any]] = {}
        for ref in refs:
            key = json.dumps(ref, sort_keys=True, ensure_ascii=False)
            unique[key] = ref
        return [unique[key] for key in sorted(unique)]

    alignments = semantics.get("endpoint_contract_alignment", [])
    if isinstance(alignments, list):
        for alignment in alignments:
            if not isinstance(alignment, Mapping):
                warnings.append("ignored malformed endpoint contract alignment record")
                continue
            status = _text(alignment.get("status"))
            method = _text(alignment.get("method")).upper()
            endpoint_path = _text(alignment.get("path"))
            if not method or not endpoint_path or status not in {
                "EXACT_MATCH", "MULTIPLE_CODE_MATCHES",
                "DOCUMENTED_NOT_FOUND_IN_CODE", "CODE_ENDPOINT_NOT_DOCUMENTED",
            }:
                warnings.append("ignored unsupported or incomplete endpoint contract alignment")
                continue
            flow = _path_for(method, endpoint_path) if status == "EXACT_MATCH" else None
            refs = _api_refs(alignment, flow)
            if status == "EXACT_MATCH":
                category = "api_contract_verification"
                purpose = f"核验已精确匹配的 API 契约：{method} {endpoint_path}"
                steps = [
                    "在项目负责人批准的隔离环境中确认服务版本、路由前缀、测试账号和测试数据",
                    "按文档记录的 HTTP method 与 path 发起请求；不得由静态分析结果推定请求体或参数",
                    "记录实际响应、服务端日志和数据副作用，并依据已确认需求逐项核对",
                    "单独核验该接口的认证、角色和资源归属边界；静态 guard 仅作为检查线索",
                ]
                limitations = [
                    "精确 method/path 匹配仅证明静态路由字符串相同，不证明运行时注册、请求可达或功能正确。",
                    "文档未提供的请求参数、响应结构、状态码和业务预期必须由需求负责人确认，不得编造。",
                ]
            elif status == "DOCUMENTED_NOT_FOUND_IN_CODE":
                category = "api_contract_review"
                purpose = f"复核文档接口未在当前静态代码范围内匹配：{method} {endpoint_path}"
                steps = [
                    "确认 API 文档版本、代码分支、服务模块和分析范围一致",
                    "人工检查 router mount、API gateway、版本前缀、动态路由、生成代码及其他服务入口",
                    "由需求/接口负责人确认该契约是否仍有效，并决定补充证据、实现或更新文档",
                ]
                limitations = [
                    "DOCUMENTED_NOT_FOUND_IN_CODE 不是接口缺失或功能未实现的结论。",
                    "当前对照未展开 router mount/base path、gateway rewrite、版本前缀或动态路由。",
                ]
            elif status == "CODE_ENDPOINT_NOT_DOCUMENTED":
                category = "api_contract_review"
                purpose = f"复核代码路由未出现在当前 API 文档中：{method} {endpoint_path}"
                steps = [
                    "确认文档收集范围、版本和服务边界，检查是否存在其他接口规范",
                    "由接口负责人确认该路由是内部接口、遗漏文档、遗留入口还是有意公开接口",
                    "按确认结果补充契约、记录豁免依据或安排后续评审；不得直接判为无效接口",
                ]
                limitations = [
                    "CODE_ENDPOINT_NOT_DOCUMENTED 不证明接口错误、未授权或不应存在。",
                    "文档可能不完整，当前分析也可能未覆盖所有文档位置。",
                ]
            else:
                category = "api_contract_review"
                purpose = f"复核存在多个静态代码路由匹配：{method} {endpoint_path}"
                steps = [
                    "检查所有匹配路由的文件、行号、服务模块和实际部署边界",
                    "确认重复注册是否有意设计，并核对路由顺序、中间件和版本策略",
                    "由接口负责人确认唯一契约归属后，再制定运行时验证场景",
                ]
                limitations = [
                    "MULTIPLE_CODE_MATCHES 只表示静态匹配到多个路由，不代表运行时冲突或缺陷。",
                    "静态执行路径仅在唯一精确 endpoint 关联时附加；未唯一关联时不拼接图谱证据。",
                ]
            candidates.append(_candidate(
                category, f"{method} {endpoint_path}", purpose, refs, steps, limitations,
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

    function_views = []
    if isinstance(business_graph, Mapping):
        understanding = business_graph.get("business_understanding")
        if isinstance(understanding, Mapping) and isinstance(understanding.get("business_function_views", []), list):
            function_views = [item for item in understanding.get("business_function_views", []) if isinstance(item, Mapping)]
    function_view_by_endpoint = {str(item.get("endpoint_id")): item for item in function_views
                                 if isinstance(item.get("endpoint_id"), str)}

    # S2.13-C: synthesize a reviewable test candidate only for a resolved UI → API → endpoint chain.
    frontend_chains = understanding.get("frontend_business_chains", []) if isinstance(understanding, Mapping) else []
    if not isinstance(frontend_chains, list):
        warnings.append("business_graph.frontend_business_chains is malformed; frontend flow candidates omitted")
        frontend_chains = []
    for chain in frontend_chains:
        if not isinstance(chain, Mapping) or chain.get("resolution_status") != "EXACT_MATCH":
            continue
        method = _text(chain.get("method")).upper()
        endpoint_path = _text(chain.get("path"))
        endpoint_id = _text(chain.get("endpoint_node_id"))
        if not method or not endpoint_path or not endpoint_id:
            warnings.append("frontend business chain missing exact endpoint identity; candidate omitted")
            continue
        matching_paths = [
            item for item in execution_paths
            if isinstance(item, Mapping) and _text(item.get("endpoint_id")) == endpoint_id
            and _text(item.get("method")).upper() == method
            and _text(item.get("endpoint")) == endpoint_path
        ]
        if len(matching_paths) != 1:
            warnings.append(f"frontend chain {method} {endpoint_path} has no unique endpoint execution path")
            continue
        flow = matching_paths[0]
        refs: list[dict[str, Any]] = []
        for source, line, kind in (
            (chain.get("source_file"), chain.get("event_line"), "frontend_event"),
            (chain.get("api_call_file"), chain.get("api_call_line"), "frontend_api_call"),
        ):
            ref = _source_ref(source, line, evidence_kind=kind)
            if ref:
                refs.append(ref)
        source = flow.get("source")
        if isinstance(source, Mapping):
            ref = _source_ref(source.get("path"), source.get("line"), evidence_kind="backend_route")
            if ref:
                refs.append(ref)
        handler = flow.get("handler")
        if isinstance(handler, Mapping):
            ref = _source_ref(handler.get("path"), handler.get("line"),
                              evidence_kind="backend_handler", symbol_id=handler.get("symbol_id"))
            if ref:
                refs.append(ref)
        for key, kind in (("call_chain", "backend_call_chain"),
                          ("database_accesses", "database_access"),
                          ("authorization", "authorization"),
                          ("identity_resource_flow", "identity_resource")):
            values = flow.get(key)
            if key == "authorization" and isinstance(values, Mapping):
                values = values.get("evidence", [])
            if not isinstance(values, list):
                continue
            for item in values:
                if not isinstance(item, Mapping):
                    continue
                location = item.get("call_site") if key == "call_chain" else item
                location = location if isinstance(location, Mapping) else {}
                ref = _source_ref(
                    location.get("path"), location.get("line"), evidence_kind=kind,
                    evidence_id=(item.get("database_access_id") or item.get("authorization_check_id")
                                 or item.get("identity_evidence_id") or item.get("target_id")),
                )
                if ref:
                    refs.append(ref)
        unique_refs = {
            json.dumps(item, sort_keys=True, ensure_ascii=False): item for item in refs
        }
        refs = [unique_refs[key] for key in sorted(unique_refs)]
        stage_status = flow.get("stage_status") if isinstance(flow.get("stage_status"), Mapping) else {}
        function_view = function_view_by_endpoint.get(endpoint_id)
        candidate = _candidate(
            "frontend_backend_flow", f"{chain.get('event_name') or 'UI event'} → {method} {endpoint_path}",
            f"验证前端交互是否按已确认需求正确触发并完成后端接口流程：{method} {endpoint_path}",
            refs,
            [
                "在授权的隔离环境中由真实页面触发对应 UI 事件，记录页面、账号、输入和事件结果",
                "核对前端实际发出的 HTTP method/path 与静态候选链一致，并记录请求/响应及关联日志",
                "核对后端路由、Handler 和调用链的实际执行情况，记录分支、异常与服务端证据",
                "若存在数据库访问、授权或身份/资源线索，分别核验数据副作用、角色边界及资源归属；不得仅凭静态线索判定正确",
                "将 UI 展示状态、接口响应和持久化结果与经批准的业务验收标准逐项比较",
            ],
            [
                "UI → API → endpoint 关系来自有限静态符号调用与精确 method/path 匹配，不证明页面运行时可达、事件实际触发或网络请求成功。",
                f"后端阶段状态：{json.dumps(dict(stage_status), ensure_ascii=False, sort_keys=True)}；各阶段均需运行时验证。",
                "静态数据库/授权/身份线索仅用于确定测试检查点，不证明实际读写、权限正确性或资源隔离。",
                "缺少需求、角色矩阵、请求/响应契约或测试数据时，预期结果必须由负责人确认。",
            ],
        )
        if isinstance(function_view, Mapping):
            candidate["business_function_id"] = function_view.get("business_function_id")
            candidate["function_coverage_linkage"] = "TEST_DESIGN_CANDIDATE_LINKED"
            candidate["test_execution_coverage"] = "NOT_MEASURED"
        candidates.append(candidate)

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
        "business_function_coverage": {
            "function_view_count": len(function_views),
            "function_views_with_test_design_candidate": len({item.get("business_function_id") for item in candidates if item.get("business_function_id")}),
            "test_execution_coverage": "NOT_MEASURED",
            "note": "候选测试关联仅表示已生成测试设计，不表示测试执行或代码覆盖率。",
        },
        "execution_status": "NOT_EXECUTED",
        "design_note": "候选测试需经需求确认和用户授权后才能执行；本模块不执行命令。",
        "warnings": warnings,
        "candidates": candidates,
    }
