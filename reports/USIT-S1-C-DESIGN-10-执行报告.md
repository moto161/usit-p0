# USIT-S1-C-DESIGN-10｜文件级结论与分支决策层设计评审 执行报告

- **工单编号**：USIT-S1-C-DESIGN-10
- **执行时间**：2026-10-20（GMT+8）
- **工作区绝对路径**：`C:\Users\Administrator\WorkBuddy\2026-10-15-22-09-47\usit_p0`
- **执行模式**：**严格 READ-ONLY｜仅设计**。不实施、不接线、不运行真实项目扫描/构建/安装/登录/网络；不修改源码/测试/配置/manifest/数据库/哈希基线/既有报告。
- **设计基线**：IMPLEMENT-01（投影机制已实现并测试）、POST-IMPLEMENT-AUDIT-01（READY_FOR_DESIGN_REVIEW）、REVIEW-07 §3/§4（file_conclusion 与 `decide_file_conclusion` 伪代码）、REVIEW-09（计数模型）。
- **边界声明**：`READY_FOR_DESIGN_REVIEW` **不**等于接线实施授权；本报告为设计规格，供制定下一份**实施授权**工单，本身不产生实施/接线授权。

> **三种状态声明**：① 设计已产出 ✅（本报告）；② 源码已实现 ❌（file_conclusion 层未实现，S1-C 未接线）；③ 测试已执行 ❌（本工单 READ-ONLY，本文所有推演均为**设计**，非运行结果）。

---

## 1. 现状与证据位置

### 1.1 已实现（SOURCE_CONFIRMED，本次实读）
| 能力 | 位置 | 状态 |
|---|---|---|
| 逐记录投影 + 互斥计数 `N_*`/`Nr` | `understanding_projection.py:416-484`（`project_records`） | 已实现 + 已测试（PASS） |
| 候选/非候选/未尝试/异常分类 | `:366-388` `_intended_record_category`；`:391-413` 降级项 | 已实现 |
| 证据不伪造 fallback | `parser_contract.py:95-119` `_classify_graph_evidence`；`:122-161` `adapt_graph_v1` | 已实现 + 已测试 |
| 恒等式 (1)-(5) | 代码级推导成立（AUDIT-01 §3） | 已验证 |
| 每记录结论类型 | `understanding_model.py:30-35` `ConclusionType`（5 值，硬阻 VERIFIED_FACT） | 已实现 |

### 1.2 未实现（DESIGN_ONLY / NOT_VERIFIED）
| 能力 | 位置 | 状态 |
|---|---|---|
| **文件级结论 `file_conclusion`** | `understanding_model.py` **无**文件级类型（class 清单仅 ConclusionType/SourceType/.../UncertaintyRecord） | **未实现（新设计）** |
| **分支决策层 `decide_file_conclusion`** | 仅存于 `REVIEW-07 §4` 伪代码（`:192-219`） | **未实现（本工单规格化）** |
| 投影→接线 | `understanding_projection` 在 `usit/` 内无调用方（grep 证实） | **未接线（BLOCKER-1）** |
| `meaningful_count` / `excerpt_verified` | REVIEW-07 F1-F4 未来能力 | **FUTURE（结构性不可达）** |

### 1.3 关键设计约束（来自既有审查，须继承）
- `file_conclusion` 是**独立 8 值枚举分类**，与 `item.complete` **正交**（REVIEW-07 §3，`:134-136`）。
- `project_records` 输出仅 `items`+`stats`，**不计算** file_conclusion/分支（F-11）；分支属本工单规格化的决策层。
- 冲突合成项是**独立追加投影**，`proj = Nr + cf`，**不得**计入原始记录数。
- 静态推断**不得**升级为已验证事实；VERIFIED_FACT 被模型硬阻。

---

## 2. A. 文件级结论模型（DESIGN）

### 2.1 输入
**投影派生信号**（来自单文件的 `ProjectionResult` + items）：
- `n_records` = `N_raw`（= `Nr`，1:1）。
- `has_conflict` = (`conflict` stat > 0) = (`cf` > 0)。
- `all_non_success` = 所有 per-record item 的 `resolution_status ∈ NON_SUCCESS`（可由 items 派生）。
- `all_evidence_empty` = (`N_candidate == 0`)。
- `resolved_ev_count` = 计数 {item.resolution_status=="RESOLVED" 且 item 为候选（有 evidence）}（可由 items 派生；≈ `static_inference`，另计 RESOLVED+hypothesis+evidence 边界）。
- `meaningful_count` = 计数 {RESOLVED 且 `evidence_meaningful=="True"`} —— **当前结构性 0**（依赖 `excerpt_verified`，FUTURE）。

**扫描器文件元数据**（接线时由 scanner 供给，已确认存在）：
- `size_bytes`（`scanner.py:459-462`）、`has_content`（`readable_text`/非空，`:462`）、`has_symbols`（`symbols` 非空，`:462`）、`file_degraded`（`_describe_degradation`/`analysis_diagnostics`，`:401-448`）。

### 2.2 允许状态（8 值枚举，`FileConclusion`——**新类型，待实现**）
| # | 状态 | 语义 | 当前可达 |
|---|---|---|---|
| 1 | `NO_SEMANTIC_CONTENT` | 文件空/无内容（≠无理解，是无内容） | ✅ |
| 2 | `NO_RECORDS` | 有内容但无解析记录（子原因 no_symbols/symbols_no_records） | ✅ |
| 3 | `UNKNOWN_RESOLUTION` | 全部记录非成功，或全部无候选证据 | ✅ |
| 4 | `CONFLICT_ONLY` | 存在冲突且无有意义证据 | ✅ |
| 5 | `DEGRADED_PARSE` | 文件降级且无有意义证据 | ✅ |
| 6 | `MEANINGFUL_EVIDENCE` | 存在已验证/有意义证据 | ❌ **FUTURE**（需 excerpt_verified） |
| 7 | `INSUFFICIENT_EVIDENCE` | 有 RESOLVED+候选证据但无已验证证据（当前典型） | ✅ |
| 8 | `UNKNOWN` | 歧义/无法判定（显式不确定态） | ✅ |

### 2.3 语义区分（不得升级）
- **文件存在/有内容**（分支 0 的反面）≠ 语义理解。
- 每记录 `STATIC_INFERENCE` → 文件级仅能到 `INSUFFICIENT_EVIDENCE`（状态 7），**不是**已验证。
- `AI_HYPOTHESIS` / `UNKNOWN` / `CONFLICT` → 永不成功；文件级不产生成功结论。
- 唯一的"正面"状态 6 依赖**授权验证协议**（excerpt_verified），当前**不存在** ⇒ 当前构建**无任何文件级"已验证"出口**。这与 `verified_fact=0` 门禁一致。

### 2.4 输出
`FileConclusion` 记录：`{file_path, conclusion(枚举), reasons[], n_records, conflict_count, resolved_ev_count, evidence_refs(支持记录的 projection_id/结论 id), projection_schema_version}`。**绝不**包含 `VERIFIED_FACT` 或数值置信度。

### 2.5 优先级 / 冲突 / 未知 / 降级
- **优先级**：有序首次命中（见 §3 决策表），互斥。
- **冲突处理**：`has_conflict` 且 `meaningful==0` → 分支 3；但若先被分支 2 吞并（如 C21 全非成功），冲突**仍单独计数**（cf 不变），仅 file_conclusion 取 UNKNOWN_RESOLUTION。冲突不改变原始记录计数。
- **未知处理**：分支 8 `UNKNOWN` 为显式歧义出口，**不**静默映射为成功或具体成因。
- **降级**：`file_degraded` 且 `meaningful==0` 且未被 2/3 吞并 → 分支 4 `DEGRADED_PARSE`。

**A 结论：PASS（设计完整；状态 6 标 FUTURE，当前无可验证出口，遵守不升级红线）。**

---

## 3. B. 分支决策模型（DESIGN，规格化 REVIEW-07 §4）

### 3.1 决策表（有序首次命中，互斥）
| 优先级 | 分支 | 触发条件 | file_conclusion | reasons | 典型样例 |
|---|---|---|---|---|---|
| 0 | empty | `size_bytes==0` 或 `not has_content` | NO_SEMANTIC_CONTENT | empty_or_no_semantic_content | C4 |
| 1 | no_records | `n_records==0` | NO_RECORDS | no_symbols（无符号）/ symbols_no_records（有符号无记录） | C5/C6 |
| 2 | all_non_success_or_no_evidence | `all_non_success` 或 `all_evidence_empty` | UNKNOWN_RESOLUTION | all_records_non_success_or_no_evidence | C3/C18/C20/C21 |
| 3 | conflict_only | `has_conflict` 且 `meaningful==0` | CONFLICT_ONLY | conflict_pending | C10 |
| 4 | degraded | `file_degraded` 且 `meaningful==0` | DEGRADED_PARSE | degraded_evidence | C7 |
| 6 | meaningful **[FUTURE]** | `meaningful>=1` | MEANINGFUL_EVIDENCE | has_meaningful_evidence(±degraded_evidence) | —（不可达） |
| 7 | insufficient | `resolved_ev_count>=1` | INSUFFICIENT_EVIDENCE | missing_or_empty_evidence | C1/C2/C11-C15 |
| 8 | unknown(兜底) | else | UNKNOWN | indeterminate_resolution_without_usable_evidence | C19 |

> 分支 2 的越过充要条件（REVIEW-07 §2.1）：文件有记录 且 ∃RESOLVED 且 ∃候选证据。分支 3/4/7/8 均在已越过分支 2 的域内判定。

### 3.2 信号精确定义（投影/扫描器来源）
| 信号 | 定义 | 来源 |
|---|---|---|
| `n_records` | `N_raw` | 投影 stats |
| `all_non_success` | 所有 item.resolution_status ∈ NON_SUCCESS | 投影 items |
| `all_evidence_empty` | `N_candidate == 0` | 投影 stats |
| `has_conflict` | `conflict > 0` | 投影 stats |
| `resolved_ev_count` | #{RESOLVED ∧ 候选} | 投影 items 派生 |
| `meaningful_count` | #{RESOLVED ∧ evidence_meaningful} = **结构性 0** | FUTURE |
| `file_degraded` | 行级降级元数据 | 扫描器 |
| `has_symbols`/`size_bytes`/`has_content` | 文件元数据 | 扫描器 |

### 3.3 互斥性 / 无法判定 / 证据引用
- **互斥**：有序首次命中 ⇒ 每个输入恰得一分支（可用自动化断言验证：对任意输入，`decide` 返回唯一 (conclusion, branch)）。
- **无法判定**：落分支 8 `UNKNOWN`（显式歧义），附 `indeterminate_resolution_without_usable_evidence`；**不**默认高置信态。
- **证据引用**：每个 file_conclusion 附 `reasons[]` + 支持记录的 `projection_id`/结论 id（可追溯至具体证据）。

### 3.4 最小正反例（供 Task E 编号化）
| 分支 | 正例（命中） | 反例（不命中） |
|---|---|---|
| 0 | size_bytes==0 | size_bytes>0 且有记录 |
| 1 | 有内容、0 记录、无符号 | ≥1 记录 |
| 2 | 全 NON_SUCCESS（C3）/ 全无候选（C18） | 存在 RESOLVED+候选（C1） |
| 3 | 冲突 + meaningful==0 + 过 2（C10） | 无冲突 |
| 4 | file_degraded + meaningful==0 + 过 2/3（C7） | 未降级 |
| 7 | resolved_ev_count>=1 + 过 2/3/4（C1） | resolved_ev_count==0 |
| 8 | C19（RESOLVED 非Mapping证据 + NON_SUCCESS 候选） | 有 RESOLVED+候选 |

**红线（不得弱信号高置信）**：**任何分支不得仅凭文件名/扩展名/单一弱信号定论**。`has_symbols` 必须来自真实符号提取（非扩展名推断）；`file_degraded` 必须来自真实解析诊断（非扩展名）；无分支以 filename 为条件。

**B 结论：PASS（决策表有序互斥、信号可派生或来自扫描器、无法判定有显式出口、无弱信号高置信）。**

---

## 4. C. 投影→文件级聚合规则（DESIGN）

| 场景 | 聚合规则 | 对计数的影响 |
|---|---|---|
| 一文件多记录 | `n_records=N_raw`；逐记录→item；分支用聚合信号 | 每记录 1 item，1:1 |
| 重复记录（同 ID） | 计入 `N_raw`（两条均在）；`duplicate_records` 为独立设计量（**不在** N_*）；同指纹不产冲突 | 不加 cf |
| 同身份异内容 | 冲突：`cf>0`→`has_conflict` | 冲突项**独立追加**，`proj=Nr+cf` |
| 跨状态冲突 | C10（RESOLVED+UNRESOLVED）/C21（UNRESOLVED+AMBIGUOUS）：指纹不同→冲突组 | cf=组数 |
| 无记录 | `n_records==0`→分支 1 | 全计数 0 |
| 有符号无记录 | `n_records==0` 且 `has_symbols`→分支 1(symbols_no_records) | 需扫描器 has_symbols |
| 孤立记录（orphan） | 文件上下文影响 item 完整性，**不**改 file_conclusion 分支，**不**改 n_records/has_conflict | 正交 |
| 无效证据（非 Mapping） | 记录→`N_evidence_none`/UNKNOWN item；若全部如此→`all_evidence_empty` | 计入 N_evidence_none |
| 诊断越界 | 文件/条目级关注点，不改候选性（line 非法在 `_build_evidence` 丢弃） | 正交 |
| 不同来源记录 | adapt_graph_v1 节点 + js_ts + analysis 诊断均为 parser-contract 记录→统一计入 `N_raw`、统一投影；**不**按来源分支（避免弱信号） | 统一计数 |

**核心不变量**：`n_records = N_raw`（冲突合成项追加**前**的原始记录数）；冲突合成项（cf）是**附加投影**，**绝不**并入 `n_records` 或任何 per-record `N_*`。即文件级统计用 `N_raw`，**永不**用 `proj`。

**C 结论：PASS（聚合规则明确，冲突项不重复计入原始记录数，orphan/诊断越界/来源为正交量）。**

---

## 5. D. 输出契约草案（DESIGN，不擅自改既有 schema）

### 5.1 ProjectionResult ↔ 文件级结论关系
- `ProjectionResult`（items+stats）是**单文件**（或按 file_path 分组）的输入；`file_conclusion = decide(file_context, projection_signals)`。
- **分组决策**：投影输出按 `file_path` 分组，每组一个 `ProjectionResult`→一个 `FileConclusion`（一文件一结论）。

### 5.2 JSON 字段草案（新构件，schema `usit-file-understanding-v1`）
```jsonc
// understanding.json（新文件，加法式；不改 report/scan_manifest/knowledge_graph/test_plan/parser_contract）
{
  "schema_version": "usit-file-understanding-v1",
  "projection_schema_version": "usit-understanding-projection-v1",
  "files": [
    {
      "file_path": "pkg/mod.py",
      "file_conclusion": "INSUFFICIENT_EVIDENCE",   // 8 值枚举之一；绝无 VERIFIED_FACT
      "branch": 7,
      "reasons": ["missing_or_empty_evidence"],
      "n_records": 1,                                 // == N_raw，非 proj
      "conflict_count": 0,                            // == cf
      "resolved_ev_count": 1,
      "meaningful_count": 0,                          // 结构性 0（FUTURE）
      "evidence_refs": ["proj:...", "conc:..."],      // 可追溯 projection_id/结论 id
      "counts": { "N_raw":1,"N_candidate":1,"N_non_candidate":0,"N_construct_ok":1,
                  "N_construct_fail":0,"N_not_attempted":0,"N_unexpected_error":0,
                  "N_evidence_none":0,"Nr":1 }
    }
  ],
  "totals": { "files": 1, "by_conclusion": {"INSUFFICIENT_EVIDENCE":1} }
}
```

### 5.3 schema 版本 / 向后兼容 / 落点 / 未知字段
- **版本**：新 `usit-file-understanding-v1`；既有 `PROJECTION_SCHEMA`/`CONTRACT_VERSION` 等**不改**。
- **向后兼容**：纯加法；既有输出文件**零改动**；消费方按 `schema_version` 门控，**忽略未知字段**（前向兼容），缺失可选字段按"缺席"处理（**不**伪造）。
- **候选落点**：由 `scan_project` 在 `scanner.py:1160` 的 `write_json(...)` 处**新增** `understanding.json`（加法，不改既有文件名/结构）；`report.md` 增一节人类可读摘要（markdown 加法，低风险）。**不**向 report.json/scan_manifest.json 追加键（那会改其 schema）。
- **未知字段处理**：读取方忽略未知键；`file_conclusion` 仅接受 8 值枚举，异值→该文件记 `UNKNOWN` + 原因 `invalid_conclusion_value`（不崩溃、不猜测）。

**D 结论：PASS（新加法式 understanding.json，既有 schema 零改动，版本门控 + 未知字段忽略 + 缺席不伪造）。**

---

## 6. E. 编号化验收矩阵（DESIGN；file_conclusion 未实现 ⇒ 全部 NOT_RUN 直至实施）

> 映射 C1–C21 + 边界；`A`=可自动化契约（合成记录/合成 file_context 单测），`S`=需真实扫描或人工审查，`F`=FUTURE 不可达。

| 编号 | 输入（records + file_context） | 预期 branch/file_conclusion | 负向断言 | 类型 |
|---|---|---|---|---|
| FC-01(C1) | 1×RESOLVED候选；size>0,has_symbols,未降级 | 7 / INSUFFICIENT_EVIDENCE | ≠VERIFIED/≠MEANINGFUL；N_raw=1 | A |
| FC-02(C2) | 1×RESOLVED 仅{graph_node_id}（历史输入） | 7 / INSUFFICIENT_EVIDENCE | 不因 graph_node_id 升级 | A |
| FC-03(C3) | 1×NON_SUCCESS候选 | 2 / UNKNOWN_RESOLUTION | 不计成功 | A |
| FC-04(C4) | size_bytes==0 | 0 / NO_SEMANTIC_CONTENT | — | A |
| FC-05(C5) | 内容,0 记录,无符号 | 1 / NO_RECORDS(no_symbols) | — | A |
| FC-06(C6) | 内容,0 记录,有符号 | 1 / NO_RECORDS(symbols_no_records) | — | A |
| FC-07(C7) | RESOLVED候选 + file_degraded | 4 / DEGRADED_PARSE | 降级不伪造证据 | A |
| FC-08(C10) | RESOLVED+NON_SUCCESS 同身份（冲突） | 3 / CONFLICT_ONLY | cf=1；n_records=2（非 proj=3） | A |
| FC-09(C16) | 2×RESOLVED 相同（重复） | 7 / INSUFFICIENT_EVIDENCE | cf=0（重复非冲突） | A |
| FC-10(C17) | NON_SUCCESS+RESOLVED 不同身份 | 7 / INSUFFICIENT_EVIDENCE | cf=0 | A |
| FC-11(C18) | RESOLVED 非Mapping证据 | 2 / UNKNOWN_RESOLUTION | N_candidate=0；不计成功 | A |
| FC-12(C19) | RESOLVED非Mapping + NON_SUCCESS候选 | 8 / UNKNOWN | 不静默落它支；非成功 | A |
| FC-13(C20) | 2×NON_SUCCESS 不同身份 + 降级 | 2 / UNKNOWN_RESOLUTION | 分支2 吞并降级 | A |
| FC-14(C21) | 2×NON_SUCCESS 同身份异内容 | 2 / UNKNOWN_RESOLUTION | cf=1；分支2 优先不影响 cf | A |
| FC-15(C8) | NON_SUCCESS候选 + 降级 | 2 / UNKNOWN_RESOLUTION | 降级被序2吞并 | A |
| FC-16(C11-C15) | RESOLVED候选 + orphan/diag越界/path∉manifest/line非正/TestSpec缺必填 | 7 / INSUFFICIENT_EVIDENCE | 文件级缺陷不降级结论；正交 | A |
| FC-B1 | 多冲突组（X,Y 各冲突） | 3 / CONFLICT_ONLY | cf=2；n_records=4 | A |
| FC-B2 | 混合来源（graph 节点 + js_ts 诊断记录） | 按信号判定（非按来源） | 不按来源分支 | A |
| FC-17 | 恒等式：file_conclusion 不变量 | n_records==N_raw；proj==Nr+cf；file_conclusion∉{VERIFIED_FACT} | 全样本断言 | A |
| FC-18 | 真实项目扫描端到端 | understanding.json 生成，抽查符合 FC-01.. | 需真实扫描 | S |
| FC-19 | file_degraded 来自真实解析诊断 | DEGRADED_PARSE 真实触发 | 需真实降级文件 | S |
| FC-20(C 状态6) | excerpt_verified 有意义证据 | MEANINGFUL_EVIDENCE | **FUTURE 不可达** | F |

**可自动化 vs 需真实扫描/人工**：FC-01..FC-17 + FC-B* 可用合成记录/合成 file_context 单测（A）；FC-18/19 需真实扫描（S）；FC-20 需授权验证协议（F，当前不可达）。**以上全部 NOT_RUN**（file_conclusion 未实现）。

**E 结论：PASS（矩阵编号化、含负向/冲突/未知/证据不足/恒等式，且区分可自动化/需扫描/FUTURE）。**

---

## 7. F. 接线前置条件清单（DESIGN）

| # | 前置条件 | 说明 |
|---|---|---|
| F-1 | **实施授权工单** | 修改 `scanner.py`/新增 `file_conclusion` 层需独立授权（本工单不授权） |
| F-2 | **接口** | 新增 `decide_file_conclusion(file_context, projection_signals) -> FileConclusion`（新模块或 `understanding_projection` 扩展）；不破坏既有 API |
| F-3 | **数据契约** | 输入=投影 signals（§2.1）+ scanner file_context（size_bytes/has_content/has_symbols/file_degraded）；按 file_path 分组 |
| F-4 | **错误处理** | 决策层不因畸形输入崩溃（降级 UNKNOWN+reason）；批次级错误（如投影 TypeError）上抛，不伪装 |
| F-5 | **输出版本** | 新增 `understanding.json`（`usit-file-understanding-v1`），`scanner.py:1160` 加法写入；既有文件零改动 |
| F-6 | **回归测试** | 既有测试全绿 + 新增 FC-01..FC-17 单测；`test_s1c_cases` 扩展至 file_conclusion |
| F-7 | **哈希基线治理** | `scanner.py` 受 `ZXRXF7_WATCH` 冻结且**已漂移**（见 §8）；接线改 scanner.py 会持续触发守卫失败 |
| F-8 | **端到端验收** | 真实扫描产出 understanding.json；符合 FC-18/19；确认无 VERIFIED_FACT、冲突不双计、门禁不变 |

### 7.1 scanner.py 冻结哈希漂移（独立问题记录）
- **事实**：基线 `outputs/p0710zxrxf/PRE_SHA256.json`（Oct-17）记录 `scanner.py=c70a1a4b…`，当前 `a009b8db…`（已漂移）；其余 6 监视文件相符（孤立单文件漂移）；非 IMPLEMENT-01 引入（AUDIT-01 §6）。
- **处置原则**：**本工单不更新 `PRE_SHA256.json`，不把其陈旧当作更新授权的依据。** 基线再生必须是接线实施工单内一个**显式、独立授权**的步骤，附自带审计轨迹（谁授权、为何、前后哈希、复核命令），与代码改动分离留痕。

**F 结论：PASS（前置条件完整；哈希漂移作独立问题记录，未更新基线、未视为授权）。**

---

## 8. 未决问题（DESIGN，供实施工单裁定）

| # | 未决问题 | 建议 |
|---|---|---|
| U1 | `resolved_ev_count` 精确口径（RESOLVED∧候选 vs STATIC_INFERENCE 计数，含 hypothesis 边界） | 实施时以 item.resolution_status=="RESOLVED" ∧ 有 evidence_refs 精确判定，并加单测固化 |
| U2 | file_context 中 `has_content` 定义（readable_text vs 非空字节） | 用 `size_bytes>0 and readable_text`，与 scanner 行字段对齐 |
| U3 | `MEANINGFUL_EVIDENCE`(状态6) 命名与 excerpt_verified 协议 | 属 F1-F4 未来能力，另行授权；本设计仅占位标 FUTURE |
| U4 | understanding.json 是否需要 totals/by_conclusion 汇总 | 建议含（低风险加法），便于人工审查 |
| U5 | cli.py 是否需新子命令/flag | 默认无需（cli 薄封装 scan_project 自动继承）；如需显式入口另行设计 |
| U6 | orphan/diagnostics越界 是否影响 file_conclusion | 设计定为**正交**（仅影响 item 完整性）；如后续要求体现，需新增枚举值并重审恒等式 |

---

## 9. 固定门禁复核（原值保持）

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
| `BLOCKER-1`（投影未接线） | **未解除** |
| `BLOCKER-2`（fallback） | 机制已修正待接线后复核，本工单不解除 |

未宣称可信项目理解/完整测试/自动修复；未修改任何源码/测试/配置/manifest/基线/既有报告；未运行真实扫描/构建/登录/网络。

---

## 10. 结论汇总与最终结论

| 任务 | 结论 |
|---|---|
| A 文件级结论模型 | **PASS**（8 值枚举、优先级、冲突/未知/降级、不升级红线；状态6 FUTURE） |
| B 分支决策模型 | **PASS**（有序互斥决策表、信号可派生、显式未知出口、无弱信号高置信） |
| C 聚合规则 | **PASS**（冲突项不双计入原始记录数；orphan/诊断/来源正交） |
| D 输出契约 | **PASS**（新加法式 understanding.json，既有 schema 零改动） |
| E 验收矩阵 | **PASS**（FC-01..20 编号化，区分可自动化/需扫描/FUTURE；全 NOT_RUN） |
| F 接线前置条件 | **PASS**（F-1..F-8；哈希漂移独立记录，未更新基线） |

### 最终结论：**READY_FOR_IMPLEMENTATION_REVIEW**

- 文件级结论模型、分支决策表、聚合规则、输出契约、编号化验收矩阵、接线前置条件均已形成**可独立审查**的设计规格，满足据此制定下一份**实施授权**工单的条件。
- file_conclusion 层**未实现、未接线、未测试**（本文推演均为设计，非运行结果）；当前构建**无文件级"已验证"出口**，与 `verified_fact=0` 门禁一致。
- `scanner.py` 冻结哈希漂移作为**独立问题**记录，未更新基线、未视为授权；接线实施须先获得独立授权并在授权下权威再生基线。
- 固定门禁与 BLOCKER 全原值保持；本报告**不构成**实施或接线授权。

---

**设计边界最终声明**：本工单仅授权文件级结论与分支决策层的**设计**及只读证据复核。任何源码修改、接线、基线更新、构建、安装、登录、网络访问或真实项目扫描均超出授权范围。进入实施须另行独立授权。
