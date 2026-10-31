# USIT-S1-C-DESIGN-REVIEW-07｜分支可达性与证据统计口径最终复核（READ-ONLY）

- **工单编号**：USIT-S1-C-DESIGN-REVIEW-07
- **执行时间**：2026-10-20 08:29 (GMT+8)
- **工作区绝对路径**：`C:\Users\Administrator\WorkBuddy\2026-10-15-22-09-47\usit_p0`
- **执行模式**：**严格只读设计审查** —— 仅读取源码/测试/报告，新增本设计报告；**禁止修改**任何源码/测试/配置/manifest/图谱/原 `test_plan.json`/其他既有产物；**禁止**接线、实现、构建、安装、登录、网络与 Android 测试。
- **修订基准**：`reports/USIT-S1-C-DESIGN-REVIEW-06-执行报告.md`（REVIEW-06），并继承 `USIT-S1-C-DESIGN-01` / `REVIEW-02` ~ `REVIEW-05` 的有效约束。
- **设计依据**：全部结论来自对当前工作区源码的 grep + 区间阅读（见 §0 实测证据），非历史转述或推断。

---

## 0. 实际检查文件与行号（Inspection Evidence）

| # | 检查对象 | 方法 | 观察结果（文件:行） | 可信状态 |
|---|---|---|---|---|
| I-1 | 投影接线（BLOCKER-1） | `grep -rn "understanding_projection\|project_records\|project_record\b" usit/ --include=*.py` | 仅 `understanding_projection.py` 自身引用；`cli.py` 未 import → 投影完全未接线 | 代码实测 |
| I-2 | `EvidenceReference` 真实字段 | 读 `understanding_model.py:197-214` | 字段：`source_schema/source_id/relative_path/line/symbol/locator/summary/collection_status/trust_level/content_present/digest_expected/digest_match/source_trusted/execution_verified/raw_value/mapping_method`。**无 `reference_exists` / `path_valid` / `line_valid_structural` / `summary_verification`** | 代码实测 |
| I-3 | `_build_evidence` 填值 | 读 `understanding_projection.py:122-149` | `summary=证据文本片段`，`collection_status=PRESENT`，不传 `digest_expected/digest_match`；`relative_path/line/symbol/locator` 取自 record | 代码实测 |
| I-4 | `_classify` 边界 | 读 `understanding_projection.py:100-119` | NON_SUCCESS 或 evidence 空/非 Mapping → UNKNOWN；RESOLVED+非空 Mapping evidence → STATIC_INFERENCE | 代码实测 |
| I-5 | `_stable_id` 无 index | 读 `understanding_projection.py:74-80` | 纯内容派生 sha256，无序号 | 代码实测 |
| I-6 | `project_record` projection_id | 读 `understanding_projection.py:195-205` | 相同 record → 共享同一 projection_id | 代码实测 |
| I-7 | `project_records` 基数 | 读 `understanding_projection.py:348-371` | `items = records + conflicts` → `projected_items = len(records)+len(conflicts)`；`verified_fact=0` | 代码实测 |
| I-8 | evidence fallback | 读 `parser_contract.py:94-125` | `:100` `evidence = item.get("evidence") or {}`；`:121` `"evidence": evidence or {"graph_node_id": id}`；`or` 仅替换**假值**（None/空），不替换真值字符串/列表 | 代码实测 |
| I-9 | `validate_record` 定义 | 读 `parser_contract.py:67-91` | 返回违规列表（**不修复、不删除**）；`:87-88` 要求 `evidence` 为 dict 且非空 | 代码实测 |
| I-10 | scanner degradation/sha256/manifest/test_plan | 读 `scanner.py:400-453,462,495,1098-1103,1108` | `row['sha256']` 与 `analysis_degradation` 透传 manifest；test_plan 静态 3 项骨架 | 代码实测 |
| I-11 | `TestSpecification.complete` 派生 | 读 `understanding_model.py:390-415` | `complete = not incomplete_reasons`；必填缺失进 `incomplete_reasons`（真实字段） | 代码实测 |
| I-12 | 硬阻断 | 读 `understanding_model.py:278-279,323-337` | 拒 `VERIFIED_FACT`；`ExecutionRecord` 仅 `NOT_RUN` | 代码实测 |
| I-13 | `excerpt_verified` 是否存在 | `grep -rn "excerpt_verified" usit/ tests/` | `usit/*.py` 与 `tests/*.py` 中零命中 | 代码实测（grep） |
| I-14 | pre-fallback evidence 状态是否被保留 | 读 `parser_contract.py:100,121` | 原始 evidence 是否为空在适配后被 `graph_node_id` 覆盖；适配输出无字段保留"原为空" → `N_no_evidence`（fallback 前为空）**NOT_AVAILABLE** | 代码实测 |
| I-15 | `_build_evidence` 是否抛错 | 读 `understanding_projection.py:122-126` | `if not isinstance(evidence, Mapping) or not evidence: return None` —— 返回 `None` 而非抛错 | 代码实测 |
| I-16 | `EvidenceReference` 构造抛错条件 | 读 `understanding_model.py:216-235` | `__post_init__` 在 trust/execution/line/字符串字段违规时 `raise`；但 `_build_evidence` 恒传安全值 → `N_construct_fail` ≈ 0（保留项） | 代码实测 |
| **I-17** | **`adapt_graph_v1` 是否放行非 Mapping evidence** | 读 `parser_contract.py:94-125` | `adapt_graph_v1` **不调用** `validate_record`；`:121` `evidence or {...}` 仅替换假值 → 若图节点 `evidence="字符串"`（truthy），适配后仍为字符串（非 Mapping）。故**适配输出可携带非 Mapping evidence 记录** | 代码实测（本次新增） |
| **I-18** | **`validate_record` 是否剔除违规记录** | 读 `scanner.py:1074-1076` | `parser_contract['record_validation_errors']=[... if (problems:=validate_record(record))]` —— **仅记录错误，不删除记录**；违规记录仍保留在 `parser_contract['records']` 并流向下游 | 代码实测（本次新增） |

> **三种状态声明（强制区分）**：
> - **设计已修订**：✅ 本报告（REVIEW-07）完成。
> - **源码已实现**：❌ 否。S1-C 未实施；`understanding_projection` 仍仅孤立存在（I-1 BLOCKER）。
> - **测试已执行**：❌ 否。本工单 READ-ONLY，未运行任何实现或测试；下文统计口径/决策表/示例均为**设计规格**，待授权实施后验证。

---

## 1. 修订前后差异总览（REVIEW-06 → REVIEW-07）

| 修订点 | REVIEW-06（旧） | REVIEW-07（新） | 依据 |
|---|---|---|---|
| **分支 8 可达性** | “防御，当前不可达” | **可达**（修正错误表述）；给出精确可达条件与示例 C19 | I-17 + I-18 |
| **`N_evidence_none` 子集归属** | 误列“⊆ 有候选证据” | 修正为 **= `N_non_candidate`（无候选记录的补集）**，非候选子集 | I-8 / I-15 |
| **候选/非候选拆分** | 未显式定义 `N_candidate` / `N_non_candidate` | 显式定义两计数，重写恒等式层级 | I-8 / I-15 |
| **`item_complete` 字段来源** | 伪代码直接引用 `ev.reference_exists/path_valid/line_valid_structural` | **明确标注为派生校验字段（非模型字段）**，列真实字段/派生/注解/上下文四类来源表 | I-2（grep 零命中） |
| **门禁传播边界** | 仅说 file_context 全局 | 显式区分 item 级原因存储 vs file 级聚合；说明 file_conclusion 与 item.complete 正交、不矛盾 | 工单 #4 |
| **补充边界输入** | 未含"全部 NON_SUCCESS+降级/冲突"与"混合+全部 RESOLVED 无候选" | 新增 C20/C21/C19 等，覆盖三类 | 工单 #5 |
| **恒等式前提** | 仅给等式 | 每个恒等式补**适用范围/单位/前置条件/不成立原因** | 工单 #6 |
| **状态真实性** | 已区分三类 | 强化 PASS/FAIL/未测试矩阵，杜绝虚构 | 工单 #7 |

---

## 2. 逐项修订（对应工单 7 项要求）

### 2.1 分支可达性（工单 #1）

**§5 全部分支条件的逐条可达性**：

| 分支 | 条件 | 可达性 | 依据 |
|---|---|---|---|
| 0 | `empty`（size_bytes==0 / 无内容） | ✅ 当前可达 | 边界 B0 |
| 1 | `no_records`（n_records==0） | ✅ 当前可达 | B1a/B1b/B1c |
| 2 | `all_non_success` 或 `all_evidence_empty` | ✅ 当前可达 | B2a/B2b |
| 3 | `has_conflict and meaningful==0` | ✅ 当前可达 | B3 |
| 4 | `file_degraded and meaningful==0` | ✅ 当前可达 | B4 |
| 6 | `meaningful>=1`（需 `excerpt_verified`） | ⚠️ **仅未来可达** | I-13 |
| 7 | `resolved_ev_count>=1` | ✅ 当前可达（典型） | B7 |
| **8** | 越过序 2 且 `resolved_ev_count==0` | ✅ **当前可达**（REVIEW-06 误判为不可达） | **I-17 + I-18** |

**分支 8 可达性证明（关键修正）**：
- 越过序 2 的充要条件是：文件**有记录**且 **∃ 至少一条 RESOLVED** 且 **∃ 至少一条 `has_evidence_candidate==True`**。
- 分支 8 命中还需 `meaningful_count==0`（当前恒真）且 `resolved_ev_count==0`，即 **没有任何“RESOLVED 且 `has_evidence_candidate`”的记录**。
- 这要求：RESOLVED 记录本身 **`has_evidence_candidate==False`**（即 evidence 非 Mapping，例：图节点 `evidence="字符串"`），而另一条 **NON_SUCCESS 记录 `has_evidence_candidate==True`**（经 fallback 得到 `{graph_node_id}`，I-8）。
- 该输入在当前契约下**可产出**：`adapt_graph_v1` 放行非 Mapping evidence（I-17：`or` 不替换真值字符串），且 `validate_record` 仅记录不剔除（I-18：scanner.py:1074-1076），故记录流到 `_build_evidence` 返回 `None`（I-15）→ 该 RESOLVED 记录 `has_evidence_candidate=False`、贡献 `N_evidence_none`。
- **结论**：分支 8 在**当前规则下可达**，REVIEW-06 “当前不可达”为错误表述，已修正。其语义为 **`INDETERMINATE`/UNKNOWN**——“文件含可解析记录，但可解析记录均无可用证据，而另一些（非成功）记录有证据”，属真实存在的歧义态，值得显式标出而非静默落入它支。

**补充混合组合（工单 #1 明确要求）**：
- “RESOLVED + 非 Mapping evidence” 单记录文件 → `all_evidence_empty=True` → **分支 2 `UNKNOWN_RESOLUTION`**（即 B2b）。
- “NON_SUCCESS + 候选 evidence” 混合“RESOLVED + 非 Mapping evidence” → 越过序 2、resolved_ev_count==0 → **分支 8 `UNKNOWN`**（即 C19）。

### 2.2 证据计数重定义（工单 #2）

**按数据流阶段拆分**（适配前 / 适配后 / 候选 / 构造结果）：

| 阶段 | 计数 | 定义 | 可得性 |
|---|---|---|---|
| 适配前（parser 输入） | `N_raw_pre` | 图节点+边原始数 | ✅ 1:1 映射（I-17 不改条数） |
| 适配后 | `N_raw` | 适配 records 数 = `N_raw_pre` | ✅ |
| 候选判定 | `N_candidate` | `count(has_evidence_candidate(r)==True)`（post-fallback Mapping 真值） | ✅ |
| 候选判定 | `N_non_candidate` | `count(has_evidence_candidate(r)==False)` = **`N_evidence_none`** | ✅ |
| 构造结果（候选内） | `N_construct_ok` | `_build_evidence` 返回有效 `EvidenceReference` | ✅ ⊆ `N_candidate` |
| 构造结果（候选内） | `N_construct_fail` | `_build_evidence` 抛错（I-16，当前 ≈ 0） | ✅ ⊆ `N_candidate` |
| 构造结果（非候选） | `N_evidence_none` | `_build_evidence` 返回 `None`（非 Mapping/空） | ✅ = `N_non_candidate` |
| 适配前空 | `N_no_evidence` | fallback 前 evidence 为空 | ❌ **NOT_AVAILABLE**（I-14） |

**互斥性与完整覆盖**：
- `N_raw = N_candidate + N_non_candidate`（按 `has_evidence_candidate` 二分，互斥且穷尽）。
- `N_candidate = N_construct_ok + N_construct_fail`（候选内构造结果二分）。
- 故 `N_raw = N_construct_ok + N_construct_fail + N_evidence_none`（= REVIEW-06 式 A，本次**修正其子集标注**）。
- **关键修正**：`N_evidence_none` 是 **`N_non_candidate` 的别名 / 无候选记录的计数**，绝非“有候选证据”的子集。REVIEW-06 表中 `N_evidence_none ⊆ 有候选` 为错误箭头，已删。

### 2.3 字段来源（工单 #3）

`item_complete()` 所用字段按**真实性**四类列明（不得冒充模型字段）：

| 字段 | 类别 | 来源 / 计算时机 | 缺失处理 |
|---|---|---|---|
| `spec.incomplete_reasons` | **真实模型字段** | `TestSpecification.incomplete_reasons`（I-11），`__post_init__` 由必填字段派生 | 恒存在（可为空 list） |
| `ev.summary` | **真实模型字段** | `EvidenceReference.summary`（I-2:205） | 可 `None` |
| `ev.collection_status` | **真实模型字段** | `EvidenceReference.collection_status`（I-2:206） | 恒 `PRESENT`（I-3） |
| `ev.trust_level` | **真实模型字段** | `EvidenceReference.trust_level`（I-2:207） | 恒 `NOT_VERIFIED` |
| `ev.relative_path` / `ev.line` / `ev.source_id` / `ev.locator` | **真实模型字段** | `EvidenceReference`（I-2:200-204,211） | 可 `None`/可缺 |
| `ev.reference_exists` | **派生校验字段（非模型）** | 由 `ev.source_id and ev.relative_path` 非空判定；**非 EvidenceReference 属性**（grep 零命中，I-2） | 缺则判 False |
| `ev.path_valid` | **派生校验字段（非模型）** | 由 `ev.relative_path ∈ manifest.files[].path` 判定；**非 EvidenceReference 属性** | 否即 False |
| `ev.line_valid_structural` | **派生校验字段（非模型）** | 由 `ev.line` 为正整数判定（仅结构上合法）；**非 EvidenceReference 属性** | 否即 False |
| `link.summary_verification` | **链接层注解（非模型）** | REVIEW-04/05 定义；落在 `understanding_linkage.json.evidence_refs[]`，不写入 `EvidenceReference` | 恒 `NOT_CHECKED` |
| `link.evidence_meaningful` | **链接层注解（非模型）** | `classify_evidence_meaning()` 派生（`False`/`UNKNOWN`，I-13） | 恒非 `True` |
| `link.evidence_has_semantic_keys` | **链接层注解（非模型）** | 派生信息标注（非成功判定） | — |
| `ctx.record_non_success` | **ItemContext 聚合（提案对象）** | 由 `record.resolution_status ∈ NON_SUCCESS` | — |
| `ctx.orphan_record` | **ItemContext 聚合（提案对象）** | 由 `record.file_path ∉ manifest` | — |
| `ctx.diagnostic_index_invalid` | **ItemContext 聚合（提案对象）** | 由 `diagnostics_ref` 越界/非法 | — |
| `ctx.file_degraded` | **ItemContext 聚合（file_context）** | 由 `analysis_degradation.degraded` | — |
| `ctx.file_has_conflict` | **ItemContext 聚合（file_context）** | 由冲突记录存在 | — |

> **不得冒充**：`reference_exists` / `path_valid` / `line_valid_structural` 在当前 `EvidenceReference` 代码中**不存在**（grep `usit/` 零命中，I-2），它们是 S1-C 设计预留的**校验输出**，须由独立校验步骤计算，伪代码不得写成已存储属性。

### 2.4 门禁传播（工单 #4）

- **item 级原因**：存于每条 `projection_item` / `test_plan_candidate.item` 的 `incomplete_reasons[]`（真实字段派生，I-11）。`complete = (len(incomplete_reasons)==0)`。
- **file 级**：`files[].complete_status` 是**派生聚合**（`bool(items) and all(item.complete)`），**不**作为独立“原因”存储；`files[].file_conclusion` 是**独立分类**（8 值枚举），存于 `files[].file_conclusion`。
- **二者正交、不矛盾**：`file_conclusion` 回答“该文件的理解状态如何”（如 `INSUFFICIENT_EVIDENCE`）；`item.complete` 回答“该条测试规格是否齐备且可验证”。文件判 `INSUFFICIENT_EVIDENCE` 与条目 `complete=False`（门禁未过）**不冲突**——前者是理解层结论，后者是验证层门禁。
- **降级/冲突的有意全局性**：`degraded_evidence` / `conflict_pending` 作为 file_context，**有意**写入文件内**所有**条目的 `incomplete_reasons[]`。这不是“株连”——它是证据源本身的真实共享属性（整个文件的证据被降级/冲突影响），与 item 级结构性缺陷（缺字段、orphan、越界）**隔离**：后者逐条独立计算、互不传染，前者全局一致施加，属合理设计。

### 2.5 优先级与样例（工单 #5）

新增边界输入，覆盖三类：
- “全部 NON_SUCCESS + 文件降级” → **C20**：分支 2（`UNKNOWN_RESOLUTION`，降级被序 2 吞并）。
- “全部 NON_SUCCESS + 冲突” → **C21**：分支 2（`UNKNOWN_RESOLUTION`，冲突被序 2 吞并）。
- “混合 resolution + 全部 RESOLVED 无候选证据” → **C19**：分支 8（`UNKNOWN`）。
- 共 **19 个当前可达样例（C1–C19 + C20/C21）**，≥15，逐条逐步推演见 §5。
- 未来能力（F1–F4，依赖 `excerpt_verified`）**单独列于 §6，不计入当前可达**。

### 2.6 恒等式审查（工单 #6）

见 §7：每个恒等式给出适用范围/单位/前置条件/不成立原因；重复记录、`conflict_items`、`projected_items` 分属不同维度，不得混称。

### 2.7 状态真实性（工单 #7）

见 §9 PASS/FAIL/未测试矩阵；无实际执行项一律标“未测试”，不虚构 PASS/测试结果或功能能力。

---

## 3. 完整性伪代码（字段来源标注修订）

```python
def item_complete(spec, ev, link, ctx) -> tuple[bool, list[str]]:
    # spec.incomplete_reasons  → 真实模型字段 (I-11)
    reasons = list(spec.incomplete_reasons)
    # ev.reference_exists / path_valid / line_valid_structural → 派生校验字段 (I-2, 非模型)
    if ev is None or not (ev.reference_exists and ev.path_valid and ev.line_valid_structural):
        reasons.append("missing_or_invalid_evidence")          # item_context
    # link.evidence_meaningful → 链接层注解 (I-13)
    if link.evidence_meaningful != "True":                       # 当前恒 False/UNKNOWN
        reasons.append("evidence_not_meaningful")               # item_context
    # link.summary_verification → 链接层注解，恒 NOT_CHECKED
    if link.summary_verification != "VERIFIED":                  # 门禁：足以独立阻止 complete=True
        reasons.append("summary_not_verified")                  # (可与其他原因并存)
    if ctx.record_non_success:                                   # ItemContext
        reasons.append("non_success_resolution")                # item_context
    if ctx.orphan_record:
        reasons.append("orphan_record")                         # item_context
    if ctx.diagnostic_index_invalid:
        reasons.append("invalid_diagnostic_reference")          # item_context
    if ctx.file_degraded:                                        # file_context（有意全局）
        reasons.append("degraded_evidence")
    if ctx.file_has_conflict:                                    # file_context（有意全局）
        reasons.append("conflict_pending")
    return (len(reasons) == 0), dedupe(reasons)                  # 任一原因即 False
```

> 注：`ev.reference_exists / path_valid / line_valid_structural` 在伪代码中以 `ev.` 前缀出现仅为可读性；**实现时须由独立校验步骤计算**（I-2 证明它们非 `EvidenceReference` 属性）。

---

## 4. 文件结论决策表（确定性布尔伪代码 + 边界验证）

```python
def decide_file_conclusion(f) -> tuple[str, list[str]]:
    empty        = (f.size_bytes == 0) or (not f.has_content)
    no_records   = (f.n_records == 0)
    if empty:                                              # 分支 0
        return ("NO_SEMANTIC_CONTENT", ["empty_or_no_semantic_content"])
    if no_records:                                         # 分支 1（守卫 all() 空陷阱）
        reason = "no_symbols" if not f.has_symbols else "symbols_no_records"
        return ("NO_RECORDS", [reason])
    all_non_success    = all(r.resolution_status in NON_SUCCESS for r in f.records)
    all_evidence_empty = all(not has_evidence_candidate(r) for r in f.records)
    meaningful_count   = sum(1 for r in f.records
                             if r.resolution_status == "RESOLVED" and r.evidence_meaningful == "True")  # 当前恒 0
    resolved_ev_count  = sum(1 for r in f.records
                             if r.resolution_status == "RESOLVED" and has_evidence_candidate(r))
    if all_non_success or all_evidence_empty:              # 分支 2
        return ("UNKNOWN_RESOLUTION", ["all_records_non_success_or_no_evidence"])
    if f.has_conflict and meaningful_count == 0:           # 分支 3
        return ("CONFLICT_ONLY", ["conflict_pending"])
    if f.file_degraded and meaningful_count == 0:          # 分支 4
        return ("DEGRADED_PARSE", ["degraded_evidence"])
    if meaningful_count >= 1:                              # 分支 6 [FUTURE]
        reasons = ["has_meaningful_evidence"]
        if f.file_degraded: reasons.append("degraded_evidence")
        if f.has_conflict:  reasons.append("conflict_pending")
        return ("PARTIAL_UNDERSTANDING", reasons)
    if resolved_ev_count >= 1:                             # 分支 7（当前典型）
        return ("INSUFFICIENT_EVIDENCE", ["missing_or_empty_evidence"])
    return ("UNKNOWN", ["indeterminate_resolution_without_usable_evidence"])  # 分支 8（当前可达）
```

### 边界输入 → 唯一分支验证

| 边界 | 输入条件 | 命中分支 | 文件结论 |
|---|---|---|---|
| B0 | `size_bytes==0` | 0 | `NO_SEMANTIC_CONTENT` |
| B1a | 0 记录且无符号 | 1 | `NO_RECORDS`(no_symbols) |
| B1b | 0 记录且有符号无记录 | 1 | `NO_RECORDS`(symbols_no_records) |
| B1c | 0 记录：`no_records` 先于 `all()`（空陷阱规避） | 1 | `NO_RECORDS` |
| B2a | 全部 NON_SUCCESS | 2 | `UNKNOWN_RESOLUTION` |
| B2b | 全部 RESOLVED 但 evidence 全非 Mapping（all_evidence_empty） | 2 | `UNKNOWN_RESOLUTION` |
| B3 | 冲突 + ≥1 RESOLVED+候选，无 True | 3 | `CONFLICT_ONLY` |
| B4 | 文件降级 + ≥1 RESOLVED+候选，无冲突，无 True | 4 | `DEGRADED_PARSE` |
| B6 [FUTURE] | ≥1 RESOLVED+候选且 evidence_meaningful=="True" | 6 | `PARTIAL_UNDERSTANDING` |
| B7 | ≥1 RESOLVED+候选，无冲突/降级，无 True | 7 | `INSUFFICIENT_EVIDENCE` |
| **B8** | **RESOLVED+非 Mapping + NON_SUCCESS+候选（越过序 2，resolved_ev_count==0）** | **8** | **`UNKNOWN`（当前可达）** |

> 10 个边界覆盖枚举中除 `PARTIAL_UNDERSTANDING` 外的全部**当前可达**值，且每边界命中唯一分支；分支 8 经 B8 证实可达。

---

## 5. 当前可达样例逐步推演（C1–C19 + C20/C21，共 21 个，≥15）

> 通用前提：所有条目 `complete=False`（门禁 `summary_not_verified` 恒命中，§3）；下表“额外原因”省略恒定项 `summary_not_verified` 与（有候选时）`evidence_not_meaningful`。双维度独立给出：`resolution ∈ {RESOLVED, NON_SUCCESS}`；`evidence` 当前 = `post_fallback:非空dict`（C19/C20 例外为非 Mapping/全 NON_SUCCESS）。

**逐步推演示例（关键边界）**：

- **C19（分支 8，逐步）**：输入 `mixed.py`：R1(RESOLVED, evidence="字符串"→非 Mapping)、R2(NON_SUCCESS, fallback→{graph_node_id})。
  1. `empty?` 否；`no_records?` 否（2 条）。
  2. `all_non_success?` 否（R1 为 RESOLVED）。
  3. `all_evidence_empty?` 否（R2 有候选）。→ 越过分支 2。
  4. `has_conflict?` 否；`file_degraded?` 否 → 不进 3/4。
  5. `meaningful_count?` 0 → 不进 6。
  6. `resolved_ev_count = count(RESOLVED and 候选)` = 0（R1 非 Mapping→无候选）→ 不进 7。
  7. **→ 分支 8 `UNKNOWN`**。计数：N_raw=2, N_candidate=1(R2), N_non_candidate=1(R1), N_construct_ok=1, N_evidence_none=1。恒等式 `2 = 1+0+1` ✓。
  - 条目：R1 `missing_or_invalid_evidence`(ev=None)+`non_success?`否（R1 是 RESOLVED）仅 `missing_or_invalid_evidence`；R2 `non_success_resolution`+`evidence_not_meaningful=False`。各自 `complete=False`。

- **C20（全部 NON_SUCCESS + 文件降级，逐步）**：输入 `deg_all.py`：R1/R2 均 NON_SUCCESS，file_degraded=True。
  1. `empty?` 否；`no_records?` 否。
  2. `all_non_success?` **是** → **分支 2 `UNKNOWN_RESOLUTION`**（降级被序 2 吞并，不进分支 4）。计数 N_raw=2, N_candidate=2(均 fallback 候选), N_construct_ok=2。
  - 条目：各 `non_success_resolution` + `degraded_evidence`(file_context) + 恒定项。

- **C21（全部 NON_SUCCESS + 冲突，逐步）**：输入 `cf_all.py`：R1/R2 均 NON_SUCCESS，has_conflict=True。
  1. `all_non_success?` 是 → **分支 2 `UNKNOWN_RESOLUTION`**（冲突被序 2 吞并）。计数同 C20。

| # | resolution 维度 | evidence 维度 | 分支 | `file_conclusion` | 条目 `complete` | 额外 `incomplete_reasons` | 计数变化 | 依据 |
|---|---|---|---|---|---|---|---|---|
| C1 | RESOLVED | 真实 summary（非空 dict） | 7 | `INSUFFICIENT_EVIDENCE` | False | `evidence_not_meaningful=UNKNOWN` | N_raw+1, N_candidate+1, N_construct_ok+1 | I-3/I-15 |
| C2 | RESOLVED | 仅 graph_node_id | 7 | `INSUFFICIENT_EVIDENCE` | False | `evidence_not_meaningful=False` | N_raw+1, N_candidate+1, N_construct_ok+1 | I-8 |
| C3 | NON_SUCCESS | post_fallback 非空 | 2 | `UNKNOWN_RESOLUTION` | False | `non_success_resolution` | N_raw+1, N_candidate+1, N_construct_ok+1 | I-17 |
| C4 | —（无记录） | — | 0 | `NO_SEMANTIC_CONTENT` | N/A | — | 文件级无 records | B0 |
| C5 | —（0 记录） | — | 1 | `NO_RECORDS`(no_symbols) | N/A | — | N_raw=0 | B1a |
| C6 | —（0 记录有符号） | — | 1 | `NO_RECORDS`(symbols_no_records) | N/A | — | N_raw=0 | B1b |
| C7 | RESOLVED（无 True） | 非空 dict | 4 | `DEGRADED_PARSE` | False | `degraded_evidence`[fc] | N_raw+1, N_candidate+1, N_construct_ok+1, file_degraded=True | §1 |
| C8 | **全部 NON_SUCCESS** | post_fallback 非空 | **2** | **`UNKNOWN_RESOLUTION`** | False | `all_records_non_success`(file_degraded 被吞并) | N_raw+1, N_candidate+1, N_construct_ok+1 | 修正（原误判分支4） |
| C9 | 全部 NON_SUCCESS | post_fallback 非空 | 2 | `UNKNOWN_RESOLUTION` | False | `non_success_resolution` | N_raw+1, N_candidate+1, N_construct_ok+1 | I-14 |
| C10 | ≥1 RESOLVED + ≥1 NON_SUCCESS（有冲突） | 非空 dict | 3 | `CONFLICT_ONLY` | False | `conflict_pending`[fc] | N_raw+1, N_candidate+1, N_construct_ok+1, has_conflict=True | B3 |
| C11 | RESOLVED + orphan | 非空 dict | 7 | `INSUFFICIENT_EVIDENCE` | False | `orphan_record`(+`missing_or_invalid_evidence`) | N_raw+1, N_construct_ok+1 | I-1 |
| C12 | RESOLVED + diagnostics_ref 越界 | 非空 dict | 7 | `INSUFFICIENT_EVIDENCE` | False | `invalid_diagnostic_reference` | N_raw+1, N_construct_ok+1 | I-10 |
| C13 | RESOLVED + relative_path∉manifest | 非空 dict | 7 | `INSUFFICIENT_EVIDENCE` | False | `missing_or_invalid_evidence` | N_raw+1, N_construct_ok+1 | I-3 |
| C14 | RESOLVED + line 非正整数 | 非空 dict | 7 | `INSUFFICIENT_EVIDENCE` | False | `missing_or_invalid_evidence`(line 仅结构) | N_raw+1, N_construct_ok+1 | I-11 |
| C15 | RESOLVED + TestSpec 缺必填 | 非空 dict | 7 | `INSUFFICIENT_EVIDENCE` | False | spec 派生缺失字段名 | N_raw+1, N_construct_ok+1 | I-11 |
| C16 | RESOLVED ×2 完全相同（重复） | 非空 dict | 7 | `INSUFFICIENT_EVIDENCE` | False | 同对应单条 | N_raw+2, duplicate_records+1 | §6 |
| C17 | 混合：NON_SUCCESS + RESOLVED | 非空 dict | 7 | `INSUFFICIENT_EVIDENCE` | False（按条各自） | 各条按自身 | N_raw+2, N_construct_ok+2 | 序2 不命中 |
| C18 | RESOLVED + evidence 非 Mapping（单记录文件） | **非 Mapping → None** | 2 | `UNKNOWN_RESOLUTION` | False | `missing_or_invalid_evidence`(ev=None) | N_raw+1, **N_evidence_none+1** | I-15（B2b 实例） |
| **C19** | **RESOLVED + 非 Mapping + NON_SUCCESS + 候选** | **混合** | **8** | **`UNKNOWN`** | False（按条） | R1:`missing_or_invalid_evidence`; R2:`non_success_resolution` | N_raw+2, N_candidate+1, N_evidence_none+1 | **I-17+I-18（分支8可达）** |
| **C20** | **全部 NON_SUCCESS + 文件降级** | post_fallback 非空 | **2** | **`UNKNOWN_RESOLUTION`** | False | `all_records_non_success` + `degraded_evidence`[fc] | N_raw+1, N_construct_ok+1 | 工单#5 补充 |
| **C21** | **全部 NON_SUCCESS + 冲突** | post_fallback 非空 | **2** | **`UNKNOWN_RESOLUTION`** | False | `all_records_non_success` + `conflict_pending`[fc] | N_raw+1, N_construct_ok+1 | 工单#5 补充 |

> 共 **21 个当前可达样例（C1–C21）**，全部经 §4 伪代码确定性推导，结论自洽；其中 C19/C20/C21 为本报告新增边界。

---

## 6. 未来假设样例（[FUTURE-ONLY]，不计入当前可达）

| # | 未来场景 | 分支 | 文件结论 | 说明 |
|---|---|---|---|---|
| F1 | RESOLVED + `evidence_meaningful=="True"`（需 `excerpt_verified`） | 6 | `PARTIAL_UNDERSTANDING` | REVIEW-04 E1 正确归属 |
| F2 | 降级 + ≥1 条 meaningful==True RESOLVED | 6 | `PARTIAL_UNDERSTANDING`(+`degraded_evidence`) | REVIEW-04 E7 未来版 |
| F3 | 冲突 + ≥1 条 meaningful==True RESOLVED | 6 | `PARTIAL_UNDERSTANDING`(+`conflict_pending`) | REVIEW-04 E11 未来版 |
| F4 | 输入 β：降级 + 1 条 meaningful==True RESOLVED | 6 | `PARTIAL_UNDERSTANDING` | REVIEW-04 §7 输入 β 未来版 |

> **当前不可达根因**：`excerpt_verified` 在 `usit/` 与 `tests/` 中均不存在（I-13）→ `meaningful_count` 恒 0 → 分支 6 永不触发。

---

## 7. 计数恒等式审查（适用范围 / 单位 / 前置 / 不成立原因）

| 恒等式 | 范围 | 单位 | 前置条件 | 不成立原因 |
|---|---|---|---|---|
| `N_raw = N_candidate + N_non_candidate` | 全部适配记录 | 条 | 每条按 `has_evidence_candidate` 恰好归入一侧 | 若某记录缺失 evidence 字段无法判定（当前不会发生，I-8 保证有字段） |
| `N_candidate = N_construct_ok + N_construct_fail` | 候选记录 | 条 | `_build_evidence` 对每条候选要么返回引用要么抛错 | 若 `_build_evidence` 对候选返回 None（当前逻辑不会，I-15 仅对非 Mapping 返回 None，而候选定义即 Mapping 真值）→ 计数遗漏 |
| `N_raw = N_construct_ok + N_construct_fail + N_evidence_none` | 全部适配记录 | 条 | 上两条同时成立；`N_evidence_none = N_non_candidate` | 同上游；`N_no_evidence` 不得进入此式（NOT_AVAILABLE，I-14） |
| `projected_items = raw_records + conflict_items` | 投影输出 | 条目 | `project_records` 实现为 `items = records + conflicts`（I-7） | 若实现改为去重或合并冲突 → 不成立 |
| `duplicate_records = raw_records − unique_projection_ids_raw` | 投影输入 | 条 | `_stable_id` 内容派生（I-5）且记录逐条计入 | 若内容随扫描变动导致 id 不稳定 → 跨扫描不成立 |
| `conflict_items = len(conflicts)` | 投影输出 | 条目 | `_detect_conflicts` 输出 | 与重复/原始记录**独立维度**，不得与 `duplicate_records` 混算 |

> **维度隔离**：`raw_records`（记录数）、`unique_projection_ids_raw`（不同 id 数）、`duplicate_records`（重复条数）、`conflict_items`（冲突条数）、`projected_items`（最终条目数）分属不同计数，互不包含，禁止统称为“记录数”。

---

## 8. 记录键稳定范围与重复/投影统计（继承，无变更）

- 关联键 `record_key = rec:{projection_id}:{first_index}`：**本次扫描内有效**；跨扫描稳定身份 **UNRESOLVED**（不宣称）。
- 数值示例（`dup.py` 含 R_a,R_a,R_b，0 冲突）：`raw_records=3`；`projection_ids=[pid_a,pid_a,pid_b]`；`record_keys=[rec:pid_a:0, rec:pid_a:1, rec:pid_b:2]`（不丢）；`duplicate_groups={pid_a:[0,1]}`（`R_a@1→first_index=0`，无自指/环）；`unique_projection_ids_raw=2`；`duplicate_records=1`；`projected_items=3+0=3`（重复未扣）✓。

---

## 9. 未解决问题、BLOCKER 与 PASS/FAIL/未测试矩阵

### 9.1 BLOCKER 清单（继续列为代码修改事项，不得自行接线/解除）
| ID | 描述 | 影响 | 处理要求 |
|---|---|---|---|
| BLOCKER-1 | 投影未接线（I-1） | S1-C 无 `projection_items` 输入 | 实施前须授权接线（独立后处理调用 `project_records()` 或改 `cli.py`）——代码修改 |
| BLOCKER-2 | evidence `graph_node_id` fallback（I-8）致 `STATIC_INFERENCE` 过宽 | 无真实摘要也计推断 | S1-C 用 `evidence_meaningful` 加严；不修改 S1-A |

### 9.2 未解决问题（Unresolved）
1. BLOCKER-1 采用“独立后处理”还是“改 cli.py 接线”需授权。
2. `analysis_degradation` 仍为死字段；linkage 透传未接入渲染。
3. 真实项目验证与性能：`UNKNOWN`（未实测）。
4. **Android 端到端自主测试：未实现**。
5. `excerpt_verified` 摘要验证协议：当前不存在（I-13），属未来能力。
6. `N_no_evidence`（pre-fallback 空）当前 NOT_AVAILABLE（I-14）；未来须按 REVIEW-06 §2.3 透传 `evidence_was_empty_before_adapt` 方可计数。
7. `N_construct_fail`（构造抛错）当前 ≈ 0（I-16）；若未来 S1-C 直接构造 `EvidenceReference` 传入非法参数才可能触发。
8. 跨扫描稳定身份：未解决（§8）。
9. **分支 8 语义命名**：本报告采用 `UNKNOWN`（indeterminate）承载；若审查认为应单独枚举（如 `INDETERMINATE`），可在实施时调整——属命名决策，不影响可达性结论。

### 9.3 PASS / FAIL / 未测试矩阵（设计验收，非实现验收）
| 项 | 状态 |
|---|---|
| 分支 8 可达性证明（修正“当前不可达”错误） | ✅ 设计已修订（PASS 作为设计交付） |
| `N_evidence_none` 子集归属修正（非候选，非“有候选”子集） | ✅ 设计已修订 |
| 候选/非候选计数显式拆分 + 恒等式层级 | ✅ 设计已修订 |
| `item_complete` 字段来源四类分离（真实/派生/注解/上下文） | ✅ 设计已修订 |
| 门禁传播边界（item 存储 / file 聚合 / 正交不矛盾） | ✅ 设计已修订 |
| 补充边界输入（C19/C20/C21）+ ≥15 当前可达逐步推演 | ✅ 设计已修订（21 例） |
| 恒等式前提审查（范围/单位/前置/不成立） | ✅ 设计已修订 |
| 源码契约证据（§0 I-1–I-18） | ✅ 代码实测坐实 |
| **源码已实现** | ❌ **否**（S1-C 未实施） |
| **测试已执行（本次）** | ❌ **否**（READ-ONLY，未运行） |
| 历史测试证据引用 | ⚠️ 仅引 REVIEW-02 §F / S1-B 报告，已标来源与范围，非本次执行 |

---

## 10. 固定门禁（原值保持，全程未变）
| 门禁 | 值 |
|---|---|
| `F1` | `NOT_READY_TO_CLOSE` |
| `P0.8` | `HOLD` |
| `F2` | `STOP` |
| `历史授权连续性` | `UNRESOLVED` |
| `安全信任链` | `BLOCKED / NOT_VERIFIED` |

---

## 11. 最终结论

1. **设计已修订（本次交付）**：REVIEW-07 在 REVIEW-06 基础上，基于 `adapt_graph_v1` 放行非 Mapping evidence（I-17）与 `validate_record` 非破坏性（I-18）的实测，逐项闭合 7 项要求：① **分支可达性**——逐条核算 §4 全分支，证明**分支 8 当前可达**（精确条件 + 示例 C19），修正 REVIEW-06 “当前不可达”错误表述；补充“RESOLVED+非 Mapping”与“NON_SUCCESS+候选”混合组合（§2.1）；② **证据计数**——按适配前/后/候选/构造四阶段重定义，显式拆分 `N_candidate`/`N_non_candidate`，修正 `N_evidence_none` 子集归属（=无候选计数，非“有候选”子集），重写恒等式层级（§2.2/§7）；③ **字段来源**——四类分离（真实模型字段 / 派生校验字段 / 链接层注解 / ItemContext 聚合），明确 `reference_exists/path_valid/line_valid_structural` 非 `EvidenceReference` 属性（grep 零命中，I-2），不得冒充（§2.3/§3）；④ **门禁传播**——item 级原因存储、file 级派生聚合、`file_conclusion` 与 `item.complete` 正交不矛盾（§2.4）；⑤ **优先级与样例**——新增 C19/C20/C21 边界，提供 21 个当前可达逐步推演（§5）；⑥ **恒等式审查**——每个等式补适用范围/单位/前置/不成立原因，维度隔离（§7）；⑦ **状态真实性**——强化 PASS/FAIL/未测试矩阵，杜绝虚构（§9.3）。
2. **源码已实现：否**。S1-C 未进入实施；`understanding_projection` 仍孤立未接线（BLOCKER-1，I-1）。
3. **测试已执行：否**。本工单严格 READ-ONLY，未运行任何实现或测试；所有决策表、统计、示例为**设计规格**，待授权实施后验证。
4. **硬约束全部维持**：`verified_fact=0`、`ExecutionRecord` 全 `NOT_RUN`、无虚构成功；`summary_verification=NOT_CHECKED` 恒为真、受约束条目 `complete=False`（门禁，未为求 True 放宽）；`degraded=False` 不代表已理解；不放宽信任门禁；不把 UNKNOWN/AMBIGUOUS/DYNAMIC/EXTERNAL/UNRESOLVED/CONFLICT 升级为成功。
5. **建议**：采纳 REVIEW-07 作为 S1-C 最新设计规格；实施前须**另行授权**处理 BLOCKER-1（接线）与 BLOCKER-2（完整性加严），且不得自行进入 S1-C 实现。本设计规格的“通过”**不等于**源码实现通过或测试通过（验收规则明示）。

---

**STOP**：仅提交设计修订，未修改任何文件。等待人工审查与独立授权；不得自行进入 S1-C 实施，不得修改 `understanding_projection.py` / `parser_contract.py` / `cli.py`，不得解除 BLOCKER-1/2。
