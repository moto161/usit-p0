"""Render the business traceability matrix as an auditable Markdown review report."""
from __future__ import annotations
from typing import Any


def _cell(value: Any) -> str:
    return str(value if value is not None else "—").replace("|", "\\|").replace("\r", " ").replace("\n", " ")


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
    for row in report.get("rows", []):
        if not isinstance(row, dict):
            continue
        endpoint = row.get("endpoint") if isinstance(row.get("endpoint"), dict) else {}
        api = f"{endpoint.get('method') or '—'} {endpoint.get('path') or '—'}"
        source = f"{row.get('feature_source') or '—'}:{row.get('feature_line') or '—'}"
        lines.append(f"| {_cell(row.get('feature_name'))} | {_cell(source)} | {_cell(row.get('mapping_decision'))} | {_cell(row.get('business_function_id'))} | {_cell(api)} | {_cell(row.get('traceability_status'))} |")
    lines.extend(["", "## 分层证据状态", ""])
    for row in report.get("rows", []):
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
        candidates = row.get("test_candidate_ids", [])
        observation = row.get("runtime_observation") if isinstance(row.get("runtime_observation"), dict) else {}
        correlation_ids = observation.get("correlation_ids", [])
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
                for case in actual_cases:
                    if isinstance(case, dict):
                        lines.append(
                            f"  - {_cell(case.get('test_case_id'))}：{_cell(case.get('status'))}"
                        )
            missing_cases = execution_detail.get("mapped_cases_missing_from_report", [])
            if isinstance(missing_cases, list) and missing_cases:
                lines.append("- 映射但未出现在报告中的 testcase：")
                for case_id in missing_cases:
                    lines.append(f"  - {_cell(case_id)}")
        lines.append("- 证据引用：")
        refs = row.get("evidence_refs", [])
        if refs:
            for ref in refs:
                if isinstance(ref, dict):
                    lines.append(f"  - {_cell(ref.get('kind'))}: {_cell(ref.get('path'))}:{_cell(ref.get('line'))}")
        else:
            lines.append("  - 无可用源码证据引用")
        lines.append("")
    lines.extend(["## 未关联记录", "",
        f"- 未关联运行时证据功能 ID：{_cell(', '.join(report.get('unlinked_runtime_function_ids', [])) or '无')}",
        f"- 未关联测试设计功能 ID：{_cell(', '.join(report.get('unlinked_test_design_function_ids', [])) or '无')}",
        f"- 无效运行状态功能 ID：{_cell(', '.join(report.get('invalid_runtime_status_function_ids', [])) or '无')}",
        "", "## 输入 SHA-256", "", "| 工件 | SHA-256 |", "|---|---|"])
    for key, value in sorted(report.get("input_hashes", {}).items()):
        lines.append(f"| {_cell(key)} | {_cell(value)} |")
    lines.extend(["", "## 解释边界", ""])
    for item in report.get("limitations", []):
        lines.append(f"- {_cell(item)}")
    lines.append("")
    return "\n".join(lines)
