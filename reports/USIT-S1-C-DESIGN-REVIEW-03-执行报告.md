# USIT-S1-C-DESIGN-REVIEW-03｜规格一致性修订与只读复核（READ-ONLY）

- **工单编号**：USIT-S1-C-DESIGN-REVIEW-03
- **执行时间**：2026-10-19 22:56 (GMT+8)
- **工作区绝对路径**：`C:\Users\Administrator\WorkBuddy\2026-10-15-22-09-47\usit_p0`
- **执行模式**：**严格只读设计修订** —— 仅读取源码/测试/报告，新增/修订本设计报告；**禁止修改**任何源码/测试/配置/manifest/图谱/原 test_plan.json/其他既有产物；**禁止执行** S1-C 实现、Android 测试、构建、安装、登录、网络或任何项目写入
- **修订基准**：`reports/USIT-S1-C-DESIGN-REVIEW-02-执行报告.md`（REVIEW-02），并继承 `USIT-S1-C-DESIGN-01`
- **设计依据**：全部结论来自对当前工作区源码的 grep + 区间阅读（见 §0 实测证据），非历史转述或推断

---

## 0. 实际检查文件与方法（Inspection Evidence）

| # | 检查对象 | 方法 | 观察结果（文件:行） | 可信状态 |
|---|---|---|---|---|
| I-1 | **投影接线（BLOCKER）** | `grep -rn "understanding_projection\|project_records\|project_record\b" usit/ --include=*.py` 并排除自身；查 `scanner.py` import | **除 `understanding_projection.py` 自身外，`usit/` 内零调用**；`scanner.py` 无 import → 投影**完全未接线** | 代码实测（grep） |
| I-2 | `EvidenceReference` 字段语义 | 读 `understanding_model.py:198-256` | `source_id:200`、`relative_path:201`、`summary:205`（文本片段，非哈希）、`collection_status:206`、`trust_level:207`、`content_present:208`、`digest_expected:209`、`digest_match:210`；构造级拒绝 `source_trusted/execution_verified/TRUSTED`（:223-224）；`digest_match=True` 须有 `digest_expected`（:250-251） | 代码实测 |
| I-3 | `_build_evidence` 实际填值 | 读 `understanding_projection.py:122-149` | `summary=evidence.get("summary")`（:141，文本片段）；`collection_status=CollectionStatus.PRESENT`（:142，**非 NOT_VERIFIED**）；`digest_expected` 未传→默认 `None`；`digest_match` 未传→默认 `None` | 代码实测 |
| I-4 | `_classify` 判定边界 | 读 `understanding_projection.py:100-119` | `resolution_status∈NON_SUCCESS`→`UNKNOWN`；`evidence` 非 Mapping 或为空→`UNKNOWN`(`missing_or_empty_evidence`)；否则（RESOLVED+非空 evidence mapping）→`STATIC_INFERENCE` | 代码实测 |
| I-5 | `_stable_id` 派生规则 | 读 `understanding_projection.py:74-80` | 纯内容派生 sha256，**不含 index/序号** → 相同输入必得相同 ID | 代码实测 |
| I-6 | `project_record` 的 projection_id | 读 `understanding_projection.py:195-205` | `projection_id=_stable_id("proj",{object_type,path,symbol,line,status,reasons})` → **两条相同 record 共享同一 projection_id** | 代码实测 |
| I-7 | `project_records` 统计与基数 | 读 `understanding_projection.py:348-371` | `items=[project_record(r) for r in records]` 逐条生成（不丢原始）；`projected_items=len(items)`；`static_inference` 按 item 计；`verified_fact` 写死 `0` | 代码实测 |
| I-8 | `parser_contract` 空 evidence fallback | 读 `parser_contract.py:94-121` | `adapt_graph_v1`：`evidence = evidence or {"graph_node_id": item.get("id")}`（:121）→ 空 evidence 被填成非空 dict，使 `_classify` 判为"有 evidence" | 代码实测 |
| I-9 | `parser_contract` 必填校验 | 读 `parser_contract.py:67-88` | `validate_record`：12 必填；`evidence` 必须非空 dict（:87-88），但因 I-8 fallback 总能通过 | 代码实测 |
| I-10 | `analyze_file` row 字段 | 复用 REVIEW-02 I-4/I-5 | row 含 `analysis_degradation`（:495）、`analysis_diagnostics`；`diagnostics_ref` 为数组索引（可越界/None）；manifest 不含源码文本 | 代码实测 |

> **三种状态声明（强制区分）**：
> - **设计已修订**：✅ 本报告（REVIEW-03）完成。
> - **源码已实现**：❌ 否。S1-C 未实施；`understanding_projection` 仍仅在 `usit/` 内孤立存在（I-1 BLOCKER）。
> - **测试已执行**：❌ 否。本工单 READ-ONLY，未运行任何测试；下文引用的任何"测试"均指 REVIEW-02 §F / S1-B 既有报告中的**历史证据**，已标注来源与范围，非本次执行。

---

## I. 相对 REVIEW-02 的修订清单（修正两处真实矛盾）

| 编号 | REVIEW-02 矛盾点 | 本修订（REVIEW-03）处理 |
|---|---|---|
| R-A | §B.3 写"`collection_status = CollectionStatus.NOT_VERIFIED`（不置 PRESENT）" | **与 S1-A 实现冲突**（I-3：`_build_evidence` 实际置 `PRESENT`）。修订：S1-C **保留 S1-A 的 `collection_status=PRESENT`**（`PRESENT`=证据片段在 artifact 中已收集/存在，与 `trust_level=NOT_VERIFIED`=来源未信任**正交**）；"未验证"语义由**新增字段 `summary_verification=NOT_CHECKED`** 表达，不改动 `collection_status` |
| R-B | §C.1 描述"RESOLVED + 非空 evidence 才 STATIC_INFERENCE"未揭示 fallback 漏洞 | **与 S1-A 行为细微不符**（I-4/I-8）：空 evidence 被 `adapt_graph_v1` 填成 `{"graph_node_id":id}`，使无真实摘要的 RESOLVED 记录也拿 `STATIC_INFERENCE`。修订：S1-A 的 projection 结论**保持不变**（由 S1-A 决定）；S1-C 在**计划完整性层**引入独立的 `evidence_meaningful` 严格判定（见 §C.4），graph_node_id-only 视为"证据不足/仅图节点"，据此标记 `incomplete_reasons`，不改变 S1-A 计数 |
| R-C | §A.2/A.3 未明确"重复 record 的 projection_id 是否相同" | 明确（I-5/I-6）：因 `_stable_id` 无 index，**完全相同的 record 共享同一 `projection_id`**；`duplicate_of` 指向该**共享内容 ID**；原始 record 列表全保留（见 §A 问题二） |
| R-D | §B 未核实 `digest_expected` 真实语义 | 明确（I-2）：`digest_expected` 是"**被比对对象的期望摘要**"，`digest_match` 是"实际摘要 vs 期望摘要"比对结果；二者针对**同一比对对象**。`summary` 是**文本片段**，**不是**摘要哈希（见 §E 问题五） |

---

## A 项：问题一 — 条目级与文件级完整性分离

### A.1 判定对象定义

- **条目级 `item.complete`**：针对**单条**候选 test-plan 条目（对应一个 projection_item / record）。其 `complete` 仅由**该条目自身的** `TestSpecification` 派生缺失 + 该条目自身的五类禁令命中情况决定（见 §D 伪代码）。**单条失败不影响同文件内其他独立条目**。
- **文件级 `file.complete_status`**（新引入，汇总用）：由**该文件所有条目**的 `complete` 做逻辑**与**（AND）得出；同时汇总文件级状态（如 `file_conclusion`、是否有孤立/冲突/降级）。文件级是**视图/汇总**，不反向改写条目级。

### A.2 混合成功/失败完整示例（确定性）

> 输入：单文件 `app.py`，含 3 条 parser-contract record：
> - `R1`：`RESOLVED` + 真实 evidence（含 summary）→ 投影 `STATIC_INFERENCE`，证据五状态全 true
> - `R2`：`RESOLVED` + 仅 `{"graph_node_id":..}`（无 summary）→ 投影 `STATIC_INFERENCE`（S1-A 行为），但 `evidence_meaningful=False`
> - `R3`：`UNRESOLVED`（解析降级）→ 投影 `UNKNOWN`

| 条目 | 条目级 complete | 原因 |
|---|---|---|
| item(R1) | **True** | evidence 有效、非降级、非冲突、摘要 NOT_CHECKED（本阶段恒命中）…→ 见 §D.4 说明 |
| item(R2) | **False** | `evidence_meaningful=False` → `incomplete_reasons=['insufficient_meaningful_evidence']` |
| item(R3) | **False** | `non_success_resolution` + `degraded_evidence` |

- **文件级 `file.complete_status = False`**（AND 汇总：一条 False 即 False），但 **item(R1) 仍独立标记为 `complete=True`**——单条失败未株连独立有效条目。
- 文件级 `file_conclusion = PARTIAL_UNDERSTANDING`（因存在 ≥1 条 RESOLVED+meaningful evidence，见 §C 优先级）。

**结论**：条目级与文件级解耦；失败隔离，汇总透明。

---

## B 项：问题二 — 重复记录与投影基数

### B.1 实测基数规则（来自 I-5/I-6/I-7）

- **完全重复 record**（内容级完全相同，含 `evidence_summary_hash` 相同）：`project_records` 对**每条**仍独立生成 `ProjectedItem`（不跳过、不丢原始记录）→ `total_records` 与 `projected_items` 均如实 +1 每条。
- 因 `_stable_id` **无 index**（I-5），两条相同 record 的 `projection_id` **完全相同** → `projected_items` 列表中会有 2 个 item 共享 1 个 `projection_id`。
- `static_inference` 按 item 计数 → 重复项**各自贡献 1**（若均为 RESOLVED+evidence）。

### B.2 `duplicate_of` 指向与统计口径

- **`duplicate_of` 指向该共享的 `projection_id`**（内容稳定标识，即首条与后续条相同的那个 ID）。因无 index ID，不存在"首条带序号 ID"的说法。
- **统计口径（避免双重计数误解）**：
  - `total_records` = 原始 record 数（含重复）—— **全保留，不丢**
  - `projected_items` = 生成 item 数 = `total_records + conflicts`
  - `unique_projection_ids` = 按 `projection_id` 去重后的数量（重复项折叠）
  - `static_inference` = 按 item 逐条计（重复项各自 +1）
  - `duplicate_groups` = 记录"哪些 `projection_id` 对应 >1 条原始 record"
- **不得静默丢失**：linkage 层 `record_keys[]` 必须列出**所有原始 record 的稳定键**（含重复），即使它们映射到同一 `projection_id`；`duplicate_of` 仅作关联标注，不删除任一侧语义。

---

## C 项：问题三 — 文件结论（file_conclusion）优先级

### C.1 互斥优先级表（相同输入确定性产出同一结论）

按**从高优先级到低优先级**判定，**命中即停**（互斥保证确定性）：

| 优先级 | 条件 | `file_conclusion` |
|---|---|---|
| 1 | 文件 `size_bytes==0` 或内容为空 | `NO_SEMANTIC_CONTENT` |
| 2 | `analysis_degradation.degraded==True` 且无可恢复语义（如 PYTHON_SYNTAX_ERROR / SEMANTIC_PARSER_UNAVAILABLE / UNSUPPORTED_LANGUAGE） | `DEGRADED_PARSE` |
| 3 | 文件在 `parser_contract.json` 中 0 条匹配 record | `NO_RECORDS` |
| 4 | 有 record 但**全部** `resolution_status∈NON_SUCCESS` 或 evidence 空 | `UNKNOWN_RESOLUTION` |
| 5 | 有内容、有 record、但**无任何** `evidence_meaningful==True` 的 RESOLVED record（如仅 graph_node_id fallback） | `INSUFFICIENT_EVIDENCE` |
| 6 | 存在冲突 record（`has_conflict`）且**无** meaningful RESOLVED record | `CONFLICT_ONLY` |
| 7 | 存在 ≥1 条 `evidence_meaningful==True` 的 RESOLVED record | `PARTIAL_UNDERSTANDING` |
| 8 | 其他（兜底） | `UNKNOWN` |

> **互斥保证**：上表顺序即优先级；每条文件至多落入一种结论（确定性）。
> **与 REVIEW-02 的差异**：REVIEW-02 §C.2 未规定冲突/降级的相对优先级，且把"无符号"单列；本表把"空文件"置顶、"降级"高于"无记录"、"冲突"仅在没有 meaningful 理解时独立成结论，消除歧义。

### C.2 与退化情形原因绑定

每个非 `PARTIAL_UNDERSTANDING` 结论必须带 `reasons[]`（如 `empty_or_no_semantic_content`、`degraded_evidence`、`no_parser_contract_records`、`all_records_non_success_or_no_evidence`、`missing_or_empty_evidence`、`conflict_pending`）。

---

## D 项：问题四 — 证据有效性统计（五状态分计，禁合并）

### D.1 五状态独立分子/分母（来自 I-2/I-3）

对**每一条** `EvidenceReference`，分别报告：

| 状态 | 取值 | 分子/分母口径 |
|---|---|---|
| `reference_exists` | true/false | 分子=构造成功且 `source_id`+`relative_path` 非空；分母=全部 evidence |
| `path_valid` | true/false/UNKNOWN | 分子=`relative_path` 命中 manifest `files[].path`；分母=全部 |
| `line_valid` | structurally_present/NOT_CHECKED | 分子=`line` 为正整数（结构合法）；分母=全部 |
| `diagnostic_index_valid` | true/false | 分子=`diagnostics_ref` 为非负整数且 `< len(analysis_diagnostics)`；分母=全部（仅对含 ref 的） |
| `summary_verification` | NOT_CHECKED/UNVERIFIABLE | 分子=`VERIFIED` 数（**本阶段恒 0**）；分母=全部 |

**禁止**：不得用单一"证据有效率 = 有效/总数"掩盖某一状态的失败。报告必须列出每个状态的 `valid / invalid / unknown_or_not_checked` 三数。

### D.2 `evidence_meaningful` 严格判定（R-B 引入，用于计划完整性）

```
def evidence_meaningful(ev: EvidenceReference, record: Mapping) -> bool:
    # 仅 graph_node_id fallback 不算"有意义语义证据"
    ev_body = record.get("evidence") or {}
    if set(ev_body.keys()) <= {"graph_node_id"}:
        return False
    # 须有真实摘要文本 或 可定位到真实符号的 locator
    return bool(ev.summary) or bool(ev.locator and ev.locator != f"record#{record.get('node_type') or 'unknown'}")
```

- `evidence_meaningful==False` → 候选计划条目 `incomplete_reasons` 追加 `insufficient_meaningful_evidence`（**不改变 S1-A 的 `STATIC_INFERENCE` 计数**，仅影响 S1-C 计划完整性）。

---

## E 项：问题五 — 摘要字段契约（digest_expected 语义澄清）

### E.1 `EvidenceReference` 真实语义（I-2）

- `summary`（:205）= **证据文本片段**（如行源码、符号定义文本），**绝对不是**哈希值。
- `digest_expected`（:209）= "被比对对象的**期望摘要**"（可选）。
- `digest_match`（:210）= "实际摘要 vs `digest_expected`" 的比对结果（可选，默认 None）。
- 约束（:250-251）：`digest_match=True` **必须**先有 `digest_expected`，否则构造抛错。

### E.2 文件级 SHA-256 与片段摘要的边界（严禁伪装）

- **`row['sha256']`（manifest 文件级摘要）≠ `evidence.summary`（片段文本）**：
  - 文件级 SHA-256 **可**放入 `digest_expected`（表示"期望该文件整体的摘要"），但**当且仅当**实际重读文件计算摘要并比对后才能置 `digest_match`——本阶段禁止重读（manifest 不含文本，I-10），故 **`digest_match` 恒为 `None`**。
  - **严禁**把文件 SHA-256 填入 `summary`（那是文本片段字段，语义错配）。
  - **严禁**把 `digest_expected=文件SHA256` 与 `digest_expected` 自身比较并宣称 `digest_match=True`（自比 MATCH，REVIEW-02 §B 已禁，此处重申）。

### E.3 S1-C 字段映射（修订后，修正 R-A）

| `EvidenceReference` 字段 | S1-C 取值 | 说明 |
|---|---|---|
| `summary` | `evidence.get("summary")`（文本片段） | 仅持有，不验证；绝不填文件哈希 |
| `collection_status` | **保留 S1-A 的 `PRESENT`** | `PRESENT`=片段在 artifact 存在；与 `trust_level` 正交（修正 R-A） |
| `trust_level` | `NOT_VERIFIED` | 构造强制 |
| `digest_expected` | `row['sha256']`（文件级，可选设） | 仅记录期望，不比对 |
| `digest_match` | `None` | 禁止重读，不得计算 |
| `summary_verification` | `"NOT_CHECKED"`（**新增字段**，非 S1-A 原生） | 表达"未做当前摘要验证"，与 `collection_status` 分离 |
| `content_present` | `True`（扫描时曾读取） | 不代表当前仍有效 |

---

## F 项：问题六 — 投影接线前提（BLOCKER）

### F.1 只读核实结论（I-1）

- `understanding_projection.project_records()` 是纯函数，输入 parser-contract records，输出 `ProjectionResult`（含 `projection_items`）。
- 但 `usit/` 内**除 `understanding_projection.py` 自身外，无任何模块 import 或调用** `project_records` / `project_record`；`scanner.py` 也未 import。
- → **S1-A 投影能力孤立未接线**：`parser_contract.json` records 虽已存在（REVIEW-01 已证），但其 → `project_records()` → `projection_items` 的链路**从未在扫描流水线中触发**。

### F.2 BLOCKER 声明

- **BLOCKER-1（投影未接线）**：S1-C 若依赖 `projection_items` 作为关联层核心输入，则**实施前必须先建立调用**（在 S1-C 内作为**独立后处理步骤显式调用 `project_records(parser_contract_records)`**，或经授权修改 `cli.py` 接线）。**不得假设已接线**。
- **BLOCKER-2（证据 fallback 导致 STATIC_INFERENCE 过宽）**：I-8 的 `graph_node_id` fallback 使无真实摘要的 RESOLVED 记录也获 `STATIC_INFERENCE`；S1-C 计划完整性须用 `evidence_meaningful`（D.2）加严，但**投影结论本身由 S1-A 决定，S1-C 不修改 S1-A**。
- 两项 BLOCKER 均为**设计层已知约束**，不影响本设计报告交付，但**实施前必须单独授权处理**（尤其 BLOCKER-1 的接线动作属于代码修改，超出本 READ-ONLY 工单）。

---

## G 项：最终字段 / schema 规格

### G.1 `understanding_linkage.json`（`usit-understanding-linkage-v1`）

| 路径 | 类型 | 说明 |
|---|---|---|
| `schema_version` | str | `usit-understanding-linkage-v1` |
| `files[]` | array | 每文件一条 |
| `files[].file_key` | str | = manifest `path`（相对） |
| `files[].analysis_degradation` | obj | 透传 scanner 行（含 `degraded,reason,method,resolution_status,diagnostics_ref`） |
| `files[].file_conclusion` | str | §C.1 枚举之一 |
| `files[].reasons[]` | str[] | 结论原因 |
| `files[].record_keys[]` | str[] | **全部**原始 record 稳定键（含重复，不丢） |
| `files[].projection_ids[]` | str[] | 对应 projection_item 的 `projection_id`（重复 record 共享同 ID） |
| `files[].evidence_ref_keys[]` | str[] | 关联 EvidenceReference 键 |
| `files[].duplicate_groups` | obj | `{projection_id: [原始record索引列表]}` |
| `files[].orphan_record` | bool | record.file_path 不在 manifest |
| `files[].has_conflict` | bool | 冲突记录存在 |
| `files[].complete_status` | bool | 文件级 AND 汇总（§A.1） |
| `projection_items[]` | array | 来自 `project_records().items`（纯引用 `projection_id`） |
| `evidence_refs[]` | array | 含 §D.1 五状态 + `summary_verification` + §E.3 映射 |
| `stats` | obj | 见 §H |

### G.2 `understanding_test_plan_candidate.json`（`usit-understanding-testplan-candidate-v1`）

| 路径 | 类型 | 说明 |
|---|---|---|
| `schema_version` | str | `usit-understanding-testplan-candidate-v1` |
| `derived_from` | str | 关联 `understanding_linkage.json` 的 `file_key` |
| `understanding_section` | obj | 元信息 |
| `items[]` | array | 每条目对应一个 projection_item/record |
| `items[].item_key` | str | `(file_key, qualified_name or record_key)` |
| `items[].evidence_ref_keys[]` | str[] | 关联证据 |
| `items[].degradation_aware` | bool | 是否感知降级 |
| `items[].complete` | bool | **条目级**（§A.1/§D） |
| `items[].incomplete_reasons[]` | str[] | 非空则 `complete=False` |
| `items[].execution` | obj | `ExecutionRecord()` 默认 **NOT_RUN** |
| `items[].evidence_meaningful` | bool | §D.2 严格判定 |

> **隔离保证**：两个产物均为**新增独立文件**；原 `test_plan.json` / `scan_manifest.json` / `knowledge_graph.json` / `report.md` **schema 与内容不变**（S1-B 已验证 symbols[]/calls[] 未变）。旧 schema 行（无 `analysis_degradation`）标 `provenance=UNKNOWN` + `limitations`，不崩溃。

---

## H 项：判定优先级 + 完整性伪代码

### H.1 文件结论优先级（§C.1 已列，此处固化）

`NO_SEMANTIC_CONTENT > DEGRADED_PARSE > NO_RECORDS > UNKNOWN_RESOLUTION > INSUFFICIENT_EVIDENCE > CONFLICT_ONLY > PARTIAL_UNDERSTANDING > UNKNOWN`（命中即停，互斥）。

### H.2 条目级完整性伪代码（分离于文件级）

```
def item_complete(spec, ev: EvidenceReference, ctx) -> tuple[bool, list[str]]:
    reasons = list(spec.incomplete_reasons)  # TestSpecification 派生缺失
    if ev is None or not (ev.reference_exists and ev.path_valid and ev.line_valid_structural):
        reasons.append("missing_or_invalid_evidence")
    if not ctx.evidence_meaningful:
        reasons.append("insufficient_meaningful_evidence")   # R-B 加严
    if ctx.file_degraded:
        reasons.append("degraded_evidence")
    if ctx.any_record_non_success:
        reasons.append("non_success_resolution")
    if ctx.has_conflict:
        reasons.append("conflict_pending")
    if ctx.orphan_record:
        reasons.append("orphan_record")
    if ctx.summary_verification != "VERIFIED":   # 本阶段恒 NOT_CHECKED
        reasons.append("summary_not_verified")
    if ctx.diagnostic_index_invalid:
        reasons.append("invalid_diagnostic_reference")
    return (len(reasons) == 0), dedupe(reasons)

# 文件级：仅汇总，不反向改写条目
file.complete_status = all(item.complete for item in file_items)
```

---

## I 项：统计口径（五维分计，禁合并）

| 维度 | 统计 | 验收期望 |
|---|---|---|
| 结构完整 | `item.complete==True` 数 / 总条目 | 本阶段因 `summary_not_verified` 恒命中，**预期接近 0**，诚实结果 |
| 证据有效（分状态） | 每状态 valid/invalid/unknown 三数 | 分别报告，不合并 |
| 摘要已验证 | `summary_verification=='VERIFIED'` 数 | **恒 0** |
| 语义推断 | `project_records().stats.static_inference` | 仅来自 RESOLVED+evidence record；不来自文件级误判 |
| 执行状态 | `NOT_RUN` 数 / `PASS` 数 | 全部 NOT_RUN；PASS 恒 0 |

> 真实项目字段分布与性能：**仍标记 `UNKNOWN`**（未实测，不冒充）。

---

## J 项：异常与负例（含混合示例见 §A.2）

| # | 场景 | 预期 | 失败条件 |
|---|---|---|---|
| S1 | 正常解析+meaningful evidence | item(R1).complete 依 §D.2 判定 | 全文件被误标 complete |
| S2 | 解析降级 | `DEGRADED_PARSE`；`degraded_evidence`∈reasons | 产生 STATIC_INFERENCE |
| S3 | 证据引用失效（ref 越界） | `diagnostic_index_valid=false`；`invalid_diagnostic_reference` | 标 valid |
| S4 | 未知语言 | `UNKNOWN`/无语义声明 | 标已理解 |
| S5 | 旧 schema（无 analysis_degradation） | 不崩溃；`provenance=UNKNOWN` | 抛异常 |
| S6 | 确定性（两次运行） | `projection_id` 全等；`verified_fact==0` | ID 不同 |
| S7 | 空文件 | `NO_SEMANTIC_CONTENT` | 标已理解 |
| S8 | 无符号文件 | `DISCOVERY_ONLY_NO_RECORDS` | STATIC_INFERENCE |
| **S9** | **重复 record**（2 条完全相同） | `record_keys[]` 列 2 条；`projection_ids` 共享同 ID；`duplicate_groups` 标记；`static_inference` +2（若 RESOLVED） | 静默丢 1 条 / `projection_id` 不同 |
| **S10** | **graph_node_id-only evidence** | 投影 `STATIC_INFERENCE`（S1-A）；但 `evidence_meaningful=False` → item.complete=False + `insufficient_meaningful_evidence` | 计划条目误标 complete |
| S11 | 孤立 record | `orphan_record=true` | 谎称已扫描 |
| S12 | 摘要未检查 | 全 `summary_verification==NOT_CHECKED`；零 `digest_match==True` | 出现 MATCH |
| S13 | 路径无效 | `path_valid=false` | 标 true |
| S14 | 行号无效 | `line_valid` 仅 structurally_present；绝不标"已验证行范围" | 标 true |

---

## K 项：未解决问题、BLOCKER 清单与状态

### K.1 BLOCKER 清单

| ID | 描述 | 影响 | 处理要求 |
|---|---|---|---|
| BLOCKER-1 | 投影未接线（I-1） | S1-C 无 `projection_items` 输入 | 实施前须授权接线（独立后处理调用 `project_records` 或改 `cli.py`） |
| BLOCKER-2 | evidence graph_node_id fallback 致 STATIC_INFERENCE 过宽（I-8） | 无真实摘要也计推断 | S1-C 用 `evidence_meaningful` 加严计划完整性；不改性 S1-A |

### K.2 未解决问题（Unresolved）

1. S1-A 投影未接线（同 BLOCKER-1），采用"独立后处理"还是"改 cli.py 接线"需授权。
2. `analysis_degradation` 仍为死字段；linkage 透传但未接入图谱/报告渲染（后续决策）。
3. 真实项目验证与性能：`UNKNOWN`。
4. **Android 端到端自主测试：未实现**，本修订未新增该能力。
5. 当前摘要验证需重读源码，被禁止；若未来授权重读，须单独设计校验协议。

### K.3 PASS/FAIL/未测试状态（设计验收，非实现验收）

| 项 | 状态 |
|---|---|
| 六项问题逐条处理（§A–§F） | ✅ **设计已修订（PASS 作为设计交付）** |
| 源码契约证据（§0 I-1–I-10） | ✅ 代码实测坐实 |
| 修正 REVIEW-02 两处矛盾（R-A/R-B/R-C/R-D） | ✅ 已修订 |
| BLOCKER 列出（F.2） | ✅ 已列 |
| **源码已实现** | ❌ **否**（S1-C 未实施） |
| **测试已执行（本次）** | ❌ **否**（READ-ONLY，未运行） |
| 历史测试证据引用 | ⚠️ 仅引 REVIEW-02 §F / S1-B 报告，已标来源与范围，非本次执行 |

---

## L 项：固定门禁（原值保持，全程未变）

| 门禁 | 值 |
|---|---|
| `F1` | `NOT_READY_TO_CLOSE` |
| `P0.8` | `HOLD` |
| `F2` | `STOP` |
| `历史授权连续性` | `UNRESOLVED` |
| `安全信任链` | `BLOCKED / NOT_VERIFIED` |

---

## M 项：最终结论

1. **设计已修订（本次交付）**：REVIEW-03 在 REVIEW-02 基础上，基于 10 项源码实测（I-1–I-10）修订了两处真实矛盾（collection_status 语义、graph_node_id fallback 致推断过宽），并逐条闭合六项问题：条目/文件级完整性分离（含混合成功/失败示例）、重复记录投影基数（共享 projection_id、duplicate_of 指向内容 ID、不丢原始）、file_conclusion 互斥优先级、五状态分计、digest_expected 语义澄清与防伪装、投影接线 BLOCKER。
2. **源码已实现：否**。S1-C 未进入实施；`understanding_projection` 仍孤立未接线（BLOCKER-1）。
3. **测试已执行：否**。本工单严格 READ-ONLY，未运行任何实现或测试；所有统计口径与负例为**设计规格**，待授权实施后验证。
4. **硬约束全部维持**：`verified_fact=0`、`ExecutionRecord` 全 `NOT_RUN`、无虚构成功；`summary_verification=NOT_CHECKED` 恒为真；`degraded=False` 不代表已理解；不放宽信任门禁；不把 UNKNOWN/AMBIGUOUS/DYNAMIC/EXTERNAL/UNRESOLVED/CONFLICT 升级为成功。
5. **建议**：采纳 REVIEW-03 作为 S1-C 最终设计规格；实施前须**另行授权**处理 BLOCKER-1（接线）与 BLOCKER-2（完整性加严），且不得自行进入 S1-C 实现。

---

**STOP**：仅提交设计修订，未修改任何文件。等待人工审查与独立授权；不得自行进入 S1-C 实施。
