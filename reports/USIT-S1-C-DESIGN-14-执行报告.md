# USIT-S1-C-DESIGN-14｜项目级诊断身份判定与原始记录统计恒等式裁定（READ-ONLY）

- **工单编号**：USIT-S1-C-DESIGN-14
- **执行时间**：2026-10-20（GMT+8）
- **工作区绝对路径**：`C:\Users\Administrator\WorkBuddy\2026-10-15-22-09-47\usit_p0`
- **执行模式**：**严格 READ-ONLY**。仅只读检查源码/既有设计报告/审计报告，并**仅新增** `reports/USIT-S1-C-DESIGN-14-执行报告.md`。禁止修改其他文件；禁止实现/接线/运行测试/真实扫描/构建/安装/登录/网络/更新冻结哈希基线。
- **前置**：DESIGN-10/11/12/13、IMPLEMENT-01、POST-IMPLEMENT-AUDIT-01。
- **目标**：仅裁定 DESIGN-13 剩余的两个问题，冻结可直接用于后续独立授权实施的最终规则。
- **标签**：`SOURCE_CONFIRMED`（实测）/`DESIGN_DECISION`（裁定）/`NOT_VERIFIED`/`FUTURE`；推演均非运行结果；未运行测试标 `NOT_RUN`。

> STOP：本工单不授权实施/接线/测试/基线更新/真实项目操作；后续须另行签发明确的 **IMPLEMENT-02** 实施授权工单。

---

## 1. P0：项目级诊断身份判定（冻结）

### 1.1 真实构造路径与可验证字段（SOURCE_CONFIRMED）
| 记录类别 | 源码位置 | node_type | file_path / source_location.file_path | evidence | diagnostics |
|---|---|---|---|---|---|
| **项目级诊断**（js_ts） | scanner.py:1061-1065 | `'diagnostic'` | `''` / `''` | `{'diagnostic': code}` | `[diagnostic]` |
| **文件级诊断**（analysis） | scanner.py:1066-1073 | `'diagnostic'` | `r['path']`（非空）/ `r['path']` | `{'diagnostic': code}` | `[diagnostic]` |
| 图-文件/符号/未解析节点 | adapt_graph_v1（:122-161）；节点 :982/:987/:1009 | `'file'/'function'/…/'unresolved'` | rel（非空） | `{'path':…}` 等（无 `diagnostic` 键） | `[]` |
| 图-外部/候选节点 | 节点 :1006/:1022 | `'external'/'candidate'` | `''`（evidence 无 path） | `{'note':…}`（无 `diagnostic` 键） | `[]` |

**实测确认**：external/candidate 节点记录 `file_path==''` 但 `node_type∈{external,candidate}` 且 evidence 无 `diagnostic` 键 ⇒ **空路径≠项目级诊断**。仅 `node_type=='diagnostic'` 且 evidence 含 `diagnostic` 键的记录才是诊断。

### 1.2 身份判定最终规则（DESIGN_DECISION）
> 原则：`source_location.file_path==''` **仅候选条件**，不能单独证明项目级诊断；必须依据**可核验的记录类型（`node_type`）、诊断字段（`evidence['diagnostic']`/`diagnostics`）**判定，不得仅凭路径分类；不得凭猜测新增/伪造来源字段。

**判定顺序（互斥，首次命中）**：
1. **FILE_RECORD**：`source_location.file_path` 为**非空字符串**且**精确匹配** manifest 文件（rel，大小写/分隔符不归一）→ 归属该文件组。（含文件级诊断：`node_type=='diagnostic'` + 非空路径 → 归该文件。）
2. **PROJECT_DIAGNOSTIC**（已确认）：`file_path==''` **且** `node_type=='diagnostic'` **且** `'diagnostic' in evidence` → `project_diagnostics[]`。
3. **UNMATCHED_RECORD**（其余全部）：
   - 非空路径但不匹配 manifest → reason `no_matching_manifest_file`；
   - 路径缺失/`None`/非字符串（含非 Mapping 记录）→ reason `invalid_or_missing_file_path`；
   - **空路径但无法确认项目级诊断**（含 external/candidate 节点、`node_type=='diagnostic'` 但缺 `evidence['diagnostic']` 字段、node_type 缺失/未知）→ reason `empty_path_not_confirmed_project_diagnostic`。

**冻结红线**：身份不明的空路径记录**进入 `unmatched_records[]` 并带明确 reason**，**不得**伪装成项目级诊断、**不得**静默丢弃、**不得**伪造来源字段。现有字段不足以可靠区分处已标 `NOT_VERIFIED`（见 §4 风险）。

### 1.3 未匹配记录条目结构（沿用 DESIGN-13，冻结）
```
unmatched_records[] entry = { file_path(原样), reason(上述枚举), source(可核验来源, 无则 "unknown"),
                              projection(可投影则 to_dict()|null), identity_basis(判定依据快照) }
```
- `identity_basis`：记录判定所依据的 `{node_type, has_diagnostic_field, file_path}`，供审计（非伪造，仅为已存在字段的快照）。

**P0 结论：PASS**（身份判定基于可核验 node_type/诊断字段而非空路径alone；三结果互斥；身份不明空路径保守入 unmatched 不伪装）。

---

## 2. P0 未来单测矩阵（≥4，全部 NOT_RUN）

| 编号 | 输入记录 | 预期去向 | 计数 | 负向断言 | 标签 |
|---|---|---|---|---|---|
| T-01 | 合法项目级诊断：`file_path=''`, `node_type='diagnostic'`, `evidence={'diagnostic':'JS_X'}`, `diagnostics=[{...}]` | project_diagnostics[] | project_diagnostic_raw_records +1；不入 n_records_total | 不入 files[]；不入 unmatched[] | DESIGN_DECISION |
| T-02 | 空路径普通记录：`file_path=''`, `node_type='external'`, `evidence={'note':...}`（无 diagnostic 字段） | unmatched_records[](empty_path_not_confirmed_project_diagnostic) | unmatched_raw_records +1 | **不**入 project_diagnostics[]（不凭空路径伪装）；不入 files | DESIGN_DECISION |
| T-03 | 非空未匹配记录：`file_path='C:/abs/x.py'`（非 manifest） | unmatched_records[](no_matching_manifest_file) | unmatched_raw_records +1 | 不入 files；不静默丢弃 | DESIGN_DECISION |
| T-04 | 异常缺身份字段：`file_path=''`, `node_type='diagnostic'` 但 evidence 无 `diagnostic` 键（或 node_type 缺失） | unmatched_records[](empty_path_not_confirmed_project_diagnostic / missing_identity_fields) | unmatched_raw_records +1 | 不凭 node_type 单独诊断为项目级；不伪造 evidence 字段 | DESIGN_DECISION |

全部 `NOT_RUN`（file_conclusion 层未实现；设计推演非测试通过）。

---

## 3. P1：原始记录计数与汇总恒等式（冻结）

### 3.1 统计对象与逐条归属规则（DESIGN_DECISION）
- `total_input_records` = **本次处理的原始输入记录数**（= 输入记录批长度）。**不得**用投影 item 数、冲突合成项数、去重后记录数或输出数组条目数冒充。
- **每条原始输入记录恰入一个统计类别**（由**路径/身份**在投影前判定，投影成败**不改变**类别）：
  1. 已匹配文件记录（matched_file）；
  2. 已确认项目级诊断记录（project_diagnostic）；
  3. 未匹配记录（unmatched：含投影失败、非 Mapping、路径缺失/类型错误、身份无法确认）。
- `project_diagnostics[]` 与 `unmatched_records[]` **逐条保留原始记录可追溯**；**即使投影失败也保留原始记录计数与失败原因**，不得因投影失败少计。

### 3.2 变量定义（DESIGN_DECISION）
| 变量 | 定义 | 计算来源 |
|---|---|---|
| `total_input_records` | 原始输入记录总数 | 输入批长度 |
| `matched_file_raw_records` | 归入文件组的原始记录数 | `= Σ files[].n_records` |
| `project_diagnostic_raw_records` | 已确认项目级诊断原始记录数 | `= len(project_diagnostics[])` |
| `unmatched_raw_records` | 未匹配原始记录数 | `= len(unmatched_records[])` |
| `cf`（每文件） | 该文件冲突组数（附加投影，**非**原始记录） | `_detect_conflicts` 输出数 |
| `Nr`（每文件） | 该文件原始记录投影数（1:1） | `= files[].n_records` |

### 3.3 冻结恒等式（DESIGN_DECISION）
```
K1 total_input_records = matched_file_raw_records + project_diagnostic_raw_records + unmatched_raw_records
K2 totals.n_records_total = Σ files[].n_records = matched_file_raw_records
K3 totals.unmatched_total = unmatched_raw_records
K4 totals.project_diagnostic_total = project_diagnostic_raw_records
K5 totals.files = len(files[]) = manifest 有效文件条目数
K6 Σ totals.by_conclusion.values() = totals.files        （每文件恰 1 结论）
K7 totals.conflict_total = Σ files[].conflict_count       （每文件内每冲突组仅计 1 次）
K8 逐文件 projected_items == Nr + cf；冲突合成项 ∉ n_records_total，且非原始输入记录
```
- 由 K1+K2+K3+K4 可校验：`total_input_records == totals.n_records_total + totals.project_diagnostic_total + totals.unmatched_total`（记录穷尽划分）。

### 3.4 冲突合成项定位（DESIGN_DECISION）
- 冲突合成项属**投影阶段附加项**，**不属于原始输入记录**；不进入 `total_input_records`/`n_records_total`/`unmatched_total`/`project_diagnostic_total`；仅进入 `totals.conflict_total`（=Σcf）与逐文件 `projected_items`。保持 `projected_items == Nr + cf`。

**P1 结论：PASS**（统计对象、逐条归属、投影失败不少计、K1–K8 恒等式及变量定义冻结）。

---

## 4. P1 未来单测矩阵（≥5，全部 NOT_RUN）

| 编号 | 场景 | 输入 | 预期/计数 | 负向断言 | 标签 |
|---|---|---|---|---|---|
| U-01 | 正常文件记录 | 记录 file_path=manifest rel | matched_file；N_raw+1；n_records_total+1 | 不入 unmatched/project_diag | DESIGN_DECISION |
| U-02 | 项目级诊断 | js_ts 项目诊断（§1.1 项目级） | project_diagnostic_raw+1；project_diagnostic_total+1 | 不入 n_records_total；不入 files | DESIGN_DECISION |
| U-03 | 未匹配记录 | file_path 非空非 manifest | unmatched_raw+1；unmatched_total+1 | 不入 n_records_total；不丢弃 | DESIGN_DECISION |
| U-04 | 投影失败记录（已匹配） | 已匹配文件记录 + 注入异常 | 仍计入该文件 N_raw（N_construct_fail/N_unexpected_error+1）；**不少计** | n_records_total 不减少；失败原因保留 | DESIGN_DECISION |
| U-05 | 投影失败记录（未匹配/非Mapping） | 非 Mapping 或无效路径 + 投影失败 | 计入 unmatched_raw_records；失败原因保留于 entry | 不少计；不丢弃 | DESIGN_DECISION |
| U-06 | 冲突合成项不重复 | 2 记录同身份异内容 | projected_items==Nr+cf(=1)；conflict_count=1；cf∉n_records_total | 同冲突不双计；冲突项不冒充原始记录 | DESIGN_DECISION |
| U-07 | 混合输入汇总恒等式 | 文件记录 + 项目诊断 + 未匹配 + 冲突 | total==n_records_total+project_diagnostic_total+unmatched_total；Σby_conclusion==totals.files；conflict_total==Σcf | 划分穷尽；各 totals 自洽 | DESIGN_DECISION |

全部 `NOT_RUN`（file_conclusion 层未实现；设计推演非测试通过）。

---

## 5. 兼容性与固定门禁复核

### 5.1 兼容性（DESIGN_DECISION）
- 保持新输出 schema `usit-file-understanding-v1`；本轮**无新增字段**（身份判定规则与恒等式为对 DESIGN-12/13 已冻结字段的语义精化），`unmatched_records[].reason` 枚举值扩充（`empty_path_not_confirmed_project_diagnostic` 等），仍属既有数组内取值扩展。
- **不修改** `report.json`/`scan_manifest.json`/`knowledge_graph.json`/`test_plan.json`/`parser_contract.json` 及既有 schema 版本。
- **不修改** `understanding_projection.py`/`parser_contract.py`/`scanner.py`/`understanding_model.py`/`schema_adapter.py`/测试/配置。

### 5.2 固定门禁（原值保持）
| 门禁 | 值 |
|---|---|
| `F1` | `NOT_READY_TO_CLOSE` |
| `P0.8` | `HOLD` |
| `F2` | `STOP` |
| `历史授权连续性` | `UNRESOLVED` |
| `安全信任链` | `BLOCKED / NOT_VERIFIED` |
| `verified_fact` | `0` |
| `ExecutionRecord` | `NOT_RUN` |
| `summary_verification` | `NOT_CHECKED` |
| 受约束条目 `complete` | `False` |
| `BLOCKER-1` | **不解除** |
| `BLOCKER-2` | 仍待接线后复核 |

不调查/不更新冻结哈希基线；不以本工单结论解除任何门禁。

---

## 6. 未解决风险（冻结清单，交 IMPLEMENT-02）
| # | 风险 | 状态/建议 |
|---|---|---|
| V-1 | 空路径非诊断实体（external/candidate 节点）归入 unmatched_records[](empty_path_not_confirmed_project_diagnostic)，"unmatched"标签语义不精确 | 三向划分内保守处置（保留+计数+明确 reason）；若需专用"项目级非诊断实体"桶，须 schema 变更+单独授权（IMPLEMENT-02 决策） |
| V-2 | 空路径非诊断实体**数量**可能较大（每外部调用/框架候选各一节点，:1006/:1022 去重） | 可能推高 unmatched_total；IMPLEMENT-02 需评估是否单列桶或汇总计数 |
| V-3 | `node_type=='diagnostic'` 依赖 scanner 约定，非 schema 强制 | 现为 SOURCE_CONFIRMED（:1063/:1070 硬编码）；若未来构造变化，身份规则需重审；缺 diagnostic 字段时保守入 unmatched |
| V-4 | 身份判定字段快照 `identity_basis` 仅为已存在字段快照，非新增来源 | 不伪造；仅供审计追溯 |
| V-5 | scanner.py 冻结哈希漂移根因 | 待调查（DESIGN-11 §6），本工单不涉基线 |

---

## 7. 文件修改核验（READ-ONLY 保持）
| 文件 | 状态 |
|---|---|
| usit/understanding_projection.py / parser_contract.py / scanner.py / understanding_model.py / schema_adapter.py | 未修改 |
| tests/、配置、manifest、既有报告、冻结哈希基线 | 未修改（未调查/未更新基线） |
| 新增 | 仅 `reports/USIT-S1-C-DESIGN-14-执行报告.md` |

（源码/基线哈希与 DESIGN-13 结束时一致：understanding_projection `8bb0fb65…`、parser_contract `482400f9…`、scanner `a009b8db…`、understanding_model `4668760b…`、schema_adapter `c44f60e4…`、PRE_SHA256 `3aaabb4e…`。）

---

## 8. 结论汇总
| 项 | 结论 |
|---|---|
| P0-a 诊断记录构造路径核查 | **PASS**（SOURCE_CONFIRMED） |
| P0-b 项目级诊断身份判定规则 | **PASS**（DESIGN_DECISION） |
| P1 原始记录计数与汇总恒等式 | **PASS**（DESIGN_DECISION） |
| 兼容性与固定门禁 | **PASS** |

### 最终结论：**READY_FOR_IMPLEMENTATION_REVIEW**

- **P0 冻结**：`source_location.file_path==''` **仅候选条件**；PROJECT_DIAGNOSTIC 须 `file_path==''` **且** `node_type=='diagnostic'` **且** `'diagnostic' in evidence`（可核验记录类型+诊断字段，实测佐证 external/candidate 空路径节点非诊断）；FILE_RECORD=非空路径精确匹配 manifest；其余（含身份不明空路径）→ UNMATCHED_RECORD（明确 reason），**不伪装、不丢弃、不伪造**。T-01..T-04 单测设计（全 NOT_RUN）。
- **P1 冻结**：`total_input_records`=原始输入数（非投影/冲突/去重/输出条目）；每条原始记录由**路径/身份**（投影前）恰入 matched/project_diagnostic/unmatched，投影成败不改类别⇒**不少计**；K1–K8 恒等式及变量定义冻结；冲突合成项为投影阶段附加项，`projected_items==Nr+cf`，不入 `n_records_total`。U-01..U-07 单测设计（全 NOT_RUN）。
- 至此 DESIGN-13 剩余两问题已裁定，S1-C 文件级结论层契约**完全冻结**，可直接用于后续独立授权实施。
- 兼容性：无新增顶层字段，仅扩充 `unmatched_records[].reason` 枚举取值；既有 schema 与 5 个源文件零改。
- 固定门禁与 BLOCKER 全原值保持；未修改任何项目文件（仅新增本报告，§7）。

---

**最终 STOP**：本工单仅授权只读裁定并新增本报告。即使结论为 READY_FOR_IMPLEMENTATION_REVIEW，也**不授权**源码实现、scanner/CLI 接线、测试执行、哈希基线更新或真实项目操作；后续必须另行签发明确的 **IMPLEMENT-02** 实施授权工单。
