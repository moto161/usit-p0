# USIT-S1-C-DESIGN-REVIEW-05｜可达性与决策表最终一致性修订（READ-ONLY）

- **工单编号**：USIT-S1-C-DESIGN-REVIEW-05
- **执行时间**：2026-10-19 23:37 (GMT+8)
- **工作区绝对路径**：`C:\Users\Administrator\WorkBuddy\2026-10-15-22-09-47\usit_p0`
- **执行模式**：**严格只读设计审查** —— 仅读取源码/测试/报告，新增/修订本设计报告；**禁止修改**任何源码/测试/配置/manifest/图谱/原 `test_plan.json`/其他既有产物；**禁止**接线、实现、构建、安装、登录、网络与 Android 测试。
- **修订基准**：`reports/USIT-S1-C-DESIGN-REVIEW-04-执行报告.md`（REVIEW-04），并继承 `USIT-S1-C-DESIGN-01` / `REVIEW-02` / `REVIEW-03`。
- **设计依据**：全部结论来自对当前工作区源码的 grep + 区间阅读（见 §0 实测证据），非历史转述或推断。

---

## 0. 实际检查文件与行号（Inspection Evidence）

> 下列 I-1–I-12 在 REVIEW-04 已坐实，本次复核确认未变；**I-13 为本次新增关键实测**。

| # | 检查对象 | 方法 | 观察结果（文件:行） | 可信状态 |
|---|---|---|---|---|
| I-1 | 投影接线（BLOCKER-1） | `grep -rn "understanding_projection\|project_records\|project_record\b" usit/ --include=*.py` | 仅 `understanding_projection.py` 自身引用；`cli.py` 未 import → 投影完全未接线 | 代码实测 |
| I-2 | `EvidenceReference` 真实字段 | 读 `understanding_model.py:197-214` | 字段无 `summary_verification`；`digest_expected:209`/`digest_match:210` 默认 `None` | 代码实测 |
| I-3 | `_build_evidence` 填值 | 读 `understanding_projection.py:122-149` | `summary=证据文本片段`，`collection_status=PRESENT`，不传 `digest_expected/digest_match` | 代码实测 |
| I-4 | `_classify` 边界 | 读 `understanding_projection.py:100-119` | NON_SUCCESS 或 evidence 空 → UNKNOWN；RESOLVED+非空 evidence → STATIC_INFERENCE | 代码实测 |
| I-5 | `_stable_id` 无 index | 读 `understanding_projection.py:74-80` | 纯内容派生 sha256，无序号 | 代码实测 |
| I-6 | `project_record` projection_id | 读 `understanding_projection.py:195-205` | 相同 record → 共享同一 projection_id | 代码实测 |
| I-7 | `project_records` 基数 | 读 `understanding_projection.py:348-371` | `items = records + conflicts` → `projected_items = len(records)+len(conflicts)`；`verified_fact=0` | 代码实测 |
| I-8 | evidence fallback | 读 `parser_contract.py:94-121` | `evidence or {"graph_node_id": id}` → 空 evidence 被填成非空 | 代码实测 |
| I-9 | 必填校验 | 读 `parser_contract.py:67-91` | 12 必填；因 I-8 fallback，`evidence` 总能通过 | 代码实测 |
| I-10 | scanner degradation/sha256/manifest/test_plan | 读 `scanner.py:400-453,462,495,1098-1103,1108` | `row['sha256']` 与 `analysis_degradation` 透传 manifest；test_plan 静态 3 项骨架 | 代码实测 |
| I-11 | `TestSpecification.complete` 派生 | 读 `understanding_model.py:390-415` | `complete = not incomplete_reasons`；必填缺失进 `incomplete_reasons` | 代码实测 |
| I-12 | 硬阻断 | 读 `understanding_model.py:278-279,323-337` | 拒 `VERIFIED_FACT`；`ExecutionRecord` 仅 `NOT_RUN` | 代码实测 |
| **I-13** | **`excerpt_verified` 是否存在** | `grep -rn "excerpt_verified" usit/ tests/` | **`usit/*.py` 与 `tests/*.py` 中零命中**；仅本 REVIEW-04/REVIEW-05 报告自身的 `classify_evidence_meaning` 伪代码中出现该词作为"未来预留标记"。**结论：`excerpt_verified` 在当前代码与数据中均不存在，无任何记录可携带它** | 代码实测（grep） |

> **三种状态声明（强制区分）**：
> - **设计已修订**：✅ 本报告（REVIEW-05）完成。
> - **源码已实现**：❌ 否。S1-C 未实施；`understanding_projection` 仍仅孤立存在（I-1 BLOCKER）。
> - **测试已执行**：❌ 否。本工单 READ-ONLY，未运行任何实现或测试；下文统计口径/决策表/示例均为**设计规格**，待授权实施后验证。

---

## 1. 当前可达性分析（本修订的核心结论）

依据 I-13（`excerpt_verified` 不存在），重算 REVIEW-04 的 `classify_evidence_meaning`：

```
classify_evidence_meaning(evidence, record, manifest_paths):
    keys = set(evidence.keys())
    if keys <= {"graph_node_id"}:        # 仅有图节点引用，无语义摘录
        return "False"
    # 需要本构建不具备的证明：excerpt_verified 溯源标记
    provenance_ok = bool(evidence.get("excerpt_verified")) and (record_path in manifest_paths)
    #                              ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
    # I-13：excerpt_verified 不存在 → provenance_ok 恒为 False
    if provenance_ok:
        return "True"
    return "UNKNOWN"
```

**可达性推论（当前）**：
- `evidence_meaningful == "True"` **在当前设计中不可达**（provenance 永远无法满足）。
- 所有记录只能落为 `"False"`（仅 `graph_node_id`）或 `"UNKNOWN"`（有 summary/locator 但无法证明摘录真实）。
- 因此 REVIEW-04 §4 决策表**序 6**（`≥1 条 evidence_meaningful=="True"` 的 RESOLVED 记录 → `PARTIAL_UNDERSTANDING`）**当前不可达**。
- REVIEW-04 的 **E1 / E7 / 输入 β** 把结论写成了 `PARTIAL_UNDERSTANDING`，与自身的 §4 自相矛盾（§4 序 5 对"有 RESOLVED 但无 True"应判 `INSUFFICIENT_EVIDENCE`）。这是 REVIEW-04 遗留的逻辑矛盾，本修订统一纠正。

**重算后当前可达的结论分布**：
- 含 ≥1 条 RESOLVED 且有证据记录（无 True）→ **`INSUFFICIENT_EVIDENCE`**（序 5/序 7）。
- 含冲突且无 True → **`CONFLICT_ONLY`**（序 3）。
- 文件降级且无 True → **`DEGRADED_PARSE`**（序 4）。
- `PARTIAL_UNDERSTANDING` → **仅未来可达**（须 `excerpt_verified` 协议上线）。

---

## 2. 逐项修订（对应工单 8 项要求）

### 2.1 统一当前可达状态
- 重算 E1：原 `PARTIAL_UNDERSTANDING` → 修正为 **`INSUFFICIENT_EVIDENCE`**（当前可达）。
- 重算 E7：原"降级 + 有意义记录 → PARTIAL_UNDERSTANDING" → "有意义"=True 不可达 → 修正为 **`DEGRADED_PARSE`**（当前可达）。
- 重算 输入 β（REVIEW-04 §7）：原"降级 + 有意义 RESOLVED → PARTIAL_UNDERSTANDING" → 标为 **[FUTURE-ONLY]**，当前不可达。
- 重算 E11：原"冲突 + 有意义记录 → PARTIAL_UNDERSTANDING" → 修正为 **`CONFLICT_ONLY`**（当前可达）。
- 原 REVIEW-04 §6 中所有标"正（正确识别部分理解）"且结论为 `PARTIAL_UNDERSTANDING` 的条目，一律重新归类；`PARTIAL_UNDERSTANDING` 仅在 §7 未来假设中保留。
- 见 §6（当前可达样例）与 §7（未来假设样例）。

### 2.2 消除文件结论条件歧义
- §4 每个分支改写为**确定性布尔函数**（见 §5 伪代码），明确谓词定义：
  - `all_non_success` = 所有 record `resolution_status ∈ NON_SUCCESS`。
  - `all_evidence_empty` = 所有 record 在 parser-contract fallback **之前** `evidence` 为空（即无候选证据）。
  - `meaningful_count` = `count(resolution_status==RESOLVED and evidence_meaningful=="True")` → 当前恒 0。
  - `resolved_with_evidence_count` = `count(resolution_status==RESOLVED and 有非空 evidence)`。
  - `has_conflict` / `file_degraded` = file_context 全局标志。
- **优先级澄清**：先判"是否有任何可理解记录"（`all_non_success` 或 `all_evidence_empty` → `UNKNOWN_RESOLUTION`）；再在"确有 RESOLVED+证据"前提下，按 `CONFLICT_ONLY`(序3) → `DEGRADED_PARSE`(序4) → `PARTIAL_UNDERSTANDING`(序6,未来) → `INSUFFICIENT_EVIDENCE`(序7) 判定。"全部 NON_SUCCESS"、"evidence 全空"、"RESOLVED 但证据不充分"三者**互斥分层**：前两者在序 2 合并处理（均表示"无可理解记录"），后者（序 7）是序 2 之后的子分支。
- 用 ≥8 个边界输入逐项验证命中唯一分支（见 §5 表）。

### 2.3 明确 `complete` 的含义（门禁 vs 质量指标）
- `complete` 在本设计中是**当前阶段的门禁状态（gate state）**，**不是**长期质量评分。
- 当前 `complete` 被 `summary_not_verified` 原因**结构性强制为 False**（因为 `summary_verification` 注解恒为 `NOT_CHECKED`，无任何记录通过摘要验证）。
- **下游防误解要求**：消费方**不得**把 `complete=False` 当作"质量差/代码坏"的统一信号。必须**同时读取 `incomplete_reasons[]`** 区分成因：
  - `summary_not_verified`：仅表示"摘要未经验证"，是当前全员强制的门禁状态，不代表内容本身有问题；
  - `missing_or_invalid_evidence` / `orphan_record` / `invalid_diagnostic_reference` / `non_success_resolution`：表示结构性或解析层面的真实缺陷，属"质量/正确性"问题。
- 只有当未来 `summary_verification` 协议上线且某条目通过验证后，`summary_not_verified` 不再恒命中，`complete` 才会反映真实完整性；在那之前 `complete=False` 一律读作"门禁未通过 / 尚不可验证"。

### 2.4 补齐证据计数边界
五类计数互不重叠或明确包含，恒等式见 §8：
1. `N_raw`（原始记录数）：文件内 parser-contract 记录总数（含重复）。
2. `N_no_evidence`（无证据记录数）：`evidence` 在 fallback **之前**为空/缺失的记录数（真·无证据）。
3. `N_evidence_candidate`（存在证据候选的记录数）：`= N_raw - N_no_evidence`。
4. `N_construct_ok`（成功构造 EvidenceReference 数）。
5. `N_construct_fail`（构造失败数）：候选构造抛错者，记入 `construction_failures[]`，不静默丢弃。
- 包含关系：`N_evidence_candidate = N_construct_ok + N_construct_fail`；`N_raw = N_construct_ok + N_construct_fail + N_no_evidence`。

### 2.5 明确记录键的稳定范围
- `rec:{projection_id}:{first_index}` 是**本次扫描内的关联键**，仅用于本次扫描的重复分组/去重关联。
- **不承诺跨扫描稳定**：其中 `first_index` 是本次扫描的顺序下标，跨扫描会变化；`projection_id` 虽为内容哈希（相同内容→相同 id），但仅当内容逐字节一致时才相同，且我们**不保证**两次扫描中"同一逻辑实体"拥有相同内容（代码可能改动）。
- **跨扫描关联：未解决（UNRESOLVED）**。本设计不宣称具备跨扫描稳定身份；若未来需要，须单独设计实体对齐协议（如稳定符号路径 + 语义锚点），不在本工单范围。

### 2.6 复核重复记录与投影统计
分项（见 §9 恒等式与示例）：
- `raw_records = N_raw`（含重复）。
- `unique_projection_ids_raw` = raw 记录去重后的不同 `projection_id` 数。
- `duplicate_records = N_raw - unique_projection_ids_raw`（非首现、共享 projection_id 者）。
- `conflict_items = len(conflicts)`。
- `projected_items = N_raw + conflict_items`（I-7；**重复记录不扣除**，仅共享 id）。
- **适用条件**：`projected_items = raw_records + conflict_items` 由 `project_records` 实现（items=records+conflicts）恒真；它**不**表示"去重后"——重复记录仍各占一条 item。
- **无自指/无环/不丢**：`duplicate_of` 指向 `first_index`（< 当前 index），故自身不会指向自己（无自指），且总指向最早首现（无链式环）；`record_keys[]` 列出全部 N_raw（不丢），`projected_items` 含全部 N_raw + conflicts（不丢）。

### 2.7 重新做一致性自证
- §6 提供 **17 个当前可达样例**（≥12），每个给出：输入条件 / 命中分支 / 文件结论 / 条目完整性 / 统计变化 / 依据。
- §7 单独列出**未来假设样例**（明确标注 [FUTURE-ONLY]，不混入当前验收通过项）。
- §10 用同一组输入逐条推演，证明字段/规则/统计/示例相互一致。

---

## 3. 统一字段 / schema 规格（继承 REVIEW-04，仅标注可达性）

### 3.1 `EvidenceReference`（真实模型，S1-C 不改写）

| 字段 | S1-C 取值 | 说明 |
|---|---|---|
| `summary` | 文本片段 | 仅持有，不验证；绝不填文件哈希 |
| `collection_status` | `PRESENT` | 片段在 artifact 存在；与 `trust_level` 正交 |
| `trust_level` | `NOT_VERIFIED` | 构造强制 |
| `content_present` | `True` | 扫描时曾读取，不代表当前仍有效 |
| `digest_expected` | `None` | 禁止填文件级 sha256 |
| `digest_match` | `None` | 禁止重读，不得计算 |
| `source_id/relative_path/line/locator` | 来自 record | 结构字段，供五状态校验 |

> `EvidenceReference` 无 `summary_verification` 字段（I-2）。该值作为链接层注解存在（§3.3），不落入此冻结 dataclass。

### 3.2 `understanding_linkage.json`（`usit-understanding-linkage-v1`）
同 REVIEW-04 §2.2；`evidence_refs[]` 每条含：`summary_verification="NOT_CHECKED"`、`evidence_has_semantic_keys`、`evidence_meaningful`（当前仅 `"False"`/`"UNKNOWN"`）、`scan_file_sha256`。

### 3.3 `understanding_test_plan_candidate.json`（`usit-understanding-testplan-candidate-v1`）
同 REVIEW-04 §2.3；`items[].complete` 本阶段恒 `False`（门禁，见 §2.3）；`items[].evidence_meaningful` 当前仅 `"False"`/`"UNKNOWN"`。

---

## 4. 完整性伪代码（item_context / file_context 分离 + 门禁语义）

```python
def item_complete(spec, ev, link, ctx) -> tuple[bool, list[str]]:
    reasons = list(spec.incomplete_reasons)            # (1) TestSpecification 派生缺失 (I-11)
    if ev is None or not (ev.reference_exists and ev.path_valid and ev.line_valid_structural):
        reasons.append("missing_or_invalid_evidence")  # (2) item_context
    if link.evidence_meaningful != "True":             # (3) "False"/"UNKNOWN" 均非 True (R3)
        reasons.append("evidence_not_meaningful")      #     当前恒命中（因无 True）
    if link.summary_verification != "VERIFIED":        # (4) 门禁：当前恒 NOT_CHECKED
        reasons.append("summary_not_verified")         #     ★本阶段强制 complete=False 的唯一根因
    if ctx.record_non_success:
        reasons.append("non_success_resolution")       # (5) item_context
    if ctx.orphan_record:
        reasons.append("orphan_record")                # (6) item_context
    if ctx.diagnostic_index_invalid:
        reasons.append("invalid_diagnostic_reference") # (7) item_context
    if ctx.file_degraded:
        reasons.append("degraded_evidence")            # file_context（全局共享事实）
    if ctx.file_has_conflict:
        reasons.append("conflict_pending")             # file_context
    return (len(reasons) == 0), dedupe(reasons)

# 文件级：仅汇总，不反向改写条目
file.complete_status = bool(file_items) and all(item.complete for item in file_items)
```

**要点（门禁语义）**：
- 因 (4) `summary_not_verified` **恒命中**，所有受约束条目 `complete=False`；这是**门禁关闭**，不是质量差。
- **下游必须读 `incomplete_reasons`** 区分"仅未验证"(summary_not_verified) 与"结构性缺陷"(其余)。
- `evidence_not_meaningful`、(5)(6)(7) 均为逐条计算，互不传染；file_context 全局一致施加，非株连。

---

## 5. 文件结论决策表（确定性布尔伪代码 + 8 边界验证）

```python
def decide_file_conclusion(f) -> tuple[str, list[str]]:
    # 谓词（file 级聚合）
    empty        = (f.size_bytes == 0) or (not f.has_content)
    no_records   = (f.n_records == 0)
    all_non_success   = all(r.resolution_status in NON_SUCCESS for r in f.records)
    all_evidence_empty = all(not has_evidence_candidate(r) for r in f.records)  # fallback 之前
    meaningful_count   = sum(1 for r in f.records
                             if r.resolution_status == "RESOLVED" and r.evidence_meaningful == "True")
    resolved_ev_count  = sum(1 for r in f.records
                             if r.resolution_status == "RESOLVED" and has_evidence_candidate(r))

    if empty:
        return ("NO_SEMANTIC_CONTENT", ["empty_or_no_semantic_content"])           # 分支 0
    if no_records:
        reason = "no_symbols" if not f.has_symbols else "symbols_no_records"
        return ("NO_RECORDS", [reason])                                           # 分支 1
    if all_non_success or all_evidence_empty:
        return ("UNKNOWN_RESOLUTION", ["all_records_non_success_or_no_evidence"]) # 分支 2
    # 此后：≥1 条 RESOLVED + 非空证据
    if f.has_conflict and meaningful_count == 0:
        return ("CONFLICT_ONLY", ["conflict_pending"])                            # 分支 3
    if f.file_degraded and meaningful_count == 0:
        return ("DEGRADED_PARSE", ["degraded_evidence"])                           # 分支 4
    if meaningful_count >= 1:                                                     # 分支 6 [FUTURE]
        reasons = ["has_meaningful_evidence"]
        if f.file_degraded: reasons.append("degraded_evidence")
        if f.has_conflict:  reasons.append("conflict_pending")
        return ("PARTIAL_UNDERSTANDING", reasons)
    if resolved_ev_count >= 1:                                                    # 分支 7（当前典型）
        return ("INSUFFICIENT_EVIDENCE", ["missing_or_empty_evidence"])
    return ("UNKNOWN", ["unknown"])                                               # 分支 8（防御，当前不可达）
```

### 8 边界输入 → 唯一分支验证

| 边界 | 输入条件 | 命中分支 | 文件结论 |
|---|---|---|---|
| B0 | `size_bytes==0` | 0 | `NO_SEMANTIC_CONTENT` |
| B1a | 0 记录且无符号 | 1 | `NO_RECORDS`(no_symbols) |
| B1b | 0 记录且有符号无记录 | 1 | `NO_RECORDS`(symbols_no_records) |
| B2a | 全部 NON_SUCCESS | 2 | `UNKNOWN_RESOLUTION` |
| B2b | 全部 RESOLVED 但 fallback 前 evidence 全空 | 2 | `UNKNOWN_RESOLUTION`(all_evidence_empty) |
| B3 | 冲突 + ≥1 RESOLVED+证据，无 True | 3 | `CONFLICT_ONLY` |
| B4 | 文件降级 + ≥1 RESOLVED+证据，无冲突，无 True | 4 | `DEGRADED_PARSE` |
| B7 | ≥1 RESOLVED+证据，无冲突、未降级，无 True | 7 | `INSUFFICIENT_EVIDENCE` |
| B6 [FUTURE] | ≥1 RESOLVED+证据且 evidence_meaningful=="True" | 6 | `PARTIAL_UNDERSTANDING`（仅未来） |
| B8 [DEF] | 理论上 resolved_ev_count==0 却越过序 2 | 8 | `UNKNOWN`（防御，当前不可达） |

> 8 个边界覆盖枚举中除 `PARTIAL_UNDERSTANDING` 外的全部 **当前可达** 值（`NO_SEMANTIC_CONTENT/NO_RECORDS/UNKNOWN_RESOLUTION/CONFLICT_ONLY/DEGRADED_PARSE/INSUFFICIENT_EVIDENCE`），且每个边界命中唯一分支。

---

## 6. 至少 12 个当前可达样例（逐条：输入 / 分支 / 结论 / 完整 / 统计 / 依据）

> 所有条目 `complete=False`（门禁，§2.3/§4）；下表 `incomplete_reasons` **省略恒定项** `summary_not_verified` 与（有证据时）`evidence_not_meaningful`，仅列额外成因，便于阅读。标注 `[+summary_not_verified][+evidence_not_meaningful=UNKNOWN|False]` 表示恒有。

| # | 输入场景（当前可达） | 分支 | `file_conclusion` | 条目 `complete` | 额外 `incomplete_reasons`（除恒定项） | 统计变化 | 依据 |
|---|---|---|---|---|---|---|---|
| C1 | 正常解析 + 真实 summary（无摘录证明） | 7 | `INSUFFICIENT_EVIDENCE` | False | `evidence_not_meaningful=UNKNOWN` | N_raw+1, N_construct_ok+1, resolved_ev_count+1 | §1 重算 E1 |
| C2 | 正常解析 + 仅 `graph_node_id` | 7 | `INSUFFICIENT_EVIDENCE` | False | `evidence_not_meaningful=False` | N_raw+1, N_construct_ok+1 | §1 / I-8 |
| C3 | UNRESOLVED（解析降级，NON_SUCCESS） | 2 | `UNKNOWN_RESOLUTION` | False | `non_success_resolution` | N_raw+1, N_no_evidence+1 | I-4 |
| C4 | 空文件 `size_bytes==0` | 0 | `NO_SEMANTIC_CONTENT` | N/A | — | 文件级无 records | §5 B0 |
| C5 | 0 记录（无符号） | 1 | `NO_RECORDS`(no_symbols) | N/A | — | N_raw=0 | §5 B1a |
| C6 | 0 记录（有符号无记录） | 1 | `NO_RECORDS`(symbols_no_records) | N/A | — | N_raw=0 | §5 B1b |
| C7 | 降级 + RESOLVED 记录（无 True） | 4 | `DEGRADED_PARSE` | False | `degraded_evidence`[file_context] | N_raw+1, N_construct_ok+1, file_degraded=True | §1 重算 E7 |
| C8 | 降级 + 全部 NON_SUCCESS | 4 | `DEGRADED_PARSE` | False | `non_success_resolution`+`degraded_evidence` | N_raw+1, N_no_evidence+1 | §5 B4 |
| C9 | 全部 NON_SUCCESS | 2 | `UNKNOWN_RESOLUTION` | False | `non_success_resolution` | N_raw+1, N_no_evidence+1 | §5 B2a |
| C10 | 冲突 + ≥1 RESOLVED+证据（无 True） | 3 | `CONFLICT_ONLY` | False | `conflict_pending`[file_context] | N_raw+1, has_conflict=True | §1 重算 E10/E11 |
| C11 | 孤立 record（file_path 不在 manifest）+ RESOLVED+证据 | 7 | `INSUFFICIENT_EVIDENCE` | False | `orphan_record` | N_raw+1, orphan=True | I-1 语义 |
| C12 | diagnostics_ref 越界 + RESOLVED+证据 | 7 | `INSUFFICIENT_EVIDENCE` | False | `invalid_diagnostic_reference` | N_raw+1, N_construct_ok+1 | I-10 |
| C13 | relative_path 不在 manifest + RESOLVED+证据 | 7 | `INSUFFICIENT_EVIDENCE` | False | `missing_or_invalid_evidence` | N_raw+1, N_construct_ok+1 | I-3 |
| C14 | line 非正整数 + RESOLVED+证据 | 7 | `INSUFFICIENT_EVIDENCE` | False | `missing_or_invalid_evidence`(line 仅结构) | N_raw+1, N_construct_ok+1 | I-11 |
| C15 | TestSpecification 缺必填 + RESOLVED+证据 | 7 | `INSUFFICIENT_EVIDENCE` | False | spec 派生缺失字段名 | N_raw+1, N_construct_ok+1 | I-11 |
| C16 | 重复记录 ×2（完全相同）+ RESOLVED+证据 | 7 | `INSUFFICIENT_EVIDENCE` | False | 同对应单条 | N_raw+2, duplicate_records+1 | §9 |
| C17 | 混合：NON_SUCCESS + RESOLVED+证据（无 True） | 7 | `INSUFFICIENT_EVIDENCE` | False（按条各自） | 各条按自身（non_success / evidence_not_meaningful） | N_raw+2 | §5 序2 不命中 |

> 共 17 个当前可达样例（C1–C17），全部经 §5 伪代码确定性推导，结论自洽。

---

## 7. 未来假设样例（[FUTURE-ONLY]，不混入当前验收通过项）

> 以下条件**当前不可达**（依赖 `excerpt_verified` 协议，I-13）。仅作前瞻设计，参与未来验收，不计当前 PASS/FAIL。

| # | 未来场景 | 分支 | 文件结论 | 说明 |
|---|---|---|---|---|
| F1 | RESOLVED + `evidence_meaningful=="True"`（需 `excerpt_verified` 溯源标记） | 6 | `PARTIAL_UNDERSTANDING` | REVIEW-04 E1 原写法的正确归属 |
| F2 | 降级 + ≥1 条 meaningful==True RESOLVED | 6 | `PARTIAL_UNDERSTANDING`(+`degraded_evidence`) | REVIEW-04 E7 原写法（未来版） |
| F3 | 冲突 + ≥1 条 meaningful==True RESOLVED | 6 | `PARTIAL_UNDERSTANDING`(+`conflict_pending`) | REVIEW-04 E11 原写法（未来版） |
| F4 | 输入 β：降级 + 1 条 meaningful==True RESOLVED | 6 | `PARTIAL_UNDERSTANDING` | REVIEW-04 §7 输入 β（未来版） |

> **当前不可达根因**：`excerpt_verified` 在 `usit/` 与 `tests/` 中均不存在（I-13）→ `meaningful_count` 恒 0 → 分支 6 永不触发。

---

## 8. 证据计数边界与恒等式（对应 §2.4）

| 计数 | 定义 | 互斥/包含关系 |
|---|---|---|
| `N_raw` | 原始记录数（含重复） | — |
| `N_no_evidence` | fallback 前 `evidence` 为空/缺失的记录数 | 与 `N_evidence_candidate` 互补 |
| `N_evidence_candidate` | 有非空 evidence 候选 = `N_raw − N_no_evidence` | ⊇ `N_construct_ok` |
| `N_construct_ok` | 成功构造 `EvidenceReference` 数 | ⊆ `N_evidence_candidate` |
| `N_construct_fail` | 构造抛错数（记 `construction_failures[]`） | ⊆ `N_evidence_candidate` |

**恒等式**：
- (A) `N_raw = N_evidence_candidate + N_no_evidence`
- (B) `N_evidence_candidate = N_construct_ok + N_construct_fail`
- (C) `N_raw = N_construct_ok + N_construct_fail + N_no_evidence`

> 实践：parser-contract fallback（I-8）保证候选记录总能被 `_build_evidence` 构造 → 通常 `N_construct_fail=0`，`N_construct_ok = N_evidence_candidate`。但若 record 缺必填字段致构造抛错，须进 `N_construct_fail` 并报告，**不得静默丢弃**。

---

## 9. 记录键稳定范围与重复/投影统计复核（对应 §2.5/§2.6）

### 9.1 记录键稳定范围
- 关联键 `record_key = rec:{projection_id}:{first_index}`：**本次扫描内有效**。
- `projection_id` 为内容哈希（I-5），**内容一致→一致**，但**不保证跨扫描稳定身份**（代码改动会改变内容；逻辑实体可能换内容）。
- **跨扫描关联：UNRESOLVED**（本设计不宣称具备）。

### 9.2 分项统计与恒等式

| 项 | 定义 | 关系 |
|---|---|---|
| `raw_records` | `= N_raw` | — |
| `unique_projection_ids_raw` | raw 记录去重后的不同 `projection_id` 数 | — |
| `duplicate_records` | `= N_raw − unique_projection_ids_raw` | 共享 id 的非首现记录 |
| `conflict_items` | `= len(conflicts)` | — |
| `projected_items` | `= N_raw + conflict_items` | I-7；重复不扣除 |

**恒等式**：`projected_items = raw_records + conflict_items`（恒真）；`duplicate_records = raw_records − unique_projection_ids_raw`。

**适用条件**：`projected_items = raw_records + conflict_items` 由 `project_records`（items=records+conflicts）保证，**不**表示去重；重复记录各自保留为一条 item，仅共享 `projection_id`。

### 9.3 无自指 / 无环 / 不丢 验证（数值示例）
输入：`dup.py` 含 `R_a, R_a, R_b`（R_a 完全相同，R_b 不同），0 conflicts。
- `raw_records = 3`；`projection_ids = [pid_a, pid_a, pid_b]`（I-5/I-6 共享）。
- `record_keys = [rec:pid_a:0, rec:pid_a:1, rec:pid_b:2]`（**全保留，不丢**）。
- `duplicate_groups = {pid_a: [0, 1]}`；`duplicate_of`：`R_a@1 → first_index=0`，`R_a@0 → None`。
  - 自指检查：`1 → 0`（≠1）✓ 无自指；环检查：链止于 `0 → None` ✓ 无环。
- `unique_projection_ids_raw = 2`；`duplicate_records = 3 − 2 = 1`。
- `conflict_items = 0`；`projected_items = 3 + 0 = 3`（重复未扣）✓。

---

## 10. 一致性自证（同一组输入逐条推演）

取 **输入 α**（`app.py` 3 条）：`R1`(RESOLVED+真实summary,无摘录证明)、`R2`(RESOLVED+仅graph_node_id)、`R3`(UNRESOLVED)。

1. **构造/字段**：`_build_evidence` 对 R1/R2 成功（I-3）；R3 因 NON_SUCCESS 仍可有 evidence（假设无）→ 本例 R3 视为 N_no_evidence。`N_raw=3`，`N_evidence_candidate=2`（R1,R2），`N_no_evidence=1`（R3），`N_construct_ok=2`，`N_construct_fail=0`。恒等式 (C)：`3 = 2+0+1` ✓。
2. **evidence_meaningful**（R3 规则）：R1 有 summary 无 `excerpt_verified` → `UNKNOWN`；R2 仅 `graph_node_id` → `False`；均 ≠ `True` → `meaningful_count=0`。
3. **文件结论（§5）**：非空、有记录、非全 NON_SUCCESS（R1/R2 RESOLVED）、非全空 → 越过序 2；无冲突；未降级；`meaningful_count=0` → 不进序 6；`resolved_ev_count=2≥1` → **`INSUFFICIENT_EVIDENCE`**（分支 7）✓ 与 C1/C2 一致，纠正了 REVIEW-04 把 R1 误标 `PARTIAL_UNDERSTANDING`。
4. **条目 complete（§4）**：三者均含 `summary_not_verified`（恒）；R1 加 `evidence_not_meaningful=UNKNOWN`，R2 加 `evidence_not_meaningful=False`，R3 加 `non_success_resolution`（且 R3 为 N_no_evidence，ev=None→`missing_or_invalid_evidence`）。每条独立 ✓ 不传染。
5. **统计（§8）**：`reference_exists/path_valid/line_valid` 按结构计；`summary_verification` VERIFIED=0；`static_inference`（I-7 投影层）=2（R1,R2 RESOLVED）→ 与"文件结论 INSUFFICIENT_EVIDENCE"不冲突（投影层计数来自 S1-A，结论来自 S1-C 加严）✓。
6. **重复/投影（§9）**：若 R1/R2 内容相同则 `duplicate_records=1`，`projected_items=3+0` ✓ 无自指/环/丢。

→ 字段定义、判定规则（§4/§5）、计数恒等式（§8）、重复统计（§9）、样例（§6）在同一输入下结论一致，无内部矛盾。

---

## 11. 未解决问题、BLOCKER 清单与状态

### 11.1 BLOCKER 清单（继续列为代码修改事项，不得自行接线/解除）
| ID | 描述 | 影响 | 处理要求 |
|---|---|---|---|
| BLOCKER-1 | 投影未接线（I-1） | S1-C 无 `projection_items` 输入 | 实施前须授权接线（独立后处理调用 `project_records()` 或改 `cli.py`）——代码修改，超出本 READ-ONLY 工单 |
| BLOCKER-2 | evidence `graph_node_id` fallback（I-8）致 `STATIC_INFERENCE` 过宽 | 无真实摘要也计推断 | S1-C 用 `evidence_meaningful` 加严计划完整性；不修改 S1-A |

### 11.2 未解决问题（Unresolved）
1. BLOCKER-1 采用"独立后处理"还是"改 cli.py 接线"需授权。
2. `analysis_degradation` 仍为死字段；linkage 透传未接入图谱/报告渲染。
3. 真实项目验证与性能：`UNKNOWN`（未实测，不冒充）。
4. **Android 端到端自主测试：未实现**，本修订未新增该能力。
5. `excerpt_verified` 摘要验证协议：当前不存在（I-13），属未来能力；启用前须单独设计溯源标记与校验流程，本设计仅预留 `evidence_meaningful` 判定入口。
6. 跨扫描稳定身份：未解决（§2.5/§9.1）。

### 11.3 PASS/FAIL/未测试状态（设计验收，非实现验收）
| 项 | 状态 |
|---|---|
| 统一当前可达状态（E1/E7/输入β 重算，未来场景单列） | ✅ 设计已修订 |
| §4 决策表歧义消除（确定性布尔 + 8 边界唯一分支） | ✅ 设计已修订 |
| `complete` 门禁语义明确 + 防下游误解 | ✅ 设计已修订 |
| 证据计数边界 + 恒等式（§8） | ✅ 设计已修订 |
| 记录键稳定范围（扫描内；跨扫描未解决） | ✅ 设计已修订 |
| 重复/投影统计复核 + 恒等式 + 无自指/环/丢 | ✅ 设计已修订 |
| 一致性自证（≥12 当前可达 + 未来单列） | ✅ 设计已修订 |
| 源码契约证据（§0 I-1–I-13） | ✅ 代码实测坐实 |
| **源码已实现** | ❌ 否（S1-C 未实施） |
| **测试已执行（本次）** | ❌ 否（READ-ONLY，未运行） |
| 历史测试证据引用 | ⚠️ 仅引 REVIEW-02 §F / S1-B 报告，已标来源与范围，非本次执行 |

---

## 12. 固定门禁（原值保持，全程未变）
| 门禁 | 值 |
|---|---|
| `F1` | `NOT_READY_TO_CLOSE` |
| `P0.8` | `HOLD` |
| `F2` | `STOP` |
| `历史授权连续性` | `UNRESOLVED` |
| `安全信任链` | `BLOCKED / NOT_VERIFIED` |

---

## 13. 最终结论

1. **设计已修订（本次交付）**：REVIEW-05 在 REVIEW-04 基础上，基于 `excerpt_verified` 不存在的实测（I-13）统一了当前可达状态，并逐项修订：① 重算 E1/E7/输入β 与 §4、§7，纠正 REVIEW-04 把 `PARTIAL_UNDERSTANDING` 误用于当前不可达场景的矛盾，未来假设单独标注（§1/§6/§7）；② §4 每个分支改为确定性布尔伪代码，澄清"全部 NON_SUCCESS / evidence 全空 / RESOLVED 但证据不充分"的分层优先级，并用 ≥8 边界输入验证唯一分支（§5）；③ 明确 `complete` 是**当前阶段门禁状态**、非质量指标，强制要求下游读取 `incomplete_reasons` 防误解（§2.3/§4）；④ 补齐证据计数边界并给出恒等式 `N_raw = N_construct_ok + N_construct_fail + N_no_evidence`（§2.4/§8）；⑤ 明确 `rec:{projection_id}:{first_index}` 仅扫描内有效、跨扫描身份未解决（§2.5/§9.1）；⑥ 复核重复/投影统计，给出 `projected_items = raw_records + conflict_items` 及无自指/环/丢验证（§2.6/§9）；⑦ 重做一致性自证，提供 17 个当前可达样例 + 未来假设单列（§6/§7/§10）。
2. **源码已实现：否**。S1-C 未进入实施；`understanding_projection` 仍孤立未接线（BLOCKER-1，I-1）。
3. **测试已执行：否**。本工单严格 READ-ONLY，未运行任何实现或测试；所有决策表、统计、示例为**设计规格**，待授权实施后验证。
4. **硬约束全部维持**：`verified_fact=0`、`ExecutionRecord` 全 `NOT_RUN`、无虚构成功；`summary_verification=NOT_CHECKED` 恒为真、受约束条目 `complete=False`（门禁，未为求 True 放宽）；`degraded=False` 不代表已理解；不放宽信任门禁；不把 UNKNOWN/AMBIGUOUS/DYNAMIC/EXTERNAL/UNRESOLVED/CONFLICT 升级为成功。
5. **建议**：采纳 REVIEW-05 作为 S1-C 最终设计规格；实施前须**另行授权**处理 BLOCKER-1（接线）与 BLOCKER-2（完整性加严），且不得自行进入 S1-C 实现。本设计规格的"通过"**不等于**源码实现通过或测试通过（验收规则明示）。

---

**STOP**：仅提交设计修订，未修改任何文件。等待人工审查与独立授权；不得自行进入 S1-C 实施，不得解除 BLOCKER-1/2。
