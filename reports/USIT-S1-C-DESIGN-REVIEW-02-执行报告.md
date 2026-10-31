# USIT-S1-C-DESIGN-REVIEW-02｜关联层设计修订（READ-ONLY 设计修订）

- **工单编号**：USIT-S1-C-DESIGN-REVIEW-02
- **执行时间**：2026-10-19 22:34 (GMT+8)
- **工作区绝对路径**：`C:\Users\Administrator\WorkBuddy\2026-10-15-22-09-47\usit_p0`
- **执行模式**：**严格只读设计修订** —— 仅修订设计报告；未修改任何源码/测试/配置/数据库/既有产物；未运行实现性代码；未进入 S1-C 实施、未执行 Android 测试、构建、安装、登录、网络或项目写入
- **解释器**：`Python 3.12.10`（仅用于只读核验，未执行任何新增逻辑）
- **修订基准**：`reports/USIT-S1-C-DESIGN-01-执行报告.md`（原设计）
- **修订动因**：审查意见指出原设计在「关联键、摘要验证、正常解析误判、计划完整性校验」四项存在缺陷；本修订给出可执行规格
- **设计依据**：A 项所有契约均来自对 `understanding_projection.py` / `understanding_model.py` / `parser_contract.py` / `scanner.py` 的直接阅读（见 §0 实测），非历史转述

---

## 0. 实测检查依据（Inspection Basis，含本轮新增核验）

| # | 检查对象 | 方法 | 直接观察结果 | 可信状态 |
|---|---|---|---|---|
| I-1 | `understanding_projection.py` | 全文件阅读 | `project_record(record)` 1 个 record → 1 个 `ProjectedItem`；`_classify`(:100-119) 仅当 `resolution_status=='RESOLVED'` **且** `evidence` 非空 mapping 才给 `STATIC_INFERENCE`，否则 `UNKNOWN`；`_build_evidence`(:122-149) 设 `collection_status=PRESENT`、`content_present=True`、`trust_level=NOT_VERIFIED`、`source_trusted=False`、`execution_verified=False`；`project_records` stats 含 `verified_fact=0`（:368） | 代码实测 |
| I-2 | `understanding_model.py` | 全文件阅读 | `EvidenceReference` 字段含 `digest_expected`、`digest_match`（:209-210）；构造级拒绝 `source_trusted/execution_verified/TRUSTED`（:223-224）；`digest_match=True` 须有 `digest_expected`（:250-251）；`ConclusionRecord` `VERIFIED_FACT` 硬阻断（:278-279, :316-320）；`ExecutionRecord` 仅允许 `NOT_RUN`（:332-337, :346-350）；`TestSpecification.complete` 由真实必填字段推导（:390-411, :414-415） | 代码实测 |
| I-3 | `parser_contract.py` | 全文件阅读 | `validate_record` 必填 12 字段（:69-73）：`language,file_path,parser_name,parser_version,node_type,qualified_name,source_location,relation_type,resolution_status,confidence,evidence,diagnostics`；`adapt_graph_v1`（:94-125）产出的 record 含 `source_location{file_path,line,column}`、`resolution_status`、`evidence`、`diagnostics` | 代码实测 |
| I-4 | `scanner._degradation_metadata`（:400-453） | 区间阅读 | 返回字典键：`degraded, reason, method, resolution_status, diagnostics_ref`；`diagnostics_ref` 为 `analysis_diagnostics` 数组中的**索引**（可越界/为 None） | 代码实测 |
| I-5 | `scanner.analyze_file`（:456-496） | 区间阅读 | row 字段：`path,language,sha256,size_bytes,symbols,imports,calls,analysis_diagnostics,analysis_degradation(:495)`；`_captured_text` 在序列化前被剥离（仅内存态） | 代码实测 |
| I-6 | `scanner` manifest 装配（原 A 项 I-5） | 复用 | `scan_manifest.json` `files[]` = 每行除 `_captured_text` 外所有键 → 含 `analysis_degradation`/`analysis_diagnostics`/`symbols`/`calls`/`sha256`，但**不含 `_captured_text`（源码文本不落盘）** | 代码实测 |
| I-7 | `understanding_projection._detect_conflicts`（:251-298） | 区间阅读 | 冲突键 = `(source_location.file_path, qualified_name)`；仅当数量>1 且 fingerprint 不同 → `CONFLICT`；双方保留，不自动裁决 | 代码实测 |

> 关键事实：manifest **不含源码文本**（I-6），因此"重新读取源码以核对当前摘要/digest"在 S1-C 后处理中**不可行且被本修订禁止**；任何"当前摘要"均无法获得，只能持有扫描时摘要。

---

## I. 相对原设计（DESIGN-01）的修改清单

| 编号 | 原设计缺陷 | 本修订处理 | 位置 |
|---|---|---|---|
| M1 | 仅隐含"按文件关联"，未定义稳定关联键与基数 | 新增 §A 显式关联模型：5 类实体基数 + 稳定键 + 多记录/无记录/重复/冲突/孤立处理；**禁止按数组位置关联** | §A |
| M2 | 摘要验证含糊，曾写"digest_match 由行 sha 比对得出"且暗示 MATCH | 新增 §B：扫描时摘要 vs 当前摘要分离；不重读源码时 `summary_verification=NOT_CHECKED`；**禁止用同一摘要字段自比宣称 MATCH**；定义 5 个独立有效性状态 | §B |
| M3 | "正常解析 → STATIC_INFERENCE"未加约束，存在误判风险 | 新增 §C：`degraded=False` 不自动等于 STATIC_INFERENCE；仅当 record `RESOLVED`+非空 evidence 才允许；空文件/无符号/无记录/证据不足必须有**显式 file_conclusion + 原因** | §C |
| M4 | 计划完整性仅提"带 evidence 或 incomplete_reasons" | 新增 §D：统一 `compute_incomplete_reasons`，合并 `TestSpecification` 派生缺失 + S1-C 五类禁令（降级/未知/冲突/无效引用/摘要未验证/孤立）；非完整即 `complete=False` | §D |
| M5 | 产物描述未坚决排除覆盖原 test_plan.json | 明确 §E：**新增独立候选产物** `understanding_test_plan_candidate.json`，**永不覆盖** `test_plan.json`；补充孤立/重复/回滚细则 | §E |
| M6 | 仅 6 场景，缺负例 | §F 在 6 场景基础上**新增 8 个负例**（空文件、无符号、一文件多记录、孤立记录、重复记录、摘要未检查、路径无效、行号无效） | §F |
| M7 | 验收"成功门槛"仍可被误解为单一成功率 | §G：结构完整/证据有效/摘要已验证/语义推断/执行状态**五类分别统计**，禁止合并模糊"成功率"；真实项目与性能仍 `UNKNOWN` | §G |

> 其余未列项（B 项核心目标/非目标、C.1 输入、C.3 证据引用强制约束、C.5 冲突复用、安全信任链、门禁）**保持原设计不变**。

---

## A 项（修订）：关联模型 — 基数、稳定键与异常记录处理

### A.1 五类实体与基数

| 实体 | 身份来源 | 与文件基数 | 与 record 基数 |
|---|---|---|---|
| **File**（manifest 行） | `file_key = row.path`（manifest 相对路径，唯一） | 1 | 1:N |
| **Parser-Contract Record** | 见 A.2 `record_key` | 1 文件 : N 记录 | 1 |
| **Projection Item** | `projection_id`（内容派生，稳定，`understanding_projection._stable_id`） | 1 文件 : N 项 | 1 record : 1 item（外加可能的 CONFLICT 项） |
| **Evidence Reference** | `evidence_ref_key = (relative_path, line, symbol, locator)` | 1 文件 : N 引用 | 1 record 至多 1 引用（来自 `record.evidence`） |
| **Test-Plan Item** | `item_key = (file_key, qualified_name or record_key)` | 1 文件 : N 项 | 并入其来源 record/projection |

### A.2 稳定关联键（禁止数组位置关联）

- **Record 主键（去重/冲突/关联用）**：
  `record_key = (norm(file_path), qualified_name, node_type or relation_type, resolution_status, evidence_summary_hash)`
  - `norm(file_path)`：将 `source_location.file_path` 归一化为与 manifest `files[].path` 同口径的相对路径（去 `./`、统一 `/`）。
  - `evidence_summary_hash`：对 `record.evidence` 做内容派生哈希（复用 `_canon` 思路），用于区分"同标识不同内容"。
- **跨实体关联方式**：linkage 条目内部用**显式字段引用**而非数组下标：
  - `linkage_entry.file_key` 关联 manifest 行；
  - `linkage_entry.record_keys[]` 关联该文件所有 parser-contract record（经 `parser_contract.json` 逐条匹配 `norm(file_path)==file_key`）；
  - `linkage_entry.projection_ids[]` 关联 `project_records()` 输出（record 顺序与 projection 顺序 1:1，但**键以 `projection_id` 存，不存下标**）；
  - `linkage_entry.evidence_ref_keys[]` 关联 `EvidenceReference`；
  - 候选 test-plan 条目用 `item_key` 回指 `file_key` + `projection_id`。

### A.3 异常记录处理（明确规则）

| 情形 | 判定与处理 |
|---|---|
| **一文件多记录** | 正常 1:N；逐 record 投影，逐条挂 `projection_id`；`file_conclusion` 由聚合规则（§C）得出，不并入 |
| **无记录** | 文件在 `parser_contract.json` 中 0 条匹配 → `linkage_entry.projection_ids=[]`，`file_conclusion=NO_RECORDS`，原因 `no_parser_contract_records`；候选计划条目 `discovery_only=True` + `incomplete_reasons=['no_parser_contract_records']` |
| **重复记录** | 两条 `record_key`（含 `evidence_summary_hash`）**完全相同** → 保留首条，后续标 `duplicate_of=首条 projection_id`，`reasons` 含 `duplicate_record`；**不静默丢弃任一侧语义**（遵循 `understanding_model` 去重哲学：仅完全相同才去重） |
| **冲突记录** | 同一 `(norm(file_path), qualified_name)` 但 `evidence_summary_hash` 不同 → 由 `understanding_projection._detect_conflicts`（I-7）产出 `CONFLICT` 项；linkage 标 `has_conflict=True`，候选计划条目 `incomplete_reasons=['conflict_pending']` |
| **孤立记录（orphan）** | `norm(record.file_path)` **不在** manifest `files[].path` 集合 → 仍投影但标 `orphan_record=True`，`provenance=UNVERIFIED_FILE`；候选计划条目 `incomplete_reasons=['orphan_record']`；不谎称文件已扫描 |

**A 项结论**：关联以内容派生键为准，全程无数组位置依赖；五类异常均有确定性处置，不崩溃、不静默。

---

## B 项（修订）：摘要与证据有效性 — 分离"扫描时"与"当前"

### B.1 核心约束

- **扫描时摘要**（`scan_time_summary`）： record `evidence.summary` / `EvidenceReference.summary` 中保存的、扫描阶段捕获的文本片段。它**是真实存在**的字段。
- **当前摘要**（`current_summary`）：需重新读取源码方能获得。**S1-C 禁止重新读取源码**（manifest 不含文本，I-6），故"当前摘要"在本阶段**不可得**。
- **禁止行为**：不得把 `scan_time_summary` 与自身比较并宣称 `MATCH`；不得因存在 `scan_time_summary` 就标记"内容已验证"。

### B.2 五个独立有效性状态（各自枚举，不合并）

| 状态 | 取值 | 判定方法（仅需 artifact，不重读源码） |
|---|---|---|
| **reference_exists** | `true/false` | `EvidenceReference` 构造成功且 `source_id` 与 `relative_path` 均非空（I-1 `_build_evidence`） |
| **path_valid** | `true/false/UNKNOWN` | `relative_path` 归一化后命中 manifest `files[].path` → `true`；否则 `false`；无法判定 → `UNKNOWN` |
| **line_valid** | `structurally_present` / `NOT_CHECKED` | `line` 为正整数 → `structurally_present`（结构合法）；是否落在"当前文件行数范围内"需重读源码 → **`NOT_CHECKED`**，绝不标 `true` |
| **diagnostic_index_valid** | `true/false` | `analysis_degradation.diagnostics_ref`（I-4）为非负整数且 `< len(row.analysis_diagnostics)` → `true`；越界/None → `false`（**此判断仅凭 artifact，可验证**） |
| **content_summary_verified** | `NOT_CHECKED` / `UNVERIFIABLE` | 不重读源码 → **恒为 `NOT_CHECKED`**；若 artifact 缺失 `scan_time_summary` 且无其他证据 → `UNVERIFIABLE` |

### B.3 对 EvidenceReference 字段的强制映射（修订后）

- `collection_status = CollectionStatus.NOT_VERIFIED`（**不置 PRESENT**，因无当前验证；纠正原设计隐含乐观）
- `digest_expected = row['sha256']`（文件级摘要，manifest 已有，**可设置**）
- `digest_match = None`（**不计算**；要算需重读源码，禁止；杜绝自比 MATCH）
- `trust_level = NOT_VERIFIED`；`source_trusted=False`；`execution_verified=False`（构造级强制，I-2）
- `summary = scan_time_summary`（仅持有，不验证）；另存 `summary_verification = "NOT_CHECKED"`
- `content_present`：仅表示"扫描时曾读取行文本"，**不代表当前仍有效**

**B 项结论**：扫描时摘要与当前摘要被强制分离；5 状态独立可断言；任何"已验证"声明在无重读时均不可得，杜绝自比 MATCH。

---

## C 项（修订）：结论生成规则 — 正常解析不自动等于 STATIC_INFERENCE

### C.1 前提（来自代码硬约束）

`understanding_projection._classify`（I-1）仅在 **`resolution_status=='RESOLVED'` 且 `evidence` 非空 mapping** 时才输出 `STATIC_INFERENCE`；其余一律 `UNKNOWN`。因此"文件被 Python AST 正常解析（`analysis_degradation.degraded=False`）"**不构成**产生 `STATIC_INFERENCE` 的充分条件——还需该文件存在 `RESOLVED`+有证据的 parser-contract record。

### C.2 文件级显式结论（`file_conclusion`）规则

| 情形 | `file_conclusion` | 必带原因 | 是否产生 STATIC_INFERENCE |
|---|---|---|---|
| 有 ≥1 条 `RESOLVED`+非空 evidence 的 record | `PARTIAL_UNDERSTANDING` | — | 仅对应 record 项产生（按 `_classify`） |
| 有 record 但全部非成功/证据空 | `UNKNOWN_RESOLUTION` | `all_records_non_success_or_no_evidence` | 否（全 UNKNOWN） |
| 无 parser-contract record | `NO_RECORDS` | `no_parser_contract_records` | 否 |
| 文件空（`size_bytes==0`）或无语义内容 | `NO_SEMANTIC_CONTENT` | `empty_or_no_semantic_content` | 否 |
| 有内容但无符号且无 record | `DISCOVERY_ONLY_NO_RECORDS` | `no_symbols_no_records` | 否 |
| 证据不足（record 存在但 evidence 空） | `INSUFFICIENT_EVIDENCE` | `missing_or_empty_evidence`（与 I-1 `_classify` 一致） | 否 |

### C.3 误判防护

- **禁止**：因 `analysis_degradation.degraded==False` 直接标记文件为"已理解/STATIC_INFERENCE"。
- **强制**：`file_conclusion` 必须由 §C.2 表推导；`STATIC_INFERENCE` 计数只能来自 `project_records().stats.static_inference`（I-1，record 级）。
- 每个非 `PARTIAL_UNDERSTANDING` 结论必须在 linkage 条目 `reasons[]` 中记录明确原因（呼应"未知不冒充成功"）。

**C 项结论**：正常解析仅代表"分析方法未降级"，不等于语义理解成功；退化情形一律显式结论 + 原因，无静默 STATIC_INFERENCE。

---

## D 项（修订）：计划完整性校验 — 统一规则

### D.1 统一计算（伪规格）

```
def compute_incomplete_reasons(spec: TestSpecification, ctx) -> list[str]:
    # 1) TestSpecification 自身派生缺失（understanding_model:390-411）
    reasons = list(spec.incomplete_reasons)  # 已合并派生缺失
    # 2) S1-C 五类禁令（任一命中即非完整）
    if not spec.evidence_refs or any(not ref_valid(r) for r in spec.evidence_refs):
        reasons.append("missing_or_invalid_evidence")
    if ctx.file_degraded:
        reasons.append("degraded_evidence")
    if ctx.any_record_non_success:
        reasons.append("non_success_resolution")
    if ctx.has_conflict:
        reasons.append("conflict_pending")
    if ctx.orphan_record:
        reasons.append("orphan_record")
    if ctx.summary_verification != "VERIFIED":   # 本阶段恒 NOT_CHECKED
        reasons.append("summary_not_verified")
    if ctx.diagnostic_index_invalid:
        reasons.append("invalid_diagnostic_reference")
    return dedupe(reasons)

item.complete = (len(reasons) == 0)   # 复用 TestSpecification.complete 语义
```

### D.2 硬性不变量（代码级已强制，设计重申）

- `VERIFIED_FACT` **恒为 0**：`ConclusionRecord` 构造拒绝 `VERIFIED_FACT`（I-2 :278-279, :316-320）；`project_records` stats 写死 `verified_fact=0`（I-1 :368）。
- `ExecutionRecord` **保持 `NOT_RUN`**：构造仅允许 `NOT_RUN`（I-2 :332-337），`PASS` 被拒（:346-350）。
- **禁止生成虚构执行成功**：候选计划条目 `execution` 字段一律 `ExecutionRecord()`（默认 NOT_RUN）；`complete=True` 仅表示"计划结构完整、可人工审阅"，**绝不等同于已执行/已通过**。

### D.3 非完整处置

- 命中 D.1 任一禁令 → `complete=False`，`incomplete_reasons` 显式列出原因；候选计划条目仍保留（供人工复核），但**不得**进入"可执行/已验证"集合。

**D 项结论**：完整性 = `TestSpecification` 派生完整 ∧ 全部 5 类禁令未命中；降级/未知/冲突/无效引用/摘要未验证/孤立均强制非完整；零虚构成功状态。

---

## E 项（修订）：产物与兼容

### E.1 第一版产物（均为新增，独立命名）

1. **`understanding_linkage.json`**（`schema_version=usit-understanding-linkage-v1`）：
   - 顶层 `files[]`，每文件含 `file_key, analysis_degradation(透传), file_conclusion, reasons[], projection_ids[], record_keys[], evidence_ref_keys[], orphan_record, has_conflict`；
   - 顶层 `projection_items[]`（来自 `project_records()`，纯引用 `projection_id`）；
   - 顶层 `evidence_refs[]`（含 B.2 五状态与 `summary_verification`）；
   - 顶层 `stats`（见 §G，五类分计）。
2. **`understanding_test_plan_candidate.json`**（`schema_version=usit-understanding-testplan-candidate-v1`）：
   - **独立候选产物，永不覆盖**原 `test_plan.json`（保留其 T001–T003 `PLAN_ONLY_NOT_EXECUTED`）；
   - 含 `understanding_section` 与按 `item_key` 的 `items[]`，每条目带 `evidence_ref_keys`、`degradation_aware`、`incomplete_reasons`、`execution=NOT_RUN`。

### E.2 新旧 schema 与兼容

- 原 `test_plan.json`、`scan_manifest.json`、`knowledge_graph.json`、`report.md` **schema 与内容均不变**（S1-B 已验证 symbols[]/calls[] 未变）。
- 旧 schema 行（无 `analysis_degradation`）：linkage 标 `provenance=UNKNOWN` + `limitations` 注"缺降级元数据"，不崩溃（同 DESIGN-01 C.6）。

### E.3 确定性与异常记录

- 确定性：`projection_id`、`conclusion_id`、`evidence_ref_key` 均内容派生（I-1 `_stable_id`），同输入→同输出。
- 孤立记录：见 A.3，标 `orphan_record`，不谎称已扫描。
- 重复记录：见 A.3，标 `duplicate_of`，不静默丢弃。

### E.4 回滚

- 纯新增产物 + 候选计划；删除 `understanding_linkage.json` / `understanding_test_plan_candidate.json` 及生成脚本即可，**对既有 artifact 零影响**（回滚风险低，须另行授权执行删除）。

---

## F 项（修订）：测试场景（原 6 + 新增 8 负例）

> 证据均为对产物字段的断言（JSON 往返），非信任声明。

| # | 场景 | 输入 | 预期输出 | 失败条件 | 证据 |
|---|---|---|---|---|---|
| S1 | 正常解析（原） | Python ok.py，无 diagnostic，`degraded=False` | 仅当存在 `RESOLVED`+evidence record 时对应项 `STATIC_INFERENCE`；`file_conclusion=PARTIAL_UNDERSTANDING` | 文件被整体标 STATIC_INFERENCE 而无具体 RESOLVED record | 断言 `file_conclusion` 来自 §C.2；断言 `static_inference` 计数 == RESOLVED+evidence record 数 |
| S2 | 解析降级（原） | broken.py，`PYTHON_SYNTAX_ERROR`，`degraded=True` | `file_conclusion=UNKNOWN_RESOLUTION`；候选计划 `incomplete_reasons` 含 `degraded_evidence` | 产生 STATIC_INFERENCE 或未标降级 | 断言 `file_conclusion==UNKNOWN_RESOLUTION`；断言 `degraded_evidence`∈incomplete_reasons |
| S3 | 证据引用失效（原） | main.js，`SEMANTIC_PARSER_UNAVAILABLE`，`ref=0` | `diagnostic_index_valid=true`，`ref_code==reason` | ref 越界仍标 valid | 负例：`diagnostics=[]` 时 `diagnostic_index_valid=false` 且 `invalid_diagnostic_reference`∈incomplete_reasons |
| S4 | 未知状态（原） | notes.xyz，`UNSUPPORTED_LANGUAGE_NO_SEMANTIC_PARSER` | `file_conclusion` 显式；无任何语义声明 | 产生 STATIC_INFERENCE | 断言 `resolution_status==UNKNOWN` 且无 `STATIC_INFERENCE` |
| S5 | 旧 schema 兼容（原） | 行缺 `analysis_degradation` | 不崩溃；`provenance=UNKNOWN` | 抛异常或静默当已验证 | 断言返回合法 `ProjectedItem`；`verified_fact==0` |
| S6 | 确定性（原） | 同 S1 两次 | `projection_id` 全等；`verified_fact==0` | ID 不同或 verified_fact>0 | 断言两次 JSON 字节一致 |
| **S7** | **空文件** | `size_bytes==0` 或内容空 | `file_conclusion=NO_SEMANTIC_CONTENT`，原因 `empty_or_no_semantic_content`；无 STATIC_INFERENCE | 标为已理解或产生推断 | 断言 `file_conclusion==NO_SEMANTIC_CONTENT`；`static_inference==0` |
| **S8** | **无符号文件** | 有内容但 `symbols=[]` 且无 parser-contract record | `file_conclusion=DISCOVERY_ONLY_NO_RECORDS` | 产生 STATIC_INFERENCE | 断言 `no_symbols_no_records`∈reasons；`projection_ids==[]` |
| **S9** | **一文件多 record** | 单文件含 3 条不同 `qualified_name` record（1 RESOLVED+evidence，2 非成功） | `projection_ids` 长度 3；仅 1 条 `STATIC_INFERENCE`；`file_conclusion=PARTIAL_UNDERSTANDING` | 合并记录或漏投影 | 断言 `len(projection_ids)==3`；`static_inference==1` |
| **S10** | **孤立记录** | record `file_path` 不在 manifest | `orphan_record=true`，`provenance=UNVERIFIED_FILE`；候选计划 `incomplete_reasons` 含 `orphan_record` | 谎称文件已扫描或丢弃记录 | 断言 `orphan_record==true`；`orphan_record`∈incomplete_reasons |
| **S11** | **重复记录** | 两条完全相同 `record_key` | 保留首条；次条 `duplicate_of=首条projection_id`，`reasons` 含 `duplicate_record` | 静默丢弃或双计 | 断言 `duplicate_record`∈次条 reasons；`static_inference` 不因重复翻倍 |
| **S12** | **摘要未检查** | 任意 record | 所有 evidence `summary_verification==NOT_CHECKED`，`digest_match==None` | 出现 `MATCH` 或 `digest_match==true` | 断言全量 `summary_verification=='NOT_CHECKED'`；零 `digest_match==True` |
| **S13** | **路径无效** | evidence `relative_path` 不命中 manifest | `path_valid=false`；候选计划 `incomplete_reasons` 含 `missing_or_invalid_evidence` | 标 `path_valid=true` | 断言 `path_valid==false`；`missing_or_invalid_evidence`∈incomplete_reasons |
| **S14** | **行号无效** | evidence `line` 非正整数或 None | `line_valid=structurally_present` 仅当正整数；绝不标"已验证行范围" | 标 `line_valid=true`（当前文件行范围） | 断言 `line_valid != 'true_against_current_file'`；`summary_verification==NOT_CHECKED` |

---

## G 项（修订）：验收标准 — 五类分计，禁合并

实施阶段须分别产出（**不得合并为单一"成功率"**）：

| 维度 | 统计口径 | 验收期望 |
|---|---|---|
| **结构完整** | `complete==True` 的候选计划条目数 / 总条目 | 仅统计"TestSpecification 派生完整 ∧ 五禁令未命中"者；本阶段因 `summary_not_verified` 恒命中，**预期接近 0 完整**，此乃诚实结果 |
| **证据有效** | 通过 B.2 五状态全部 `true/structurally_present` 的 evidence 数 / 总数 | 统计 `reference_exists∧path_valid∧line_structurally_present∧diagnostic_index_valid` |
| **摘要已验证** | `summary_verification=='VERIFIED'` 数 | **恒为 0**（本阶段不重读源码）；明确报告，不得隐藏 |
| **语义推断** | `project_records().stats.static_inference` | 仅来自 RESOLVED+evidence record；不来自文件级误判 |
| **执行状态** | `ExecutionRecord.status=='NOT_RUN'` 数 / `PASS` 数 | 全部 NOT_RUN；PASS 恒 0（构造拒绝） |

> 真实项目字段分布与性能：**仍标记 `UNKNOWN`**，不冒充已验证（同 DESIGN-01 E 项）。

**阶段成功门槛（含修订）**：
1. `verified_fact` 恒 0；`ExecutionRecord` 全 NOT_RUN；无虚构成功。
2. 非成功态（UNKNOWN/AMBIGUOUS/DYNAMIC/EXTERNAL/UNRESOLVED/CONFLICT）不得升级为 STATIC_INFERENCE 或被宣称"已理解"。
3. `file_conclusion` 必为 §C.2 显式枚举之一，且退化情形带明确原因。
4. 五类禁令任一命中 → 候选计划 `complete=False` 且 `incomplete_reasons` 显式。
5. 既有 artifact 零回归；旧 schema 行可处理；孤立/重复记录确定性处置。
6. 五维统计分别输出，无合并"成功率"。
7. `summary_verification` 全量 `NOT_CHECKED`，零 `digest_match==True`、零自比 MATCH。

---

## H 项：固定门禁（原值保持，全程未变）

| 门禁 | 值 |
|---|---|
| `F1` | `NOT_READY_TO_CLOSE` |
| `P0.8` | `HOLD` |
| `F2` | `STOP` |
| `历史授权连续性` | `UNRESOLVED` |
| `安全信任链` | `BLOCKED / NOT_VERIFIED` |

---

## I 项：未解决问题与最终建议

### 未解决问题（Unresolved）

1. **S1-A 投影未接线**仍是 S1-C 实施前提断点；本修订未改变"独立后处理"路径建议，采用与否需授权。
2. `analysis_degradation` 仍为死字段；本修订让 linkage 透传并用于计划禁令，但未接入图谱/报告渲染（与 DESIGN-01 同源，后续决策）。
3. **真实项目验证与性能**：`UNKNOWN`，未实测。
4. **Android 端到端自主测试**：**未实现**（见 §G.7 与 DESIGN-01 G 项），本修订未新增该能力。
5. 摘要"当前验证"需重读源码，被本阶段禁止；若未来授权重读，须单独设计校验协议，不在 S1-C 范围。

### 最终建议（Recommendation）

- **建议采纳本修订（REVIEW-02）作为 S1-C 实施规格**：它关闭了原设计四项缺陷（M1–M4/M6/M7），并将产物隔离（M5）落到可执行程度。
- **实施前必须另行授权**；本工单仅修订设计，未修改任何文件。
- 验收以 §G 七条门槛 + 五维分计为准，尤其**接受"结构完整≈0"为诚实结果**（因 `summary_not_verified` 恒命中），不以"全绿"评判。

---

**STOP**：仅提交设计修订，未修改任何文件。等待你与 ChatGPT 审查本报告并另行授权 S1-C 实施。
