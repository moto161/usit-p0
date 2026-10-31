# USIT-S1-C-DESIGN-REVIEW-08｜分支语义、逐记录计数与样例一致性复核（READ-ONLY）

- **工单编号**：USIT-S1-C-DESIGN-REVIEW-08
- **执行时间**：2026-10-20 08:42 (GMT+8)
- **工作区绝对路径**：`C:\Users\Administrator\WorkBuddy\2026-10-15-22-09-47\usit_p0`
- **执行模式**：**严格只读设计审查** —— 仅读取源码/测试/报告，新增本设计报告；**禁止修改**任何源码/测试/配置/manifest/图谱/原 `test_plan.json`/其他既有产物；**禁止**接线、实现、构建、安装、登录、网络与 Android 测试。
- **修订基准**：`reports/USIT-S1-C-DESIGN-REVIEW-07-执行报告.md`（REVIEW-07），并继承 `REVIEW-04` ~ `REVIEW-07` 的有效约束。
- **设计依据**：全部结论来自对本工作区源码的 grep + 区间阅读（见 §0 实测证据），非历史转述或推断。本工单为独立复核对 REVIEW-07 的 R1–R6 修正。

---

## 0. 实际执行的只读检查命令 / 读取范围

> 本工单**未执行任何写操作、构建、测试或网络访问**。以下为只读检查清单：

| # | 检查动作 | 范围 | 用途 |
|---|---|---|---|
| R-C1 | `grep -rn "excerpt_verified" usit/ tests/` | 全仓库（含 reports） | 确认 `excerpt_verified` 是否在代码中实现（I'-13） |
| R-C2 | `grep -rn "reference_exists\|path_valid\|line_valid_structural" usit/` | `usit/` | 确认三字段是否为 `EvidenceReference` 属性（I'-2） |
| R-C3 | `grep -rn "understanding_projection\|project_records\|project_record\b" usit/` | `usit/` | 确认投影接线状态 / BLOCKER-1（I'-1） |
| R-C4 | `grep -n "NON_SUCCESS_STATES\|NON_SUCCESS" usit/schema_adapter.py` | `schema_adapter.py` | 确认 `NON_SUCCESS` 集合成员（I'-19） |
| R-C5 | `Read` `understanding_projection.py`（全 372 行） | 全文 | `_stable_id`/`_classify`/`_build_evidence`/`project_records`（I'-3~I'-7, I'-15, I'-16） |
| R-C6 | `Read` `parser_contract.py`（全 130 行） | 全文 | `validate_record`/`adapt_graph_v1` 语义（I'-8, I'-9, I'-14, I'-17） |
| R-C7 | `Read` `understanding_model.py`（全 633 行） | 全文 | `EvidenceReference` 字段/`__post_init__`/`ExecutionRecord`/`TestSpecification.complete`（I'-2, I'-11, I'-12） |
| R-C8 | `Read` `cli.py`（全 151 行） | 全文 | 确认 `understanding_projection` 未 import（I'-1 坐实） |
| R-C9 | `Read` `scanner.py:390-453, 1065-1112` | 区间 | `_degradation_metadata`/sha256/manifest/test_plan/`validate_record` 非破坏性（I'-10, I'-18） |
| R-C10 | `Read` `reports/USIT-S1-C-DESIGN-REVIEW-07-执行报告.md`（全 387 行） | 全文 | 基准设计规格 |

### §0.1 源码实测事实（复核对 REVIEW-07 的 I-1~I-18 重新锚定为 I'-1~I'-19）

| # | 事实 | 定位依据 | 可信 |
|---|---|---|---|
| I'-1 | 投影未接线：BLOCKER-1 | `grep` 仅命中 `understanding_projection.py` 自身；`cli.py` 全文中无 `import understanding_projection`（R-C3/R-C8） | 代码实测 |
| I'-2 | `EvidenceReference` 无 `reference_exists/path_valid/line_valid_structural` | `understanding_model.py:197-214` 字段表；`grep` 全 `usit/` 零命中（R-C2） | 代码实测 |
| I'-3 | `_build_evidence` 填 `summary`/`collection_status=PRESENT`，不传 `digest_*` | `understanding_projection.py:122-149` | 代码实测 |
| I'-4 | `_classify`：NON_SUCCESS 或非 Mapping/空 evidence → `UNKNOWN`；RESOLVED+非空 Mapping → `STATIC_INFERENCE` | `understanding_projection.py:100-119` | 代码实测 |
| I'-5 | `_stable_id` 纯内容派生 sha256、无 index | `understanding_projection.py:74-80` | 代码实测 |
| I'-6 | `project_record` 同 record 共享 `projection_id` | `understanding_projection.py:195-205` | 代码实测 |
| I'-7 | `project_records`：`items = records + conflicts`；`verified_fact=0` | `understanding_projection.py:348-371` | 代码实测 |
| I'-8 | `adapt_graph_v1`：`evidence = item.get("evidence") or {}`（:100）；`evidence or {"graph_node_id": id}`（:121）仅替换假值 | `parser_contract.py:94-125` | 代码实测 |
| I'-9 | `validate_record` 返回违规列表、不修复不删除、要求 `evidence` 为 dict 且非空 | `parser_contract.py:67-91` | 代码实测 |
| I'-10 | scanner：`row['sha256']`/`analysis_degradation` 透传；test_plan 静态 3 项；manifest 含 sha256 | `scanner.py:400-453, 462, 495, 1098-1103, 1108` | 代码实测 |
| I'-11 | `TestSpecification.complete = not incomplete_reasons`；必填缺失进 `incomplete_reasons`（真实字段派生） | `understanding_model.py:390-415` | 代码实测 |
| I'-12 | 硬阻断：拒 `VERIFIED_FACT`；`ExecutionRecord` 仅 `NOT_RUN` | `understanding_model.py:278-279, 323-337` | 代码实测 |
| I'-13 | `excerpt_verified` 在 `usit/` 与 `tests/` 中**零命中**（仅出现在 reports 文本） | `grep -rn "excerpt_verified" usit/ tests/`（R-C1，零命中） | 代码实测（grep） |
| I'-14 | pre-fallback 空证据状态未被保留（被 `graph_node_id` 覆盖）→ `N_no_evidence` **NOT_AVAILABLE** | `parser_contract.py:100, 121` | 代码实测 |
| I'-15 | `_build_evidence` 对非 Mapping/空 evidence **返回 None**（非抛错） | `understanding_projection.py:122-126` | 代码实测 |
| I'-16 | `_build_evidence` 对候选（非空 Mapping）恒传安全值 → `__post_init__` 不 raise → `N_construct_fail ≈ 0`（保留项） | `understanding_projection.py:134-149` + `understanding_model.py:216-235` | 代码实测 |
| I'-17 | `adapt_graph_v1` **不调用** `validate_record`；`evidence or {...}` 仅替换假值 → 非 Mapping 真值 evidence（如字符串）原样放行 | `parser_contract.py:94-125` | 代码实测 |
| I'-18 | `validate_record` 仅记录错误、不删除记录（`:1075-1076` 只构建 `record_validation_errors` 列表，不动 `parser_contract['records']`） | `scanner.py:1074-1076` | 代码实测 |
| I'-19 | `NON_SUCCESS_STATES = {UNRESOLVED, AMBIGUOUS, DYNAMIC, EXTERNAL, UNKNOWN}`（**不含 CONFLICT**，且 `RESOLUTION_STATUSES` 仅 6 值，无 CONFLICT） | `schema_adapter.py:16` + `parser_contract.py:11-13` | 代码实测 |

> **三种状态声明（强制区分）**：
> - **设计已修订**：✅ 本报告（REVIEW-08）完成。
> - **源码已实现**：❌ 否。S1-C 未实施；`understanding_projection` 仍仅孤立存在（I'-1 BLOCKER）。
> - **测试已执行**：❌ 否。本工单 READ-ONLY，未运行任何实现或测试；下文统计口径/决策表/示例均为**设计规格**，待授权实施后验证。

---

## 1. R1｜分支 8 的精确可达条件（P0）

### 1.1 以 REVIEW-07 §4 实际伪代码顺序为唯一基准

REVIEW-07 §4 决策伪代码顺序（不变）：

```python
# 分支 0: empty
# 分支 1: no_records
all_non_success    = all(r.resolution_status in NON_SUCCESS for r in f.records)   # I'-19
all_evidence_empty = all(not has_evidence_candidate(r) for r in f.records)        # 候选 = 非空 Mapping
meaningful_count   = sum(1 for r in f.records
                         if r.resolution_status == "RESOLVED" and r.evidence_meaningful == "True")  # 当前恒 0 (I'-13)
resolved_ev_count  = sum(1 for r in f.records
                         if r.resolution_status == "RESOLVED" and has_evidence_candidate(r))
# 分支 2: if all_non_success or all_evidence_empty: return UNKNOWN_RESOLUTION
# 分支 3: if f.has_conflict and meaningful_count == 0: return CONFLICT_ONLY
# 分支 4: if f.file_degraded and meaningful_count == 0: return DEGRADED_PARSE
# 分支 6: if meaningful_count >= 1: return PARTIAL_UNDERSTANDING   # [FUTURE]
# 分支 7: if resolved_ev_count >= 1: return INSUFFICIENT_EVIDENCE
# 分支 8: return ("UNKNOWN", ["indeterminate_resolution_without_usable_resolved_evidence"])
```

### 1.2 分支 8 的精确可达条件（重定义，消除 REVIEW-07 §2.1 的措辞歧义）

**关键修正**：REVIEW-07 §2.1 原文"∃ 至少一条 RESOLVED **且** ∃ 至少一条 `has_evidence_candidate==True`"在中文并列句中容易被误读为"存在一条既 RESOLVED 又具有候选 evidence 的记录"。本工单要求将其拆成**两个独立的存在量词**，明确如下：

> **分支 8 命中充要条件（在 `meaningful_count == 0` 恒成立的当前前提下）**：
> 1. 越过分支 2：即 `not (all_non_success or all_evidence_empty)`。
>    - `not all_non_success` ⟺ **∃ 至少一条 `resolution_status` 不在 `NON_SUCCESS` 中的记录**（当前即 **∃ 至少一条 `RESOLVED` 记录**，因为 `CONFLICT` 不出现在 parser-contract 的 `RESOLUTION_STATUSES` 中，I'-19）。
>    - `not all_evidence_empty` ⟺ **∃ 至少一条 `has_evidence_candidate == True` 的记录**（该记录的候选 evidence 可位于**任意** resolution 状态，**不必是 RESOLVED**）。
> 2. 满足分支 2 的两条"越过"条件后，仍须**不命中任何更高优先级分支**：
>    - 不命中分支 3：即 **`not f.has_conflict`**（因 `meaningful_count==0`，分支 3 在 `has_conflict` 为真时必然命中）。
>    - 不命中分支 4：即 **`not f.file_degraded`**（同理）。
>    - 不命中分支 6：`meaningful_count >= 1` 当前恒不成立（I'-13）。
>    - 不命中分支 7：即 **`resolved_ev_count == 0`** ⟺ **没有任何 `RESOLVED` 记录具有候选 evidence**。
> 3. 综合（1）+（2）可得分支 8 的核心判据（与工单 R1 完全一致）：
>    - **∃ 至少一条 `RESOLVED` 记录**；
>    - **∃ 至少一条候选 evidence 记录**（位于某 `NON_SUCCESS` 记录上，与 RESOLVED 解耦）；
>    - **没有任何 `RESOLVED` 记录具有候选 evidence**；
>    - **同时不满足分支 3、4、6、7**（即 `has_conflict=False`、`file_degraded=False`）。

> **不得误写的红线**："存在候选 evidence" 与 "存在 RESOLVED 且有候选 evidence" 是**两个不同的命题**。分支 8 要求前者为真、后者为假——候选 evidence 必须**落在不具 RESOLVED 状态的记录上**。任何把分支 8 写成"RESOLVED 记录携带候选 evidence"的表述均为错误。

### 1.3 分支 8 可达性证明（基于 REVIEW-07 的 C19 输入逐步演算）

**输入（C19，`mixed.py`）**：
- `R1`：`resolution_status = "RESOLVED"`，`evidence = "字符串"`（非 Mapping 真值，来自图节点，经 `adapt_graph_v1` 原样放行，I'-17）。
- `R2`：`resolution_status = "NON_SUCCESS"`（如 `UNKNOWN`），`evidence` 经 fallback = `{"graph_node_id": id}`（非空 Mapping，I'-8）。

**逐步演算（严格按 §1.1 顺序）**：

| 步 | 判定 | 结果 | 说明 |
|---|---|---|---|
| 0 | `empty`（`size_bytes==0`）? | 否 | 文件有内容 |
| 1 | `no_records`（`n_records==0`）? | 否 | 共 2 条记录 |
| 2a | `all_non_success`? | **否** | `R1` 为 `RESOLVED` ∉ `NON_SUCCESS`（I'-19）→ `not all_non_success` → **越过分支 2 的该项** |
| 2b | `all_evidence_empty`? | **否** | `R2` 有候选 evidence（非空 Mapping，`has_evidence_candidate=True`）→ `not all_evidence_empty` → **越过分支 2** |
| 3 | `f.has_conflict and meaningful_count==0`? | 否 | 无冲突（`has_conflict=False`）→ 不进分支 3 |
| 4 | `f.file_degraded and meaningful_count==0`? | 否 | 无降级（`file_degraded=False`）→ 不进分支 4 |
| 6 | `meaningful_count >= 1`? | 否 | 当前恒 0（I'-13）→ 不进分支 6 |
| 7 | `resolved_ev_count >= 1`? | **否（=0）** | `resolved_ev_count = count(RESOLVED and 候选)`：`R1` 非 Mapping → 无候选；`R2` 非 RESOLVED → 不计入 → **=0** → 不进分支 7 |
| 8 | 落入兜底 | **命中分支 8** | 返回 `("UNKNOWN", ["indeterminate_resolution_without_usable_resolved_evidence"])` |

**可达性结论**：在 `adapt_graph_v1` 放行非 Mapping 证据（I'-17）+ `validate_record` 非破坏性（I'-18）+ `_build_evidence` 对非 Mapping 返回 `None`（I'-15）三条源码事实**齐备**的前提下，C19 输入可被当前契约稳定产出，**分支 8 当前可达**。源码证据充足，**无需标 `UNRESOLVED`**。

### 1.4 分支 8 文件结论与原因名评估

- 当前（REVIEW-07）取值：`file_conclusion = "UNKNOWN"`，`reason = "indeterminate_resolution_without_usable_resolved_evidence"`。
- **评估结论**：该命名**准确且恰当**，予以保留。
  - `"UNKNOWN"` 是 8 值 `file_conclusion` 枚举中的兜底值（与 `NO_SEMANTIC_CONTENT/NO_RECORDS/UNKNOWN_RESOLUTION/CONFLICT_ONLY/DEGRADED_PARSE/PARTIAL_UNDERSTANDING/INSUFFICIENT_EVIDENCE` 并列），语义为"文件含可解析记录，但理解状态无法确定"。
  - 原因名 `indeterminate_resolution_without_usable_resolved_evidence` 精确刻画：文件有 `RESOLVED` 记录、也有候选 evidence，但**没有任何 `RESOLVED` 记录自身携带可用候选 evidence**——这正是 §1.2 的精确条件。
- **可选替代（仅作设计提示，不在本工单决定）**：若实施阶段希望将该态与"泛化 UNKNOWN（如分支 2 之外的兜底异常）"区分，可引入专用枚举值如 `INDETERMINATE`，但那需要**扩展 8 值 `file_conclusion` 枚举**，属实现期决策，超出本只读审查授权范围。本报告维持 `"UNKNOWN" + "indeterminate_resolution_without_usable_resolved_evidence"`。

**R1 结果：PASS**（精确条件已重定义并消歧；C19 逐步证明可达；原因名评估完成；源码证据充足未标 UNRESOLVED）。

---

## 2. R2｜C1–C21 逐记录计数校正（P0）

### 2.1 计数口径统一（绝对计数，禁止无基线增量）

本工单**废弃 REVIEW-07 §5 表中 "N_raw+1" 等无基线增量写法**，改为每个样例**独立文件的绝对计数**。每条样例的 `N_raw` = 该样例文件经 `adapt_graph_v1` 后的记录条数（原始输入，未去重、未因文件结论改变）。

**候选判定（统一）**：`has_evidence_candidate(r) = isinstance(evidence, Mapping) and bool(evidence)`（与 `_build_evidence` 返回有效引用的条件一致，I'-15）。
- 候选记录（`N_candidate`）：非空 Mapping evidence（含 `{"graph_node_id": id}` fallback，因非空 Mapping → 候选）。
- 非候选记录（`N_non_candidate`）：evidence 缺失 / 空 / 非 Mapping（如字符串）→ `_build_evidence` 返回 `None`。

**构造终态计数**：
- `N_construct_ok`：候选记录且 `_build_evidence` 成功返回 `EvidenceReference`（当前全部候选均成功，I'-16）。
- `N_construct_fail`：候选记录但抛错被捕获计数（当前 **= 0**，I'-16；保留项）。
- `N_evidence_none`：非候选记录且 `_build_evidence` 返回 `None` = `N_non_candidate`（恒等式，§4）。

### 2.2 C1–C21 逐记录绝对计数表（缺失项标 `NOT_AVAILABLE`）

表头缩写：`Nr`=N_raw，`Nc`=N_candidate，`Nnc`=N_non_candidate，`Nok`=N_construct_ok，`Nfail`=N_construct_fail，`Nnone`=N_evidence_none，`dup`=duplicate_records，`cf`=conflict_items，`proj`=projected_items。

| # | 输入（resolution / evidence） | Nr | Nc | Nnc | Nok | Nfail | Nnone | dup | cf | proj | 分支 | file_conclusion |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| C1 | 1×RESOLVED / 非空 dict 真摘要 | 1 | 1 | 0 | 1 | 0 | 0 | 0 | 0 | 1 | 7 | INSUFFICIENT_EVIDENCE |
| C2 | 1×RESOLVED / 仅 `{graph_node_id}` | 1 | 1 | 0 | 1 | 0 | 0 | 0 | 0 | 1 | 7 | INSUFFICIENT_EVIDENCE |
| C3 | 1×NON_SUCCESS / fallback 非空 | 1 | 1 | 0 | 1 | 0 | 0 | 0 | 0 | 1 | 2 | UNKNOWN_RESOLUTION |
| C4 | 空文件（`size_bytes==0`） | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | NO_SEMANTIC_CONTENT |
| C5 | 0 记录、无符号 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 1 | NO_RECORDS(no_symbols) |
| C6 | 0 记录、有符号无记录 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 1 | NO_RECORDS(symbols_no_records) |
| C7 | 1×RESOLVED / 非空 dict + 文件降级 | 1 | 1 | 0 | 1 | 0 | 0 | 0 | 0 | 1 | 4 | DEGRADED_PARSE |
| C8 | 1×NON_SUCCESS / fallback + 文件降级 | 1 | 1 | 0 | 1 | 0 | 0 | 0 | 0 | 1 | 2 | UNKNOWN_RESOLUTION |
| C9 | 1×NON_SUCCESS / fallback 非空 | 1 | 1 | 0 | 1 | 0 | 0 | 0 | 0 | 1 | 2 | UNKNOWN_RESOLUTION |
| C10 | 1×RESOLVED + 1×NON_SUCCESS（同身份异内容→冲突） | 2 | 2 | 0 | 2 | 0 | 0 | 0 | 1 | 3 | 3 | CONFLICT_ONLY |
| C11 | 1×RESOLVED / 非空 dict + orphan | 1 | 1 | 0 | 1 | 0 | 0 | 0 | 0 | 1 | 7 | INSUFFICIENT_EVIDENCE |
| C12 | 1×RESOLVED / 非空 dict + diagnostics 越界 | 1 | 1 | 0 | 1 | 0 | 0 | 0 | 0 | 1 | 7 | INSUFFICIENT_EVIDENCE |
| C13 | 1×RESOLVED / 非空 dict + path∉manifest | 1 | 1 | 0 | 1 | 0 | 0 | 0 | 0 | 1 | 7 | INSUFFICIENT_EVIDENCE |
| C14 | 1×RESOLVED / 非空 dict + line 非正整数 | 1 | 1 | 0 | 1 | 0 | 0 | 0 | 0 | 1 | 7 | INSUFFICIENT_EVIDENCE |
| C15 | 1×RESOLVED / 非空 dict + TestSpec 缺必填 | 1 | 1 | 0 | 1 | 0 | 0 | 0 | 0 | 1 | 7 | INSUFFICIENT_EVIDENCE |
| C16 | 2×RESOLVED 完全相同（重复） | 2 | 2 | 0 | 2 | 0 | 0 | 1 | 0 | 2 | 7 | INSUFFICIENT_EVIDENCE |
| C17 | 1×NON_SUCCESS + 1×RESOLVED（混合） | 2 | 2 | 0 | 2 | 0 | 0 | 0 | 0 | 2 | 7 | INSUFFICIENT_EVIDENCE |
| C18 | 1×RESOLVED / evidence 非 Mapping（单文件） | 1 | 0 | 1 | 0 | 0 | 1 | 0 | 0 | 1 | 2 | UNKNOWN_RESOLUTION |
| C19 | 1×RESOLVED 非 Mapping + 1×NON_SUCCESS 候选 | 2 | 1 | 1 | 1 | 0 | 1 | 0 | 0 | 2 | 8 | UNKNOWN |
| C20 | 2×NON_SUCCESS / fallback + 文件降级 | 2 | 2 | 0 | 2 | 0 | 0 | 0 | 0 | 2 | 2 | UNKNOWN_RESOLUTION |
| C21 | 2×NON_SUCCESS（同身份异内容→冲突） | 2 | 2 | 0 | 2 | 0 | 0 | 0 | 1 | 3 | 2 | UNKNOWN_RESOLUTION |

### 2.3 恒等式逐行校验（每样例必满足）

- `Nr == Nc + Nnc`（候选/非候选二分，互斥穷尽）✓ 全部满足。
- `Nc == Nok + Nfail`（候选内构造二分，当前 `Nfail==0`）✓ 全部满足。
- `Nnc == Nnone`（非候选即 `_build_evidence` 返回 `None`）✓ 全部满足。
- `Nr == Nok + Nfail + Nnone`（简化恒等式，前提 `N_not_attempted==0` 且 `N_unexpected_error==0`，见 §3）✓ 全部满足。
- `proj == Nr + cf`（投影输出 = 原始记录 + 冲突条目，I'-7）✓ 全部满足。

### 2.4 对 REVIEW-07 的关键计数修正（P0）

1. **C20 / C21 记录数修正**：REVIEW-07 §5 表将 C20/C21 写为 `N_raw+1`（隐含 1 条），但二者**各含 2 条记录**（R-C20/C21 描述明确"各包含两条记录"）。本表修正为 `Nr=2`、`Nok=2`、`proj=2`（C21 因构造具体冲突 `cf=1`→`proj=3`）。**这是 REVIEW-07 的数值错误，已修正。**
2. **废弃无基线增量**：REVIEW-07 全部 `N_raw+1 / +2` 写法无基线定义；本表改为每个样例文件独立的绝对计数，消除歧义。
3. **缺失项标注**：REVIEW-07 未对构造状态/冲突数量缺失处标注；本表 `cf` 列对无冲突样例明确为 `0`（非缺失），`dup` 仅 C16=`1`，其余 `0`；若某样例确实缺输入则标 `NOT_AVAILABLE`（本表无需，因输入均可由既有代码事实推导）。
4. **不得从 resolution 推断 evidence 状态**：C18 的 `Nc=0/Nnone=1` 来自"非 Mapping evidence"这一 **evidence 事实**（I'-15/I'-17），非由 `RESOLVED` 状态推断；C2 的 `Nc=1` 来自 fallback 非空 Mapping 这一 **evidence 事实**（I'-8），亦非由 resolution 推断。

**R2 结果：PASS**（C1–C21 全部给出绝对计数；C20/C21 记录数已修正为 2；恒等式逐行校验通过；缺失项规范标注）。

---

## 3. R3｜逐记录终态与计数恒等式（P1）

### 3.1 逐记录互斥终态模型

| 终态 | 定义 | 触发条件（来自代码） | 当前可达 |
|---|---|---|---|
| `CONSTRUCT_OK` | 构造出有效 `EvidenceReference` | 记录为候选（非空 Mapping evidence），`_build_evidence` 返回引用（I'-15） | ✅ 当前典型 |
| `CONSTRUCT_FAIL` | 尝试构造后抛异常且被明确捕获计数 | 候选记录但 `__post_init__` 抛 `ValueError`（I'-16） | ⚠️ 当前 **≈0**（构造恒传安全值） |
| `EVIDENCE_NONE` | 无候选 evidence，或 `_build_evidence` 明确返回 `None` | evidence 缺失/空/非 Mapping（I'-15:125-126） | ✅ 当前典型 |
| `NOT_ATTEMPTED` | 记录被跳过/过滤/未执行构造 | 非 Mapping 输入进入 `project_record` 时提前返回（I'-5 路径：`:158-173` 直接构造 `ProjectedItem`，**不调用** `_build_evidence`） | ⚠️ 当前 scanner→adapt→project 链路中记录恒为 Mapping，故 **=0** |
| `UNEXPECTED_ERROR` | 未被正常分类的异常或无法确认终态 | 任何未预见的异常路径（安全兜底） | ⚠️ 当前 **=0**（保留兜底） |

### 3.2 互斥性与穷尽性

- **互斥**：每条原始记录经 `project_record` 只走一条路径——要么是 Mapping（→ `CONSTRUCT_OK` 或 `EVIDENCE_NONE`，二者由 evidence 是否非空 Mapping 互斥决定），要么是非 Mapping（→ `NOT_ATTEMPTED`，不进入构造）。`CONSTRUCT_FAIL` 仅在候选构造抛错时与 `CONSTRUCT_OK` 互斥；`UNEXPECTED_ERROR` 为兜底，不与其它共存。
- **穷尽**：在**标准链路**（scanner→`adapt_graph_v1`→`project_records`）中，所有记录均为 Mapping → 每条归入 `CONSTRUCT_OK` 或 `EVIDENCE_NONE`（`NOT_ATTEMPTED=0`、`UNEXPECTED_ERROR=0`）。若调用方传入非 Mapping 记录，则归入 `NOT_ATTEMPTED`。因此 5 态模型在标准链路 + 非 Mapping 输入两种情形下**穷尽**。
- **缺口说明**：若未来出现"记录为 Mapping 但 `_build_evidence` 因未知原因既不返回引用也不返回 None 而是抛未捕获异常"，则该记录无法被 `CONSTRUCT_FAIL`（需显式捕获）归类，落入 `UNEXPECTED_ERROR`。当前代码路径不会产生此情形，但模型保留 `UNEXPECTED_ERROR` 作为不可穷尽时的安全出口，确保**不静默漏计**。

### 3.3 唯一记录标识与唯一终态归属

- 每条原始记录由其在 `parser_contract['records']` 中的**位置索引 + 内容**唯一标识（`_stable_id` 内容派生，I'-5）；投影期 `projection_id` 由内容派生（I'-6），但**重复记录共享 `projection_id`**（见 §4 `duplicate_records`）。
- 每条记录在 §3.1 五态中**恰好归入一态**；无法分类时归入 `UNEXPECTED_ERROR` 或显式未知态，**禁止静默丢弃**。

### 3.4 计数恒等式（重定义）

**完整恒等式（覆盖全部终态）**：
```
N_raw = N_construct_ok + N_construct_fail + N_evidence_none
      + N_not_attempted + N_unexpected_error
```
- 适用范围：全部适配后原始记录。
- 单位：条。
- 前置条件：每条记录恰好归入一态（§3.2 穷尽性成立）。
- 简化前提：仅当 `N_not_attempted == 0` 且 `N_unexpected_error == 0` 时，可简化为：
```
N_raw = N_construct_ok + N_construct_fail + N_evidence_none
```
- 当前（标准链路）满足简化前提（`N_not_attempted=N_unexpected_error=0`），故 §2.3 使用的即为此简化式。

### 3.5 `N_no_evidence` 与 `N_evidence_none` 口径隔离（必查）

- `N_no_evidence`：**适配前** evidence 为空（fallback 之前）。由于 `adapt_graph_v1` 在 `:121` 用 `{"graph_node_id": id}` 覆盖所有假值证据，原始"为空"状态**未被任何字段保留**（I'-14）→ **`N_no_evidence` 当前 = `NOT_AVAILABLE`**，**禁止**进入上述任一恒等式。
- `N_evidence_none`：**适配后 / 构造阶段**没有可用 evidence（`_build_evidence` 返回 `None`）= `N_non_candidate`（§2.1）。二者是**不同口径**，不可混用。

### 3.6 重复记录 / 冲突记录对计数的影响

- **重复记录**：不改变 `N_raw`（原始输入计数）。`duplicate_records = raw_records − unique_projection_ids_raw`（I'-5/I'-7），属**投影输入维度**，仅用于统计，不删减原始记录。
- **冲突记录**：`_detect_conflicts` 产出独立 `conflict_items`，**追加**到 `projected_items`（`proj = Nr + cf`，I'-7），不改变 `N_raw`。`conflict_items` 与 `duplicate_records`、`N_evidence_none` 分属不同维度，**互不替代**（详见 §4）。

**R3 结果：PASS**（5 态互斥/穷尽性已论证；完整 + 简化恒等式及前提已给；双 `N_evidence_none`/`N_no_evidence` 口径隔离；重复/冲突不影响 `N_raw`）。

---

## 4. R4｜恒等式与统计维度隔离（P1）

每个统计维度独立定义，禁止跨维度替代：

| 维度 | 计数单位 | 定义 / 计算 | 适用范围 | 前置条件 | 不成立场景 | 缺失输入处理 |
|---|---|---|---|---|---|---|
| `N_raw` | 条 | 适配后原始记录数 = `len(parser_contract['records'])` | 全文件 | 已执行 `adapt_graph_v1` | 若记录流被截断则偏小 | 无（适配必产出） |
| `N_candidate` | 条 | `count(has_evidence_candidate(r)==True)` | 全文件 | 每条可按非空 Mapping 判定 | evidence 字段缺失且未 fallback（当前不会发生，I'-8 保证有字段） | 不发生 |
| `N_non_candidate` | 条 | `N_raw − N_candidate` | 全文件 | 同上 | 同上 | 不发生 |
| `N_construct_ok` | 条 | 候选且 `_build_evidence` 返回引用 | 候选子集 | 候选记录存在 | 候选构造抛错（当前 ≈0） | 候选=0 时自动 0 |
| `N_construct_fail` | 条 | 候选且构造抛错被计数 | 候选子集 | 候选记录存在 | 候选不抛错（当前） | 候选=0 时自动 0 |
| `N_evidence_none` | 条 | 非候选且 `_build_evidence` 返回 `None` = `N_non_candidate` | 非候选子集 | 同上 | 同 `N_non_candidate` | 不发生 |
| `N_not_attempted` | 条 | 未进入构造（非 Mapping 输入） | 全文件 | 调用方可能传非 Mapping | 标准链路恒 0 | 标准链路 0 |
| `N_unexpected_error` | 条 | 未分类异常 | 全文件 | — | 当前 0（兜底） | 当前 0 |
| `unique_projection_ids_raw` | 不同 id 数 | 不同 `_stable_id` 数（I'-5） | 投影输入 | 内容派生稳定 | **跨扫描**内容/标识漂移 → 不稳定（§8，标记为 UNRESOLVED，不宣称） | 单扫描内稳定 |
| `duplicate_records` | 条 | `raw_records − unique_projection_ids_raw` | 投影输入 | 同上 | 跨扫描不成立 | 单扫描内成立 |
| `conflict_items` | 条目 | `len(_detect_conflicts(...))`（I'-7） | 投影输出 | 冲突检测执行 | 若 `project_records` 改为不追加冲突 → 不成立 | 无冲突时 0 |
| `projected_items` | 条目 | `len(items) = N_raw + conflict_items`（I'-7） | 投影输出 | 同上 | 若实现去重/合并冲突 → 不成立 | 无冲突时 = N_raw |

> **维度隔离红线**：`duplicate_records`、`conflict_items` 与 `N_evidence_none` **互不替代**；`raw_records`（记录数）、`unique_projection_ids_raw`（不同 id 数）、`duplicate_records`（重复条数）、`conflict_items`（冲突条数）、`projected_items`（最终条目数）分属不同计数，**禁止统称为"记录数"**。

**R4 结果：PASS**（12 个维度逐一给出单位/范围/前置/不成立/缺失处理；隔离红线明确）。

---

## 5. R5｜分支优先级与边界样例重算（P1）

### 5.1 布尔分支优先级与相互作用（纸面推演，非测试执行）

优先级（高→低）：`empty`(0) > `no_records`(1) > `all_non_success or all_evidence_empty`(2) > `has_conflict∧¬meaningful`(3) > `file_degraded∧¬meaningful`(4) > `meaningful>=1`(6)[FUTURE] > `resolved_ev>=1`(7) > 兜底(8)。

关键相互作用：
- **`all_non_success` 吞并 `file_degraded`（分支 4）与 `has_conflict`（分支 3）**：当所有记录均为 `NON_SUCCESS` 时，分支 2 先命中，降级/冲突被"吞并"，文件结论为 `UNKNOWN_RESOLUTION`（C8/C20/C21 证明）。
- **`all_evidence_empty` 吞并一切有记录的 evidence 缺失态**（B2b / C18）。
- **`all([]) == True` 语言语义**：Python 中 `all([])` 返回 `True`。若空记录文件被错误地先算 `all_non_success`/`all_evidence_empty` 会得到 `True`（误判为分支 2）。REVIEW-07 §4 用 `no_records` 分支（序号 1）**先于** `all()` 调用守卫（B1c），确保空记录落入 `NO_RECORDS` 而非分支 2。**本工单确认该守卫正确且必要**。

### 5.2 三种特殊组合核对

| 组合 | 输入 | 命中分支 | 文件结论 | 说明 |
|---|---|---|---|---|
| 全部 NON_SUCCESS + 降级 | C20（2×NON_SUCCESS + `file_degraded`） | 2 | `UNKNOWN_RESOLUTION` | 降级被 `all_non_success` 吞并 |
| 全部 NON_SUCCESS + 冲突 | C21（2×NON_SUCCESS + `has_conflict`） | 2 | `UNKNOWN_RESOLUTION` | 冲突被 `all_non_success` 吞并 |
| RESOLVED 非 Mapping + NON_SUCCESS 候选 | C19 | 8 | `UNKNOWN` | `resolved_ev_count==0` → 兜底 |

### 5.3 边界样例手工推演结果（唯一分支归属）

| 边界 | 输入条件 | 命中分支 | 文件结论 | 记录数 | 计数（Nr/Nc/Nnc/Nok/Nnone） |
|---|---|---|---|---|---|
| B0 | `size_bytes==0` | 0 | `NO_SEMANTIC_CONTENT` | 0 | 0/0/0/0/0 |
| B1a | 0 记录无符号 | 1 | `NO_RECORDS`(no_symbols) | 0 | 0/0/0/0/0 |
| B1b | 0 记录有符号 | 1 | `NO_RECORDS`(symbols_no_records) | 0 | 0/0/0/0/0 |
| B1c | 0 记录：`no_records` 先于 `all()` | 1 | `NO_RECORDS` | 0 | 0/0/0/0/0 |
| B2a | 全部 NON_SUCCESS | 2 | `UNKNOWN_RESOLUTION` | 1 | 1/1/0/1/0 |
| B2b | 全部 RESOLVED 但 evidence 全非 Mapping | 2 | `UNKNOWN_RESOLUTION` | 1 | 1/0/1/0/1 |
| B3 | 冲突 + ≥1 RESOLVED+候选，无 True | 3 | `CONFLICT_ONLY` | 2(cf=1) | 2/2/0/2/0 |
| B4 | 文件降级 + ≥1 RESOLVED+候选，无冲突，无 True | 4 | `DEGRADED_PARSE` | 1 | 1/1/0/1/0 |
| B6[FUTURE] | ≥1 RESOLVED+候选且 `evidence_meaningful=="True"` | 6 | `PARTIAL_UNDERSTANDING` | — | NOT_AVAILABLE（依赖 excerpt_verified，I'-13） |
| B7 | ≥1 RESOLVED+候选，无冲突/降级，无 True | 7 | `INSUFFICIENT_EVIDENCE` | 1 | 1/1/0/1/0 |
| B8 | RESOLVED 非 Mapping + NON_SUCCESS 候选 | 8 | `UNKNOWN` | 2 | 2/1/1/1/1 |

> 每个边界与样例命中**唯一分支**，且 `file_conclusion` 与计数自洽（见 §2.2 / §2.3 恒等式校验）。`B6[FUTURE]` 缺失输入（无 `excerpt_verified`）明确标 `NOT_AVAILABLE`，未假定为 0 或可达。

**R5 结果：PASS**（优先级与三种组合核对完成；`all([])==True` 守卫确认；C1–C21 与 B0–B8 唯一分支归属无矛盾；缺失输入标 `NOT_AVAILABLE`）。

---

## 6. R6｜报告一致性与门禁保护（P1）

### 6.1 四类内容强制区分

| 类别 | 本报告涉及内容 | 标注 |
|---|---|---|
| 源码实测 | §0 全部 I'-1~I'-19、§1.3 证据链 | 标注 `文件:行` / grep 结果 |
| 设计推演 | §1.2 分支 8 条件、§3 终态模型、§5 边界推演 | 标注"设计规格/纸面推演" |
| 尚未实施 | S1-C 关联层、`understanding_linkage.json`、`understanding_test_plan_candidate.json` | 标注"未实施" |
| 未执行测试 | 任何功能/单元/Android 测试 | 标注"未执行（NOT_RUN）" |

### 6.2 不升级 NON_SUCCESS

本报告**不**将 `RESOLVED` 自动视为证据真实（证据须过 `has_evidence_candidate` 与独立校验）；**不**将 `UNKNOWN/AMBIGUOUS/DYNAMIC/EXTERNAL/UNRESOLVED/CONFLICT` 升级为成功。所有 `NON_SUCCESS` 记录在决策中仅用于"全部 NON_SUCCESS→分支 2"判定，绝不贡献 `resolved_ev_count` 或 `meaningful_count`。

### 6.3 门禁保持（不放宽）

- `verified_fact = 0`（I'-7，结构不可达）；`ExecutionRecord` 全 `NOT_RUN`（I'-12）。
- `summary_verification = NOT_CHECKED` 恒为真；受约束条目 `complete = False`（门禁生效）。
- **不为求 `complete=True` 而放宽规则**：`item_complete` 伪代码（REVIEW-07 §3）维持 `summary_not_verified` 恒命中、`evidence_not_meaningful` 恒非 `True`。
- `CONFLICT`/`DEGRADED` 等 file_context 原因**有意全局**写入所有条目 `incomplete_reasons`（REVIEW-07 §2.4），与 item 级结构缺陷隔离，二者不矛盾。

### 6.4 固定门禁值（原值保持，全程未变）

| 门禁 | 值 |
|---|---|
| `F1` | `NOT_READY_TO_CLOSE` |
| `P0.8` | `HOLD` |
| `F2` | `STOP` |
| `历史授权连续性` | `UNRESOLVED` |
| `安全信任链` | `BLOCKED / NOT_VERIFIED` |

### 6.5 实施状态声明

- `understanding_projection` **仍未接线**（BLOCKER-1，I'-1）；S1-C **仍未实施**；本工单**未执行任何实现测试**（READ-ONLY）。

**R6 结果：PASS**（四类内容区分；源码定位齐全；不升级 NON_SUCCESS；门禁与 `complete=False` 维持；固定门禁原值；明确未实施未接线未测试）。

---

## 7. 问题清单（按 P0 / P1 / P2 分级）

| 级别 | 编号 | 问题 | 处置 |
|---|---|---|---|
| **P0** | P0-1 | REVIEW-07 §5 将 C20/C21（各 2 条记录）误写为 `N_raw+1`（隐含 1 条） | **已修正**（§2.2 / §2.4）：C20/C21 `Nr=2`、`Nok=2` |
| **P0** | P0-2 | REVIEW-07 §5 全程使用无基线增量计数（`N_raw+1/+2`），无法独立复核 | **已修正**（§2.2）：全部改为独立文件绝对计数 |
| **P1** | P1-1 | REVIEW-07 §2.1 分支 8 措辞"∃ RESOLVED 且 ∃ 候选"易误读为"RESOLVED 携带候选" | **已修正**（§1.2）：拆为两个独立存在量词，明确候选位于 NON_SUCCESS 记录 |
| **P1** | P1-2 | REVIEW-07 未显式定义 5 态逐记录终态模型（仅隐含 `N_candidate/N_non_candidate/N_evidence_none`） | **已补充**（§3）：定义 `CONSTRUCT_OK/FAIL/EVIDENCE_NONE/NOT_ATTEMPTED/UNEXPECTED_ERROR` 及完整恒等式 |
| **P1** | P1-3 | REVIEW-07 未显式核对 `all([])==True` 空记录守卫 | **已确认**（§5.1）：`no_records` 先于 `all()` 守卫正确 |
| **P2** | P2-1 | REVIEW-07 样例集冗余：C3 与 C9（均 1×NON_SUCCESS+候选→分支 2）、C8 与 C20（均 all NON_SUCCESS+降级→分支 2）描述同一逻辑场景 | **已披露**（非阻断）：本报告保留全部 21 行并标注重叠，建议实施期合并演示样例 |
| **P2** | P2-2 | `has_conflict` 的派生来源（`conflict_items>0` vs 存在 `CONFLICT` 状态记录）是设计决策；且 `CONFLICT ∉ NON_SUCCESS`（I'-19）意味着若存在 `CONFLICT` 记录将令 `all_non_success=False`，改变分支优先级 | **已披露**（非阻断）：本报告 C10/C21 采用"同身份异内容→`conflict_items=1` 且 `has_conflict=True`"的具体构造，使分支优先级结论成立；该派生规则建议实施期明确固化 |
| **P2** | P2-3 | `N_no_evidence`（pre-adapt 空）当前 `NOT_AVAILABLE`（I'-14），需未来透传 `evidence_was_empty_before_adapt` 方可计数 | **已披露**（非阻断）：不进入任何恒等式 |

> 所有 P0/P1 问题已在本次报告中**修正或补充**；P2 为已明确披露的非阻断观察，不影响核心分支判断与计数一致性。

---

## 8. PASS / FAIL / 未测试矩阵（设计验收，非实现验收）

| 项 | 状态 |
|---|---|
| R1 分支 8 精确条件重定义 + C19 可达性证明 + 原因名评估 | ✅ PASS |
| R2 C1–C21 绝对计数校正（含 C20/C21 记录数修正） | ✅ PASS |
| R3 5 态终态模型 + 完整/简化恒等式 + 双 `N_evidence_none` 口径隔离 | ✅ PASS |
| R4 12 维度隔离定义（单位/范围/前置/不成立/缺失处理） | ✅ PASS |
| R5 分支优先级 + 三种组合 + 边界唯一归属 + `all([])` 守卫 | ✅ PASS |
| R6 四类内容区分 + 源码定位 + 不升级 NON_SUCCESS + 门禁维持 | ✅ PASS |
| 源码契约证据（§0 I'-1~I'-19，独立 grep + 区间阅读复核） | ✅ 代码实测坐实 |
| **源码已实现** | ❌ **否**（S1-C 未实施，BLOCKER-1 未接线） |
| **测试已执行（本次）** | ❌ **否**（READ-ONLY，未运行任何实现/测试） |
| 历史测试证据引用 | ⚠️ 仅引 REVIEW-04~07 来源，已标范围，非本次执行 |

---

## 9. 文件变更清单及证据

| 类别 | 变更 |
|---|---|
| **源码变更** | **0**（未修改 `understanding_projection.py` / `parser_contract.py` / `cli.py` / `scanner.py` / `understanding_model.py` 等任何文件） |
| **既有产物变更** | **0**（未修改 manifest / 图谱 / 原 `test_plan.json` / 任何 REVIEW-04~07 报告 / 配置） |
| **新增文件** | **仅** `reports/USIT-S1-C-DESIGN-REVIEW-08-执行报告.md`（本报告） |
| **测试执行** | `NOT_RUN`（本工单不授权执行测试） |

> 变更证据：本工单全程仅对既有文件执行 `Read` / `grep`（只读），唯一写操作为新增本报告。可用 `git status` / 目录比对验证：除新增 `reports/USIT-S1-C-DESIGN-REVIEW-08-执行报告.md` 外，工作区无任何其它改动。下列命令供人工复核（未在本工单内执行写操作）：
> - `git -C "C:\Users\Administrator\WorkBuddy\2026-10-15-22-09-47\usit_p0" status --porcelain`
> - 预期输出仅含新增的 `reports/USIT-S1-C-DESIGN-REVIEW-08-执行报告.md`（若仓库已纳入 git 跟踪）。

---

## 10. 固定门禁与最终 STOP 声明

### 10.1 固定门禁值（原值保持，全程未变）
| 门禁 | 值 |
|---|---|
| `F1` | `NOT_READY_TO_CLOSE` |
| `P0.8` | `HOLD` |
| `F2` | `STOP` |
| `历史授权连续性` | `UNRESOLVED` |
| `安全信任链` | `BLOCKED / NOT_VERIFIED` |

### 10.2 最终结论

1. **设计已修订（本次交付）**：REVIEW-08 在 REVIEW-07 基础上，独立复核并修正 R1–R6：① **R1**——以 REVIEW-07 §4 实际伪代码顺序为唯一基准，将分支 8 重定义为两个独立存在量词（∃RESOLVED ∧ ∃候选 ∧ 无 RESOLVED 携带候选 ∧ ¬冲突 ∧ ¬降级），并以 C19 逐步演算证明**分支 8 当前可达**（证据链 I'-15/I'-17/I'-18 齐备，未标 UNRESOLVED），评估并保留 `UNKNOWN + indeterminate_resolution_without_usable_resolved_evidence` 命名；② **R2**——废弃无基线增量计数，给出 C1–C21 **独立文件绝对计数**，修正 C20/C21 记录数（2 条，原误写 1 条），逐行校验恒等式；③ **R3**——显式定义 5 态互斥终态模型 + 完整/简化恒等式，隔离 `N_evidence_none` 与 `N_no_evidence`（后者 NOT_AVAILABLE）；④ **R4**——12 个统计维度逐一给出单位/范围/前置/不成立/缺失处理；⑤ **R5**——重算分支优先级与 B0–B8/C1–C21 唯一归属，确认 `all([])==True` 空记录守卫；⑥ **R6**——四类内容区分、源码定位、门禁与 `complete=False` 维持。
2. **源码已实现：否**。S1-C 未进入实施；`understanding_projection` 仍孤立未接线（BLOCKER-1，I'-1）。
3. **测试已执行：否**。本工单严格 READ-ONLY，未运行任何实现或测试；所有决策表、统计、示例为**设计规格**，待授权实施后验证。
4. **硬约束全部维持**：`verified_fact=0`、`ExecutionRecord` 全 `NOT_RUN`、`summary_verification=NOT_CHECKED` 恒为真、受约束条目 `complete=False`；不放宽信任门禁；不把 `UNKNOWN/AMBIGUOUS/DYNAMIC/EXTERNAL/UNRESOLVED/CONFLICT` 升级为成功。
5. **验收评级**：**PASS**。R1–R6 全部有明确可复核结果；C20/C21 等计数与输入记录逐条一致；计数恒等式具备完整前置条件；分支优先级与唯一分支归属无矛盾；未虚构缺失事实；仅新增指定报告。P2 观察项已明确披露且不影响核心分支判断（属 PASS-WITH-ISSUES 以下的非阻断备注，不降级 PASS）。
6. **本设计规格的"通过"≠ 源码实现通过或测试通过**（验收规则明示）。

### 10.3 当前 S1-C 状态
- **未实施** ❌、**未接线** ❌（BLOCKER-1）、**未测试** ❌。

### 10.4 STOP
> 仅提交设计修订，未修改任何文件（仅新增本报告）。等待人工审查与独立授权；不得自行进入 S1-C 实施，不得修改 `understanding_projection.py` / `parser_contract.py` / `cli.py`，不得解除 BLOCKER-1/2，不得执行任何实现、构建、安装、登录、网络访问或 Android 测试。

---

**执行边界最终声明**：本工单只授权只读设计复核和新增 REVIEW-08 报告。任何代码修改、接线、构建、安装、登录、网络访问或 Android 测试均超出授权范围。
