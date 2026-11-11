"""Render the business traceability matrix as an auditable Markdown review report."""
from __future__ import annotations
from typing import Any


def _cell(value: Any) -> str:
    return str(value if value is not None else "—").replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def _items(value: Any) -> list[Any]:
    """Return list-shaped report data; malformed optional collections are omitted."""
    return value if isinstance(value, list) else []


def render_business_traceability_markdown(report: dict[str, Any]) -> str:
    if not isinstance(report, dict) or report.get("schema_version") != "usit-business-traceability-matrix-v1":
        raise ValueError("report must use usit-business-traceability-matrix-v1")
    lines = [
        "# USIT 业务需求跨层追溯报告", "",
        f"- 模式：{_cell(report.get('assessment_mode'))}",
        f"- 需求功能：{_cell(report.get('feature_count'))}",
        f"- 业务功能视图：{_cell(report.get('business_function_view_count'))}",
        f"- 已登记审阅决策：{_cell(report.get('reviewed_mapping_count'))}",
        f"- 未建立审阅映射：{_cell(report.get('unmapped_feature_count'))}",
        "", "## 需求追溯总览", "",
        "| 需求功能 | 来源 | 审阅决策 | 业务功能 ID | API | 追溯状态 |",
        "|---|---|---|---|---|---|",
    ]
    for row in _items(report.get("rows")):
        if not isinstance(row, dict):
            continue
        endpoint = row.get("endpoint") if isinstance(row.get("endpoint"), dict) else {}
        api = f"{endpoint.get('method') or '—'} {endpoint.get('path') or '—'}"
        source = f"{row.get('feature_source') or '—'}:{row.get('feature_line') or '—'}"
        lines.append(f"| {_cell(row.get('feature_name'))} | {_cell(source)} | {_cell(row.get('mapping_decision'))} | {_cell(row.get('business_function_id'))} | {_cell(api)} | {_cell(row.get('traceability_status'))} |")
    lines.extend(["", "## 分层证据状态", ""])
    for row in _items(report.get("rows")):
        if not isinstance(row, dict):
            continue
        review = row.get("review") if isinstance(row.get("review"), dict) else {}
        lines.extend([f"### {_cell(row.get('feature_name'))}", "",
            f"- 需求来源：{_cell(row.get('feature_source'))}:{_cell(row.get('feature_line'))}",
            f"- 映射决策：{_cell(row.get('mapping_decision'))}",
            f"- 审阅记录：{_cell(review.get('reviewed_by') or '无')} / {_cell(review.get('reviewed_at') or '无')}",
            f"- 审阅理由：{_cell(review.get('rationale') or '尚无审阅记录')}",
            "", "| 层级 | 状态 | 解析口径 |", "|---|---|---|"])
        stages = row.get("stage_status", {})
        parser_scope = row.get("stage_parser_scope", {})
        if isinstance(stages, dict):
            for key, value in stages.items():
                scope = parser_scope.get(key) if isinstance(parser_scope, dict) else None
                scope_text = ", ".join(str(code) for code in scope) if isinstance(scope, list) and scope else "—"
                lines.append(f"| {_cell(key)} | {_cell(value)} | {_cell(scope_text)} |")
        candidates = _items(row.get("test_candidate_ids"))
        observation = row.get("runtime_observation") if isinstance(row.get("runtime_observation"), dict) else {}
        correlation_ids = _items(observation.get("correlation_ids"))
        lines.extend(["", f"- 测试候选 ID：{_cell(', '.join(map(str, candidates)) if candidates else '无')}",
            f"- 运行时测试：{_cell(stages.get('test_execution') if isinstance(stages, dict) else 'UNKNOWN')}",
            f"- 运行时事件观察：{_cell(observation.get('status') or (stages.get('runtime_observation') if isinstance(stages, dict) else 'NOT_MEASURED'))}",
            f"- 运行时关联 ID：{_cell(', '.join(map(str, correlation_ids)) if correlation_ids else '无')}",
            f"- 代码覆盖：{_cell(stages.get('code_coverage') if isinstance(stages, dict) else 'UNKNOWN')}"])
        runtime_evidence = row.get("runtime_evidence")
        runtime_evidence = runtime_evidence if isinstance(runtime_evidence, dict) else {}
        source_version = runtime_evidence.get("source_version_binding_status")
        if source_version:
            lines.append(f"- 源码版本绑定（测试快照 vs 覆盖率）：{_cell(source_version)}")
        consistency = observation.get("consistency") or runtime_evidence.get("runtime_consistency_status")
        if consistency:
            lines.append(f"- 运行时一致性（事件显式字段 vs 静态视图）：{_cell(consistency)}")
            detail = runtime_evidence.get("runtime_consistency_detail")
            if isinstance(detail, dict):
                for mismatch in _items(detail.get("mismatches")):
                    if isinstance(mismatch, dict):
                        lines.append(
                            f"  - 不一致：{_cell(mismatch.get('event_id'))} / {_cell(mismatch.get('field'))}："
                            f"静态声称 {_cell(mismatch.get('expected'))}，事件报告 {_cell(mismatch.get('actual'))}"
                        )
        claim = runtime_evidence.get("claim_vs_observed")
        if isinstance(claim, dict):
            claimed = claim.get("claimed") if isinstance(claim.get("claimed"), dict) else {}
            observed = claim.get("observed_by_parent") if isinstance(claim.get("observed_by_parent"), dict) else {}
            lines.append(
                f"- 声称 vs 父进程观测：测试 {_cell(claimed.get('test_execution_status'))}；"
                f"覆盖 {_cell(claimed.get('code_coverage_status'))}；"
                f"父进程观测退出码 {_cell(observed.get('pytest_exit_code') if observed.get('pytest_exit_code') is not None else '未观测')}"
            )
        for correlation in _items(observation.get("correlations")):
            if not isinstance(correlation, dict):
                continue
            lines.append(
                f"- 运行时关联详情：{_cell(correlation.get('correlation_id'))}；"
                f"拓扑：{_cell(correlation.get('parent_topology_status') or 'NOT_PROVIDED')}；"
                f"父子关联覆盖：{_cell(correlation.get('parent_link_coverage_ratio') if correlation.get('parent_link_coverage_ratio') is not None else 'NOT_PROVIDED')}"
            )
            attribution = correlation.get("function_attribution_status")
            if attribution:
                function_ids = _items(correlation.get("function_ids_in_correlation"))
                lines.append(
                    f"  - 业务功能归属：{_cell(attribution)}；"
                    f"关联功能：{_cell(', '.join(map(str, function_ids)) if function_ids else '未提供')}"
                )
            event_ids = _items(correlation.get("observed_event_ids_by_timestamp"))
            if event_ids:
                lines.append(f"  - 事件审阅顺序（非因果）：{_cell(' → '.join(map(str, event_ids)))}")
            for edge in _items(correlation.get("explicit_parent_edges")):
                if isinstance(edge, dict):
                    lines.append(
                        f"  - 采集端声明父子边：{_cell(edge.get('parent_event_id'))} → {_cell(edge.get('child_event_id'))}"
                    )
            for field, label in (
                ("root_event_ids", "根事件"),
                ("leaf_event_ids", "叶事件"),
                ("branching_parent_event_ids", "分支父事件"),
                ("isolated_event_ids", "孤立事件"),
                ("cycle_event_ids", "循环事件"),
            ):
                values = _items(correlation.get(field))
                if values:
                    lines.append(f"  - {label}：{_cell(', '.join(map(str, values)))}")
            if correlation.get("interpretation"):
                lines.append(f"  - 解释边界：{_cell(correlation.get('interpretation'))}")
        execution_detail = runtime_evidence.get("test_execution_detail")
        if isinstance(execution_detail, dict):
            completeness = execution_detail.get("mapped_case_completeness") or "NOT_PROVIDED"
            reason = execution_detail.get("execution_status_reason") or "未提供"
            lines.append(f"- 测试报告完整性：{_cell(completeness)}；状态原因：{_cell(reason)}")
            actual_cases = execution_detail.get("mapped_test_cases", [])
            if isinstance(actual_cases, list) and actual_cases:
                lines.append("- 已报告 testcase：")
                for case in _items(actual_cases):
                    if isinstance(case, dict):
                        lines.append(
                            f"  - {_cell(case.get('test_case_id'))}：{_cell(case.get('status'))}"
                        )
            missing_cases = execution_detail.get("mapped_cases_missing_from_report", [])
            if isinstance(missing_cases, list) and missing_cases:
                lines.append("- 映射但未出现在报告中的 testcase：")
                for case_id in _items(missing_cases):
                    lines.append(f"  - {_cell(case_id)}")
        test_candidates = row.get("test_candidates", [])
        if isinstance(test_candidates, list) and test_candidates:
            lines.append("- 测试设计候选详情：")
            for candidate in test_candidates:
                if not isinstance(candidate, dict):
                    continue
                lines.append(
                    f"  - {_cell(candidate.get('test_id'))} / {_cell(candidate.get('category'))}："
                    f"{_cell(candidate.get('purpose'))}"
                )
                lines.append(
                    f"    - 设计状态：{_cell(candidate.get('design_status'))}；"
                    f"执行状态：{_cell(candidate.get('execution_status'))}；"
                    f"预期结果：{_cell(candidate.get('expected_results_status'))}"
                )
                for precondition in _items(candidate.get("preconditions")):
                    lines.append(f"    - 前置条件：{_cell(precondition)}")
                for action in _items(candidate.get("actions")):
                    if isinstance(action, dict):
                        lines.append(
                            f"    - 步骤 { _cell(action.get('step')) }：{_cell(action.get('action'))}；"
                            f"预期：{_cell(action.get('expected_result'))}"
                        )
                        for evidence in _items(action.get("evidence_to_collect")):
                            lines.append(f"      - 采集证据：{_cell(evidence)}")
                for requirement in _items(candidate.get("evidence_requirements")):
                    if isinstance(requirement, dict):
                        lines.append(
                            f"    - 证据要求：{_cell(requirement.get('evidence_type'))} / "
                            f"{_cell(requirement.get('requirement') or requirement.get('source_path'))}"
                        )
                authorization_evidence = candidate.get("authorization_evidence")
                if isinstance(authorization_evidence, dict):
                    lines.append(
                        f"    - 安全授权线索（静态）：{_cell(authorization_evidence.get('status') or 'UNKNOWN')}"
                    )
                for evidence_key, evidence_label in (
                    ("call_chain_evidence", "调用链"),
                    ("database_access_evidence", "数据库访问"),
                    ("identity_dataflow_evidence", "身份数据流"),
                ):
                    evidence_items = _items(candidate.get(evidence_key))
                    if evidence_items:
                        lines.append(f"    - {evidence_label}证据：")
                        for evidence_item in evidence_items:
                            if not isinstance(evidence_item, dict):
                                continue
                            path = evidence_item.get("path")
                            line = evidence_item.get("line")
                            lines.append(
                                f"      - {_cell(path or evidence_item.get('kind') or evidence_item.get('operation') or '结构化证据')}"
                                f"{':' + _cell(line) if line is not None else ''}"
                            )
                for limitation in _items(candidate.get("limitations")):
                    lines.append(f"    - 候选限制：{_cell(limitation)}")
        lines.append("- 证据引用：")
        refs = _items(row.get("evidence_refs"))
        if refs:
            for ref in _items(refs):
                if isinstance(ref, dict):
                    lines.append(f"  - {_cell(ref.get('kind'))}: {_cell(ref.get('path'))}:{_cell(ref.get('line'))}")
        else:
            lines.append("  - 无可用源码证据引用")
        lines.append("")
    lines.extend(["## 运行时证据冲突", "",
        f"- 冲突数量：{_cell(report.get('runtime_evidence_conflict_count', len(_items(report.get('runtime_evidence_conflicts')))))}",
        f"- 未知运行时功能 ID：{_cell(', '.join(map(str, _items(report.get('unknown_runtime_function_ids')))) or '无')}",
    ])
    conflicts = _items(report.get("runtime_evidence_conflicts"))
    if conflicts:
        for conflict in conflicts:
            if isinstance(conflict, dict):
                details = [f"{key}={value}" for key, value in sorted(conflict.items())]
                lines.append(f"- {_cell('; '.join(details))}")
    else:
        lines.append("- 无运行时证据冲突明细")
    lines.extend(["", "## 未关联记录", "",
        f"- 未关联运行时证据功能 ID：{_cell(', '.join(map(str, _items(report.get('unlinked_runtime_function_ids')))) or '无')}",
        f"- 未关联测试设计功能 ID：{_cell(', '.join(map(str, _items(report.get('unlinked_test_design_function_ids')))) or '无')}",
        f"- 无效运行状态功能 ID：{_cell(', '.join(map(str, _items(report.get('invalid_runtime_status_function_ids')))) or '无')}",
        "", "## 输入 SHA-256", "", "| 工件 | SHA-256 |", "|---|---|"])
    for key, value in sorted((report.get("input_hashes") if isinstance(report.get("input_hashes"), dict) else {}).items()):
        lines.append(f"| {_cell(key)} | {_cell(value)} |")
    lines.extend(["", "## 解释边界", ""])
    for item in _items(report.get("limitations")):
        lines.append(f"- {_cell(item)}")
    lines.append("")
    return "\n".join(lines)
