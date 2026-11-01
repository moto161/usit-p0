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
            "", "| 层级 | 状态 |", "|---|---|"])
        stages = row.get("stage_status", {})
        if isinstance(stages, dict):
            for key, value in stages.items():
                lines.append(f"| {_cell(key)} | {_cell(value)} |")
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
        lines.append("- 证据引用：")
        refs = _items(row.get("evidence_refs"))
        if refs:
            for ref in _items(refs):
                if isinstance(ref, dict):
                    lines.append(f"  - {_cell(ref.get('kind'))}: {_cell(ref.get('path'))}:{_cell(ref.get('line'))}")
        else:
            lines.append("  - 无可用源码证据引用")
        lines.append("")
    lines.extend(["## 未关联记录", "",
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
