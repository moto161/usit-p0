# USIT-S1-C-DESIGN-11｜文件级结论设计矛盾裁定与实施规格冻结（READ-ONLY）

- **工单编号**：USIT-S1-C-DESIGN-11
- **执行时间**：2026-10-20（GMT+8）
- **工作区绝对路径**：`C:\Users\Administrator\WorkBuddy\2026-10-15-22-09-47\usit_p0`
- **执行模式**：**严格 READ-ONLY**。只读核对源码与既有报告，裁定设计矛盾；**禁止**修改源码/测试/配置/数据库/manifest/报告/冻结哈希基线；**禁止**接线/真实扫描/构建/安装/登录/网络。
- **前置**：USIT-S1-C-DESIGN-10（设计规格）、IMPLEMENT-01 + POST-IMPLEMENT-AUDIT-01（投影机制已实现并审计）、REVIEW-07 §3/§4、REVIEW-09。
- **目标**：在 DESIGN-10 基础上形成**冻结实施规格**，供后续**独立授权**实施工单直接使用。
- **授权边界**：本工单**不授权**任何实现/接线/基线更新；即使结论为 READY_FOR_IMPLEMENTATION_REVIEW，仍须等待后续独立实施授权。

> **标签约定**：`SOURCE_CONFIRMED`=本次实读源码/实测坐实；`DESIGN_DECISION`=本工单设计裁定；`NOT_VERIFIED`=缺直接证据；`FUTURE`=依赖未授权能力、当前不可达。设计推演**均非运行结果**。

---

## 1. 分支与诊断语义裁定（对应工单 1）

### 1.1 矛盾识别：主结论覆盖冲突/降级信息（DESIGN_DECISION 依据）
DESIGN-10 的有序决策表中，**分支 2（UNKNOWN_RESOLUTION）会吞并冲突与降级**：
- C21（2×NON_SUCCESS 同身份异内容）：`all_non_success=True` → 分支 2 → `file_conclusion=UNKNOWN_RESOLUTION`，但**实际存在冲突** `cf=1`。
- C8/C20（NON_SUCCESS + 文件降级）：分支 2 → `UNKNOWN_RESOLUTION`，但**实际降级** `file_degraded=True`。

⇒ 单看 `file_conclusion` 会**丢失**"有冲突"/"已降级"信息（主结论覆盖诊断）。

### 1.2 裁定：保留独立诊断标志，不改 8 值枚举（DESIGN_DECISION）
- **不改变**既有 8 值 `file_conclusion` 枚举，**不改变**有序决策表与分支优先级。
- **新增独立诊断标志**（与分支解耦、独立计算）进 `FileConclusion` 输出：
  - `has_conflict` = (`conflict_count > 0`)，**无论命中何分支**都如实呈现。
  - `file_degraded` = 扫描器降级信号（tri-state，见 §5），**无论命中何分支**都如实呈现。
  - `conflict_count` = `cf`（冲突组数）。
- 效果：C21 → `file_conclusion=UNKNOWN_RESOLUTION` **且** `has_conflict=true, conflict_count=1`；C8/C20 → `UNKNOWN_RESOLUTION` **且** `file_degraded=true`；C10 → `CONFLICT_ONLY` **且** `has_conflict=true`（一致）。信息不再被主结论覆盖。
- **兼容影响**：纯加法（新字段）；不改枚举值/分支结果；既有 `file_conclusion` 消费方零影响。

### 1.3 逐分支复核（优先级 = 有序首次命中）
| 分支 | 条件 | file_conclusion | 优先级 | 与独立标志关系 | 标签 |
|---|---|---|---|---|---|
| 0 | `has_content==False`（确定空） | NO_SEMANTIC_CONTENT | 最高 | — | DESIGN_DECISION |
| 1 | `n_records==0` | NO_RECORDS(no_symbols/symbols_no_records/symbol_status_unknown) | 2 | — | DESIGN_DECISION |
| 2 | `all_non_success` 或 `all_evidence_empty` | UNKNOWN_RESOLUTION | 3 | 吞并冲突/降级→由独立标志保留 | DESIGN_DECISION |
| 3 | `has_conflict` 且 `meaningful==0` | CONFLICT_ONLY | 4 | 与 has_conflict 标志一致 | DESIGN_DECISION |
| 4 | `file_degraded==True` 且 `meaningful==0` | DEGRADED_PARSE | 5 | 与 file_degraded 标志一致 | DESIGN_DECISION |
| 6 | `meaningful>=1` | MEANINGFUL_EVIDENCE | 6 | — | **FUTURE** |
| 7 | `resolved_ev_count>=1` | INSUFFICIENT_EVIDENCE | 7 | — | DESIGN_DECISION |
| 8 | else | UNKNOWN | 兜底 | — | DESIGN_DECISION |

**结论 1：PASS**（矛盾已识别并裁定：主结论保持有序优先级 + 新增 has_conflict/file_degraded/conflict_count 独立诊断标志，解决信息覆盖；8 值枚举未改；兼容性为纯加法）。

---

## 2. 证据与状态边界裁定（对应工单 2）

### 2.1 resolved_ev_count 精确口径（DESIGN_DECISION，消除 DESIGN-10 的 U1 模糊）
**定义**：`resolved_ev_count` = 逐记录 item 中满足 `resolution_status=="RESOLVED"` **且** `conclusion_type==STATIC_INFERENCE` 的计数。

### 2.2 五态精确区分（SOURCE_CONFIRMED：`_classify` :100-119；ConclusionType :30-35）
| 概念 | 性质 | 定义 | 是否计入 resolved_ev_count |
|---|---|---|---|
| `RESOLVED` | resolution_status 值 | 记录解析状态为 RESOLVED（`∈RESOLUTION_STATUSES` 且 `∉NON_SUCCESS`） | 前提条件 |
| `STATIC_INFERENCE` | conclusion_type | RESOLVED + 候选证据 + 非 hypothesis | **是** |
| `AI_HYPOTHESIS` | conclusion_type | RESOLVED + 候选证据 + `hypothesis=True` | **否**（显式假设，非可靠静态推断） |
| `UNKNOWN` | conclusion_type | 非成功状态 / 缺失·空·非Mapping证据 / 非法状态 / 非Mapping记录 | 否 |
| `CONFLICT` | conclusion_type | 仅 `_detect_conflicts` 合成项（:283），从不出现在逐记录 item | 否（非逐记录） |

### 2.3 裁定后果与备选（DESIGN_DECISION）
- 采用保守口径：**仅 STATIC_INFERENCE 计入** resolved_ev_count。
- 后果：仅有"RESOLVED+AI_HYPOTHESIS"记录的文件 → `resolved_ev_count=0` →（通过分支 2，因它是 RESOLVED+候选）无冲突、未降级 → **分支 8 UNKNOWN**（诚实的歧义态，而非暗示存在可靠静态证据）。
- 备选（计入 hypothesis → 分支 7 INSUFFICIENT_EVIDENCE）已评估：会让"仅假设"文件被标为"有静态证据但不充分"，过度暗示；不采用。兼容影响：仅影响 hypothesis-only 边界文件（真实 scanner 路径不产 hypothesis，`adapt_graph_v1` 不设该字段）。

### 2.4 状态升级红线（SOURCE_CONFIRMED + DESIGN_DECISION）
- `MEANINGFUL_EVIDENCE`（状态 6）**保持 FUTURE**：仅在 `excerpt_verified` 授权验证协议**真实存在并通过测试**后方可达（REVIEW-07 F1-F4）。
- **禁止任何状态自动升级为 `VERIFIED_FACT`**：`ConclusionRecord.__post_init__` 硬阻 VERIFIED_FACT（`understanding_model.py:278-279,316-320`）；`project_records` 的 `verified_fact` 恒 0（:471）。file_conclusion **无**"已验证"出口。

**结论 2：PASS**（resolved_ev_count 精确定义、五态区分、MEANINGFUL_EVIDENCE 保持 FUTURE、无 VERIFIED_FACT 升级）。

---

## 3. ID 与证据追溯核实（对应工单 3）

### 3.1 存在性/稳定性/唯一性（SOURCE_CONFIRMED + 实测）
| ID | 位置 | 存在 | 稳定(确定性) | 唯一性 |
|---|---|---|---|---|
| `projection_id` | `:195-205` `_stable_id("proj",…)` | ✅ | ✅ 内容派生 SHA-256 | **否**：重复记录共享（实测 2 条相同 → 1 个 distinct id） |
| `conclusion_id` | `:216` `_stable_id("conc",…)` | ✅ | ✅ | **否**：同 projection_id 派生，重复共享 |
| `uncertainty_id` | `:231`/`:168` | ✅ | ✅ | 内容派生 |
| 冲突项 `projection_id` | `:280` `_stable_id("conflict",path,symbol,ids)` | ✅ | ✅ | 每组唯一（含冲突 ids 集合） |

> **关键事实**：`projection_id`/`conclusion_id` 是**内容标识**（content identity），**非记录实例唯一键**；相同内容→相同 ID（重复记录场景）。规格**不得**假定其按实例唯一。

### 3.2 不依赖伪造 ID 的追溯方案（DESIGN_DECISION）
- 追溯链：`file_path`（分组键）→ 原始记录（成员）→ 投影 item（1:1，携 `projection_id`=内容 id）→ `conclusion_id`（有结论时）或 `uncertainty_id`（UNKNOWN 时）。
- 冲突合成项：独立 `conflict:…` projection_id + `uncertainty.conflicting_conclusion_ids`（引用冲突记录的 projection_id，实测 2 refs）。
- **重复记录**：共享 `projection_id` 标识"该内容"；实例数由 `N_raw` 体现。`evidence_refs` 引用 `projection_id`（内容 id）。
- **实例级定位**（如需区分重复实例）：用复合定位 `(file_path, ordinal_index)`，**不伪造**新 ID。

### 3.3 引用关系表（DESIGN_DECISION）
| 从 | 到 | 关系 | 依据 |
|---|---|---|---|
| file_path | 原始记录 | 1→N（分组） | 记录 source_location.file_path/file_path |
| 原始记录 | 投影 item | 1→1（project_record） | :152-248 |
| 投影 item | conclusion_id / uncertainty_id | 1→(0/1) | :213-237 |
| 冲突合成项 | 冲突记录的 projection_id | 1→N（conflicting_conclusion_ids） | :280,:293 |

**结论 3：PASS**（ID 存在且稳定；唯一性限制已查实并在规格中如实处理；追溯方案不依赖伪造 ID；引用关系明确）。

---

## 4. 实际分组与计数核实（对应工单 4）

### 4.1 project_records 作用域（SOURCE_CONFIRMED + 实测）
- **批次级**：`project_records(records)` 接受任意 Sequence（可混多文件）；`N_raw=len(records)` 统计**全部输入**（实测 2 文件混合 → N_raw=2）。**不按文件分组**。
- 冲突键 = `(source_location.file_path, qualified_name)`（:257-260）⇒ **跨文件永不冲突**（实测：a.py/foo + b.py/foo → conflict=0）。

### 4.2 文件级分组裁定（DESIGN_DECISION）
- 文件结论层**必须先按 `file_path` 分组**原始记录，再**逐文件组**调用 `project_records`。
- 由此：文件级 `N_raw` = 该组原始记录数（**仅该文件**）；冲突合成项（cf）独立、**不计入 N_raw**。
- **冲突安全**：因冲突键含 file_path，跨文件记录永不冲突，故逐文件分组**不会遗漏**任何冲突（不存在跨文件冲突）。

### 4.3 最小反例（对应工单要求）
| 反例 | 输入 | 预期 | 污染检查 |
|---|---|---|---|
| 跨文件同身份 | a.py/foo RESOLVED + b.py/foo UNRESOLVED | 各组 N_raw=1, cf=0；无冲突 | 跨文件不冲突 ✅ |
| 跨文件冲突 | （构造上不可能） | 不存在 | 键含 file_path ✅ |
| 多来源诊断记录 | analysis 诊断（file_path=r['path']）入各自文件；js_ts 项目诊断（file_path=''） | 前者计入所属文件 N_raw；后者入空路径桶，**不属任何真实文件**，项目级处理 | 真实文件 N_raw 仅含本文件记录 ✅ |

> js_ts 项目诊断 `file_path=''`（scanner.py:1062-1065）是**已确认**的多来源边界（SOURCE_CONFIRMED）：空 file_path 记录不进任何真实文件的 N_raw，须在项目级单独处理（见 §7 未决 U-7）。

**结论 4：PASS**（project_records 批次级已核实；文件级分组裁定使 N_raw 文件作用域纯净；跨文件不污染；多来源空路径记录有明确处置）。

---

## 5. 文件上下文契约裁定（对应工单 5）

### 5.1 信号来源（SOURCE_CONFIRMED）
| 信号 | 来源 | 位置 |
|---|---|---|
| `size_bytes` | `row['size_bytes']`（int，analyze_file 恒设） | scanner.py:459-462 |
| `readable_text` | `row['readable_text']`（bool，恒设） | :462 |
| `symbols` | `row['symbols']`（list，恒设；:474/:480/:915 填充） | :462 |
| `analysis_degradation` | `row['analysis_degradation']=_degradation_metadata(row,lang)` | :495（:945 重算）；**additive/optional**（:493，防御读取 :939 `.get(...) or {}`） |

### 5.2 三态契约（DESIGN_DECISION：缺失≠否定事实）
每个信号取 `True / False / UNKNOWN`：
- `has_content` = `size_bytes` 是 int>0 且 `readable_text is True`；`size_bytes`/`readable_text` 缺失或类型错 → **UNKNOWN**（**不**默认空）。
- `has_symbols` = `symbols` 是 list 且非空；缺失/非 list → **UNKNOWN**（**不**默认"无符号"）。
- `file_degraded` = `analysis_degradation.get('degraded')` 当且仅当其为 bool；`analysis_degradation` 缺失或 `degraded` 非 bool → **UNKNOWN**（**不**默认"未降级"）。

### 5.3 分支守卫仅用确定值（DESIGN_DECISION）
- 分支 0 **仅** `has_content==False`（确定空）时命中；`has_content==UNKNOWN` → 不命中分支 0。
- 分支 1 子原因：`has_symbols==False`→no_symbols；`==True`→symbols_no_records；`==UNKNOWN`→**symbol_status_unknown**。
- 分支 4 **仅** `file_degraded==True` 命中；`==UNKNOWN` → 不命中分支 4，且结论携带"降级状态不确定"注记（`file_degraded` 独立标志=UNKNOWN）。
- **必要信号 UNKNOWN → 保留 UNKNOWN 结论或显式降级原因**，绝不把缺失当作否定事实。

**结论 5：PASS**（来源已核实；三态契约确保缺失不默认真空/无降级；分支仅用确定值；必要信号不可判定保留 UNKNOWN）。

---

## 6. 冻结哈希治理（只读调查，对应工单 6）

### 6.1 差异事实（SOURCE_CONFIRMED）
- 守卫基线 `outputs/p0710zxrxf/PRE_SHA256.json`（**文件时间 Oct-17 17:19**）记录 `usit/scanner.py = c70a1a4baf3a6a17cb7ec0ed4748db7194d70e3008ab32f0ac05e3e55f797912`。
- 当前 `usit/scanner.py = a009b8dbb1329c2de030089ea4b441288812cbd5dbaf5eead4bc6b5c472be416`（**已漂移**）。
- 其余 6 个 `ZXRXF7_WATCH` 文件当前哈希与基线**逐一相符** ⇒ 孤立单文件漂移。

### 6.2 哈希时间线（SOURCE_CONFIRMED：多个 PRE 变体记录不同 scanner.py 哈希）
`56902af7`(p0710r–v) → `17ea4964`(p0710w–x) → `99859c7a`(p0710y) → `c70a1a4b`(p0710zxrxf, Oct-17) → 当前 `a009b8db`。
⇒ scanner.py 在多次会话中演进；当前值**新于** Oct-17 基线。

### 6.3 归因边界（NOT_VERIFIED：无直接证据，不归因）
- **无**把漂移归因到某一次具体修改的**直接证据**（缺跨会话的 scanner.py 前后哈希链）。按工单要求**不归因**，记录为"自 Oct-17 基线漂移，原因待调查"。

### 6.4 建议调查/审计步骤（只读，DESIGN_DECISION）
1. 枚举 Oct-17 17:19 至 Oct-20 间的会话/工单（S1-A/S1-B 及各 REVIEW/IMPL）。
2. 收集各工单报告/产物中记录的 scanner.py 前后哈希（若有）。
3. 用 §6.2 时间线 + 各会话 pre/post 哈希尝试链接成链（session_N.post == session_{N+1}.pre）。
4. 仅当出现**直接哈希链证据**时方归因；否则维持"待调查"。
5. **禁止**在无授权下更新 `PRE_SHA256.json`；基线再生必须是独立授权步骤并附审计轨迹。

**结论 6：PASS（只读）**（差异与时间线已核实；未归因（无直接证据）；仅提出调查步骤；未更新基线、未消除漂移）。

---

## 7. 冻结实施规格（对应工单 7）

### 7.1 修订决策表（冻结；DESIGN_DECISION，分支语义同 DESIGN-10 + §1 独立标志）
输入信号（三态）→ 有序首次命中 → `file_conclusion`（8 值）+ 独立标志 `{has_conflict, conflict_count, file_degraded}` + `reasons[]` + `evidence_refs[]`。
（分支表见 §1.3；此处冻结"分支定主结论、独立标志定冲突/降级信息"的二元结构。）

### 7.2 字段契约（冻结；FileConclusion 输出）
```
FileConclusion {
  file_path: str                         # 分组键（SOURCE_CONFIRMED 来源）
  file_conclusion: Enum8                 # NO_SEMANTIC_CONTENT/NO_RECORDS/UNKNOWN_RESOLUTION/
                                         #   CONFLICT_ONLY/DEGRADED_PARSE/MEANINGFUL_EVIDENCE[FUTURE]/
                                         #   INSUFFICIENT_EVIDENCE/UNKNOWN   （绝无 VERIFIED_FACT）
  branch: int(0..8)                      # 命中分支号（便于审计）
  reasons: list[str]                     # 分支原因 + 信号不确定注记
  # --- 独立诊断标志（§1.2，解耦于分支）---
  has_conflict: bool                     # conflict_count>0
  conflict_count: int                    # == cf
  file_degraded: TriState                # True/False/UNKNOWN（独立于分支呈现）
  # --- 文件作用域计数（§4.2：仅本文件原始记录）---
  n_records: int                         # == 本文件 N_raw（冲突合成项不计入）
  resolved_ev_count: int                 # RESOLVED ∧ STATIC_INFERENCE（§2.1）
  meaningful_count: int                  # 结构性 0（FUTURE）
  counts: {N_raw,N_candidate,N_non_candidate,N_construct_ok,N_construct_fail,
           N_not_attempted,N_unexpected_error,N_evidence_none,Nr}   # 本文件组
  # --- 追溯（§3：内容 id，不假定实例唯一）---
  evidence_refs: list[str]               # 支持记录的 projection_id（内容 id）
  conflicting_refs: list[str]            # 冲突项的 conflicting_conclusion_ids（若有）
  projection_schema_version: str         # usit-understanding-projection-v1
}
```

### 7.3 编号化反例与负向断言（冻结；映射 C1–C21 + 边界）
| 编号 | 输入 | 预期 file_conclusion / branch | 独立标志 | 负向断言 | 标签 |
|---|---|---|---|---|---|
| F-01(C1) | 1×RESOLVED候选 | INSUFFICIENT_EVIDENCE/7 | hc=F,fd=F | ≠VERIFIED/MEANINGFUL；n_records=1 | DESIGN_DECISION |
| F-03(C3) | 1×NON_SUCCESS候选 | UNKNOWN_RESOLUTION/2 | hc=F | 不计成功 | DESIGN_DECISION |
| F-04(C4) | size_bytes==0 | NO_SEMANTIC_CONTENT/0 | — | — | DESIGN_DECISION |
| F-05(C5) | 内容,0记录,无符号(has_symbols=F) | NO_RECORDS(no_symbols)/1 | — | — | DESIGN_DECISION |
| F-06(C6) | 内容,0记录,有符号(has_symbols=T) | NO_RECORDS(symbols_no_records)/1 | — | — | DESIGN_DECISION |
| F-06b | 内容,0记录,has_symbols=UNKNOWN | NO_RECORDS(symbol_status_unknown)/1 | — | 不默认 no_symbols | DESIGN_DECISION |
| F-07(C7) | RESOLVED候选+file_degraded=T | DEGRADED_PARSE/4 | fd=T | 降级不伪造证据 | DESIGN_DECISION |
| F-08(C10) | RESOLVED+NON_SUCCESS同身份 | CONFLICT_ONLY/3 | hc=T,cc=1 | n_records=2(非proj=3) | DESIGN_DECISION |
| F-09(C16) | 2×RESOLVED相同 | INSUFFICIENT_EVIDENCE/7 | hc=F | cf=0；重复非冲突 | DESIGN_DECISION |
| F-11(C18) | RESOLVED非Mapping证据 | UNKNOWN_RESOLUTION/2 | hc=F | N_candidate=0；不计成功 | DESIGN_DECISION |
| F-12(C19) | RESOLVED非Mapping+NON_SUCCESS候选 | UNKNOWN/8 | hc=F | 不静默落他支；非成功 | DESIGN_DECISION |
| F-13(C20) | 2×NON_SUCCESS不同身份+降级 | UNKNOWN_RESOLUTION/2 | **fd=T** | 分支2吞并降级但标志保留 | DESIGN_DECISION |
| F-14(C21) | 2×NON_SUCCESS同身份异内容 | UNKNOWN_RESOLUTION/2 | **hc=T,cc=1** | 分支2吞并冲突但标志保留；cf=1 | DESIGN_DECISION |
| F-15 | RESOLVED+AI_HYPOTHESIS only | UNKNOWN/8 | hc=F | resolved_ev_count=0(排除hypothesis) | DESIGN_DECISION |
| F-16 | size_bytes 缺失 | 不命中分支0；若无其它定论→UNKNOWN | — | 不默认空(NO_SEMANTIC_CONTENT) | DESIGN_DECISION |
| F-17 | 跨文件同身份(a/foo+b/foo) | 各组独立；cf=0 | — | 跨文件不冲突、不污染 | SOURCE_CONFIRMED |
| F-18 | js_ts 项目诊断(file_path='') | 不入任何真实文件 N_raw | — | 项目级处理 | SOURCE_CONFIRMED |
| F-19 | 恒等式 | n_records==N_raw; proj==Nr+cf; file_conclusion∉{VERIFIED_FACT} | — | 全样本断言 | DESIGN_DECISION |
| F-20(C 状态6) | excerpt_verified 有意义证据 | MEANINGFUL_EVIDENCE/6 | — | **FUTURE 不可达** | FUTURE |

> 负向断言覆盖：不升级 VERIFIED_FACT、不默认空/无符号/无降级、冲突/降级不被主结论吞没（经独立标志）、跨文件不污染、重复非冲突、hypothesis 排除、空路径记录隔离。

### 7.4 实施前置条件（冻结）
1. **独立实施授权工单**（本工单不授权）。
2. 新增 `FileConclusion` 类型（8 值枚举，`understanding_model.py`，与 item.complete 正交）。
3. 新增 `decide_file_conclusion(file_context, projection_signals)`：按 file_path 分组→逐组 project_records→有序决策 + 独立标志。
4. 三态 file_context 契约（§5.2）；分支仅用确定值。
5. 输出：新加法式 `understanding.json`（`usit-file-understanding-v1`，scanner.py:1160 写入）；既有 schema 零改动。
6. 回归：既有全绿 + F-01..F-19 单测（hypothesis/跨文件/空路径/三态缺失边界全覆盖）。
7. 冻结哈希治理：scanner.py 受 ZRXRF7_WATCH 冻结且已漂移（§6）；接线改 scanner.py 会触发守卫，须在**独立授权**下权威再生 `PRE_SHA256.json`（附审计轨迹）——**本工单不更新、不视为授权**。
8. 端到端：真实扫描产出 understanding.json，符合 F-17/F-18，确认无 VERIFIED_FACT、冲突不双计、门禁不变。

### 7.5 未决问题（冻结清单，交实施工单裁定）
| # | 未决 | 建议/状态 |
|---|---|---|
| U-1 | AI_HYPOTHESIS 计入口径 | 已裁定：排除（§2.3） |
| U-2 | has_content 精确定义 | size_bytes int>0 且 readable_text True |
| U-3 | 状态6 命名/excerpt_verified 协议 | FUTURE，另行授权 |
| U-4 | understanding.json 是否含 totals | 建议含（加法低风险） |
| U-5 | cli 是否需新入口 | 默认无需（薄封装 scan_project 自动继承） |
| U-6 | orphan/diag越界是否影响 file_conclusion | 正交（仅影响 item 完整性） |
| U-7 | 空 file_path 记录（js_ts 项目诊断）归属 | 项目级 `<project-level>` 桶，不入真实文件 N_raw |
| U-8 | scanner.py 冻结漂移根因 | 待调查（§6.4），无直接证据不归因 |

---

## 8. 文件修改核验（READ-ONLY 保持）

| 文件 | 状态 |
|---|---|
| `usit/understanding_projection.py` | 未修改（`8bb0fb65…`） |
| `usit/parser_contract.py` | 未修改（`482400f9…`） |
| `usit/scanner.py` | 未修改（`a009b8db…`） |
| `usit/understanding_model.py` | 未修改（`4668760b…`） |
| `usit/schema_adapter.py` | 未修改（`c44f60e4…`） |
| `outputs/p0710zxrxf/PRE_SHA256.json` | **未修改**（`3aaabb4e…`，未更新基线） |
| 既有报告 / 测试 / 配置 | 未修改 |
| 新增 | 仅 `reports/USIT-S1-C-DESIGN-11-执行报告.md` |

（哈希复验见 §10 命令；与 DESIGN-10 结束时逐一相符。）

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
| `BLOCKER-1` | **不解除**（仍未接线） |
| `BLOCKER-2` | 仍待接线后复核 |

---

## 10. 逐项结论汇总

| 工单项 | 结论 | 关键标签 |
|---|---|---|
| 1 分支与诊断语义 | **PASS** | DESIGN_DECISION（新增独立诊断标志，不改枚举） |
| 2 证据与状态边界 | **PASS** | SOURCE_CONFIRMED + DESIGN_DECISION；MEANINGFUL_EVIDENCE=FUTURE |
| 3 ID 与追溯 | **PASS** | SOURCE_CONFIRMED（内容 id、非实例唯一）；追溯不伪造 |
| 4 分组与计数 | **PASS** | SOURCE_CONFIRMED（批次级/跨文件不冲突）+ DESIGN_DECISION（按文件分组） |
| 5 文件上下文契约 | **PASS** | SOURCE_CONFIRMED（来源）+ DESIGN_DECISION（三态，缺失≠否定） |
| 6 冻结哈希治理 | **PASS（只读）** | SOURCE_CONFIRMED（差异/时间线）+ NOT_VERIFIED（根因）；未更新基线 |
| 7 冻结规格 | **PASS** | 决策表/字段契约/反例/前置齐备，逐项打标 |

### 最终结论：**READY_FOR_IMPLEMENTATION_REVIEW**

- 设计矛盾已裁定：① 主结论有序优先级 + `has_conflict`/`file_degraded`/`conflict_count` **独立诊断标志**（解决分支 2 吞并冲突/降级的信息丢失，不改 8 值枚举）；② `resolved_ev_count` 精确定义为 RESOLVED∧STATIC_INFERENCE（排除 AI_HYPOTHESIS/UNKNOWN/CONFLICT）；③ `projection_id`/结论 ID 为**内容标识、非实例唯一**（已查实），追溯方案不依赖伪造 ID；④ 文件级按 `file_path` 分组，`N_raw` 仅本文件原始记录、冲突合成项不计入、跨文件不污染；⑤ 文件上下文三态契约，缺失不默认真空/无降级；⑥ 冻结哈希漂移只读记录、未归因、未更新基线。
- `MEANINGFUL_EVIDENCE`/`excerpt_verified` 保持 **FUTURE**；无任何状态升级为 `VERIFIED_FACT`。
- 本规格**可直接用于**后续独立授权实施工单；但本工单**不授权**实现/接线/基线更新，结论为 READY_FOR_IMPLEMENTATION_REVIEW **仍须等待**后续独立实施授权。
- 固定门禁与 BLOCKER 全原值保持；未修改任何源码/测试/配置/manifest/基线/既有报告（§8）。

---

**授权边界最终声明**：本工单仅授权只读设计矛盾裁定与规格冻结。任何源码修改、接线、基线更新、构建、安装、登录、网络访问或真实项目扫描均超出授权范围。
