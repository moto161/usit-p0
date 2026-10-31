# USIT-S1-C-DESIGN-12｜零记录文件覆盖与输出契约最终裁定（READ-ONLY）

- **工单编号**：USIT-S1-C-DESIGN-12
- **执行时间**：2026-10-20（GMT+8）
- **工作区绝对路径**：`C:\Users\Administrator\WorkBuddy\2026-10-15-22-09-47\usit_p0`
- **执行模式**：**严格 READ-ONLY**。只读核对源码、DESIGN-10/11 及既有审查报告；**禁止**修改任何文件、接线、测试执行、真实扫描、构建、安装、登录、网络及冻结哈希更新。
- **前置**：DESIGN-10（初版规格）、DESIGN-11（矛盾裁定+规格冻结）、IMPLEMENT-01 + POST-IMPLEMENT-AUDIT-01、REVIEW-07/09。
- **目标**：解决实施前剩余契约矛盾，给出**最终可执行冻结规格**；本工单**不授权实现**。
- **标签**：`SOURCE_CONFIRMED`（实测）/`DESIGN_DECISION`（裁定）/`NOT_VERIFIED`（缺证据）/`FUTURE`（不可达）；推演均非运行结果。

> 授权边界：只读审查，不实施/不接线/不更新基线；即使结论 READY_FOR_IMPLEMENTATION_REVIEW 仍须等独立实施授权。

---

## 1. 零记录文件覆盖裁定（工单 1）

### 1.1 实际结构核实（SOURCE_CONFIRMED）
- **权威文件清单** = `scan_manifest.json` 的 `files`：`[{k:v for k,v in r.items() if k!='_captured_text'} for r in rows]`（scanner.py:1108），每条含 `path/size_bytes/readable_text/symbols/language/category/...`（row 结构 :462）。这是**已扫描文件的权威枚举**。
- **每个已扫描文件恒产 file 节点**：`for f in rows: nodes.append({...'type':'file'...})`（scanner.py:981-982，无条件）。该 file 节点经 `adapt_graph_v1` 产 1 条记录（`resolution_status="UNKNOWN"`（file 节点无 status→默认，:106/:141）、`evidence={'path':...}` 非空→候选）。
- **项目级空路径诊断**：js_ts 项目诊断记录 `file_path=''`（scanner.py:1062-1065）；per-file analysis 诊断 `file_path=r['path']`（:1068-1072）。

### 1.2 裁定（DESIGN_DECISION）
- **文件列表来源 = 清单（manifest['files']），不是记录遍历**。对每个 manifest 文件：按其 `path`（= rel）从记录集合中**归属**该文件的记录（可能为 0），产出一个 `FileConclusion`。⇒ **保证每个已扫描文件都有结论，含零记录文件**。
- **零记录文件**（manifest 有条目但无归属记录）→ `n_records=0` → 分支 1 `NO_RECORDS`（子原因按 has_symbols 三态）。真实扫描路径中因 :982 恒建 file 节点，零记录**极少发生**（防御性/非 scanner 调用方）；规格仍必须支持。
- **项目级空路径诊断独立输出**：`file_path==''` 的记录**不**并入任何文件组、**不**伪装成真实文件；单独进 `understanding.json` 的 `project_diagnostics[]`（§6）。
- 敏感文件：`_scrub_sensitive_manifest`(:524-526) 已从 manifest 移除敏感文件、`_scrub_sensitive_graph`(:510-521) 移除其节点 ⇒ 敏感文件既不在清单也不产记录，不生成结论（一致排除）。

**结论 1：PASS**（清单迭代保证全覆盖；空路径诊断独立；零记录有明确分支）。

---

## 2. 文件内容状态裁定（工单 2）

### 2.1 矛盾识别（修正 DESIGN-11 §5.2）
DESIGN-11 定义 `has_content=(size_bytes>0 AND readable_text)`，会把"非空但不可读"（size>0, readable_text=False）判为 `has_content=False` → 落分支 0（空）——**违反"禁止把不可读文件认定为空文件"**。DESIGN-12 以 `content_status` 四分取代，并**仅以 `size_bytes==0` 定"空"**。

### 2.2 content_status 四分（DESIGN_DECISION，SOURCE_CONFIRMED 来源 :462）
| content_status | 条件 | 命中分支 | file_conclusion | reasons | 备注 |
|---|---|---|---|---|---|
| `empty` | `size_bytes==0`（确定） | 0 | NO_SEMANTIC_CONTENT | empty_or_no_semantic_content | 零字节 |
| `unreadable` | `size_bytes>0` 且 `readable_text==False` | **2**（非 0） | UNKNOWN_RESOLUTION | all_records_non_success_or_no_evidence | 见 2.3；**不**判空 |
| `readable` | `size_bytes>0` 且 `readable_text==True` | 正常评估 | 依信号 | 依分支 | — |
| `unknown` | `size_bytes`/`readable_text` 缺失或类型错 | 分支 0 **不**命中 | 依其余信号（记录仍在则照常；无记录则分支1 symbol_status_unknown） | 视情况 | 不默认空 |

### 2.3 不可读非空文件的精确路径（SOURCE_CONFIRMED + DESIGN_DECISION）
- 不可读文件仍恒得 file 节点记录（:981-982），该记录 `UNKNOWN`+候选。
- 单该记录：`all_non_success=True` → **分支 2 UNKNOWN_RESOLUTION**（优先级高于分支 4 降级）。
- **不落分支 0**（size>0 非空）✓ 满足硬约束。
- **局限（列出）**：8 值枚举无专用 `UNREADABLE` 态；`unreadable` 与"可读但全非成功"均落 `UNKNOWN_RESOLUTION`，仅在 `file_context.readable_text=False` 诊断标志 + reasons 可区分。规格要求输出携带 `readable_text` 三态标志以供下游辨别，**不改枚举**。

### 2.4 上下文缺失/类型错误（DESIGN_DECISION）
- `size_bytes` 缺失/非 int → `content_status=unknown`，分支 0 不命中（**不**默认空）。
- `readable_text` 缺失/非 bool → 同属 unknown（不可断言 readable/unreadable）。
- `symbols` 缺失/非 list → `has_symbols=UNKNOWN`（**不**默认无符号）；分支 1 子原因 `symbol_status_unknown`。
- 必要信号 UNKNOWN → 保留 `UNKNOWN` 结论或显式降级原因，绝不把缺失当否定事实。

**结论 2：PASS**（三态/四态分离明确；不可读不判空；枚举不足处以 UNKNOWN/明确原因+局限处理，不改枚举）。

---

## 3. hypothesis 分支规格修正（工单 3）

### 3.1 重新推演（SOURCE_CONFIRMED 实测）
输入：单记录 `resolution_status="RESOLVED"`、`evidence` 非空 Mapping（候选）、`hypothesis=True`、无冲突、无降级。
- `project_record` → `_classify`：RESOLVED（非非成功）+ evidence 候选 + hypothesis=True → **`AI_HYPOTHESIS`**（:116-117）；`is_success=False`；有 evidence_refs（是候选）。
- 实测 stats：`N_candidate=1, N_construct_ok=1, conflict=0`；`resolved_ev_count(RESOLVED∧STATIC_INFERENCE)=0`。
- 分支路径：
  - 0：size>0，跳过。
  - 1：`n_records=1`，跳过。
  - 2：`all_non_success=False`（RESOLVED）且 `all_evidence_empty=False`（N_candidate=1）⇒ **不命中**（越过）。
  - 3：`has_conflict=False`，跳过。
  - 4：`file_degraded=False`，跳过。
  - 6：`meaningful_count=0`，跳过。
  - 7：`resolved_ev_count=0`（AI_HYPOTHESIS **不计入**）⇒ **不命中**。
  - 8：else → **`UNKNOWN`**（indeterminate_resolution_without_usable_evidence）。

### 3.2 修正后的规格 + 可单测用例（DESIGN_DECISION）
- **结论**：仅"RESOLVED+AI_HYPOTHESIS+候选"文件 → **分支 8 `UNKNOWN`**（保守：假设非可靠静态推断，不计入 resolved_ev_count）。
- **可直接用于未来单测**：
  - 输入：`[{resolution_status:"RESOLVED", evidence:{"locator":"x:1","kind":"definition"}, hypothesis:True, node_type:"function", qualified_name:"f", source_location:{...}}]`，file_context `{size_bytes>0, readable_text=True, has_symbols=True, file_degraded=False}`。
  - 预期：`branch=8`, `file_conclusion=UNKNOWN`, `resolved_ev_count=0`, `N_candidate=1`, `N_construct_ok=1`, `conflict_count=0`。
  - 负向断言：`file_conclusion != INSUFFICIENT_EVIDENCE`（hypothesis 不计入 resolved 证据）、`!= CONFLICT_ONLY`、`!= VERIFIED_FACT`（且 `MEANINGFUL_EVIDENCE` 不可达）；item `is_success==False`。
  - 备选（计入 hypothesis→分支 7 INSUFFICIENT_EVIDENCE）不采用（过度暗示存在可靠证据）。

**结论 3：PASS**（hypothesis 路径重推演确认为分支 8 UNKNOWN；给出可单测输入/预期/负向断言）。

---

## 4. 冲突引用含义核实（工单 4）

### 4.1 真实来源与含义（SOURCE_CONFIRMED + 实测）
- `_detect_conflicts`：`entries.append((item.projection_id, fingerprint))`（:271）；冲突项 `conflicting_conclusion_ids=ids`，其中 `ids=tuple(sorted(pid for pid,_ in entries))`（:277,:293）。
- ⇒ `ids` = 冲突各方 item 的 **`projection_id`**（前缀 `proj:`）。
- **实测**：冲突项 `conflicting_conclusion_ids` 值 = `['proj:4783c81c…','proj:9b31a763…']`；`== projection_ids? True`；`== conclusion_ids? False`。
- **字段名与含义不一致（已确认）**：名为 `conflicting_conclusion_ids`，实存 **projection_id**。

### 4.2 兼容方案（DESIGN_DECISION，不改代码/不伪造 ID）
- **不修改** `understanding_projection.py` 既有字段名与代码。
- 新输出 `understanding.json` 采用**准确命名** `conflicting_refs`（或 `conflicting_projection_ids`），并**在规格中注明**：来源字段 `UncertaintyRecord.conflicting_conclusion_ids` 实际承载 **projection_id**（内容 id），非 conclusion_id。
- 因冲突各方**内容不同**（否则不冲突），其 projection_id **互异**（内容派生）⇒ 冲突引用无重复 ID 歧义（区别于 duplicate 场景）。
- 追溯链：冲突项（`conflict:…` id）→ `conflicting_refs`（各方 projection_id）→ 各投影 item（内容 id）→ 原始记录。

**结论 4：PASS**（字段名≠含义已查实；兼容方案不改为、不伪造 ID；冲突引用无歧义）。

---

## 5. 路径分组契约（工单 5）

### 5.1 扫描器既有规范化（SOURCE_CONFIRMED）
- 规范形：`rel = path.relative_to(root).as_posix()`（:149,:457,:744,:858）⇒ **相对 root、正斜杠**。
- 记录 file_path 来源 = graph 节点 `evidence.path` = 文件 `rel`（file 节点 :982 `evidence={'path':f['path']}`），与 manifest `path`（=rel）**同源一致**。
- `casefold()` 仅用于**排序键**（:172），**不**用于分组等价。

### 5.2 分组契约（DESIGN_DECISION）
| 情形 | 规则 |
|---|---|
| 规范形 | 分组键 = `rel`（相对 root、正斜杠、`.as_posix()`）；记录 file_path 与 manifest path 均为此形 |
| 分隔符 | 统一正斜杠（复用 `.as_posix()`）；不得混用 `\` 与 `/` 作不同键 |
| 大小写 | **不**做大小写归一（casefold 仅排序用）；`Pkg/Mod.py` 与 `pkg/mod.py` 视为**不同**键，**不**假定等价 |
| 绝对路径 | 记录携带绝对路径（非规范形）时**不**强行等同于 rel；视为未匹配 manifest → 归入未匹配/项目级，不断言等价 |
| 空路径 | `file_path==''` → **非真实文件** → `project_diagnostics[]`，不并入任何文件组 |
| 匹配 | 记录按 file_path(rel) 归属 manifest 文件；manifest 文件无匹配记录 → 零记录组（分支 1） |

> **禁止凭猜测认定路径等价**（大小写/分隔符/绝对-相对）。若未来需大小写不敏感归并，须作为显式实施决策并加测试，本规格不断言。

**结论 5：PASS**（复用扫描器 rel/as_posix 规范形；大小写/绝对路径/空路径规则明确；不猜测等价）。

---

## 6. 输出契约冻结（工单 6）

### 6.1 understanding.json 冻结 schema（`usit-file-understanding-v1`；DESIGN_DECISION）
```jsonc
{
  "schema_version": "usit-file-understanding-v1",
  "projection_schema_version": "usit-understanding-projection-v1",
  "generated_from": { "manifest_schema": "usit-manifest-v1", "contract_version": "usit-parser-result-v1" },
  "files": [ /* FileConclusion，按 manifest['files'] 顺序，每文件一条 */ ],
  "project_diagnostics": [ /* file_path=='' 的项目级诊断，独立于 files */ ],
  "totals": {
    "files": <int>,
    "by_conclusion": { "<FileConclusion>": <int>, ... },
    "n_records_total": <int>,          // 各文件 N_raw 之和（不含项目级诊断、不含冲突合成项）
    "conflict_total": <int>            // 各文件 cf 之和
  }
}
```

### 6.2 文件级条目 FileConclusion（冻结；承接 DESIGN-11 §7.2 + 本轮补充）
```
file_path(str, rel/as_posix) | file_conclusion(Enum8, 绝无VERIFIED_FACT) | branch(0..8) | reasons[]
has_conflict(bool) | conflict_count(int==cf) | file_degraded(TriState)
content_status(empty/unreadable/readable/unknown) | readable_text(TriState)   // §2 新增
n_records(==本文件N_raw) | resolved_ev_count | meaningful_count(0)
counts{N_raw,N_candidate,N_non_candidate,N_construct_ok,N_construct_fail,N_not_attempted,N_unexpected_error,N_evidence_none,Nr}
evidence_refs[](projection_id 内容id) | conflicting_refs[](冲突各方 projection_id；来源字段名 misleading 已注明)
projection_schema_version
```

### 6.3 冻结裁定（DESIGN_DECISION）
- **顶层字段**：`schema_version / projection_schema_version / generated_from / files[] / project_diagnostics[] / totals{}`（如上）。
- **项目级诊断表示**：`project_diagnostics[]`（`file_path==''` 的记录，含原 diagnostic 内容 + 来源），**不**入 `files[]`。
- **totals/by_conclusion**：**纳入**（`files/by_conclusion/n_records_total/conflict_total`）；`n_records_total` = 各文件 `N_raw` 之和（**不含**项目级诊断、**不含**冲突合成项 cf）。
- **schema 版本**：`usit-file-understanding-v1`（新）；不改 `usit-manifest-v1`/`usit-understanding-projection-v1`/`usit-parser-result-v1` 等既有版本。
- **未知字段策略**：消费方按 `schema_version` 门控、**忽略未知键**（前向兼容）；写入方**不**输出未定义字段；缺失可选字段按"缺席"处理（不伪造）。
- **既有输出 schema 不修改**：`report.json/scan_manifest.json/knowledge_graph.json/test_plan.json/parser_contract.json` 零改动；`understanding.json` 为**新增加法式**文件（scanner.py:1160 `write_json` 处写入）；`report.md` 增人类可读摘要节（markdown 加法）。

**结论 6：PASS**（顶层/文件级/项目级/totals/版本/未知字段策略全部冻结；既有 schema 零改）。

---

## 7. 验收矩阵扩充（工单 7；全部 NOT_RUN，设计推演非测试通过）

> 承接 DESIGN-11 F-01..F-20；以下为本轮新增/修订案例。逐项含预期结论/分支/计数/负向断言。

| 编号 | 场景 | 输入 | 预期 file_conclusion/branch | 计数 | 负向断言 | 标签 |
|---|---|---|---|---|---|---|
| G-01 | 零记录文件（清单有条目、无归属记录） | manifest 文件 + 0 记录；has_symbols=F | NO_RECORDS(no_symbols)/1 | n_records=0, 全 N_*=0 | ≠NO_SEMANTIC_CONTENT；≠UNKNOWN_RESOLUTION | DESIGN_DECISION |
| G-02 | 零记录+符号状态未知 | manifest 文件 + 0 记录；has_symbols=U | NO_RECORDS(symbol_status_unknown)/1 | n_records=0 | 不默认 no_symbols | DESIGN_DECISION |
| G-03 | 不可读非空文件 | size>0, readable_text=F（恒得 file 节点 UNKNOWN 记录） | UNKNOWN_RESOLUTION/2 | n_records=1(N_candidate=1,Nok=1) | **≠NO_SEMANTIC_CONTENT**（不判空）；content_status=unreadable | SOURCE_CONFIRMED+DESIGN_DECISION |
| G-04 | 零字节文件 | size==0 | NO_SEMANTIC_CONTENT/0 | — | ≠NO_RECORDS；≠UNKNOWN_RESOLUTION | DESIGN_DECISION |
| G-05 | 上下文缺失（size_bytes 缺） | size_bytes=None, 有记录 | 分支0不命中；依记录（如全非成功→UNKNOWN_RESOLUTION/2） | 依信号 | ≠NO_SEMANTIC_CONTENT（不默认空）；content_status=unknown | DESIGN_DECISION |
| G-06 | hypothesis-only | RESOLVED+AI_HYPOTHESIS+候选, 无冲突无降级 | UNKNOWN/8 | N_candidate=1,Nok=1,rev=0 | ≠INSUFFICIENT_EVIDENCE；≠CONFLICT_ONLY；≠VERIFIED_FACT；is_success=F | SOURCE_CONFIRMED(实测) |
| G-07 | 冲突引用类型 | 2 记录同身份异内容冲突 | CONFLICT_ONLY/3 | cf=1,n_records=2 | conflicting_refs==projection_ids（非 conclusion_id）；refs 互异 | SOURCE_CONFIRMED(实测) |
| G-08 | 路径等价-同形 | 两记录 file_path 同为 "pkg/mod.py" | 同组合并 | 合并计数 | — | DESIGN_DECISION |
| G-09 | 路径不等价-分隔符 | "pkg\\mod.py" vs "pkg/mod.py" | **不同**组（不断言等价） | 分组 | 不得凭猜测合并 | DESIGN_DECISION |
| G-10 | 路径不等价-大小写 | "Pkg/Mod.py" vs "pkg/mod.py" | **不同**组 | 分组 | 不大小写归一 | DESIGN_DECISION |
| G-11 | 绝对 vs 相对 | 记录 file_path 绝对路径 | 不匹配 manifest → 未匹配/项目级 | — | 不强行等同于 rel | DESIGN_DECISION |
| G-12 | 项目级诊断隔离 | js_ts 项目诊断 file_path='' | 入 project_diagnostics[]，**不**入 files[] | 不计入任何文件 n_records；totals.n_records_total 不含 | 不伪装真实文件；不污染文件级统计 | SOURCE_CONFIRMED |
| G-13 | 恒等式（汇总） | 全样本 | n_records==本文件N_raw; proj==Nr+cf; n_records_total==ΣN_raw(不含诊断/冲突项); file_conclusion∉{VERIFIED_FACT} | — | 全样本断言 | DESIGN_DECISION |

**所有测试标记 `NOT_RUN`**（file_conclusion 层未实现；本节为设计推演，非测试通过）。

**结论 7：PASS**（零记录/不可读/上下文未知/hypothesis/冲突引用/路径等价/项目级隔离全覆盖；逐项负向断言；全 NOT_RUN）。

---

## 8. 未决问题（冻结清单，交实施工单）
| # | 未决 | 状态/建议 |
|---|---|---|
| U-1 | 不可读文件枚举表达局限 | UNKNOWN_RESOLUTION + readable_text 标志 + content_status=unreadable；不新增枚举 |
| U-2 | 真实路径零记录极少（:982 恒建 file 节点） | 规格仍防御支持；实施单测用合成零记录组 |
| U-3 | 大小写/绝对路径归并 | 本规格不断言等价；如需须显式决策+测试 |
| U-4 | 敏感文件排除一致性 | 复用 _scrub_sensitive_* （清单与图谱双剔除），不额外处理 |
| U-5 | scanner.py 冻结漂移根因 | 待调查（DESIGN-11 §6），无直接证据不归因；基线须授权下再生 |
| U-6 | 大小写/分隔符在不同 OS | 以 `.as_posix()` 为准；跨 OS 归一留作未来显式决策 |

---

## 9. 文件修改核验（READ-ONLY 保持）
| 文件 | 状态 |
|---|---|
| usit/understanding_projection.py / parser_contract.py / scanner.py / understanding_model.py / schema_adapter.py | 未修改 |
| outputs/p0710zxrxf/PRE_SHA256.json | **未修改**（未更新基线） |
| tests/、配置、manifest、既有报告 | 未修改 |
| 新增 | 仅 `reports/USIT-S1-C-DESIGN-12-执行报告.md` |

---

## 10. 固定门禁复核（原值保持）
F1=NOT_READY_TO_CLOSE / P0.8=HOLD / F2=STOP / 历史授权连续性=UNRESOLVED / 安全信任链=BLOCKED-NOT_VERIFIED / verified_fact=0 / ExecutionRecord=NOT_RUN / summary_verification=NOT_CHECKED / complete=False / BLOCKER-1 不解除 / BLOCKER-2 待接线后复核。

---

## 11. 结论汇总
| 工单项 | 结论 |
|---|---|
| 1 零记录文件覆盖 | **PASS** |
| 2 文件内容状态 | **PASS** |
| 3 hypothesis 分支 | **PASS** |
| 4 冲突引用含义 | **PASS** |
| 5 路径分组契约 | **PASS** |
| 6 输出契约冻结 | **PASS** |
| 7 验收矩阵扩充 | **PASS** |

### 最终结论：**READY_FOR_IMPLEMENTATION_REVIEW**

- 本轮裁定的核心修正：① 文件列表来源=**清单**（非记录遍历），空路径诊断独立进 `project_diagnostics[]`；② 修正 DESIGN-11 `has_content` 混淆——**仅 `size_bytes==0` 定空**，不可读非空文件→分支 2 `UNKNOWN_RESOLUTION`（不判空，携带 readable_text 标志，列枚举局限）；③ hypothesis-only 确认为**分支 8 `UNKNOWN`**（resolved_ev_count 排除 AI_HYPOTHESIS）并给出可单测规格；④ `conflicting_conclusion_ids` 实为 **projection_id**（名≠义），新输出用准确命名；⑤ 路径分组复用 `rel/as_posix()`，不断言大小写/绝对-相对等价；⑥ `understanding.json`（`usit-file-understanding-v1`）顶层/文件级/项目级/totals/未知字段策略全部冻结，既有 schema 零改。
- 所有案例含负向断言且**全标 NOT_RUN**（file_conclusion 未实现；设计推演非测试通过）；`MEANINGFUL_EVIDENCE`/`excerpt_verified` 保持 FUTURE；无 VERIFIED_FACT 升级。
- 冻结规格**可直接用于**后续独立授权实施工单；本工单**不授权**实现/接线/基线更新；固定门禁与 BLOCKER 全原值保持；未修改任何文件（§9）。

---

**授权边界最终声明**：本工单仅授权只读审查与输出契约最终裁定。任何源码修改、接线、测试执行、基线更新、构建、安装、登录、网络访问或真实项目扫描均超出授权范围；进入实施须另行独立授权。
