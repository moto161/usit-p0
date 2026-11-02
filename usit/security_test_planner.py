"""Evidence-driven security test candidates; static analysis only.

This module proposes tests from endpoint, authorization, call-chain and database
evidence. It never labels an endpoint vulnerable and never executes requests.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

SCHEMA_VERSION = "usit-security-test-candidates-v1"
_LIMIT = 10_000


def _stable_id(kind: str, identity: Mapping[str, Any]) -> str:
    raw = json.dumps(identity, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return "sec-" + kind + "-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _endpoint_label(node: Mapping[str, Any]) -> str:
    details = _dict(node.get("business_details"))
    method = str(details.get("method") or "").upper()
    path = str(details.get("endpoint") or node.get("label") or "")
    return (method + " " + path).strip()


def _evidence_ref(path: Any, line: Any = None, **extra: Any) -> dict[str, Any] | None:
    if not isinstance(path, str) or not path.strip():
        return None
    result: dict[str, Any] = {"path": path, "confidence": "STATIC_INFERENCE"}
    if isinstance(line, int) and not isinstance(line, bool) and line > 0:
        result["line"] = line
    result.update({key: value for key, value in extra.items() if value is not None and value != ""})
    return result


def _candidate(kind: str, endpoint: str, purpose: str, evidence: list[dict[str, Any]],
               steps: list[str], limitations: list[str], *, call_chain: list[Any],
               authorization: Mapping[str, Any], database: list[Any],
               identity_flow: list[Any] | None = None,
               business_function_id: str | None = None) -> dict[str, Any]:
    identity = {"kind": kind, "endpoint": endpoint, "evidence": evidence, "steps": steps}
    return {
        "test_id": _stable_id(kind, identity),
        "business_function_id": business_function_id,
        "category": "security_" + kind,
        "target": endpoint,
        "purpose": purpose,
        "basis": "STATIC_INFERENCE",
        "evidence_refs": evidence,
        "call_chain_evidence": call_chain,
        "authorization_evidence": dict(authorization),
        "database_access_evidence": database,
        "identity_dataflow_evidence": identity_flow or [],
        "preconditions": [
            "由项目负责人批准的隔离测试环境，不连接生产服务或真实用户数据",
            "提供明确的身份/角色矩阵、数据所有权规则、接口需求和测试账号",
            "测试请求与数据变更均须在可销毁的专用测试数据集内进行",
        ],
        "steps": steps,
        "actions": [
            {
                "step": index,
                "action": action,
                "expected_result": (
                    "由需求负责人依据已批准的身份、角色、资源所有权和接口验收标准填写；"
                    "当前静态证据不足以确定，不得将占位内容作为 PASS 判据。"
                ),
                "evidence_to_collect": [
                    "原始请求与响应",
                    "服务端授权/审计日志",
                    "测试前后数据快照及实际副作用",
                ],
            }
            for index, action in enumerate(steps, start=1)
        ],
        "expected_result": (
            "以经确认的身份、角色、资源所有权和接口策略为准；当前静态证据不能确定"
            "具体 HTTP 状态码或响应内容，未确认需求前不得判定 PASS/FAIL。"
        ),
        "expected_results_status": "OWNER_CONFIRMATION_REQUIRED",
        "evidence_requirements": [
            {
                "evidence_type": item.get("evidence_type", "SOURCE_REFERENCE"),
                "source_path": item.get("path"),
                "line": item.get("line"),
            }
            for item in evidence
        ] + [
            {"evidence_type": "RUNTIME_TEST_RESULT", "requirement": "保存原始测试报告及 testcase ID"},
            {"evidence_type": "AUTHORIZATION_DECISION", "requirement": "记录实际身份、角色、资源归属与授权决策"},
            {"evidence_type": "BUSINESS_ORACLE", "requirement": "保存与已批准验收标准逐项比较的结果"},
        ],
        "design_status": "CANDIDATE_NEEDS_REQUIREMENTS",
        "execution_status": "NOT_EXECUTED",
        "limitations": limitations,
    }


def plan_security_tests(business_graph: Mapping[str, Any]) -> dict[str, Any]:
    """Generate deterministic security test candidates from a projected graph."""
    if not isinstance(business_graph, Mapping):
        raise TypeError("business_graph must be a mapping")
    nodes = _list(business_graph.get("nodes"))
    if not isinstance(business_graph.get("nodes"), list):
        raise ValueError("business_graph.nodes must be a list")

    endpoints = [node for node in nodes
                 if isinstance(node, Mapping) and node.get("business_kind") == "endpoint"]
    candidates: list[dict[str, Any]] = []
    endpoint_rows: list[dict[str, Any]] = []
    understanding = _dict(business_graph.get("business_understanding"))
    raw_views = _list(understanding.get("business_function_views"))
    views_by_endpoint: dict[str, list[str]] = {}
    for view in raw_views:
        if not isinstance(view, Mapping):
            continue
        endpoint_id = view.get("endpoint_id")
        function_id = view.get("business_function_id")
        if isinstance(endpoint_id, str) and isinstance(function_id, str) and function_id:
            views_by_endpoint.setdefault(endpoint_id, []).append(function_id)

    for node in endpoints:
        label = _endpoint_label(node)
        if not label:
            continue
        endpoint_id = node.get("id")
        matched_functions = views_by_endpoint.get(endpoint_id, []) if isinstance(endpoint_id, str) else []
        business_function_id = matched_functions[0] if len(matched_functions) == 1 else None
        source = _dict(node.get("evidence"))
        auth = _dict(node.get("authorization_evidence"))
        association = _dict(node.get("handler_association"))
        chain = _list(association.get("call_chain_evidence"))
        database = _list(association.get("database_access_evidence"))
        identity_flow = _list(association.get("identity_dataflow_evidence"))
        refs: list[dict[str, Any]] = []
        ref = _evidence_ref(source.get("path"), source.get("line"),
                            endpoint=label, evidence_type="endpoint_registration")
        if ref:
            refs.append(ref)
        for item in _list(auth.get("route_middleware")) + _list(auth.get("reachable_function_checks")):
            if not isinstance(item, Mapping):
                continue
            ref = _evidence_ref(item.get("path") or source.get("path"), item.get("line"),
                                evidence_type="authorization_indicator",
                                guard_names=item.get("guard_names"))
            if ref:
                refs.append(ref)
        for entry in database:
            if not isinstance(entry, Mapping):
                continue
            ref = _evidence_ref(entry.get("path"), entry.get("line"),
                                evidence_type="database_access",
                                operation=entry.get("operation"), tables=entry.get("tables"),
                                columns=entry.get("columns"))
            if ref:
                refs.append(ref)
        for entry in identity_flow:
            if not isinstance(entry, Mapping):
                continue
            ref = _evidence_ref(entry.get("path"), entry.get("line"),
                                evidence_type="identity_dataflow",
                                identity_kind=entry.get("kind"),
                                identity_evidence_id=entry.get("identity_evidence_id"),
                                scope=entry.get("scope"))
            if ref:
                refs.append(ref)
        for path_key in ("target_path",):
            ref = _evidence_ref(association.get(path_key), association.get("target_line"),
                                evidence_type="handler_definition")
            if ref:
                refs.append(ref)
        # Include concrete call-site evidence, not just a flattened endpoint label.
        for chain_item in chain:
            if not isinstance(chain_item, Mapping):
                continue
            for step in _list(chain_item.get("steps")):
                if not isinstance(step, Mapping):
                    continue
                site = _dict(step.get("call_site"))
                ref = _evidence_ref(site.get("path"), site.get("line"),
                                    evidence_type="call_site",
                                    target_id=step.get("target_id"),
                                    resolution_status=step.get("resolution_status"))
                if ref:
                    refs.append(ref)
        # Stable de-duplication while preserving evidence order.
        seen: set[str] = set()
        refs = [item for item in refs
                if not (json.dumps(item, sort_keys=True, ensure_ascii=False) in seen
                        or seen.add(json.dumps(item, sort_keys=True, ensure_ascii=False)))]

        limitations = [
            "所有关联均为静态候选，不证明请求运行时可达、权限检查执行或数据库操作发生。",
            "当前调用链有界且仅跟随唯一 RESOLVED 符号边；动态分派、未解析边及截断部分未覆盖。",
            "当前数据库证据仅覆盖有限 SQL 字面量，不代表完整 ORM、字段数据流或真实数据访问策略。",
        ]
        steps = [
            "依据已批准的接口策略建立未登录、普通用户和适用高权限账号",
            "在隔离环境分别请求该接口，记录请求身份、响应、服务端日志及数据副作用",
            "对照已确认的认证/授权规则核验每种身份的允许与拒绝行为",
        ]
        # Authentication presence is a verification question, never a vulnerability verdict.
        candidates.append(_candidate(
            "authentication_boundary", label,
            "验证接口的未登录访问边界及认证策略是否按需求生效",
            refs, steps,
            limitations + ["NOT_DETECTED_STATIC_SCAN 只表示当前有限静态扫描未发现线索，不代表接口缺少认证。"],
            call_chain=chain, authorization=auth, database=database, identity_flow=identity_flow,

            business_function_id=business_function_id,
        ))

        guards = []
        for item in _list(auth.get("route_middleware")) + _list(auth.get("reachable_function_checks")):
            if isinstance(item, Mapping):
                guards.extend(str(name).lower() for name in _list(item.get("guard_names")))
        has_role_guard = any(("admin" in name or "role" in name or "permission" in name)
                             for name in guards)
        sensitive_db = [item for item in database if isinstance(item, Mapping)
                        and str(item.get("operation") or "").upper() in {"SELECT", "UPDATE", "DELETE"}]
        if has_role_guard and sensitive_db:
            candidates.append(_candidate(
                "role_boundary", label,
                "验证普通用户与角色不足用户访问受限数据接口时的拒绝边界",
                refs,
                ["依据需求确认允许访问的角色及受限资源范围",
                 "使用普通用户/低权限角色请求该接口，并保留服务端审计与数据快照",
                 "使用获准角色执行对照请求，比较访问结果和数据副作用"],
                limitations + ["检测到角色/权限 guard 名称不证明角色判定正确，也不证明所有分支均受保护。"],
                call_chain=chain, authorization=auth, database=sensitive_db,

                business_function_id=business_function_id,
            ))

        request_to_owner = [
            item for item in identity_flow if isinstance(item, Mapping)
            and item.get("kind") == "direct_request_identity_to_sql_owner_binding"
        ]
        trusted_to_owner = [
            item for item in identity_flow if isinstance(item, Mapping)
            and item.get("kind") == "direct_authenticated_identity_to_sql_owner_binding"
        ]
        if request_to_owner:
            candidates.append(_candidate(
                "request_identity_owner_binding", label,
                "核实 SQL 资源归属条件是否使用请求可控的 user_id/owner_id，而非经验证的当前登录身份",
                refs,
                ["确认接口契约中 user_id/owner_id 的来源及其是否允许客户端指定",
                 "在隔离环境分别使用本人身份和另一用户身份提交不同归属 ID",
                 "记录服务端最终绑定的 SQL 参数、授权决策及数据副作用，并与需求定义的资源归属规则对照"],
                limitations + [
                    "仅检查 SQL 调用第一个参数之后的同一行位置参数表达式；不解析 ORM、命名参数映射或多行调用，仍不证明数据库驱动实际绑定。",
                    "必须检查完整参数数组、变量赋值、框架绑定规则和运行时身份；当前证据不是 IDOR 或漏洞结论。",
                ],
                call_chain=chain, authorization=auth, database=database, identity_flow=identity_flow,

                business_function_id=business_function_id,
            ))
        if trusted_to_owner:
            candidates.append(_candidate(
                "authenticated_identity_owner_binding", label,
                "验证数据库资源归属是否确实绑定到当前已认证身份",
                refs,
                ["核对身份来源是否由认证中间件可信建立且不可被请求参数覆盖",
                 "在隔离环境以两个用户身份分别读取/修改各自及对方资源",
                 "记录实际 SQL 参数和资源访问结果，确认数据库归属条件与认证身份一致"],
                limitations + [
                    "req.user.id 等命名模式不证明对象可信、已认证或未被覆盖。",
                    "局部别名仅覆盖同一简单语句块；未验证跨函数、闭包、包装器、ORM 或运行时认证上下文。",
                ],
                call_chain=chain, authorization=auth, database=database, identity_flow=identity_flow,

                business_function_id=business_function_id,
            ))

        owner_columns = sorted({
            str(column).lower() for item in database if isinstance(item, Mapping)
            for column in _list(item.get("columns"))
            if str(column).lower() in {"owner_id", "user_id", "created_by", "account_id"}
        })
        if owner_columns and sensitive_db:
            candidates.append(_candidate(
                "cross_user_resource_isolation", label,
                "验证不同用户之间的照片/私密数据读取与修改隔离",
                refs,
                ["准备用户 A、用户 B 及分别归属两人的隔离测试资源",
                 "用户 A 请求读取/修改用户 B 的资源标识，覆盖正常与边界标识",
                 "核对响应内容、数据库记录、文件访问日志及审计日志，确认未发生越权副作用"],
                limitations + [
                    "发现 owner/user/account 字段只是数据访问线索，不证明该字段用于授权判断。",
                    "必须由需求明确资源归属规则；不得把可控 ID 参数本身判定为 IDOR。",
                ],
                call_chain=chain, authorization=auth, database=sensitive_db,

                business_function_id=business_function_id,
            ))
            candidates.append(_candidate(
                "owner_parameter_tampering", label,
                "验证请求中的 user_id/owner_id 等归属参数是否可被越权篡改",
                refs,
                ["确认接口允许的资源归属字段及其权威来源（会话、令牌或请求体）",
                 "在隔离数据中分别提交本人 ID、其他用户 ID 和无效 ID",
                 "核对服务端最终采用的归属身份、数据库副作用及审计记录"],
                limitations + [
                    "SQL 列名不能证明这些列来自请求参数，也不能证明请求参数可控。",
                    "只有在接口契约确认参数存在后才执行该测试；禁止据此推断已存在漏洞。",
                ],
                call_chain=chain, authorization=auth, database=sensitive_db,

                business_function_id=business_function_id,
            ))

        endpoint_rows.append({
            "label": label, "business_function_id": business_function_id, "database_ids": {
                str(item.get("database_access_id")) for item in database
                if isinstance(item, Mapping) and item.get("database_access_id")
            },
            "auth_status": str(auth.get("status") or "UNKNOWN"),
            "refs": refs, "chain": chain, "auth": auth, "database": database,
        })

    # Detect shared database evidence exposed by multiple endpoints with differing
    # static authorization signals; this is only a candidate to inspect alternate paths.
    for index, left in enumerate(endpoint_rows):
        for right in endpoint_rows[index + 1:]:
            shared = left["database_ids"] & right["database_ids"]
            if not shared or left["auth_status"] == right["auth_status"]:
                continue
            for row, other in ((left, right), (right, left)):
                shared_db = [item for item in row["database"]
                             if isinstance(item, Mapping)
                             and str(item.get("database_access_id")) in shared]
                candidates.append(_candidate(
                    "alternate_entrypoint_authorization",
                    row["label"] + " (共享数据库访问；对照入口 " + other["label"] + ")",
                    "检查共享 service/repository 的其他入口是否具有符合需求的独立授权控制",
                    row["refs"],
                    ["分别沿两个接口入口核对路由中间件、handler、service 和数据库访问证据",
                     "在隔离环境使用相同测试身份访问两个入口并记录服务端授权决策",
                     "确认每个入口是否按其自身业务策略执行授权，而非仅依赖另一入口的保护"],
                    [
                        "共享 database_access_id 仅表示静态图谱关联到同一数据库访问候选。",
                        "权限状态不同不证明存在绕过；中间件顺序、包装器和运行时路由尚未完整建模。",
                    ],
                    call_chain=row["chain"], authorization=row["auth"], database=shared_db,
                    business_function_id=row.get("business_function_id"),
                ))

    if len(candidates) > _LIMIT:
        raise ValueError(f"security candidate limit exceeded: {len(candidates)} > {_LIMIT}")
    candidates.sort(key=lambda item: (item["category"], item["target"], item["test_id"]))
    counts: dict[str, int] = {}
    for item in candidates:
        counts[item["category"]] = counts.get(item["category"], 0) + 1
    return {
        "schema_version": SCHEMA_VERSION,
        "model": "SecurityTestPlanner",
        "candidate_count": len(candidates),
        "unlinked_business_function_candidate_count": sum(1 for item in candidates if not item.get("business_function_id")),
        "category_counts": dict(sorted(counts.items())),
        "execution_status": "NOT_EXECUTED",
        "design_note": "安全测试候选仅用于需求审查；仅当 endpoint_id 与业务功能视图唯一匹配时才建立 business_function_id；静态线索不是漏洞结论，执行前必须另行授权。",
        "candidates": candidates,
    }
