# USIT-S1-C-IMPLEMENTATION-READINESS-01｜实施前基线冻结与剩余阻断项确认（READ-ONLY）

- **工单编号**：USIT-S1-C-IMPLEMENTATION-READINESS-01
- **执行时间**：2026-10-20（GMT+8，续 Context compaction 后落地）
- **工作区绝对路径**：`C:\Users\Administrator\WorkBuddy\2026-10-15-22-09-47\usit_p0`
- **执行模式**：**严格只读** —— 仅读取源码/测试/配置/manifest/图谱/REVIEW-04~09 报告；**仅允许新增本报告**。禁止修改任何源码/测试/配置/manifest/图谱/原 `test_plan.json`/既有报告；禁止接线、实现、构建、安装、登录、网络访问与 Android 测试。
- **前置基准**：`reports/USIT-S1-C-DESIGN-REVIEW-09-执行报告.md`（REVIEW-09，36,708 字节，已确认完整存在）。
- **本工单性质**：**实施前只读基线冻结 + 剩余阻断项确认。本工单不是实施授权。**
- **设计依据**：本次对 `usit/` 源码的 grep + 区间阅读（见 §2/§4 锚定），以及对 `tests/` 的只读核查（见 §5），非历史转述。

---

## 1. 执行信息

| 项 | 值 |
|---|---|
| 工单 | USIT-S1-C-IMPLEMENTATION-READINESS-01 |
| 目标 | 冻结 S1-C 实施前唯一设计基线；明确剩余阻断项；给出未来实施验收条件 |
| 授权范围 | 只读 + 仅新增本报告 |
| 实施授权 | **否**（本工单不构成 S1-C 实施授权） |
| S1-C 当前状态 | **未实施 ❌ / 未接线 ❌（BLOCKER-1）/ 未测试 ❌** |
| 源码变更 | 0 |
| 既有产物变更 | 0 |
| 测试执行 | `NOT_RUN` |
| 网络/登录/Android | 均未访问 |

---

## 2. 读取清单（只读动作与范围）

| # | 动作 | 范围 | 用途 |
|---|---|---|---|
| R-L1 | `Read` `reports/USIT-S1-C-DESIGN-REVIEW-09-执行报告.md` | 全文 377 行 | 唯一设计基线（P0/P1 修正后） |
| R-L2 | `Read` `reports/USIT-S1-C-DESIGN-REVIEW-08-执行报告.md` | 全文 437 行 | 上一代基准，确认矛盾点已修正 |
| R-L3 | `Read` `usit/understanding_projection.py` | 全文 + 重锚 122/152/158/251–298/348–371 | F-1/F-2/F-5/F-6/F-7 复验 |
| R-L4 | `Read` `usit/understanding_model.py` | 区间 30–35 | `ConclusionType` 5 值复验（F-3） |
| R-L5 | `Read` `usit/parser_contract.py` | 区间 11–12 | `RESOLUTION_STATUSES` 6 值复验（F-8） |
| R-L6 | `Read` `usit/schema_adapter.py` | 区间 16 | `NON_SUCCESS_STATES` 复验（F-9） |
| R-L7 | `grep -rn "CONSTRUCT_FAIL\|UNEXPECTED_ERROR\|N_construct_fail\|N_unexpected_error\|N_not_attempted" usit/` | `usit/` | 零命中复验（F-4） |
| R-L8 | `Read` `tests/test_understanding_projection.py` | 全文 219 行 | 核查 C1–C21 是否有独立夹具/实测（R4） |
| R-L9 | `ls tests/fixtures/` | `tests/fixtures/` | 确认是否存在 C1–C21 专用夹具目录 |

> **R1 基线追溯结论（PASS）**：REVIEW-09 完整存在（36,708 字节），其 F-1~F-11 全部关键事实的 `文件:行号` 锚点经本次 grep/区间阅读**逐一复验一致**（详见 §4）。REVIEW-08 亦存在（39,646 字节）。基线文件可定位、关键定义可定位。

---

## 3. 基线契约摘要与计数定义表（冻结态）

> 以下为 REVIEW-09（已修正）所确立的**唯一设计基线**，本工单予以冻结。任何未来实施工单必须以本表为基准，不得引入与下表矛盾的计数口径。

### 3.1 记录级两级"非候选"严格区分（核心红线）

| 层级 | 触发条件 | 归属终态 | 是否调用 `_build_evidence` | 与 `N_evidence_none` 关系 |
|---|---|---|---|---|
| 记录级非 Mapping（整条记录是 str/list/None 等） | `project_record:158` `if not isinstance(record, Mapping)` 提前返回 | `NOT_ATTEMPTED` | **否**（F-1） | **互斥**，不得双计 |
| evidence 级非 Mapping（记录是 Mapping，但其 `evidence` 非 Mapping/空） | 进入 `_classify`+`_build_evidence:122-126` 返回 `None` | `EVIDENCE_NONE` / `N_non_candidate` | 是 | 同一含义 |

### 3.2 七计数定义冻结表

| 计数 | 严格定义 | 适用输入 | 互斥/穷尽条件 | 源码是否已有实现 | 当前可否计算 |
|---|---|---|---|---|---|
| `N_raw` | 适配后原始记录总数 | 全部记录 | — | 是（`project_records:362`） | 可（`total_records`） |
| `N_candidate` | Mapping 记录且 evidence 为非空 Mapping | Mapping + 候选 evidence | 与 `N_non_candidate`/`N_not_attempted` 互斥 | 是（`has_evidence_candidate`） | 可 |
| `N_non_candidate` | **严格**：Mapping 记录且 evidence 非候选（→ `_build_evidence` 返回 None） | Mapping + 非候选 evidence | `N_non_candidate == N_evidence_none`（严格定义恒成立） | 是（`_build_evidence:122-126` 返回 None） | 可 |
| `N_construct_ok` | 候选且 `_build_evidence` 成功返回 `EvidenceReference` | 候选子集 | `N_candidate = N_construct_ok + N_construct_fail` | 间接（`static_inference`/`success_counted`） | 可（间接） |
| `N_construct_fail` | 候选但构造抛异常被显式捕获计数 | 候选子集 | 与 `CONSTRUCT_OK` 互斥 | **否**（零机制，F-3/F-4/F-5） | **不可，标 `NOT_AVAILABLE`** |
| `N_evidence_none` | `_build_evidence` 对证据非候选返回 None | Mapping + 非候选 evidence | 同 `N_non_candidate` | 是 | 可 |
| `N_not_attempted` | 记录非 Mapping，提前返回，未进入构造 | 非 Mapping 记录 | 与 `EVIDENCE_NONE` 互斥 | 否（无计数键，F-5） | 标准链路推导 0\*；非标准标 `NOT_AVAILABLE` |
| `N_unexpected_error` | 未预见异常兜底 | 任意 | 与前述互斥 | **否**（零机制） | **不可，标 `NOT_AVAILABLE`** |

### 3.3 冻结的恒等式（来自 REVIEW-09 §2.3，含前置条件）

```
(1) N_raw = N_candidate + N_non_candidate + N_not_attempted + N_unexpected_error   [穷尽]
(2) N_candidate = N_construct_ok + N_construct_fail
(3) N_non_candidate = N_evidence_none          [严格定义下恒等]
(4) N_raw = N_construct_ok + N_construct_fail + N_evidence_none   [仅当 N_not_attempted==0 且 N_unexpected_error==0]
(5) proj = Nr + cf                              [仅当 _detect_conflicts 无条件 extend 追加，F-7]
(6) dup = raw − unique_projection_ids_raw        [单扫描内成立；跨扫描 UNRESOLVED]
```

> **R3 冻结结论（PASS）**：七定义、适用输入、互斥条件、源码实现存在性、当前可计算性均已列明。`N_construct_fail`/`N_unexpected_error` 明确标 `NOT_AVAILABLE`，**不假设为 0 闭合**（本工单不虚构）。`N_non_candidate` 严格限定为 Mapping+非候选 evidence，禁止 `N_raw − N_candidate` 残差写法吸收 `NOT_ATTEMPTED`。

---

## 4. 三类事实清单（R2 事实分层）

> **强制分层，禁止混写**。本工单报告正文所有断言均归入以下三类之一。

### 4.1 SOURCE_CONFIRMED（本次实读源码/grep 定位）

| 编号 | 事实 | 定位 | 复验方式 |
|---|---|---|---|
| S-1 | `project_record` 非 Mapping 提前返回，不调用 `_build_evidence` | `understanding_projection.py:158-173` | 区间阅读 + 重锚 |
| S-2 | `_build_evidence:122-126` 仅对 Mapping 且 evidence 非 Mapping/空返回 None | `understanding_projection.py:122-126` | 区间阅读 |
| S-3 | `ConclusionType` 枚举仅 5 值，无 `CONSTRUCT_FAIL`/`UNEXPECTED_ERROR` | `understanding_model.py:30-35` | grep + 区间阅读 |
| S-4 | `usit/` 内零命中 `CONSTRUCT_FAIL`/`UNEXPECTED_ERROR`/`N_construct_fail`/`N_unexpected_error`/`N_not_attempted` | `usit/`（R-L7） | grep 零命中 |
| S-5 | `project_records` stats 仅 8 键，无构造失败/异常/未尝试键 | `understanding_projection.py:361-370` | 区间阅读 |
| S-6 | `_detect_conflicts` 产出独立 `ProjectedItem`，`resolution_status="CONFLICT"`、`conclusion_type=CONFLICT` | `understanding_projection.py:282-283` | 区间阅读 |
| S-7 | `project_records:358-359,363` 先 `project_record` 列表再 `extend(_detect_conflicts)` → `proj = Nr + cf` 恒成立 | `understanding_projection.py:358-359,363` | 区间阅读 |
| S-8 | `RESOLUTION_STATUSES` 6 值无 CONFLICT | `parser_contract.py:11-12` | grep + 区间阅读 |
| S-9 | `NON_SUCCESS_STATES` 5 值无 CONFLICT | `schema_adapter.py:16` | grep + 区间阅读 |
| S-10 | `_detect_conflicts` 冲突触发条件 `len(entries)>1 AND len(distinct)>1`（`:276`） | `understanding_projection.py:276` | 区间阅读 |
| S-11 | `project_records` 不计算 `file_conclusion` 与分支编号，输出仅 `items`+`stats` | `understanding_projection.py:348-371` | 区间阅读 |

### 4.2 DESIGN_ONLY（设计规则/分支/推演计数，未实施未测试）

| 编号 | 事实 | 来源 |
|---|---|---|
| D-1 | 5 态互斥穷尽分类与恒等式 (1)–(6) | REVIEW-09 §2.2/§2.3（设计规格层） |
| D-2 | `file_conclusion` 七分支决策（含 `CONFLICT_ONLY`/`UNKNOWN_RESOLUTION` 等）与分支 2 优先于分支 3 的优先级 | REVIEW-07 §4 决策伪代码（设计层，非源码输出，S-11） |
| D-3 | C1–C21 全表计数（含 `Nfail=NA(拟议)`/`Neu=NA(拟议)`/`[设计]` 列） | REVIEW-09 §8（设计推演） |
| D-4 | `duplicate_records` 精确定义（仅相同投影 ID 多余条数）+ 跨扫描 `UNRESOLVED` | REVIEW-09 §6.2（设计规格） |
| D-5 | `has_conflict` 由 `conflict_items>0` 派生 | REVIEW-07 §4（设计决策） |

### 4.3 NOT_VERIFIED（缺源码机制/夹具/执行结果，不得虚构）

| 编号 | 事实 | 缺口说明 |
|---|---|---|
| N-1 | `N_construct_fail` 实际捕获计数 | 源码零机制（S-3/S-4/S-5）→ `NOT_AVAILABLE`（拟议设计态） |
| N-2 | `N_unexpected_error` 实际捕获计数 | 同上 → `NOT_AVAILABLE` |
| N-3 | `N_not_attempted` 计数键 | 源码无键（S-5）；非标准链路入参时标 `NOT_AVAILABLE` |
| N-4 | C1–C21 样本的独立实测结果 | 无专用夹具（见 §5），仅有设计推演（D-3） |
| N-5 | `file_conclusion`/分支的实际运行时输出 | 属设计层（D-2），`project_records` 不产出（S-11） |
| N-6 | 跨扫描 `duplicate_records` 稳定性 | `_stable_id` 内容派生，跨扫描漂移未定义 → `UNRESOLVED` |

---

## 5. C1–C21 样例状态表（R4）

### 5.1 夹具核查结论（关键）

- `tests/fixtures/` 仅含 `graphs / java_kotlin / javascript_typescript / python_cross_module` 四类，**不存在 C1–C21 专用夹具文件**（R-L9）。
- `tests/test_understanding_projection.py` 含针对**底层机制**的单元测试（冲突检测 `:205-215`、重复非冲突 `:178-182`、非 Mapping 降级 `:159-164`、空输入 `:166-170`），**但这些是模块单元测试，并非 C1–C21 样本夹具，且本工单未运行任何测试**。
- 因此：**C1–C21 在 REVIEW-09 §8 中的计数为设计推演（D-3），无独立夹具、无独立实测**。按工单 R4 要求，逐项标 **`FIXTURE_NOT_INDEPENDENTLY_VERIFIED`**，**不得标 `TEST_PASS`**。

### 5.2 C1–C21 状态表

> 列说明：`proposed_*` = REVIEW-09 §8 设计推演值；`fixture_status` = 本工单结论；`mech_tested`（仅信息性，非本次执行）= 底层机制是否已有现存单元测试覆盖（仍标 FIXTURE_NOT_INDEPENDENTLY_VERIFIED，因非 C1–C21 专属夹具且未运行）。

| # | 设计输入（resolution/evidence） | proposed_Nr | proposed_proj | proposed_cf | proposed_分支[设计] | proposed_file_conclusion[设计] | fixture_status | mech_tested（信息性） |
|---|---|---|---|---|---|---|---|---|
| C1 | 1×RESOLVED / 非空 dict 真摘要 | 1 | 1 | 0 | 7 | INSUFFICIENT_EVIDENCE | FIXTURE_NOT_INDEPENDENTLY_VERIFIED | 部分（resolved+w/evidence→static_inference, `:84-89`） |
| C2 | 1×RESOLVED / 仅 `{graph_node_id}` | 1 | 1 | 0 | 7 | INSUFFICIENT_EVIDENCE | FIXTURE_NOT_INDEPENDENTLY_VERIFIED | 部分（缺失 evidence→unknown, `:96-102`） |
| C3 | 1×NON_SUCCESS / fallback 非空 | 1 | 1 | 0 | 2 | UNKNOWN_RESOLUTION | FIXTURE_NOT_INDEPENDENTLY_VERIFIED | 部分（non-success→unknown, `:104-110`） |
| C4 | 空文件 `size_bytes==0` | 0 | 0 | 0 | 0 | NO_SEMANTIC_CONTENT | FIXTURE_NOT_INDEPENDENTLY_VERIFIED | 部分（empty input→0, `:166-170`） |
| C5 | 0 记录、无符号 | 0 | 0 | 0 | 1 | NO_RECORDS(no_symbols) | FIXTURE_NOT_INDEPENDENTLY_VERIFIED | 部分（empty input→0, `:166-170`） |
| C6 | 0 记录、有符号无记录 | 0 | 0 | 0 | 1 | NO_RECORDS(symbols_no_records) | FIXTURE_NOT_INDEPENDENTLY_VERIFIED | 无独立夹具 |
| C7 | 1×RESOLVED / 非空 dict + 文件降级 | 1 | 1 | 0 | 4 | DEGRADED_PARSE | FIXTURE_NOT_INDEPENDENTLY_VERIFIED | 无独立夹具 |
| C8 | 1×NON_SUCCESS / fallback + 文件降级 | 1 | 1 | 0 | 2 | UNKNOWN_RESOLUTION | FIXTURE_NOT_INDEPENDENTLY_VERIFIED | 无独立夹具 |
| C9 | 1×NON_SUCCESS / fallback 非空 | 1 | 1 | 0 | 2 | UNKNOWN_RESOLUTION | FIXTURE_NOT_INDEPENDENTLY_VERIFIED | 部分（non-success→unknown） |
| C10 | 1×RESOLVED + 1×NON_SUCCESS（同身份异内容） | 2 | 3 | 1 | 3 | CONFLICT_ONLY | FIXTURE_NOT_INDEPENDENTLY_VERIFIED | 是（conflict 机制 `:205-215`，但非本样本且未运行） |
| C11 | 1×RESOLVED / 非空 dict + orphan | 1 | 1 | 0 | 7 | INSUFFICIENT_EVIDENCE | FIXTURE_NOT_INDEPENDENTLY_VERIFIED | 无独立夹具 |
| C12 | 1×RESOLVED / 非空 dict + diagnostics 越界 | 1 | 1 | 0 | 7 | INSUFFICIENT_EVIDENCE | FIXTURE_NOT_INDEPENDENTLY_VERIFIED | 无独立夹具 |
| C13 | 1×RESOLVED / 非空 dict + path∉manifest | 1 | 1 | 0 | 7 | INSUFFICIENT_EVIDENCE | FIXTURE_NOT_INDEPENDENTLY_VERIFIED | 无独立夹具 |
| C14 | 1×RESOLVED / 非空 dict + line 非正整数 | 1 | 1 | 0 | 7 | INSUFFICIENT_EVIDENCE | FIXTURE_NOT_INDEPENDENTLY_VERIFIED | 部分（invalid line dropped, `:153-155`） |
| C15 | 1×RESOLVED / 非空 dict + TestSpec 缺必填 | 1 | 1 | 0 | 7 | INSUFFICIENT_EVIDENCE | FIXTURE_NOT_INDEPENDENTLY_VERIFIED | 无独立夹具 |
| C16 | 2×RESOLVED 完全相同（重复） | 2 | 2 | 0 | 7 | INSUFFICIENT_EVIDENCE | FIXTURE_NOT_INDEPENDENTLY_VERIFIED | 是（duplicate-not-conflict `:178-182`，但非本样本且未运行） |
| C17 | 1×NON_SUCCESS + 1×RESOLVED（混合） | 2 | 2 | 0 | 7 | INSUFFICIENT_EVIDENCE | FIXTURE_NOT_INDEPENDENTLY_VERIFIED | 部分（混合路径） |
| C18 | 1×RESOLVED / evidence 非 Mapping | 1 | 1 | 0 | 2 | UNKNOWN_RESOLUTION | FIXTURE_NOT_INDEPENDENTLY_VERIFIED | 部分（missing evidence→unknown, `:96-102`） |
| C19 | 1×RESOLVED 非 Mapping + 1×NON_SUCCESS 候选 | 2 | 2 | 0 | 8 | UNKNOWN | FIXTURE_NOT_INDEPENDENTLY_VERIFIED | 部分（non-mapping degrade, `:159-164`） |
| C20 | 2×NON_SUCCESS / fallback + 文件降级 | 2 | 2 | 0 | 2 | UNKNOWN_RESOLUTION | FIXTURE_NOT_INDEPENDENTLY_VERIFIED | 无独立夹具 |
| C21 | 2×NON_SUCCESS（同身份异内容） | 2 | 3 | 1 | 2 | UNKNOWN_RESOLUTION | FIXTURE_NOT_INDEPENDENTLY_VERIFIED | 是（conflict 机制 `:205-215`，但非本样本且未运行） |

> **R4 结论（PASS）**：21 个样本全部标 `FIXTURE_NOT_INDEPENDENTLY_VERIFIED`，**无一标 `TEST_PASS`**。底层机制虽有现存单元测试覆盖，但（a）那些测试不是 C1–C21 专属夹具，（b）本工单未运行任何测试。设计推演计数（D-3）仅在授权实施并补齐夹具后才可转为实测。

---

## 6. 冲突与重复语义边界（R5）

### 6.1 必须未来验证的六类边界场景

| 场景 | 预期语义 | 源码机制（S-6/S-10） | 现状 |
|---|---|---|---|
| 同身份同内容 | 重复（`dup`），**不**冲突 | `_detect_conflicts:276` `distinct>1` 不满足 → 不产冲突 | 设计推导；`test_duplicate_records_are_stable_and_not_conflicts:178-182` 存在但未运行 |
| 同身份异内容 | **冲突**（1 个冲突条目） | `len>1 AND distinct>1` → 冲突 | 设计推导；`test_same_identity_different_content_is_conflict:205-215` 存在但未运行 |
| 不同身份同内容 | 不冲突、不重复（不同 `_stable_id`） | key=(file_path, qualified_name) 不同 | 设计推导，**无现存测试** |
| 不同状态同身份 | 归入"同身份异内容"→ 冲突 | fingerprint 含 `status` → distinct | 设计推导 |
| 多冲突记录（同一 identity 多组） | 每组产 1 个冲突条目 | 按 key 分桶，每桶最多 1 冲突 | 设计推导，**无现存测试** |
| 零记录 | `proj=0`、无冲突、无重复 | `project_records([])` → 空 | `test_empty_input_yields_empty_projection:166-170` 存在但未运行 |

### 6.2 语义区分（冻结）

- **原始记录**：`adapt_graph_v1` 产出的 Mapping 记录（S-10 产出恒 Mapping，F-10 REVIEW-09）。
- **冲突合成条目**：`_detect_conflicts` 独立产出的 `ProjectedItem`（`resolution_status="CONFLICT"`），**不修改**原始记录的 parser-contract `resolution_status`（S-6/S-8）。
- **投影 ID 重复计数**（`dup`）：仅计相同 `_stable_id` 多余条数（D-4），**禁止仅凭 ID 相同断言业务重复**（REVIEW-09 §6.2）。
- **业务重复**：两条内容相同的记录可能是同一符号在两个调用点的合法独立引用；`_stable_id` 纯内容 sha256、无 index，无法区分 → 不得断言业务重复。

> **R5 结论（PASS）**：六类边界场景与四类语义区分已明确列出，并标注哪些已有现存单元测试（均未运行）、哪些无测试，待未来实施期独立验证。

---

## 7. 实施阻断项与风险（R6）

### 7.1 P0 阻断项（必须先于实施解除）

| 编号 | 阻断项 | 证据 | 依赖 | 未来验收证据 |
|---|---|---|---|---|
| B-1 | **BLOCKER-1：`understanding_projection` 未接线**到 `scanner.py`/`cli.py` | S-11（输出仅 items+stats，无调用方集成） | 需独立实施授权 | 集成测试证明 `scanner→project_records→cli` 全链路贯通 |
| B-2 | **BLOCKER-2：`adapt_graph_v1` fallback 过宽** | REVIEW-09 F-10（`evidence or {graph_node_id}` 仅替换假值） | 需独立授权修订 | 修订后单测证明 fallback 不吞没真实空证据、不伪造 locator |
| B-3 | **异常捕获缺口**：`N_construct_fail`/`N_unexpected_error` 零机制 | S-3/S-4/S-5 | 需新增 `try/except`+计数键 | 注入异常后实测 `Nfail`/`Neu` 计数正确、不静默吞没 |
| B-4 | **接线方案未授权**：本工单仅只读，不构成接线授权 | 本工单授权边界 | 需独立授权 | 独立实施工单明确接线范围与回归基线 |

### 7.2 P1 风险

| 编号 | 风险 | 说明 | 缓解（未来） |
|---|---|---|---|
| P1-R1 | 证据引用真实性 | `EvidenceReference.__post_init__` 硬阻断信任声明（REVIEW-09 F-2/§4），已存测试 `:68-80` | 实施期保留该硬阻断，补充负向测试 |
| P1-R2 | 夹具可追溯性 | C1–C21 无专用夹具（§5.1） | 实施期建立 `tests/fixtures/s1c_cases/` 与计数列对照表 |
| P1-R3 | `CONFLICT` 与分支独立测试 | `file_conclusion`/分支属设计层（D-2/S-11） | 实施期分别验证冲突计数与分支决策，互不耦合 |
| P1-R4 | 不升级非成功态为成功 | `NON_SUCCESS_STATES` 无 CONFLICT（S-9）；现存测试 `:112-116` | 实施期保留并扩展该不变量测试 |

### 7.3 P2 观察（非阻断）

| 编号 | 观察 |
|---|---|
| P2-1 | 样例集冗余：C3/C9、C8/C20 描述同一逻辑场景（REVIEW-09 P2-1） |
| P2-2 | `has_conflict` 派生规则与 `CONFLICT ∉ NON_SUCCESS` 影响分支优先级（REVIEW-09 P2-2） |
| P2-3 | `N_no_evidence`（pre-adapt 空）当前 `NOT_AVAILABLE`（REVIEW-09 P2-3） |

> **R6 结论（PASS）**：阻断项 B-1~B-4 与风险 P1-R1~R4 已逐项列出，均标注证据、依赖与未来验收证据。**本工单不解除任何 BLOCKER**。

---

## 8. 未来实施验收清单（R7）

> 供未来独立实施工单使用，本工单不执行。最低验收证据：

1. **单元测试实际结果**：每个 C1–C21 样本的输入/预期/实际/差异/证据四联表，落地为 `tests/fixtures/s1c_cases/` + 断言，而非设计推演。
2. **计数互斥穷尽校验**：注入 `N_not_attempted>0`（非 Mapping 入参）与 `N_unexpected_error>0`（注入异常）后，验证恒等式 (1)/(4) 仍闭合，且 `N_non_candidate == N_evidence_none` 不被残差吸收。
3. **冲突数与分支分别校验**：`cf` 计数（S-6/S-10）与 `file_conclusion`/分支（D-2）独立测试，证明 C21 分支 2 优先仅影响 `file_conclusion`、不影响 `cf`（REVIEW-09 §5.3）。
4. **六类边界（§6.1）独立测试**：同身份同内容/异内容、不同身份同内容、多冲突、零记录，全部有断言。
5. **负向测试**：非法 trust 声明被 `EvidenceReference.__post_init__` 拒绝（P1-R1）；非成功态不计入 success（P1-R4）。
6. **回归/CLI 接线/失败路径**：`scanner→project_records→cli` 全链路贯通（解除 B-1）；`adapt_graph_v1` fallback 修订后不伪造（解除 B-2）；异常路径计数（解除 B-3）。
7. **变更前后清单/diff/命令/原始输出**：每次实施工单须附 `git status --porcelain`、`pytest` 原始 stdout、`md5`/哈希对照，作为"完成前验证"铁律证据。

---

## 9. 文件变更清单

| 类别 | 变更 |
|---|---|
| **源码变更** | **0**（未修改 `understanding_projection.py`/`parser_contract.py`/`understanding_model.py`/`schema_adapter.py`/`cli.py`/`scanner.py` 等任何文件） |
| **测试文件变更** | **0**（未修改 `tests/` 下任何文件，含 `test_understanding_projection.py`） |
| **配置/manifest/图谱/原 test_plan.json 变更** | **0** |
| **既有报告变更** | **0**（REVIEW-04~09 均未改动） |
| **新增文件** | **仅** `reports/USIT-S1-C-IMPLEMENTATION-READINESS-01-执行报告.md`（本报告） |
| **测试执行** | `NOT_RUN` |

> 变更证据：全程仅 `Read`/`grep`/`ls`（只读）。唯一写操作为新增本报告。人工复核命令（未在本工单执行）：`git -C "C:\Users\Administrator\WorkBuddy\2026-10-15-22-09-47\usit_p0" status --porcelain` —— 预期仅含本报告新增。

---

## 10. PASS/FAIL/未测试矩阵与最终结论

| 验收项 | 状态 | 说明 |
|---|---|---|
| R1 基线追溯（REVIEW-09/08 存在、F-1~F-11 锚点复验） | ✅ PASS | 文件存在、行号复验一致 |
| R2 事实三层分类（SOURCE_CONFIRMED/DESIGN_ONLY/NOT_VERIFIED，无混写） | ✅ PASS | §4 三表分离 |
| R3 计数模型冻结（七定义/互斥/可计算性/不假设 0） | ✅ PASS | §3 冻结表 |
| R4 C1–C21 状态（FIXTURE_NOT_INDEPENDENTLY_VERIFIED，无 TEST_PASS） | ✅ PASS | §5 全 21 项标 NOT_INDEPENDENTLY_VERIFIED |
| R5 冲突与重复语义边界（六场景+四语义） | ✅ PASS | §6 列明 |
| R6 实施阻断项（B-1~B-4/P1-R1~R4，不解除 BLOCKER） | ✅ PASS | §7 |
| R7 实施验收方案（最低证据清单） | ✅ PASS | §8 |
| 源码契约证据（S-1~S-11 独立 grep+区间阅读复验） | ✅ PASS | 代码实测坐实 |
| **源码已实现** | ❌ NOT_TESTED/否 | S1-C 未实施 |
| **测试已执行（本次）** | ❌ NOT_RUN | 只读工单，未运行 |
| 虚构计数 / 放宽门禁 / 越权实施 | ✅ NONE | 未发生 |

### 10.1 最终结论

- **验收评级**：**PASS**（完成只读核查 + 证据分层 + 可追溯 + 无虚构 + 仅新增报告）。无 `FAIL`、无 `PASS-WITH-ISSUES` 项下需标注的未独立复核缺口（所有无法独立复核项已显式标 `NOT_VERIFIED`/`NOT_AVAILABLE`/`FIXTURE_NOT_INDEPENDENTLY_VERIFIED`）。
- **本工单交付**：冻结了 S1-C 实施前唯一设计基线（§3）、完成三类事实分层（§4）、逐样本标 `FIXTURE_NOT_INDEPENDENTLY_VERIFIED`（§5）、列明剩余阻断项与未来验收清单（§7/§8）。
- **设计规格的"通过" ≠ 源码实现通过或测试通过**：C1–C21 计数仍为设计推演，待授权实施后验证。
- **三种状态声明**：设计已修订 ✅（REVIEW-09）/ 源码已实现 ❌ / 测试已执行 ❌。

---

## 11. 固定门禁状态与 STOP 声明

### 11.1 固定门禁值（原值保持，全程未变）

| 门禁 | 值 |
|---|---|
| `F1` | `NOT_READY_TO_CLOSE` |
| `P0.8` | `HOLD` |
| `F2` | `STOP` |
| `历史授权连续性` | `UNRESOLVED` |
| `安全信任链` | `BLOCKED / NOT_VERIFIED` |
| `BLOCKER-1`（投影未接线） | **未解除** |
| `BLOCKER-2`（adapt_graph_v1 fallback 过宽） | **未解除** |
| `verified_fact` | `0` |
| `ExecutionRecord` | `NOT_RUN` |
| `summary_verification` | `NOT_CHECKED` |
| 受约束条目 `complete` | `False` |

### 11.2 STOP

> **本工单仅授权实施前只读基线冻结与剩余阻断项确认，不构成 S1-C 实施授权。**
> 仅新增本报告，未修改任何文件（源码/测试/配置/manifest/图谱/原 test_plan.json/既有报告均 0 变更），未运行任何测试/构建/安装/登录/网络/Android 测试。
> 等待人工审查与**独立授权**；不得自行进入 S1-C 实施、不得修改 `understanding_projection.py`/`parser_contract.py`/`cli.py`/`scanner.py`、不得解除 BLOCKER-1/2、不得执行任何实现/构建/安装/登录/网络访问或 Android 测试。

---

**执行边界最终声明**：本工单只授权只读基线整理与新增 READINESS-01 报告。任何代码修改、接线、构建、安装、登录、网络访问或 Android 测试均超出授权范围。
