# USIT-S1-C-DESIGN-13｜未匹配记录、异常输入与汇总边界最终裁定（READ-ONLY）

- **工单编号**：USIT-S1-C-DESIGN-13
- **执行时间**：2026-10-20（GMT+8）
- **工作区绝对路径**：`C:\Users\Administrator\WorkBuddy\2026-10-15-22-09-47\usit_p0`
- **执行模式**：**严格 READ-ONLY**。只读检查源码与既有报告；**禁止**修改源码/测试/配置/报告之外的任何项目文件；**禁止**运行测试/接线/真实扫描/构建/安装/登录/联网/更新冻结哈希基线。**仅允许新增本工单执行报告**。
- **前置**：DESIGN-12（冻结规格）、DESIGN-11、IMPLEMENT-01 + POST-IMPLEMENT-AUDIT-01、REVIEW-07/09。
- **目标**：基于 DESIGN-12 冻结规格，只读裁定剩余 3 项契约风险，为后续独立实施授权提供**无歧义输入**。
- **标签**：`SOURCE_CONFIRMED`（实测）/`DESIGN_DECISION`（裁定）/`NOT_VERIFIED`/`FUTURE`；推演均非运行结果。

> 授权边界：本工单不授权实施/接线/运行测试/基线更新/真实项目操作；即使结论 READY_FOR_IMPLEMENTATION_REVIEW 仍须等独立实施工单明确授权。

---

## 1. 未匹配记录去向裁定（工单 1）

### 1.1 分组键与三向划分（SOURCE_CONFIRMED + DESIGN_DECISION）
- **分组键 = `source_location.file_path`**（`_source_location(record).get("file_path")`，`understanding_projection.py:95-97,258`）。实测：`adapt_graph_v1` 记录 `file_path == source_location.file_path`（均=rel）；`_detect_conflicts` 键**仅用** `source_location.file_path`（:258，忽略顶层 `file_path`，实测确认）⇒ 文件分组**必须**用同一字段以与冲突键一致。
- **三向穷尽划分**（每条输入记录恰入其一）：

| 类别 | 条件（分组键 `source_location.file_path`） | 去向 |
|---|---|---|
| A 项目级诊断 | `== ''`（空串） | `project_diagnostics[]` |
| B 正常文件记录 | 非空 且 命中 manifest 文件（rel 精确匹配） | 归入该文件组（`files[]`） |
| C 未匹配记录 | 非空 但 不命中任何 manifest 文件 | `unmatched_records[]` |
| C' 防御（无有效路径） | `None`/缺失/非字符串（含非 Mapping 记录） | `unmatched_records[]`（reason=invalid_or_missing_file_path） |

### 1.2 unmatched_records[] 冻结字段（DESIGN_DECISION）
```
unmatched_records[] entry = {
  "file_path": <str 原样，未匹配的非空路径；C' 为 "" 或 null>,
  "reason": "no_matching_manifest_file" | "invalid_or_missing_file_path",
  "source": <producer, 如 "graph-v1-adapter"|"js-ts"|"analysis-diagnostic"|"unknown">,
  "projection": <该记录的 ProjectedItem.to_dict()（若可投影）| null>,
  "grouping_key_field": "source_location.file_path"
}
```
- **保留、标记、不静默丢弃**；**不得**混入 `project_diagnostics[]`（A 与 C 语义不同：A=file_path 为空的项目级诊断；C=有路径但无对应清单文件）。

### 1.3 异常策略（DESIGN_DECISION）
- **默认（非严格）**：未匹配记录 → `unmatched_records[]`（降级、非静默、可追溯），计入 `totals.unmatched_total`；不阻断。
- **阻断错误（raise）**条件（明确）：
  1. manifest 结构无效（非文件条目序列/缺失）——无法建立文件清单；
  2. **系统性失配**：存在非空路径记录但 `Σ_files N_raw == 0`（即所有非空路径记录均未匹配，记录集与清单无任何对应）——理解输出将无意义；
  3. 调用方设 `strict=True` 且 `unmatched_records` 非空（选择性严格）。
- 单条/少量未匹配 → 降级（unmatched_records[]）；结构性/系统性 → 阻断。

**结论 1：PASS**（三向划分穷举互斥；unmatched 独立输出不丢弃不混入 project_diagnostics；字段/计数/异常策略冻结）。

---

## 2. 不可读文件与异常输入裁定（工单 2）

### 2.1 content_status=unreadable 不虚构记录/计数（DESIGN_DECISION）
- `content_status=unreadable` **仅描述文件上下文**（`size_bytes>0 且 readable_text=False`，来源 scanner.py:462），**不得**据此虚构记录或计数。记录的有无与计数**只来自实际投影**。

### 2.2 manifest 有文件但零记录 → NO_RECORDS（SOURCE_CONFIRMED + DESIGN_DECISION）
- 若 manifest 文件无归属记录（`n_records==0`），**无论 content_status**，均按既定规则 → **分支 1 `NO_RECORDS`**（子原因按 has_symbols 三态；content_status 作为附带上下文标志输出）。
- ** reconciled 与 DESIGN-12 G-03**：
  - 不可读非空文件**有** file 节点记录（真实路径 :981-982）→ `n_records≥1` → 分支 2 `UNKNOWN_RESOLUTION`（DESIGN-12 G-03）。
  - 不可读非空文件**零记录**（防御/非 scanner 调用方/节点被剔除）→ `n_records==0` → **分支 1 `NO_RECORDS`**（content_status=unreadable 附带）。
  - **区分因素是实际 `n_records`，而非 content_status**；content_status 不改变记录有无。

### 2.3 契约违反：阻断 vs 降级（DESIGN_DECISION + 可单测正反例）
| 情形 | 处置 | 依据 |
|---|---|---|
| 记录非 Mapping | 降级：project_record → not_attempted UNKNOWN item（:158-173）；分组阶段无有效路径 → unmatched_records[](invalid_or_missing_file_path) | SOURCE_CONFIRMED |
| 记录非序列 / manifest 非文件清单 | **阻断**：raise（project_records TypeError :436；manifest 结构校验） | DESIGN_DECISION |
| 未匹配非空路径记录（少量） | 降级：unmatched_records[] | §1.3 |
| 未匹配非空路径记录（系统性，ΣN_raw==0） | **阻断**：raise | §1.3 |
| 文件上下文缺失/类型错 | 降级：content_status=unknown / has_symbols=UNKNOWN / file_degraded=UNKNOWN，不默认否定 | DESIGN-11 §5 |

**正反例（可直接用于未来单测）**：
- 正（降级）：manifest=[{path:"a.py",size_bytes:500,readable_text:False,symbols:[]}]，记录=[] → `file_conclusion=NO_RECORDS`, `content_status=unreadable`, `n_records=0`。负向断言：`≠UNKNOWN_RESOLUTION`（不虚构 file 节点记录）、`≠NO_SEMANTIC_CONTENT`、`n_records==0`。
- 正（降级）：记录 file_path="C:/abs/x.py"（绝对、非 manifest）→ 入 `unmatched_records[]`(no_matching_manifest_file)，`files` 不含，`n_records_total` 不含。
- 反（阻断）：manifest=[]（无文件）但记录含非空 file_path → raise（系统性失配）。反（阻断）：manifest 非序列 → raise。

**结论 2：PASS**（unreadable 不虚构；零记录按 NO_RECORDS；阻断 vs 降级明确；正反例可单测）。

---

## 3. 汇总统计边界冻结（工单 3）

### 3.1 纳入/排除规则（DESIGN_DECISION）
| 输出/统计 | 纳入 | 排除 |
|---|---|---|
| `files[]` | 每个 manifest 文件各 1 条 FileConclusion | 项目级诊断、未匹配记录 |
| `project_diagnostics[]` | file_path=='' 记录 | — |
| `unmatched_records[]` | 非空未匹配 + 无有效路径记录 | — |
| `totals.files` | = `len(files[])` = manifest 文件数 | 诊断/未匹配 |
| `totals.by_conclusion` | `files[]` 的 file_conclusion 直方图（Σ==totals.files） | 诊断/未匹配 |
| `totals.n_records_total` | `Σ_files N_raw`（各文件原始记录） | 项目诊断、未匹配记录、**冲突合成项(cf)** |
| `totals.conflict_total` | `Σ_files cf`（冲突组数=冲突项数） | — |
| `totals.unmatched_total` | `= len(unmatched_records[])` | — |
| `totals.project_diagnostic_total` | `= len(project_diagnostics[])` | — |

### 3.2 冲突计数规则（DESIGN_DECISION）
- **冲突合成项不计入原始记录数**：`n_records==N_raw`（冲突追加前）；`cf` 为附加投影，`proj==Nr+cf`；`n_records_total` 只用 `N_raw`，**绝不含 cf**。
- **同一冲突不重复统计**：`_detect_conflicts` 对每个 `(source_location.file_path, qualified_name)` 桶在 `len(entries)>1 且 len(distinct)>1` 时**仅产 1 个**冲突项（:276-277）⇒ 每冲突组 1 项，`conflict_total` = 冲突项数 = 冲突组数，不重复。

### 3.3 可验证汇总恒等式（DESIGN_DECISION）
```
P1 记录穷尽划分: total_input_records == Σ_files N_raw + len(project_diagnostics) + len(unmatched_records)
P2 totals.files == len(files[]) == len(manifest 文件)
P3 Σ by_conclusion.values() == totals.files        （每文件恰 1 结论）
P4 totals.n_records_total == Σ_files N_raw          （== total_input_records − project_diag − unmatched）
P5 totals.conflict_total == Σ_files cf == 冲突项总数（每组 1 项，不重复）
P6 逐文件: projected_items == Nr + cf; n_records == N_raw
P7 冲突合成项 ∉ n_records_total; file_conclusion ∉ {VERIFIED_FACT}
```

**结论 3：PASS**（纳入/排除冻结；冲突项不计原始数、同冲突不重复；P1–P7 汇总恒等式可验证）。

---

## 4. 兼容性与固定门禁复核（工单 4）

### 4.1 兼容性（DESIGN_DECISION）
- 新增字段：`unmatched_records[]`、`totals.unmatched_total`、`totals.project_diagnostic_total` —— 均为 `usit-file-understanding-v1` 内的**加法式新增**。
- **不修改既有 schema**：`report.json/scan_manifest.json/knowledge_graph.json/test_plan.json/parser_contract.json` 及既有 manifest/projection/contract 版本号零改。
- **未知字段策略维持**：消费方按 `schema_version` 门控、忽略未知键（前向兼容）；写入方不输出未定义字段；缺失可选字段按缺席不伪造。

### 4.2 固定门禁（原值保持）
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

**结论 4：PASS**（加法式新字段遵循未知字段策略；既有 schema 零改；固定门禁全原值）。

---

## 5. 验收矩阵扩充（工单 5；全部 NOT_RUN）

> 承接 DESIGN-12 G-01..G-13；以下为本轮新增/修订，逐项含预期/分支/计数/负向断言。**全标 NOT_RUN**（file_conclusion 层未实现；设计推演非测试通过）。

| 编号 | 场景 | 输入 | 预期 | 计数 | 负向断言 | 标签 |
|---|---|---|---|---|---|---|
| H-01 | 未匹配绝对路径 | 记录 file_path="C:/abs/x.py"（非 manifest） | 入 unmatched_records[](no_matching_manifest_file)；不在 files[] | unmatched_total=1；n_records_total 不含 | 不静默丢弃；不混入 project_diagnostics | DESIGN_DECISION |
| H-02 | 空路径诊断隔离 | js_ts 项目诊断 file_path='' | 入 project_diagnostics[]；不在 files[] | project_diagnostic_total=1；n_records_total 不含 | 不伪装真实文件；不污染文件统计 | SOURCE_CONFIRMED |
| H-03 | manifest 文件零记录 | manifest 文件无归属记录 | NO_RECORDS/1 | n_records=0 | ≠NO_SEMANTIC_CONTENT；≠UNKNOWN_RESOLUTION | DESIGN_DECISION |
| H-04 | 不可读非空零记录 | manifest[{size>0,readable=F,symbols:[]}]，记录=[] | NO_RECORDS/1，content_status=unreadable | n_records=0 | **不虚构** file 节点记录；≠UNKNOWN_RESOLUTION；≠NO_SEMANTIC_CONTENT | DESIGN_DECISION |
| H-05 | 不可读非空有记录 | 同 H-04 但含 file 节点记录 | UNKNOWN_RESOLUTION/2 | n_records=1,N_candidate=1 | ≠NO_SEMANTIC_CONTENT；content_status=unreadable 附带 | SOURCE_CONFIRMED |
| H-06 | 重复冲突引用 | 2 记录同身份异内容 | CONFLICT_ONLY/3 | cf=1；conflicting_refs 为 2 个互异 projection_id | refs==projection_id（非 conclusion_id）；互异；不重复计数 | SOURCE_CONFIRMED |
| H-07 | 汇总恒等式 P1 | 混合：文件记录 + 空路径诊断 + 未匹配绝对路径 | — | total==ΣN_raw+len(projdiag)+len(unmatched) | 划分穷尽 | DESIGN_DECISION |
| H-08 | 汇总恒等式 P2/P3 | 多文件多结论 | — | Σ by_conclusion==totals.files==len(files) | 每文件恰 1 结论 | DESIGN_DECISION |
| H-09 | 汇总恒等式 P4/P5 | 含冲突文件 | — | n_records_total==ΣN_raw（不含 cf）；conflict_total==Σcf | 冲突项不计原始数；同冲突不重复 | DESIGN_DECISION |
| H-10 | 阻断-系统性失配 | manifest=[] 但记录含非空 file_path | raise（阻断） | — | 不生成无意义输出 | DESIGN_DECISION |
| H-11 | 阻断-manifest 无效 | manifest 非序列 | raise（阻断） | — | — | DESIGN_DECISION |
| H-12 | 分组键一致性 | 记录 file_path≠source_location.file_path | 分组/冲突均以 source_location.file_path 为准 | — | 不用顶层 file_path 分组（与冲突键一致） | SOURCE_CONFIRMED |

**所有案例标记 `NOT_RUN`**（file_conclusion 层未实现）。

**结论 5：PASS**（未匹配/空路径隔离/零记录/不可读零记录/重复冲突引用/汇总恒等式全覆盖，负向断言齐备，全 NOT_RUN）。

---

## 6. 未决风险（冻结清单，交实施工单）
| # | 风险 | 状态/建议 |
|---|---|---|
| R-1 | 未匹配记录的生产来源 | 确认 scanner 契约下不应出现（记录路径恒为 '' 或 manifest rel）；出现即契约违反，按 §1.3 降级/阻断 |
| R-2 | 绝对路径记录是否应归一为 rel | 本规格**不**归一（不断言等价）；若未来需归一并入 manifest，须显式决策+测试 |
| R-3 | 非 Mapping 记录的投影保留 | 入 unmatched_records[](invalid_or_missing_file_path) 并附 not_attempted 投影；真实路径不出现 |
| R-4 | scanner.py 冻结漂移根因 | 待调查（DESIGN-11 §6），无直接证据不归因；基线须授权下再生 |
| R-5 | 阻断阈值（strict/系统性）调参 | 默认非严格；系统性判据 ΣN_raw==0 已定；strict 为调用方选项 |

---

## 7. 文件修改核验（READ-ONLY 保持）
| 文件 | 状态 |
|---|---|
| usit/understanding_projection.py / parser_contract.py / scanner.py / understanding_model.py / schema_adapter.py | 未修改 |
| outputs/p0710zxrxf/PRE_SHA256.json | **未修改**（未更新基线） |
| tests/、配置、manifest、既有报告 | 未修改 |
| 新增 | 仅 `reports/USIT-S1-C-DESIGN-13-执行报告.md` |

（源码哈希与 DESIGN-12 结束时逐一相符：understanding_projection `8bb0fb65…`、parser_contract `482400f9…`、scanner `a009b8db…`、understanding_model `4668760b…`、schema_adapter `c44f60e4…`、PRE_SHA256 `3aaabb4e…`。）

---

## 8. 结论汇总
| 工单项 | 结论 |
|---|---|
| 1 未匹配记录去向 | **PASS** |
| 2 不可读文件与异常输入 | **PASS** |
| 3 汇总统计边界 | **PASS** |
| 4 兼容性与固定门禁 | **PASS** |
| 5 验收矩阵扩充 | **PASS** |

### 最终结论：**READY_FOR_IMPLEMENTATION_REVIEW**

- 本轮冻结的核心补全：① **分组键 = `source_location.file_path`**（与冲突键一致，实测确认顶层 file_path 被冲突键忽略）；② 记录**三向穷尽划分**（项目诊断 / 文件记录 / 未匹配），未匹配独立进 `unmatched_records[]`（不丢弃、不混入 project_diagnostics），并定义**阻断条件**（manifest 无效 / 系统性失配 ΣN_raw==0 / strict）；③ `content_status=unreadable` **不虚构记录**，零记录一律 `NO_RECORDS`，与 DESIGN-12 G-03 以**实际 n_records** 调和；④ 汇总纳入/排除 + **P1–P7 汇总恒等式**（记录穷尽划分、冲突项不计原始数、同冲突不重复）；⑤ 新字段加法式入 `usit-file-understanding-v1`，遵循未知字段策略，既有 schema 零改。
- 所有案例含负向断言且**全标 NOT_RUN**（file_conclusion 层未实现；设计推演非测试通过）；`MEANINGFUL_EVIDENCE` 保持 FUTURE；无 VERIFIED_FACT 升级。
- 至此 S1-C 文件级结论层契约风险已全部裁定，规格**可直接用于**后续独立授权实施工单；本工单**不授权**实施/接线/测试/基线更新/真实项目操作。
- 固定门禁与 BLOCKER 全原值保持；未修改任何项目文件（仅新增本报告，§7）。

---

**最终 STOP**：本工单仅授权只读裁定未匹配记录/异常输入/汇总边界并新增本报告。任何源码修改、接线、测试执行、基线更新、构建、安装、登录、网络访问或真实项目扫描均超出授权范围；即使结论为 READY_FOR_IMPLEMENTATION_REVIEW，仍须等待独立实施工单明确授权。
