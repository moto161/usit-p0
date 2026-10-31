# USIT-S1-C-DESIGN-REVIEW-09｜计数模型矛盾与冲突样例证据复核（READ-ONLY）

- **工单编号**：USIT-S1-C-DESIGN-REVIEW-09
- **执行时间**：2026-10-20（GMT+8，续 Context  compaction 后落地）
- **工作区绝对路径**：`C:\Users\Administrator\WorkBuddy\2026-10-15-22-09-47\usit_p0`
- **执行模式**：**严格只读设计审查** —— 仅读取源码/测试/报告，新增本设计报告；**禁止修改**任何源码/测试/配置/manifest/图谱/原 `test_plan.json`/其他既有产物；**禁止**接线、实现、构建、安装、登录、网络与 Android 测试。
- **修订基准**：`reports/USIT-S1-C-DESIGN-REVIEW-08-执行报告.md`（REVIEW-08）。
- **设计依据**：全部结论来自对本工作区源码的 grep + 区间阅读（见 §0 实测证据），非历史转述或推断。

---

## 0. 实际执行的只读检查命令 / 读取范围

> 本工单**未执行任何写操作、构建、测试或网络访问**。以下为只读检查清单（与 REVIEW-08 §0 一致，并追加本工单专属核验）：

| # | 检查动作 | 范围 | 用途 |
|---|---|---|---|
| R-C1 | `Read` `understanding_projection.py`（全 372 行） | 全文 | `_stable_id`/`_classify`/`_build_evidence`/`project_record`/`_detect_conflicts`/`project_records`（P0-R1~R6） |
| R-C2 | `Read` `parser_contract.py`（全 130 行） | 全文 | `RESOLUTION_STATUSES` 成员、`adapt_graph_v1`/`validate_record` 语义（P0-R6） |
| R-C3 | `Read` `understanding_model.py`（区间 30–35、216–235、603） | 区间 | `ConclusionType` 枚举值、`EvidenceReference.__post_init__` 硬阻断、`CONFLICT` 用法（P0-R5/R6） |
| R-C4 | `Read` `schema_adapter.py`（区间 16、149） | 区间 | `NON_SUCCESS_STATES` 成员（P0-R6） |
| R-C5 | `grep -rn "CONSTRUCT_FAIL|UNEXPECTED_ERROR|N_construct_fail|N_unexpected_error|N_not_attempted" usit/` | `usit/` | 确认源码**零命中**（P0-R4/R5） |
| R-C6 | `grep -n "RESOLUTION_STATUSES" parser_contract.py:11-12` | 区间 | 确认 6 值无 `CONFLICT`（P0-R6） |
| R-C7 | `Read` `reports/USIT-S1-C-DESIGN-REVIEW-08-执行报告.md`（全 437 行） | 全文 | 基准设计规格与待修正矛盾点（P0-R1~R9 全部） |

### §0.1 源码实测事实（R1~R6 锚定）

| # | 事实 | 定位依据 | 可信 |
|---|---|---|---|
| F-1 | `project_record` 对非 Mapping 记录（`:158` `if not isinstance(record, Mapping)`）**提前返回**直接构造 UNKNOWN `ProjectedItem`（`:159-173`），**不调用** `_build_evidence` | `understanding_projection.py:158-173` | 代码实测 |
| F-2 | `_build_evidence`（`:122-126`）仅在记录是 Mapping 且 `evidence` 非 Mapping/空时返回 `None`（→ EVIDENCE_NONE）；非 Mapping 记录根本不进入此函数 | `understanding_projection.py:122-126` + F-1 | 代码实测 |
| F-3 | `ConclusionType` 枚举仅 5 值：`VERIFIED_FACT/STATIC_INFERENCE/AI_HYPOTHESIS/UNKNOWN/CONFLICT`（`:30-35`）；**枚举中无** `CONSTRUCT_FAIL`/`UNEXPECTED_ERROR` | `understanding_model.py:30-35` | 代码实测 |
| F-4 | `grep` 在 `usit/` 中**零命中** `CONSTRUCT_FAIL`/`UNEXPECTED_ERROR`/`N_construct_fail`/`N_unexpected_error`/`N_not_attempted` | R-C5（零命中） | 代码实测（grep） |
| F-5 | `project_records` stats（`:361-370`）仅含 `total_records/projected_items/static_inference/unknown/conflict/ai_hypothesis/verified_fact/success_counted`，**无** `N_construct_fail`/`N_unexpected_error`/`N_not_attempted` 键 | `understanding_projection.py:361-370` | 代码实测 |
| F-6 | `_detect_conflicts`（`:251-298`）产出**独立** `ProjectedItem`，其 `resolution_status="CONFLICT"`（`:282`）、`conclusion_type=ConclusionType.CONFLICT`（`:283`） | `understanding_projection.py:282-283` | 代码实测 |
| F-7 | `project_records`（`:358-359`）：`items=[project_record(r) for r in records]` 后 `items.extend(_detect_conflicts(items, records))`；`stats["projected_items"]=len(items)` → `proj = Nr + cf` 恒成立 | `understanding_projection.py:358-359, 363` | 代码实测 |
| F-8 | `RESOLUTION_STATUSES`（`:11-12`）= `{RESOLVED,UNRESOLVED,AMBIGUOUS,DYNAMIC,EXTERNAL,UNKNOWN}`（6 值，**无 CONFLICT**） | `parser_contract.py:11-12` | 代码实测 |
| F-9 | `NON_SUCCESS_STATES`（`:16`）= `{UNRESOLVED,AMBIGUOUS,DYNAMIC,EXTERNAL,UNKNOWN}`（**无 CONFLICT**） | `schema_adapter.py:16` | 代码实测 |
| F-10 | `adapt_graph_v1`（`:94-125`）恒产出 Mapping 记录；`evidence = item.get("evidence") or {}`（`:100`），`evidence or {"graph_node_id": id}`（`:121`）仅替换假值 | `parser_contract.py:94-125` | 代码实测 |
| F-11 | `project_records` **不计算** `file_conclusion` 与分支编号；其输出仅为 `items` + `stats`（见 F-5/F-7）。`file_conclusion`/分支逻辑来自 REVIEW-07 §4 决策伪代码（**设计规格层，非源码实现**） | `understanding_projection.py:348-371`（无 file_conclusion 字段） | 代码实测 |

> **三种状态声明（强制区分）**：
> - **设计已修订**：✅ 本报告（REVIEW-09）完成。
> - **源码已实现**：❌ 否。S1-C 未实施；`understanding_projection` 仍仅孤立存在（BLOCKER-1）。
> - **测试已执行**：❌ 否。本工单 READ-ONLY，未运行任何实现或测试；下文统计口径/决策表/示例均为**设计规格**，待授权实施后验证。

---

## 1. P0-R1｜N_* 七个计数定义逐行核对

REVIEW-08 §2.1（line 144–151）与 §3.1（line 204–210）给出的 7 个计数定义，**逐行复核其与源码的对应关系**：

| 计数 | REVIEW-08 定义 | 源码对应 | 复核结论 |
|---|---|---|---|
| `N_candidate` | 非空 Mapping evidence 记录 | `has_evidence_candidate = isinstance(evidence, Mapping) and bool(evidence)`（与 `_build_evidence` 返回有效引用条件一致，F-2） | ✅ 与源码一致 |
| `N_non_candidate` | evidence 缺失/空/非 Mapping（如字符串）→ `_build_evidence` 返回 None | ⚠️ **定义层级混淆**（见 §2） | ❌ 须修正 |
| `N_construct_ok` | 候选且 `_build_evidence` 成功返回 `EvidenceReference` | `project_record` 对候选记录调 `_build_evidence` 并返回引用（F-2） | ✅ 与源码一致 |
| `N_construct_fail` | 候选但抛错被捕获计数 | ❌ 源码**无**捕获/计数机制（F-3/F-4/F-5） | ❌ 标注为拟议设计态（§4） |
| `N_evidence_none` | 非候选且 `_build_evidence` 返回 None | `_build_evidence` 对证据非候选返回 None（F-2） | ✅ 与源码一致 |
| `N_not_attempted` | 记录被跳过/过滤/未执行构造 | 仅非 Mapping 记录走 `:158-173` 提前返回（F-1）；源码**无**该计数键（F-5） | ⚠️ 概念正确但**非源码计数**（设计分类） |
| `N_unexpected_error` | 未分类异常 | ❌ 源码**无**捕获/计数机制（F-3/F-4/F-5） | ❌ 标注为拟议设计态（§4） |

**P0-R1 结论：PASS（带修正）**。7 个定义的层级关系已厘清；其中 `N_non_candidate` 的 REVIEW-08 措辞需按 §2 修正，`N_construct_fail`/`N_unexpected_error` 按 §4 重写为拟议设计态。

---

## 2. P0-R2 / P0-R3｜三态互斥穷尽分类 + 恒等式重写（核心修正）

### 2.1 两级"非候选"必须严格区分（P0-R2）

REVIEW-08 §2.1 line 146 原文：

> "非候选记录（`N_non_candidate`）：evidence 缺失 / 空 / **非 Mapping（如字符串）** → `_build_evidence` 返回 `None`"

该句把**两种不同层级**混为一谈：

1. **记录级非 Mapping**（如整条记录本身是字符串/列表，不是 dict）→ `project_record` 在 `:158-173` **提前返回**，**根本不调用 `_build_evidence`**（F-1）。这属于 **`NOT_ATTEMPTED`**，与 `N_evidence_none` **互斥**，绝不能计入 `N_evidence_none`，也绝不能计入 `N_non_candidate`（若 `N_non_candidate` 被定义为"mapping-without-candidate"）。
2. **evidence 级非 Mapping**（记录本身是 Mapping，但其 `evidence` 字段是字符串/空/非 Mapping）→ 记录进入 `_classify`+`_build_evidence` 正常路径（`:175-205`），`_build_evidence` 在 `:122-126` 返回 `None` → 属于 **`EVIDENCE_NONE`** / `N_non_candidate`。

REVIEW-08 的"非 Mapping（如字符串）"示例若指代"记录本身是字符串"，则错置到 `N_non_candidate`；若指代"evidence 字段是字符串"，则正确但表述模糊。**本工单明确**：
- 记录级非 Mapping → **`NOT_ATTEMPTED`**（不调 `_build_evidence`，与 `N_evidence_none` 互斥，不得双计）。
- evidence 级非 Mapping → **`EVIDENCE_NONE`**。
- **禁止**把同一非 Mapping 记录既计入 `N_evidence_none` 又计入 `N_not_attempted`。

### 2.2 互斥且穷尽的 5 态逐记录分类（P0-R3）

对 `project_records(records)` 中每条记录 `r`，按代码路径**恰好归入一态**：

| 终态 | 定义 | 触发代码路径 | 当前是否源码计数 |
|---|---|---|---|
| `NOT_ATTEMPTED` | 记录非 Mapping，提前返回，未进入构造 | `:158-173`（F-1） | 设计分类（源码无键，F-5） |
| `CONSTRUCT_OK` | 记录是候选（Mapping+非空 Mapping evidence），`_build_evidence` 返回引用 | `:180, 134-149`（F-2） | 由 `static_inference`/`success_counted` 间接体现（F-5） |
| `CONSTRUCT_FAIL` | 候选但构造抛异常且被显式捕获计数 | **不存在**（F-3/F-4/F-5） | ❌ 拟议设计态 |
| `EVIDENCE_NONE` | 记录是 Mapping 但 evidence 非候选，`_build_evidence` 返回 None | `:122-126`（F-2） | 由 `unknown` 间接体现（F-5） |
| `UNEXPECTED_ERROR` | 未预见异常兜底 | **不存在**（F-3/F-4/F-5） | ❌ 拟议设计态 |

- **互斥**：非 Mapping → `NOT_ATTEMPTED`（不进构造）；Mapping → `CONSTRUCT_OK` 或 `EVIDENCE_NONE`（由 evidence 是否候选互斥决定）；`CONSTRUCT_FAIL`/`UNEXPECTED_ERROR` 仅在未来异常捕获路径中存在，不与前三者共存。
- **穷尽**：在标准链路（scanner→`adapt_graph_v1`→`project_records`）中，所有记录均为 Mapping（F-10），故每条归入 `CONSTRUCT_OK` 或 `EVIDENCE_NONE`，`NOT_ATTEMPTED=0`；若调用方直接传入非 Mapping 记录，则归入 `NOT_ATTEMPTED`。

### 2.3 重写恒等式（消除 REVIEW-08 §2.1/§2.3 的无条件等价）

REVIEW-08 §2.1 line 151 与 §2.3 line 185 写 "`Nnc == Nnone`（恒等式）" 且 §2.3 line 186 写 "`Nr == Nok + Nfail + Nnone`"。这两式**隐含前提** `N_not_attempted == 0`，但 REVIEW-08 未显式声明，且 `N_non_candidate = N_raw - N_candidate` 的**残差写法**（§4 line 261）会在存在非 Mapping 记录时把 `NOT_ATTEMPTED` **吸收进** `N_non_candidate`，从而破坏 `N_non_candidate == N_evidence_none`。

**正确恒等式（按本工单严格定义）：**

```
(1) N_raw = N_candidate + N_non_candidate + N_not_attempted + N_unexpected_error
            └─ 穷尽：每条记录是 候选 / mapping-非候选 / 非Mapping记录 / 未预见异常 之一
(2) N_candidate = N_construct_ok + N_construct_fail
(3) N_non_candidate = N_evidence_none        ← 严格定义下恒等（两者都只计 mapping-without-candidate）
(4) （简化式，仅当 N_not_attempted == 0 且 N_unexpected_error == 0 时成立）
    N_raw = N_construct_ok + N_construct_fail + N_evidence_none
```

**关键红线（P0-R2 落点）：**
- `N_non_candidate` **严格定义**为"Mapping 记录且 evidence 非候选"，**不得**用 `N_raw - N_candidate` 残差写法（后者会吞并 `NOT_ATTEMPTED`）。
- 在严格定义下，`N_non_candidate == N_evidence_none` **恒成立且无需附加前提**；但若沿用残差定义，则**不得无条件宣称二者相等**。
- 简化式 (4) **仅当** `N_not_attempted == 0` 且 `N_unexpected_error == 0`（标准链路）成立；**不得**在未声明前提时无条件使用 §2.3 line 186 的写法。

**P0-R2 / P0-R3 结论：PASS（已修正恒等式并消除双计风险）**。

---

## 3. P0-R4｜信息不足标 NOT_AVAILABLE，禁止虚构

按工单要求，凡源码无足够信息计算的项，**标 `NOT_AVAILABLE`，不得为维持恒等式虚构计数**：

| 项 | 源码是否有机制 | 本工单标法 |
|---|---|---|
| `N_construct_fail` | 无（F-3/F-4/F-5） | `NOT_AVAILABLE`（拟议设计态） |
| `N_unexpected_error` | 无（F-3/F-4/F-5） | `NOT_AVAILABLE`（拟议设计态） |
| `N_not_attempted` | 无键，但可由"记录是否 Mapping"推导 | 标准链路下由适配契约（F-10）推导为 `0`；非标准链路标 `NOT_AVAILABLE` |
| `N_no_evidence`（pre-adapt 空） | 无字段保留（REVIEW-08 I'-14） | `NOT_AVAILABLE`（不进入任何恒等式） |
| `duplicate_records` 跨扫描稳定性 | `_stable_id` 内容派生，跨扫描漂移未定义 | 单扫描内成立；跨扫描标 `UNRESOLVED`（见 §6） |

> **禁止虚构**：若某恒等式在仅用"已实现/可推导"的状态计数时无法闭合（例如未来出现 `CONSTRUCT_FAIL` 被实测但无计数键），必须**标 `NOT_AVAILABLE` 并暴露缺口**，不得用虚构的 `N_fail=0` 强行闭合。

**P0-R4 结论：PASS（已逐项标注 NOT_AVAILABLE / 拟议态）**。

---

## 4. P0-R5｜CONSTRUCT_FAIL / UNEXPECTED_ERROR 实际捕获机制核查

REVIEW-08 §3.1 line 207/210 将 `CONSTRUCT_FAIL` 标"≈0"、`UNEXPECTED_ERROR` 标"=0（保留兜底）"——**措辞易误读为已实现部分覆盖**。本工单实测核查：

- `understanding_model.py:30-35` 的 `ConclusionType` 枚举**仅 5 值**，无 `CONSTRUCT_FAIL`/`UNEXPECTED_ERROR`（F-3）。
- `grep` 在 `usit/` 中**零命中** `CONSTRUCT_FAIL`/`UNEXPECTED_ERROR`/`N_construct_fail`/`N_unexpected_error`（F-4）。
- `project_records` 的 `stats`（`:361-370`）**无** `N_construct_fail`/`N_unexpected_error`/`N_not_attempted` 键（F-5）。
- `_build_evidence`（`:122-149`）与 `project_record`（`:152-248`）**均无 `try/except`** 包裹构造调用；`EvidenceReference.__post_init__`（`:216-235`）会在传入非法 trust 声明时 `raise`，但当前 `project_record` 恒传安全值（`:145-146`），故既不抛错也不被捕获计数。

**结论**：`CONSTRUCT_FAIL` 与 `UNEXPECTED_ERROR` **当前无任何实际捕获与计数机制**，属于**拟议设计终态**，而非"当前 ≈0 / 当前 =0（保留兜底）"的实现覆盖。**不得在报告中写成当前实现已覆盖**；正确标注为 `NOT_AVAILABLE`（拟议设计态），待未来实现 `try/except` 捕获 + 计数键后方可实测。

**P0-R5 结论：PASS（已纠正措辞，明确为拟议设计终态）**。

---

## 5. P0-R6｜C10 / C21 冲突样例证据复核

### 5.1 CONFLICT 语义澄清（独立条目 vs resolution_status）

- **CONFLICT 是 `_detect_conflicts` 产出的独立 `ProjectedItem`**（F-6），其 `resolution_status="CONFLICT"`（`:282`）、`conclusion_type=ConclusionType.CONFLICT`（`:283`，枚举值存在于 `understanding_model.py:35`，亦用于 `:603`）。
- **它不是 parser-contract 的 `resolution_status`**：`RESOLUTION_STATUSES`（`:11-12`）6 值中**无 CONFLICT**（F-8）；`NON_SUCCESS_STATES`（`:16`）亦**无 CONFLICT**（F-9）。
- 冲突条目**不修改**原始 parser-contract 记录的 `resolution_status`（原始记录仍为 `RESOLVED`/`NON_SUCCESS` 等）；`resolution_status="CONFLICT"` 仅作为**投影层合成的局部标记**存在于该独立条目上。
- 设计层 `has_conflict`（REVIEW-07 §4）由 `conflict_items > 0` 派生，属**设计规格**，非源码 `project_records` 输出（F-11）。

### 5.2 C10 两条输入记录逐字段复核

**输入（C10，`understanding_projection` 视角，2 条 Mapping 记录，同身份异内容）**：
- `R_a`：`resolution_status="RESOLVED"`，`evidence`=非空 Mapping（非 `{"graph_node_id": id}` 假值），`source_location.file_path="f"`，`qualified_name="X"`。
- `R_b`：`resolution_status="NON_SUCCESS"`（如 `UNKNOWN`），`evidence`=非空 Mapping，`source_location.file_path="f"`，`qualified_name="X"`。

**逐步演算**：
1. `project_record(R_a)`（`:175-248`）：`_classify` → `RESOLVED`∉`NON_SUCCESS` 且 evidence 候选 → `STATIC_INFERENCE`（`:119`）；`_build_evidence`→引用（`:134-149`）→ `CONSTRUCT_OK`。
2. `project_record(R_b)`：`_classify` → `NON_SUCCESS` → `UNKNOWN`（`:109-110`）；`_build_evidence`→引用 → `CONSTRUCT_OK`。
3. `_detect_conflicts`（`:251-298`）：key = `(f, X)`；`R_a` fingerprint = `{status:"RESOLVED", reasons:[...], symbol:"X"}`；`R_b` fingerprint = `{status:"NON_SUCCESS", reasons:[...], symbol:"X"}`；`len(entries)=2 > 1` 且 `distinct=2 > 1`（`:276`）→ 产出 **1 个冲突条目**（`:278-297`）。`cf=1`。
4. `project_records`（`:358-359, 363`）：`items = [proj(R_a), proj(R_b)] + [conflict]` → `proj = 2 + 1 = 3`。
5. **`projected_items = raw_records + conflict_items` 对 C10 适用**：`proj(3) == Nr(2) + cf(1)` ✅（F-7）。

**文件结论（设计规格层，F-11）**：`all_non_success`？`R_a` 为 `RESOLVED` → `False` → 分支 2 不命中；`has_conflict=True` 且 `meaningful_count==0` → 分支 3 命中 → `file_conclusion = CONFLICT_ONLY`。

**C10 计数**：`Nr=2, Nc=2, Nnc=0, Nok=2, Nfail=NA(拟议), Nnone=0, Nna=0, Neu=NA(拟议), dup=0, cf=1, proj=3, 分支3, CONFLICT_ONLY`。

### 5.3 C21 两条输入记录逐字段复核

**输入（C21，2 条 Mapping 记录，均 NON_SUCCESS，同身份异内容）**：
- `R_a`：`resolution_status="NON_SUCCESS"`，`evidence`=非空 Mapping，`file_path="f"`，`qualified_name="X"`。
- `R_b`：`resolution_status="NON_SUCCESS"`（不同内容，如 `reasons` 不同），`evidence`=非空 Mapping，`file_path="f"`，`qualified_name="X"`。

**逐步演算**：
1. `project_record(R_a/R_b)`：`_classify`→`NON_SUCCESS`→`UNKNOWN`；`_build_evidence`→引用 → 均 `CONSTRUCT_OK`。`Nc=2, Nnc=0, Nok=2`。
2. `_detect_conflicts`：key=`(f, X)`，fingerprint 异 → **1 个冲突条目**。`cf=1`。
3. `project_records`：`proj = 2 + 1 = 3`（F-7）✅。
4. **分支优先级（设计规格层，F-11）**：分支 2（`all_non_success or all_evidence_empty`）优先级 **高于** 分支 3（`has_conflict ∧ ¬meaningful`）。C21 中 `all_non_success=True` → **分支 2 先命中** → `file_conclusion = UNKNOWN_RESOLUTION`。

**关键结论（工单 P0-R6.5）**：C21 的"分支 2 优先于冲突分支"**仅影响 `file_conclusion`**，不影响冲突条目计数——`_detect_conflicts` 仍独立追加 1 个冲突条目（`cf=1, proj=3`）。源码 `project_records`（`:359`）无条件 `extend` 冲突条目，与文件结论分支选择**解耦**。

**C21 计数**：`Nr=2, Nc=2, Nnc=0, Nok=2, Nfail=NA(拟议), Nnone=0, Nna=0, Neu=NA(拟议), dup=0, cf=1, proj=3, 分支2, UNKNOWN_RESOLUTION`。

### 5.4 `projected_items = raw_records + conflict_items` 对两样例适用性

两样例均满足 `proj == Nr + cf`（F-7 恒成立），**适用** ✅。需注意：若未来实现改为"去重/合并冲突"，该式失效（见 §7 失效场景）。

**P0-R6 结论：PASS（CONFLICT 语义已厘清，C10/C21 身份字段/冲突条件/条目数已逐字段复核，分支优先级影响范围已确认）**。

---

## 6. P1-R7｜C5/C6/B1a/B1b 输入定义 + duplicate_records 精确定义 + _stable_id 行为

### 6.1 C5 / C6 / B1a / B1b 完整输入定义与适配路径

此四类均为**零记录**场景（`records=[]` 或文件无符号），属**文件级分支**（REVIEW-07 §4 分支 0/1），**非逐记录构造**：

| 样例 | 输入定义 | 适配路径（REVIEW-08 I'-8 / F-10） | 逐记录计数 | 文件结论（设计规格） |
|---|---|---|---|---|
| C4 | `size_bytes==0`（空文件） | 无记录 | 全 0 / 不适用 | `NO_SEMANTIC_CONTENT`（分支 0） |
| C5 / B1a | 0 记录、无符号 | `records=[]`，`adapt_graph_v1` 不执行 | 全 0 / 不适用 | `NO_RECORDS(no_symbols)`（分支 1） |
| C6 / B1b | 0 记录、有符号无记录 | `records=[]`（符号信息在别处，无 parser-contract 记录） | 全 0 / 不适用 | `NO_RECORDS(symbols_no_records)`（分支 1） |

> **标注**：零记录场景下 `N_candidate/N_non_candidate/N_evidence_none/N_not_attempted` 均为 **0（无记录可处理）**，但 `evidence` 状态对该场景**不适用（NOT_APPLICABLE）**，不得据此推断"evidence 全空"。缺 evidence 适配输入的信息（如预适配空证据是否透传）标 `NOT_AVAILABLE`（P0-R4，同 `N_no_evidence`）。

### 6.2 duplicate_records 精确定义（区分"相同投影 ID 多余条数"与"业务重复"）

- **定义**：`duplicate_records = raw_records − unique_projection_ids_raw`，其中 `unique_projection_ids_raw` = 对原始记录集合去重后的不同 `_stable_id` 数（F-10/`_stable_id` 内容派生，`understanding_projection.py:74-80`）。
- **语义边界**：
  - 它**仅衡量"相同投影 ID 的多余条数"**（内容完全相同导致的冗余）。
  - **不得仅凭 ID 相同断言"业务重复"**：内容相同的两条记录可能是同一符号在两个调用点的合法独立引用；`_stable_id` 纯内容 sha256、无 index（`:74-80`），无法区分"业务去重"与"内容巧合相同"。
- **`_stable_id` 行为复核**：
  - 重复内容（相同 content）→ 相同 `_stable_id` → 计入 `duplicate_records`（如 C16：2×RESOLVED 完全相同 → `dup=1`）。
  - 不同语义角色但内容相同 → 仍产生相同 `_stable_id`（因无 index/语义键），会被计为 duplicate，但**未必是业务重复**。
  - 跨扫描：同内容 → 同 `_stable_id`（确定性），故**单扫描内** `duplicate_records` 稳定；**跨扫描**内容/标识漂移未定义，标 `UNRESOLVED`（REVIEW-08 §4 line 267，本工单维持）。

**P1-R7 结论：PASS（C5/C6/B1a/B1b 输入与适配路径已补全并标 NOT_APPLICABLE；duplicate_records 精确定义与 _stable_id 行为已厘清，禁止仅凭 ID 断言业务重复）**。

---

## 7. P1-R8｜恒等式适用范围 / 前置条件 / 失效场景更新

| 恒等式 | 适用范围 | 前置条件 | 失效场景 |
|---|---|---|---|
| `N_raw = N_candidate + N_non_candidate + N_not_attempted + N_unexpected_error` | 全部适配后原始记录 | 每条记录恰好归入一态（§2.2 穷尽性） | 记录被静默丢弃/截断 → `N_raw` 偏小 |
| `N_candidate = N_construct_ok + N_construct_fail` | 候选子集 | 候选记录存在；`N_construct_fail` 已实现计数 | `N_construct_fail` 未实现 → 该式右侧 `NOT_AVAILABLE`（拟议） |
| `N_non_candidate = N_evidence_none` | mapping-without-candidate 子集 | `N_non_candidate` **严格定义**（不得用残差） | 若 `N_non_candidate` 沿用残差 `N_raw − N_candidate`，则吸收 `NOT_ATTEMPTED` → 等式破坏（§2.3 红线） |
| `N_raw = N_construct_ok + N_construct_fail + N_evidence_none`（简化式） | 标准链路 | `N_not_attempted == 0` 且 `N_unexpected_error == 0` | 非 Mapping 记录入参（`N_not_attempted>0`）或异常未捕获 → 简化式不成立 |
| `proj = Nr + cf` | `project_records` 输出 | `_detect_conflicts` 被调用且 `extend` 追加（F-7） | 若实现改为去重/合并冲突条目 → 失效 |
| `dup = raw − unique_projection_ids_raw` | 投影输入（单扫描） | 内容派生稳定（`:74-80`） | 跨扫描内容/标识漂移 → `UNRESOLVED`（§6.2） |

**P1-R8 结论：PASS（已为每条恒等式补全适用范围/前置/失效，并消除 REVIEW-08 的无前提等价写法）**。

---

## 8. P1-R9｜C1–C21 重算计数表（含 N_not_attempted / N_unexpected_error / 设计态标注）

**表头缩写**：`Nr`=N_raw，`Nc`=N_candidate，`Nnc`=N_non_candidate，`Nok`=N_construct_ok，`Nfail`=N_construct_fail，`Nnone`=N_evidence_none，`Nna`=N_not_attempted，`Neu`=N_unexpected_error，`dup`=duplicate_records，`cf`=conflict_items，`proj`=projected_items。
**标注规则**：`NA(拟议)` = 源码无机制，拟议设计态（P0-R4/R5）；`0*` = 标准链路由适配契约（F-10）推导为 0；`[设计]` = `file_conclusion`/分支属设计规格层（F-11），非源码 `project_records` 输出。

| # | 输入（resolution / evidence） | Nr | Nc | Nnc | Nok | Nfail | Nnone | Nna | Neu | dup | cf | proj | 分支[设计] | file_conclusion[设计] |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| C1 | 1×RESOLVED / 非空 dict 真摘要 | 1 | 1 | 0 | 1 | NA(拟议) | 0 | 0* | NA(拟议) | 0 | 0 | 1 | 7 | INSUFFICIENT_EVIDENCE |
| C2 | 1×RESOLVED / 仅 `{graph_node_id}` | 1 | 1 | 0 | 1 | NA(拟议) | 0 | 0* | NA(拟议) | 0 | 0 | 1 | 7 | INSUFFICIENT_EVIDENCE |
| C3 | 1×NON_SUCCESS / fallback 非空 | 1 | 1 | 0 | 1 | NA(拟议) | 0 | 0* | NA(拟议) | 0 | 0 | 1 | 2 | UNKNOWN_RESOLUTION |
| C4 | 空文件（`size_bytes==0`） | 0 | 0 | 0 | 0 | NA(拟议) | 0 | 0* | NA(拟议) | 0 | 0 | 0 | 0 | NO_SEMANTIC_CONTENT |
| C5 | 0 记录、无符号 | 0 | 0 | 0 | 0 | NA(拟议) | 0 | 0* | NA(拟议) | 0 | 0 | 0 | 1 | NO_RECORDS(no_symbols) |
| C6 | 0 记录、有符号无记录 | 0 | 0 | 0 | 0 | NA(拟议) | 0 | 0* | NA(拟议) | 0 | 0 | 0 | 1 | NO_RECORDS(symbols_no_records) |
| C7 | 1×RESOLVED / 非空 dict + 文件降级 | 1 | 1 | 0 | 1 | NA(拟议) | 0 | 0* | NA(拟议) | 0 | 0 | 1 | 4 | DEGRADED_PARSE |
| C8 | 1×NON_SUCCESS / fallback + 文件降级 | 1 | 1 | 0 | 1 | NA(拟议) | 0 | 0* | NA(拟议) | 0 | 0 | 1 | 2 | UNKNOWN_RESOLUTION |
| C9 | 1×NON_SUCCESS / fallback 非空 | 1 | 1 | 0 | 1 | NA(拟议) | 0 | 0* | NA(拟议) | 0 | 0 | 1 | 2 | UNKNOWN_RESOLUTION |
| C10 | 1×RESOLVED + 1×NON_SUCCESS（同身份异内容→冲突） | 2 | 2 | 0 | 2 | NA(拟议) | 0 | 0* | NA(拟议) | 0 | 1 | 3 | 3 | CONFLICT_ONLY |
| C11 | 1×RESOLVED / 非空 dict + orphan | 1 | 1 | 0 | 1 | NA(拟议) | 0 | 0* | NA(拟议) | 0 | 0 | 1 | 7 | INSUFFICIENT_EVIDENCE |
| C12 | 1×RESOLVED / 非空 dict + diagnostics 越界 | 1 | 1 | 0 | 1 | NA(拟议) | 0 | 0* | NA(拟议) | 0 | 0 | 1 | 7 | INSUFFICIENT_EVIDENCE |
| C13 | 1×RESOLVED / 非空 dict + path∉manifest | 1 | 1 | 0 | 1 | NA(拟议) | 0 | 0* | NA(拟议) | 0 | 0 | 1 | 7 | INSUFFICIENT_EVIDENCE |
| C14 | 1×RESOLVED / 非空 dict + line 非正整数 | 1 | 1 | 0 | 1 | NA(拟议) | 0 | 0* | NA(拟议) | 0 | 0 | 1 | 7 | INSUFFICIENT_EVIDENCE |
| C15 | 1×RESOLVED / 非空 dict + TestSpec 缺必填 | 1 | 1 | 0 | 1 | NA(拟议) | 0 | 0* | NA(拟议) | 0 | 0 | 1 | 7 | INSUFFICIENT_EVIDENCE |
| C16 | 2×RESOLVED 完全相同（重复） | 2 | 2 | 0 | 2 | NA(拟议) | 0 | 0* | NA(拟议) | 1 | 0 | 2 | 7 | INSUFFICIENT_EVIDENCE |
| C17 | 1×NON_SUCCESS + 1×RESOLVED（混合） | 2 | 2 | 0 | 2 | NA(拟议) | 0 | 0* | NA(拟议) | 0 | 0 | 2 | 7 | INSUFFICIENT_EVIDENCE |
| C18 | 1×RESOLVED / evidence 非 Mapping（单文件） | 1 | 0 | 1 | 0 | NA(拟议) | 1 | 0* | NA(拟议) | 0 | 0 | 1 | 2 | UNKNOWN_RESOLUTION |
| C19 | 1×RESOLVED 非 Mapping + 1×NON_SUCCESS 候选 | 2 | 1 | 1 | 1 | NA(拟议) | 1 | 0* | NA(拟议) | 0 | 0 | 2 | 8 | UNKNOWN |
| C20 | 2×NON_SUCCESS / fallback + 文件降级 | 2 | 2 | 0 | 2 | NA(拟议) | 0 | 0* | NA(拟议) | 0 | 0 | 2 | 2 | UNKNOWN_RESOLUTION |
| C21 | 2×NON_SUCCESS（同身份异内容→冲突） | 2 | 2 | 0 | 2 | NA(拟议) | 0 | 0* | NA(拟议) | 0 | 1 | 3 | 2 | UNKNOWN_RESOLUTION |

**逐行恒等式校验（基于本表，严格定义）**：
- `Nr == Nc + Nnc + Nna`（Nna=0* 由 F-10 推导）：全部满足 ✅。
- `Nnc == Nnone`（严格定义，不含残差吸收）：全部满足 ✅。
- `Nc == Nok + Nfail`：当前 `Nok == Nc`，`Nfail=NA(拟议)` → 在"拟议 Nfail=0"假设下成立；**源码未实测 Nfail**，故标 NA 不虚构 ✅。
- `proj == Nr + cf`：C10/C21 `proj=3 == 2+1`；其余 `proj==Nr`（cf=0）✅（F-7 恒成立）。
- `dup` 仅 C16=1，其余 0 ✅。

> **不可验证项一律标 `NOT_AVAILABLE`（拟议）而非 0**：`Nfail`/`Neu` 在全部 21 样例均标 `NA(拟议)`，未假设为 0（P0-R4/R5）。`file_conclusion`/分支列明确标注 `[设计]`，与源码 `project_records` 输出（items/stats）严格区分（F-11）。

**P1-R9 结论：PASS（C1–C21 全表重算，新增 Nna/Neu 列，区分源码实测计数与设计规格列，缺失项标 NA 未虚构）**。

---

## 9. 问题清单（按 P0 / P1 / P2 分级）

| 级别 | 编号 | 问题（REVIEW-08 缺陷） | REVIEW-09 处置 |
|---|---|---|---|
| **P0** | P0-1 | §2.1 line 146 把"记录级非 Mapping（如字符串）"错置到 `N_non_candidate`/`N_evidence_none`，混淆记录级与 evidence 级非 Mapping | **已修正**（§2.1）：记录级非 Mapping → `NOT_ATTEMPTED`（F-1），与 `N_evidence_none` 互斥，不得双计 |
| **P0** | P0-2 | §2.1 line 151 / §2.3 line 185/186 无条件写 `Nnc == Nnone` 与 `Nr == Nok+Nfail+Nnone`，隐含 `N_not_attempted==0` 未声明；`N_non_candidate = N_raw − N_candidate` 残差写法会吸收 `NOT_ATTEMPTED` | **已修正**（§2.3）：重写为 5 态完整恒等式 + 严格定义 `N_non_candidate`，消除残差吸收 |
| **P0** | P0-3 | §3.1 line 207/210 将 `CONSTRUCT_FAIL`/"UNEXPECTED_ERROR" 标"≈0"/"=0（保留兜底）"，易误读为已实现覆盖 | **已修正**（§4）：源码零机制（F-3/F-4/F-5）→ 标 `NOT_AVAILABLE`（拟议设计态），非已实现 |
| **P0** | P0-4 | §2.2 表将设计规格列（分支/file_conclusion）与源码实测列（Nr/Nc/Nnc/Nok/Nnone/dup/cf/proj）混排未标注 | **已修正**（§5/§8）：`file_conclusion`/分支明确标 `[设计]`（F-11），`Nfail`/`Neu` 标 `NA(拟议)` |
| **P0** | P0-5 | §2.2/§5 未显式区分"CONFLICT 是独立冲突条目"与"parser-contract resolution_status" | **已修正**（§5.1）：CONFLICT 为 `_detect_conflicts` 独立合成条目（F-6），非 `RESOLUTION_STATUSES` 成员（F-8/F-9） |
| **P1** | P1-1 | §2.2 未补全 C5/C6/B1a/B1b 零记录场景的 evidence 状态标注（易误推"evidence 全空"） | **已修正**（§6.1）：零记录场景 evidence 状态 `NOT_APPLICABLE`，缺输入标 `NOT_AVAILABLE` |
| **P1** | P1-2 | §4 未强调 `duplicate_records` 仅计"相同 ID 多余条数"，未禁止仅凭 ID 断言业务重复；跨扫描稳定性标注不足 | **已修正**（§6.2）：精确定义 + `_stable_id` 行为 + 禁止仅凭 ID 断言业务重复；跨扫描 `UNRESOLVED` |
| **P1** | P1-3 | §3.4/§4 恒等式未逐项给适用范围/前置/失效 | **已修正**（§7）：逐条恒等式补适用范围/前置/失效场景 |
| **P2** | P2-1 | 样例集冗余：C3/C9、C8/C20 描述同一逻辑场景（REVIEW-08 P2-2 已披露） | 维持披露（非阻断）：建议实施期合并演示样例 |
| **P2** | P2-2 | `has_conflict` 派生规则（conflict_items>0）属设计决策；`CONFLICT ∉ NON_SUCCESS` 影响分支优先级（REVIEW-08 P2-2 已披露） | 维持披露（非阻断）：C10/C21 已用具体构造固化使分支结论成立 |
| **P2** | P2-3 | `N_no_evidence`（pre-adapt 空）当前 `NOT_AVAILABLE`（REVIEW-08 P2-3 已披露） | 维持披露（非阻断）：不进入任何恒等式 |

> 所有 P0/P1 问题已在本次报告中**修正**；P2 为已明确披露的非阻断观察，不影响核心计数一致性与冲突证据。

---

## 10. PASS / FAIL / 未解决矩阵（设计验收，非实现验收）

| 项 | 状态 |
|---|---|
| P0-R1 七个 N_* 定义逐行核对 | ✅ PASS |
| P0-R2 非 Mapping 记录提前返回不双计 + 三态区分 | ✅ PASS（已修正） |
| P0-R3 互斥穷尽 5 态分类 + 恒等式重写 | ✅ PASS（已修正） |
| P0-R4 信息不足标 NOT_AVAILABLE / 不虚构 | ✅ PASS |
| P0-R5 CONSTRUCT_FAIL/UNEXPECTED_ERROR 无机制 → 拟议设计态 | ✅ PASS（已纠正措辞） |
| P0-R6 C10/C21 冲突证据复核 + CONFLICT 语义 + 分支优先级 | ✅ PASS |
| P1-R7 C5/C6/B1a/B1b + duplicate_records + _stable_id 行为 | ✅ PASS |
| P1-R8 恒等式适用范围/前置/失效更新 | ✅ PASS |
| P1-R9 C1–C21 重算计数表（含 Nna/Neu/设计态标注） | ✅ PASS |
| 源码契约证据（§0 F-1~F-11，独立 grep + 区间阅读复核） | ✅ 代码实测坐实 |
| **源码已实现** | ❌ **否**（S1-C 未实施，BLOCKER-1 未接线） |
| **测试已执行（本次）** | ❌ **否**（READ-ONLY，未运行任何实现/测试） |
| 历史测试证据引用 | ⚠️ 仅引 REVIEW-04~08 来源，已标范围，非本次执行 |

---

## 11. 文件变更清单及证据

| 类别 | 变更 |
|---|---|
| **源码变更** | **0**（未修改 `understanding_projection.py` / `parser_contract.py` / `understanding_model.py` / `schema_adapter.py` / `cli.py` / `scanner.py` 等任何文件） |
| **既有产物变更** | **0**（未修改 manifest / 图谱 / 原 `test_plan.json` / 任何 REVIEW-04~08 报告 / 配置） |
| **新增文件** | **仅** `reports/USIT-S1-C-DESIGN-REVIEW-09-执行报告.md`（本报告） |
| **测试执行** | `NOT_RUN`（本工单不授权执行测试） |

> 变更证据：本工单全程仅对既有文件执行 `Read` / `grep`（只读），唯一写操作为新增本报告。可用下列命令供人工复核（未在本工单内执行写操作）：
> - `git -C "C:\Users\Administrator\WorkBuddy\2026-10-15-22-09-47\usit_p0" status --porcelain`
> - 预期输出仅含新增的 `reports/USIT-S1-C-DESIGN-REVIEW-09-执行报告.md`（若仓库已纳入 git 跟踪）。

---

## 12. 固定门禁与最终 STOP 声明

### 12.1 固定门禁值（原值保持，全程未变）
| 门禁 | 值 |
|---|---|
| `F1` | `NOT_READY_TO_CLOSE` |
| `P0.8` | `HOLD` |
| `F2` | `STOP` |
| `历史授权连续性` | `UNRESOLVED` |
| `安全信任链` | `BLOCKED / NOT_VERIFIED` |

### 12.2 最终结论

1. **设计已修订（本次交付）**：REVIEW-09 在 REVIEW-08 基础上，独立复核并修正 P0-R1~R6 与 P1-R7~R9：① **P0-R1**——逐行核对 7 个 N_* 定义与源码对应关系；② **P0-R2/R3**——严格区分"记录级非 Mapping（NOT_ATTEMPTED，F-1 提前返回不调 `_build_evidence`）"与"evidence 级非 Mapping（EVIDENCE_NONE，F-2）"，定义互斥穷尽 5 态分类并重写恒等式（消除 `N_non_candidate` 残差吸收 `NOT_ATTEMPTED` 的风险）；③ **P0-R4**——信息不足项一律标 `NOT_AVAILABLE`/拟议态，不虚构计数；④ **P0-R5**——实测确认 `CONSTRUCT_FAIL`/`UNEXPECTED_ERROR` 在源码中**零机制**（F-3/F-4/F-5），明确为拟议设计终态，纠正 REVIEW-08 "≈0/保留兜底"措辞；⑤ **P0-R6**——厘清 CONFLICT 是 `_detect_conflicts` 独立合成条目（F-6）而非 parser-contract `resolution_status`（F-8/F-9），逐字段复核 C10/C21 身份字段/冲突条件/条目数，确认 `proj=Nr+cf` 两样例适用、C21 分支 2 优先仅影响 `file_conclusion` 不影响冲突计数；⑥ **P1-R7**——补全 C5/C6/B1a/B1b 零记录场景（evidence NOT_APPLICABLE）与 `duplicate_records` 精确定义（仅计相同 ID 多余条数，禁止仅凭 ID 断言业务重复）；⑦ **P1-R8**——为每条恒等式补适用范围/前置/失效；⑧ **P1-R9**——重算 C1–C21 全表，新增 `Nna`/`Neu` 列，区分源码实测计数与设计规格列（`[设计]`），缺失标 `NA(拟议)` 未虚构。
2. **源码已实现：否**。S1-C 未进入实施；`understanding_projection` 仍孤立未接线（BLOCKER-1，F-11）。
3. **测试已执行：否**。本工单严格 READ-ONLY，未运行任何实现或测试；所有决策表、统计、示例为**设计规格**，待授权实施后验证。
4. **硬约束全部维持**：`verified_fact=0`、`ExecutionRecord` 全 `NOT_RUN`、`summary_verification=NOT_CHECKED` 恒为真、受约束条目 `complete=False`；不放宽信任门禁；不把 `UNKNOWN/AMBIGUOUS/DYNAMIC/EXTERNAL/UNRESOLVED/CONFLICT` 升级为成功。
5. **验收评级**：**PASS（PASS-WITH-ISSUES 以下非阻断备注）**。R1–R9 全部有明确可复核结果；REVIEW-08 的计数口径矛盾（P0-1~P0-4）与冲突语义模糊（P0-5）已修正；C1–C21 重算表自洽且未虚构缺失事实；仅新增指定报告。
6. **本设计规格的"通过"≠ 源码实现通过或测试通过**（验收规则明示）。

### 12.3 当前 S1-C 状态
- **未实施** ❌、**未接线** ❌（BLOCKER-1）、**未测试** ❌。

### 12.4 STOP
> 仅提交设计修订，未修改任何文件（仅新增本报告）。等待人工审查与独立授权；不得自行进入 S1-C 实施，不得修改 `understanding_projection.py` / `parser_contract.py` / `cli.py`，不得解除 BLOCKER-1/2，不得执行任何实现、构建、安装、登录、网络访问或 Android 测试。

---

**执行边界最终声明**：本工单只授权只读设计复核和新增 REVIEW-09 报告。任何代码修改、接线、构建、安装、登录、网络访问或 Android 测试均超出授权范围。
