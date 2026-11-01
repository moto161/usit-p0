"""Render a human-readable Markdown view of a quality assessment JSON artifact."""
from __future__ import annotations
from typing import Any


def _cell(value: Any) -> str:
    return str(value if value is not None else "—").replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def render_project_quality_markdown(report: dict[str, Any]) -> str:
    if not isinstance(report, dict) or report.get("schema_version") != "usit-project-quality-assessment-v1":
        raise ValueError("report must use usit-project-quality-assessment-v1")
    inventory = report.get("inventory", {})
    dimensions = report.get("dimensions", {})
    integrity = report.get("evidence_integrity", {})
    lines = [
        "# USIT 项目质量评估报告",
        "",
        f"- 评估模式：{_cell(report.get('assessment_mode'))}",
        "- 说明：本报告不计算单一质量总分；各维度独立呈现。",
        "",
        "## 项目理解与测试资产",
        "",
        "| 项目 | 数量 |",
        "|---|---:|",
    ]
    for key, label in (
        ("documented_feature_count", "文档功能"),
        ("business_function_view_count", "静态业务功能视图"),
        ("endpoint_contract_alignment_count", "API 契约对齐记录"),
        ("test_design_candidate_count", "测试设计候选"),
        ("runtime_evidence_row_count", "运行时证据记录"),
        ("verification_gap_count", "验证缺口"),
    ):
        lines.append(f"| {label} | {_cell(inventory.get(key, 0))} |")
    lines.extend(["", "## 质量维度", "", "| 维度 | 比例 | 分子/分母 | 状态 | 解释 |", "|---|---:|---:|---|---|"])
    labels = {
        "documented_feature_traceability": "需求追溯",
        "business_function_test_design": "测试设计覆盖",
        "business_function_test_execution": "测试执行证据覆盖",
        "business_function_test_pass": "已执行功能通过比例",
        "business_function_code_coverage_measured": "代码覆盖率测量覆盖",
    }
    for key, label in labels.items():
        item = dimensions.get(key, {})
        percent = item.get("percent")
        ratio = f"{item.get('numerator', 0)}/{item.get('denominator', 0)}"
        display = f"{percent:.2f}%" if isinstance(percent, (int, float)) else "未测量/不适用"
        lines.append(f"| {label} | {display} | {ratio} | {_cell(item.get('status'))} | {_cell(item.get('measured_meaning'))} |")
    lines.extend(["", "## 测试执行状态", "", "| 状态 | 功能数 |", "|---|---:|"])
    for key, value in sorted(report.get("business_function_test_execution_status_counts", {}).items()):
        lines.append(f"| {_cell(key)} | {_cell(value)} |")
    lines.extend(["", "## 代码覆盖状态", "", "| 状态 | 功能数 |", "|---|---:|"])
    for key, value in sorted(report.get("business_function_code_coverage_status_counts", {}).items()):
        lines.append(f"| {_cell(key)} | {_cell(value)} |")
    lines.extend(["", "## 证据完整性", "", f"- 状态：{_cell(integrity.get('status', 'UNKNOWN'))}"])
    for key, label in (
        ("business_function_evidence_conflict_ids", "证据冲突功能 ID"),
        ("unknown_test_design_business_function_ids", "未知测试设计功能 ID"),
        ("unknown_runtime_evidence_business_function_ids", "未知运行时证据功能 ID"),
        ("invalid_runtime_status_business_function_ids", "无效运行状态功能 ID"),
    ):
        values = integrity.get(key, [])
        lines.append(f"- {label}：{_cell(', '.join(map(str, values)) if values else '无')}")
    hash_conflicts = integrity.get("source_hash_conflicts", [])
    lines.append(f"- 工件哈希冲突：{_cell(len(hash_conflicts))} 项")
    lines.extend(["", "## 输入工件 SHA-256", "", "| 工件 | SHA-256 |", "|---|---|"])
    for key, value in sorted(report.get("input_hashes", {}).items()):
        lines.append(f"| {_cell(key)} | {_cell(value)} |")
    lines.extend(["", "## 解释边界", ""])
    for item in report.get("limitations", []):
        lines.append(f"- {_cell(item)}")
    lines.append("")
    return "\n".join(lines)
