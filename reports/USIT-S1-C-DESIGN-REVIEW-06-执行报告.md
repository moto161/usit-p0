# USIT-S1-C-DESIGN-REVIEW-06｜决策分支与证据计数边界修正（READ-ONLY）

- **工单编号**：USIT-S1-C-DESIGN-REVIEW-06
- **执行时间**：2026-10-19 23:51 (GMT+8)
- **工作区绝对路径**：`C:\Users\Administrator\WorkBuddy\2026-10-15-22-09-47\usit_p0`
- **执行模式**：**严格只读设计审查** —— 仅读取源码/测试/报告，新增/修订本设计报告；**禁止修改**任何源码/测试/配置/manifest/图谱/原 `test_plan.json`/其他既有产物；**禁止**接线、实现、构建、安装、登录、网络与 Android 测试。
- **修订基准**：`reports/USIT-S1-C-DESIGN-REVIEW-05-执行报告.md`（REVIEW-05），并继承 `USIT-S1-C-DESIGN-01` / `REVIEW-02` / `REVIEW-03` / `REVIEW-04`。
- **设计依据**：全部结论来自对当前工作区源码的 grep + 区间阅读（见 §0 实测证据），非历史转述或推断。

---

## 0. 实际检查文件与行号（Inspection Evidence）

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
| I-13 | `excerpt_verified` 是否存在 | `grep -rn "excerpt_verified" usit/ tests/` | `usit/*.py` 与 `tests/*.py` 中零命中；仅本报告/REVIEW-04/05 伪代码作未来预留 | 代码实测（grep） |
| **I-14** | **pre-fallback evidence 状态是否被保留** | 读 `parser_contract.py:100,121` | `:100` `evidence = item.get("evidence") or {}`；`:121` `"evidence": evidence or {"graph_node_id": item.get("id")}`。**原始 evidence 是否为空在适配后即被 `graph_node_id` 覆盖，适配输出（records）中无任何字段保留"原为空"这一事实** → `N_no_evidence`（fallback 前为空）**不可从适配输出还原** | 代码实测（本次新增关键发现） |
| **I-15** | **`_build_evidence` 是否抛错** | 读 `understanding_projection.py:122-126` | `if not isinstance(evidence, Mapping) or not evidence: return None` —— **返回 `None` 而非抛错**；故"无引用"归为 `N_evidence_none`，并非"构造失败抛错" | 代码实测（本次新增） |
| **I-16** | **`EvidenceReference` 构造抛错条件** | 读 `understanding_model.py:216-235` | `__post_init__` 在 `source_trusted/execution_verified=True`、`trust_level=TRUSTED`、`line<1`、字符串字段非 str 时 `raise ValueError`（:218-230）。但 `_build_evidence`（I-3/I-15）恒传安全值（collection_status=PRESENT、trust=NOT_VERIFIED、source_trusted=False、execution_verified=False、line 经 `_as_line` 规整）→ **经此路径构造不会抛错**；`N_construct_fail`（抛错）在当前管线 ≈ 0，属保留项 | 代码实测（本次新增） |

> **三种状态声明（强制区分）**：
> - **设计已修订**：✅ 本报告（REVIEW-06）完成。
> - **源码已实现**：❌ 否。S1-C 未实施；`understanding_projection` 仍仅孤立存在（I-1 BLOCKER）。
> - **测试已执行**：❌ 否。本工单 READ-ONLY，未运行任何实现或测试；下文统计口径/决策表/示例均为**设计规格**，待授权实施后验证。

---

## 1. 修订前后差异总览（REVIEW-05 → REVIEW-06）

| 修订点 | REVIEW-05（旧） | REVIEW-06（新） | 依据 |
|---|---|---|---|
| **C8 分支** | `DEGRADED_PARSE`（分支 4） | **`UNKNOWN_RESOLUTION`（分支 2）** | 按 §5 伪代码，`all_non_success` 先于 `file_degraded` 判定 → C8 命中分支 2 |
| **C3 / C9 统计** | `N_no_evidence+1` | `N_construct_ok+1`；`N_no_evidence` 标 **NOT_AVAILABLE** | I-14：pre-fallback 空不可还原；NON_SUCCESS ≠ 无证据 |
| **`N_no_evidence` 可用性** | 定义为"fallback 前为空"，恒等式含它 | 标注 **NOT_AVAILABLE**（I-14）；恒等式改用 `N_evidence_none` | I-14 / I-15 |
| **`all_evidence_empty` 谓词** | 视为当前可达谓词 | 当前管线正常路径恒 False（fallback 后全有证据 dict）；仅当 evidence 为非 Mapping 真值（如字符串）时才可能 True → 标为"罕见/未来钩子"，不再默认可达 | I-8 / I-14 / I-15 |
| **resolution ↔ evidence** | C3/C9 由 `NON_SUCCESS` 推 `N_no_evidence` | 双维度独立；禁止由 resolution 推导 evidence 缺失 | I-14 |
| **`summary_not_verified` 措辞** | "强制 complete=False 的**唯一根因**" | "**足以独立阻止** complete=True 的门禁原因"；`incomplete_reasons[]` 可同时含其他独立原因 | 工单 #5 |
| **空集合保护** | 仅 `no_records` 前置 | 显式说明 `all()` 空陷阱 + 零记录边界例 B1c | 工单 #2 |
| **构造失败例** | 仅定义 `N_construct_fail`，无示例 | 新增 **C18**：evidence 非 Mapping → `_build_evidence` 返回 None → `N_evidence_none` | I-15 |

---

## 2. 逐项修订（对应工单 7 项要求）

### 2.1 修正 C8 分支错误 + 逐行复核 C1–C18
- **C8 重推**：输入"降级 + 全部 NON_SUCCESS"。按 §5 伪代码：`empty?` 否 → `no_records?` 否 → `all_non_success?` **是** → **分支 2 → `UNKNOWN_RESOLUTION`**（降级被"无可理解记录"优先分支吞并，不落入分支 4）。REVIEW-05 误把 C8 写成分支 4 / `DEGRADED_PARSE`，本次纠正。
- **逐行复核 C1–C18（见 §6）**：同步修正 C3、C9 的统计（去掉 `N_no_evidence+1`），并补充分辨率/证据双维度列。

### 2.2 明确空集合保护
- 伪代码（§5）在判定任何 file 级聚合谓词（`all_non_success` / `all_evidence_empty` / `meaningful_count` / `resolved_ev_count`）**之前**，先返回 `empty`（分支 0）与 `no_records`（分支 1）。
- **`all()` 空陷阱显式规避**：Python 中 `all([])` 返回 `True`。若先算 `all_non_success` 而无 `no_records` 守卫，零记录文件会被误判为 `UNKNOWN_RESOLUTION`。本设计因 `no_records` 先行返回而免疫；并在 §6 新增边界 **B1c** 显式演示。
- 新增零记录/空集合边界例：B0（空文件）、B1a/B1b（零记录无/有符号）、B1c（零记录触发 `no_records` 先于 `all()` 守卫，证明空陷阱被规避）。

### 2.3 验证原始 evidence 状态的可获取性（I-14）
- `parser_contract.adapt_graph_v1`（:94-125）：`:100` 读取 `item.get("evidence") or {}`，`:121` 输出 `"evidence": evidence or {"graph_node_id": item.get("id")}`。**适配后所有记录的证据均为非空 dict（图节点兜底），且输出 records 中没有任何字段标明"原始是否为空"**。
- 因此 **`N_no_evidence`（fallback 前为空）在当前数据流中 NOT_AVAILABLE / 不可还原**——既无独立布尔标志，也无原始 evidence 留存。REVIEW-05 §8 恒等式 (C) `N_raw = N_construct_ok + N_construct_fail + N_no_evidence` **作废**（N_no_evidence 不可得）。
- **未来最小元数据设计（仅提案，不改代码）**：在 `adapt_graph_v1` 的 `:121` 之前捕获并随记录透传 `evidence_was_empty_before_adapt = not bool(item.get("evidence"))`；或调用方在适配前于图谱源直接计算。届时 `N_no_evidence = count(该标志为 True)`。当前不实现。

### 2.4 分离解析状态与证据状态（修正 C3/C9 等）
- **两个独立维度**：
  - **resolution 维度**：`resolution_status ∈ {RESOLVED, NON_SUCCESS(...)}`——解析是否给出确定结论。
  - **evidence 维度**：`evidence_post_fallback`（当前管线恒非空 dict）/ `evidence_pre_fallback_empty`（NOT_AVAILABLE，见 I-14）/ `evidence_meaningful`（当前仅 `False`/`UNKNOWN`）。
- **禁止反向推导**：`NON_SUCCESS` / `UNRESOLVED` **绝不**蕴含"无证据"。C3/C9 的 NON_SUCCESS 记录经 fallback 后仍有 `{"graph_node_id": id}`，仍能被 `_build_evidence` 构造引用（I-15）。故 C3/C9 统计为 `N_construct_ok+1`，不再记 `N_no_evidence`。
- 每个样例（§6）均显式给出 resolution 与 evidence 两个独立输入条件及各自统计影响。

### 2.5 修正完整性门禁措辞
- `summary_not_verified` 重新定义为：**"足以独立阻止 complete=True 的门禁原因"**（sufficient-to-block gate reason），**不是**"唯一根因"。
- `incomplete_reasons[]` **可同时包含多个独立原因**：例如某条目可同时含 `summary_not_verified`（门禁）+ `orphan_record`（结构性缺陷）+ `non_success_resolution`（解析缺陷）。`complete=False` 当且仅当 `incomplete_reasons` 非空；门禁原因只是其中最常见的一项，不得排他宣称。
- §4 伪代码注释与 §2.3 要点同步修订措辞。

### 2.6 重新执行规格自洽推演（≥12 当前可达例 + 未来单列）
- §6 提供 **18 个当前可达样例（C1–C18）**，覆盖：空文件、零记录、全部 NON_SUCCESS 且 evidence 有/无、混合 resolution、降级与冲突组合、构造无引用（C18）、重复记录。每个给出：resolution 维度 / evidence 维度 / 命中分支 / 文件结论 / 条目完整性 / 计数变化 / 依据。
- §7 单独列出**未来假设样例**（[FUTURE-ONLY]，依赖 `excerpt_verified`，不混入当前验收）。
- §10 用同一组输入逐条推演，证明字段/规则/统计/示例一致。

---

## 3. 统一字段 / schema 规格（继承 REVIEW-05，仅标注证据可用性）

### 3.1 `EvidenceReference`（真实模型，S1-C 不改写）

| 字段 | S1-C 取值 | 说明 |
|---|---|---|
| `summary` | 文本片段或 `None` | 仅持有，不验证；绝不填文件哈希 |
| `collection_status` | `PRESENT` | 片段在 artifact 存在；与 `trust_level` 正交 |
| `trust_level` | `NOT_VERIFIED` | 构造强制 |
| `content_present` | `True` | 扫描时曾读取，不代表当前仍有效 |
| `digest_expected` | `None` | 禁止填文件级 sha256 |
| `digest_match` | `None` | 禁止重读，不得计算 |
| `source_id/relative_path/line/locator` | 来自 record | 结构字段，供五状态校验 |

> `EvidenceReference` 无 `summary_verification` 字段（I-2）；该值作为链接层注解存在（§3.3）。

### 3.2 `understanding_linkage.json`（`usit-understanding-linkage-v1`）
同 REVIEW-05 §3.2；`evidence_refs[]` 每条含：`summary_verification="NOT_CHECKED"`（链接层注解）、`evidence_has_semantic_keys`、`evidence_meaningful`（当前仅 `"False"`/`"UNKNOWN"`）、`scan_file_sha256`。**新增（提案，未实现）**：未来可加 `evidence_was_empty_before_adapt` 以支持 `N_no_evidence`。

### 3.3 `understanding_test_plan_candidate.json`（`usit-understanding-testplan-candidate-v1`）
同 REVIEW-05 §3.3；`items[].complete` 本阶段恒 `False`（门禁，见 §2.5/§4）；`items[].evidence_meaningful` 当前仅 `"False"`/`"UNKNOWN"`。

---

## 4. 完整性伪代码（item_context / file_context 分离 + 门禁措辞修订）

```python
def item_complete(spec, ev, link, ctx) -> tuple[bool, list[str]]:
    reasons = list(spec.incomplete_reasons)            # (1) TestSpecification 派生缺失 (I-11)
    if ev is None or not (ev.reference_exists and ev.path_valid and ev.line_valid_structural):
        reasons.append("missing_or_invalid_evidence")  # (2) item_context
    if link.evidence_meaningful != "True":             # (3) "False"/"UNKNOWN" 均非 True
        reasons.append("evidence_not_meaningful")      #     当前恒命中（无 True）
    if link.summary_verification != "VERIFIED":        # (4) 门禁：当前恒 NOT_CHECKED
        reasons.append("summary_not_verified")         #     ★足以独立阻止 complete=True 的门禁原因
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
    return (len(reasons) == 0), dedupe(reasons)        # 任一原因（含门禁）即 False；原因可并存

# 文件级：仅汇总，不反向改写条目
file.complete_status = bool(file_items) and all(item.complete for item in file_items)
```

**要点（门禁语义，修订措辞）**：
- `(4) summary_not_verified` **足以独立阻止** `complete=True`，但 `incomplete_reasons[]` 可同时含 (2)(3)(5)(6)(7) 等**其他独立原因**——它不是"唯一根因"。
- 下游必须读 `incomplete_reasons` 区分"仅未验证"(summary_not_verified) 与"结构性/解析缺陷"(其余)，不得把 `complete=False` 一律读作质量差。
- 各 item 级原因逐条计算互不传染；file_context 全局一致施加（非株连）。

---

## 5. 文件结论决策表（确定性布尔伪代码 + 空集合保护 + 8+ 边界验证）

```python
def decide_file_conclusion(f) -> tuple[str, list[str]]:
    # —— 空集合保护：先判 empty / no_records，再算任何聚合谓词 ——
    empty        = (f.size_bytes == 0) or (not f.has_content)
    no_records   = (f.n_records == 0)
    if empty:
        return ("NO_SEMANTIC_CONTENT", ["empty_or_no_semantic_content"])           # 分支 0
    if no_records:                                                                 # 守卫 all() 空陷阱
        reason = "no_symbols" if not f.has_symbols else "symbols_no_records"
        return ("NO_RECORDS", [reason])                                           # 分支 1

    # —— 以下聚合谓词仅在 records 非空后计算（all() 不会再遇空序列）——
    all_non_success    = all(r.resolution_status in NON_SUCCESS for r in f.records)
    all_evidence_empty = all(not has_evidence_candidate(r) for r in f.records)  # 当前罕见(见 I-14/I-15)
    meaningful_count   = sum(1 for r in f.records
                             if r.resolution_status == "RESOLVED" and r.evidence_meaningful == "True")  # 当前恒 0
    resolved_ev_count  = sum(1 for r in f.records
                             if r.resolution_status == "RESOLVED" and has_evidence_candidate(r))

    if all_non_success or all_evidence_empty:
        return ("UNKNOWN_RESOLUTION", ["all_records_non_success_or_no_evidence"]) # 分支 2
    # 此后：≥1 条 RESOLVED 且（至少部分）有候选证据
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
    return ("UNKNOWN", ["unknown"])                                               # 分支 8（防御）
```

> `has_evidence_candidate(r)` = `isinstance(r.evidence, Mapping) and bool(r.evidence)`。当前管线经 fallback（I-8）后通常为非空 dict → `all_evidence_empty` 正常路径恒 False；仅当 evidence 为非 Mapping 真值（如字符串，fallback 的 `or` 不替换真值）时才可能 True（见 C18）。

### 边界输入 → 唯一分支验证

| 边界 | 输入条件 | 命中分支 | 文件结论 |
|---|---|---|---|
| B0 | `size_bytes==0` | 0 | `NO_SEMANTIC_CONTENT` |
| B1a | 0 记录且无符号 | 1 | `NO_RECORDS`(no_symbols) |
| B1b | 0 记录且有符号无记录 | 1 | `NO_RECORDS`(symbols_no_records) |
| **B1c** | **0 记录：验证 `no_records` 先于 `all()` 守卫** | 1 | `NO_RECORDS`（证明空陷阱被规避，不会误判 `UNKNOWN_RESOLUTION`） |
| B2a | 全部 NON_SUCCESS | 2 | `UNKNOWN_RESOLUTION` |
| B2b | 全部 RESOLVED 但 evidence 全为非 Mapping（all_evidence_empty） | 2 | `UNKNOWN_RESOLUTION`(all_evidence_empty) |
| B3 | 冲突 + ≥1 RESOLVED+候选，无 True | 3 | `CONFLICT_ONLY` |
| B4 | 文件降级 + ≥1 RESOLVED+候选，无冲突，无 True | 4 | `DEGRADED_PARSE` |
| B7 | ≥1 RESOLVED+候选，无冲突、未降级，无 True | 7 | `INSUFFICIENT_EVIDENCE` |
| B6 [FUTURE] | ≥1 RESOLVED+候选且 evidence_meaningful=="True" | 6 | `PARTIAL_UNDERSTANDING`（仅未来） |
| B8 [DEF] | 越过序 2 却 resolved_ev_count==0 | 8 | `UNKNOWN`（防御，当前不可达） |

> 10 个边界覆盖枚举中除 `PARTIAL_UNDERSTANDING` 外的全部**当前可达**值，且每个边界命中唯一分支。

---

## 6. 至少 12 个当前可达样例（双维度；逐条：分支 / 结论 / 完整 / 计数 / 依据）

> 所有条目 `complete=False`（门禁，见 §2.5/§4）；下表 `incomplete_reasons` **省略恒定项** `summary_not_verified`（恒有）与（有候选证据时）`evidence_not_meaningful`，仅列**额外**成因。两维度独立给出：`resolution` ∈ {RESOLVED, NON_SUCCESS}；`evidence` 当前 = `post_fallback:非空dict`（C18 例外）、`pre_fallback_empty:NOT_AVAILABLE`。

| # | resolution 维度 | evidence 维度 | 分支 | `file_conclusion` | 条目 `complete` | 额外 `incomplete_reasons` | 计数变化 | 依据 |
|---|---|---|---|---|---|---|---|---|
| C1 | RESOLVED | 真实 summary（非空 dict） | 7 | `INSUFFICIENT_EVIDENCE` | False | `evidence_not_meaningful=UNKNOWN` | N_raw+1, N_construct_ok+1, resolved_ev_count+1 | I-3/I-15 |
| C2 | RESOLVED | 仅 `graph_node_id` | 7 | `INSUFFICIENT_EVIDENCE` | False | `evidence_not_meaningful=False` | N_raw+1, N_construct_ok+1 | I-8 |
| C3 | NON_SUCCESS(UNRESOLVED) | post_fallback 非空（graph_node_id） | 2 | `UNKNOWN_RESOLUTION` | False | `non_success_resolution` | N_raw+1, N_construct_ok+1 | I-14/I-15（**禁**由 NON_SUCCESS 推 N_no_evidence） |
| C4 | —（无记录） | — | 0 | `NO_SEMANTIC_CONTENT` | N/A | — | 文件级无 records | §5 B0 |
| C5 | —（0 记录） | — | 1 | `NO_RECORDS`(no_symbols) | N/A | — | N_raw=0 | §5 B1a |
| C6 | —（0 记录有符号） | — | 1 | `NO_RECORDS`(symbols_no_records) | N/A | — | N_raw=0 | §5 B1b |
| C7 | RESOLVED（无 True） | 非空 dict | 4 | `DEGRADED_PARSE` | False | `degraded_evidence`[file_context] | N_raw+1, N_construct_ok+1, file_degraded=True | §1 重算 |
| C8 | **全部 NON_SUCCESS** | post_fallback 非空 | **2** | **`UNKNOWN_RESOLUTION`** | False | `all_records_non_success_or_no_evidence`（file_degraded 被吞并） | N_raw+1, N_construct_ok+1 | **§1 修正（原误判分支4）** |
| C9 | 全部 NON_SUCCESS | post_fallback 非空 | 2 | `UNKNOWN_RESOLUTION` | False | `non_success_resolution` | N_raw+1, N_construct_ok+1 | I-14（**禁** N_no_evidence+1） |
| C10 | ≥1 RESOLVED + ≥1 NON_SUCCESS（有冲突） | 非空 dict | 3 | `CONFLICT_ONLY` | False | `conflict_pending`[file_context] | N_raw+1, N_construct_ok+1, has_conflict=True | §5 B3 |
| C11 | RESOLVED + orphan（path∉manifest） | 非空 dict | 7 | `INSUFFICIENT_EVIDENCE` | False | `orphan_record`(+`missing_or_invalid_evidence`) | N_raw+1, N_construct_ok+1 | I-1 语义 |
| C12 | RESOLVED + diagnostics_ref 越界 | 非空 dict | 7 | `INSUFFICIENT_EVIDENCE` | False | `invalid_diagnostic_reference` | N_raw+1, N_construct_ok+1 | I-10 |
| C13 | RESOLVED + relative_path∉manifest | 非空 dict | 7 | `INSUFFICIENT_EVIDENCE` | False | `missing_or_invalid_evidence` | N_raw+1, N_construct_ok+1 | I-3 |
| C14 | RESOLVED + line 非正整数 | 非空 dict | 7 | `INSUFFICIENT_EVIDENCE` | False | `missing_or_invalid_evidence`(line 仅结构) | N_raw+1, N_construct_ok+1 | I-11 |
| C15 | RESOLVED + TestSpec 缺必填 | 非空 dict | 7 | `INSUFFICIENT_EVIDENCE` | False | spec 派生缺失字段名 | N_raw+1, N_construct_ok+1 | I-11 |
| C16 | RESOLVED ×2 完全相同（重复） | 非空 dict | 7 | `INSUFFICIENT_EVIDENCE` | False | 同对应单条 | N_raw+2, duplicate_records+1 | §9 |
| C17 | 混合：NON_SUCCESS + RESOLVED | 非空 dict | 7 | `INSUFFICIENT_EVIDENCE` | False（按条各自） | 各条按自身（non_success / evidence_not_meaningful） | N_raw+2, N_construct_ok+2 | §5 序2 不命中 |
| C18 | RESOLVED + evidence 非 Mapping（如字符串） | **非 Mapping → `_build_evidence` 返回 None** | 7（同文件另有正常记录时）| `INSUFFICIENT_EVIDENCE` | False | `missing_or_invalid_evidence`（ev=None） | N_raw+1, **N_evidence_none+1**（非 N_construct_fail） | I-15（构造返回 None，非抛错） |

> 共 **18 个当前可达样例（C1–C18）**，全部经 §5 伪代码确定性推导，结论自洽。

**C18 补充说明（构造无引用 vs 构造失败）**：
- 单记录文件时，C18 的 RESOLVED+非 Mapping evidence → `has_evidence_candidate=False` → `all_evidence_empty=True` → **分支 2 `UNKNOWN_RESOLUTION`**（即 B2b 的实例）。
- 多记录文件（C18 表内约定：另有 1 条正常 RESOLVED）时 → `resolved_ev_count≥1` → **分支 7 `INSUFFICIENT_EVIDENCE`**，该坏记录贡献 `N_evidence_none+1` 且其条目 `missing_or_invalid_evidence`。
- 二者均表明：`_build_evidence` 对坏 evidence **返回 None（I-15）**，归 `N_evidence_none`；**不是** `N_construct_fail`（抛错，I-16 当前 ≈ 0）。

---

## 7. 未来假设样例（[FUTURE-ONLY]，不混入当前验收通过项）

> 以下条件**当前不可达**（依赖 `excerpt_verified` 协议，I-13）。仅作前瞻设计。

| # | 未来场景 | 分支 | 文件结论 | 说明 |
|---|---|---|---|---|
| F1 | RESOLVED + `evidence_meaningful=="True"`（需 `excerpt_verified`） | 6 | `PARTIAL_UNDERSTANDING` | REVIEW-04 E1 原写法正确归属 |
| F2 | 降级 + ≥1 条 meaningful==True RESOLVED | 6 | `PARTIAL_UNDERSTANDING`(+`degraded_evidence`) | REVIEW-04 E7 未来版 |
| F3 | 冲突 + ≥1 条 meaningful==True RESOLVED | 6 | `PARTIAL_UNDERSTANDING`(+`conflict_pending`) | REVIEW-04 E11 未来版 |
| F4 | 输入 β：降级 + 1 条 meaningful==True RESOLVED | 6 | `PARTIAL_UNDERSTANDING` | REVIEW-04 §7 输入 β 未来版 |

> **当前不可达根因**：`excerpt_verified` 在 `usit/` 与 `tests/` 中均不存在（I-13）→ `meaningful_count` 恒 0 → 分支 6 永不触发。

---

## 8. 证据计数边界与恒等式（修订：N_no_evidence → NOT_AVAILABLE）

| 计数 | 定义 | 当前可得性 | 互斥/包含关系 |
|---|---|---|---|
| `N_raw` | 原始记录数（含重复）= len(适配后 records) | ✅ 可得（1:1 映射） | — |
| `N_construct_ok` | `_build_evidence` 返回有效 `EvidenceReference` 数 | ✅ 可得 | ⊆ 有候选 |
| `N_evidence_none` | `_build_evidence` 返回 `None` 数（非 Mapping/空 evidence 后） | ✅ 可得（I-15） | ⊆ 有候选 |
| `N_construct_fail` | 构造抛错数（I-16） | ✅ 可得（当前 ≈ 0，保留项） | ⊆ 有候选 |
| `N_no_evidence` | **fallback 前 evidence 为空** | ❌ **NOT_AVAILABLE（I-14）** | 不可从适配输出还原 |

**修订后恒等式（当前管线）**：
- (A) `N_raw = N_construct_ok + N_evidence_none + N_construct_fail`
- (B) `N_construct_ok + N_evidence_none + N_construct_fail` 覆盖全部原始记录（无遗漏、无重叠）
- (C) `N_no_evidence` **不进入恒等式**（I-14：不可还原）；若未来按 §2.3 提案透传 `evidence_was_empty_before_adapt`，则可补 `N_no_evidence = count(标志为 True)`，且通常 `N_no_evidence ⊆ N_evidence_none`（但并非等同，因 `N_evidence_none` 还含非 Mapping 真值证据）。

> **REVIEW-05 恒等式 (C) 作废说明**：REVIEW-05 写 `N_raw = N_construct_ok + N_construct_fail + N_no_evidence`，但 `N_no_evidence` 基于"fallback 前为空"且当前不可得（I-14），且漏计 `N_evidence_none`（I-15）。REVIEW-06 以 (A)(B) 取代。

---

## 9. 记录键稳定范围与重复/投影统计复核（继承 REVIEW-05，无变更）

### 9.1 记录键稳定范围
- 关联键 `record_key = rec:{projection_id}:{first_index}`：**本次扫描内有效**。
- **跨扫描关联：UNRESOLVED**（不宣称具备，见 REVIEW-05 §2.5）。

### 9.2 分项统计与恒等式

| 项 | 定义 | 关系 |
|---|---|---|
| `raw_records` | `= N_raw` | — |
| `unique_projection_ids_raw` | raw 去重后的不同 `projection_id` 数 | — |
| `duplicate_records` | `= N_raw − unique_projection_ids_raw` | 共享 id 的非首现记录 |
| `conflict_items` | `= len(conflicts)` | — |
| `projected_items` | `= N_raw + conflict_items` | I-7；重复不扣除 |

**恒等式**：`projected_items = raw_records + conflict_items`（恒真）；`duplicate_records = raw_records − unique_projection_ids_raw`。

### 9.3 无自指 / 无环 / 不丢 验证（数值示例）
输入：`dup.py` 含 `R_a, R_a, R_b`（R_a 完全相同），0 conflicts。
- `raw_records = 3`；`projection_ids = [pid_a, pid_a, pid_b]`（I-5/I-6 共享）。
- `record_keys = [rec:pid_a:0, rec:pid_a:1, rec:pid_b:2]`（**全保留，不丢**）。
- `duplicate_groups = {pid_a: [0, 1]}`；`duplicate_of`：`R_a@1 → first_index=0`（≠1，无自指）；链止于 `0 → None`（无环）。
- `unique_projection_ids_raw = 2`；`duplicate_records = 3 − 2 = 1`；`projected_items = 3 + 0 = 3`（重复未扣）✓。

---

## 10. 一致性自证（同一组输入逐条推演）

取 **输入 α**（`app.py` 3 条）：`R1`(RESOLVED+真实summary)、`R2`(RESOLVED+仅graph_node_id)、`R3`(UNRESOLVED)。

1. **构造/字段（I-3/I-15）**：`_build_evidence` 对 R1/R2/R3 均返回有效引用（post-fallback 均有非空 dict：R1 有 summary，R2/R3 为 graph_node_id）→ `N_raw=3`，`N_construct_ok=3`，`N_evidence_none=0`，`N_construct_fail=0`，`N_no_evidence=NOT_AVAILABLE`。恒等式 (A)：`3 = 3+0+0` ✓。
2. **evidence_meaningful**：R1 有 summary 无 `excerpt_verified` → `UNKNOWN`；R2/R3 仅 `graph_node_id` → `False`；均 ≠ `True` → `meaningful_count=0`。
3. **文件结论（§5）**：非空、有记录（非 no_records，避开空陷阱）、非全 NON_SUCCESS（R1/R2 RESOLVED）→ 越过分支 2；无冲突；未降级；`meaningful_count=0` → 不进分支 6；`resolved_ev_count=2≥1` → **`INSUFFICIENT_EVIDENCE`**（分支 7）✓ 与 C1/C2 一致。
4. **条目 complete（§4）**：三者均含 `summary_not_verified`（门禁，足以独立阻止）；R1 另加 `evidence_not_meaningful=UNKNOWN`，R2/R3 加 `evidence_not_meaningful=False`，R3 加 `non_success_resolution`（resolution 维度独立，不推 evidence 缺失）。每条独立 ✓ 不传染；`incomplete_reasons[]` 可含多个独立原因 ✓。
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
5. `excerpt_verified` 摘要验证协议：当前不存在（I-13），属未来能力。
6. **`N_no_evidence`（pre-fallback 空）当前 NOT_AVAILABLE（I-14）**；未来须按 §2.3 提案透传 `evidence_was_empty_before_adapt` 方可计数——本设计仅提案，未改代码。
7. `N_construct_fail`（构造抛错）当前 ≈ 0（I-16）：`_build_evidence` 对坏 evidence 返回 None（I-15）而非抛错；若未来 S1-C 直接构造 `EvidenceReference` 并传入非法参数，才可能触发，须另行设计错误处理。
8. 跨扫描稳定身份：未解决（§9.1）。

### 11.3 PASS/FAIL/未测试状态（设计验收，非实现验收）
| 项 | 状态 |
|---|---|
| C8 分支错误修正（按 §5 重推为 UNKNOWN_RESOLUTION） | ✅ 设计已修订 |
| C1–C18 逐行复核（双维度、统计修正） | ✅ 设计已修订 |
| 空集合保护（`no_records` 先于 `all()`，B1c 显式） | ✅ 设计已修订 |
| `N_no_evidence` 可用性判定（NOT_AVAILABLE，I-14）+ 未来元数据提案 | ✅ 设计已修订 |
| resolution / evidence 双维度分离（禁反向推导） | ✅ 设计已修订 |
| `summary_not_verified` 门禁措辞（足以独立阻止，可并存其他原因） | ✅ 设计已修订 |
| 一致性自证（≥12 当前可达 + 未来单列） | ✅ 设计已修订 |
| 源码契约证据（§0 I-1–I-16） | ✅ 代码实测坐实 |
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

1. **设计已修订（本次交付）**：REVIEW-06 在 REVIEW-05 基础上，基于 `parser_contract` 适配过程（I-14/I-15/I-16）的实测，逐项修正：① **C8 分支纠错**——"降级 + 全部 NON_SUCCESS"按 §5 伪代码命中分支 2（`UNKNOWN_RESOLUTION`），纠正 REVIEW-05 误判的分支 4；并逐行复核 C1–C18，补充分辨率/证据双维度列（§1/§6）；② **空集合保护**——显式 `no_records` 先于 `all()` 守卫，新增 B1c 边界证明空陷阱被规避（§2.2/§5）；③ **`N_no_evidence` 可用性**——确认 pre-fallback 空状态经 fallback 被覆盖且不可还原（I-14），标 NOT_AVAILABLE 并作废 REVIEW-05 恒等式，给出修订恒等式 `N_raw = N_construct_ok + N_evidence_none + N_construct_fail` 与未来最小元数据提案（§2.3/§8）；④ **resolution/evidence 双维度分离**——禁止由 `NON_SUCCESS` 推导无证据，修正 C3/C9（§2.4/§6）；⑤ **`summary_not_verified` 门禁措辞**——改为"足以独立阻止 complete=True 的门禁原因"，明确 `incomplete_reasons[]` 可含多个独立原因（§2.5/§4）；⑥ **重新自洽推演**——18 个当前可达样例 + 未来假设单列（§6/§7/§10）。
2. **源码已实现：否**。S1-C 未进入实施；`understanding_projection` 仍孤立未接线（BLOCKER-1，I-1）。
3. **测试已执行：否**。本工单严格 READ-ONLY，未运行任何实现或测试；所有决策表、统计、示例为**设计规格**，待授权实施后验证。
4. **硬约束全部维持**：`verified_fact=0`、`ExecutionRecord` 全 `NOT_RUN`、无虚构成功；`summary_verification=NOT_CHECKED` 恒为真、受约束条目 `complete=False`（门禁，未为求 True 放宽）；`degraded=False` 不代表已理解；不放宽信任门禁；不把 UNKNOWN/AMBIGUOUS/DYNAMIC/EXTERNAL/UNRESOLVED/CONFLICT 升级为成功。
5. **建议**：采纳 REVIEW-06 作为 S1-C 最新设计规格；实施前须**另行授权**处理 BLOCKER-1（接线）与 BLOCKER-2（完整性加严），且不得自行进入 S1-C 实现。本设计规格的"通过"**不等于**源码实现通过或测试通过（验收规则明示）。

---

**STOP**：仅提交设计修订，未修改任何文件。等待人工审查与独立授权；不得自行进入 S1-C 实施，不得修改 `understanding_projection.py` 或 `cli.py`，不得解除 BLOCKER-1/2。
