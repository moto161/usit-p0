# USIT-S1-C-DESIGN-REVIEW-04｜最终规格一致性审查（READ-ONLY）

- **工单编号**：USIT-S1-C-DESIGN-REVIEW-04
- **执行时间**：2026-10-19 23:09 (GMT+8)
- **工作区绝对路径**：`C:\Users\Administrator\WorkBuddy\2026-10-15-22-09-47\usit_p0`
- **执行模式**：**严格只读设计修订** —— 仅读取源码/测试/报告，新增/修订本设计报告；**禁止修改**任何源码/测试/配置/manifest/图谱/原 `test_plan.json`/其他既有产物；**禁止执行** S1-C 实现、Android 测试、构建、安装、登录、网络或任何项目写入
- **修订基准**：`reports/USIT-S1-C-DESIGN-REVIEW-03-执行报告.md`（REVIEW-03），并继承 `USIT-S1-C-DESIGN-01` / `REVIEW-02`
- **设计依据**：全部结论来自对当前工作区源码的 grep + 区间阅读（见 §0 实测证据），非历史转述或推断

---

## 0. 实际检查文件与方法（Inspection Evidence）

| # | 检查对象 | 方法 | 观察结果（文件:行） | 可信状态 |
|---|---|---|---|---|
| I-1 | **投影接线（BLOCKER-1）** | `grep -rn "understanding_projection\|project_records\|project_record\b" usit/ --include=*.py`；读 `cli.py:1-10` import 段 | 除 `understanding_projection.py` 自身 `__all__`/`def`（:50-51, :152, :348）外，`usit/` 内**零调用**；`cli.py` 仅 import `scanner/safety/graph/benchmark/association_benchmark/android_adapter/command_planner`，**未 import 投影** → 投影完全未接线 | 代码实测（grep + 读） |
| I-2 | `EvidenceReference` 真实字段 | 读 `understanding_model.py:197-214` + `:216-257`（构造/校验） | 字段：`source_schema/source_id/relative_path/line/symbol/locator/summary/collection_status/trust_level/content_present/digest_expected/digest_match/source_trusted/execution_verified/raw_value/mapping_method`。**无 `summary_verification` 字段**。`digest_expected:209` 默认 `None`；`digest_match:210` 默认 `None`；`validate:250-251` 仅约束 `digest_match=True` 须有 `digest_expected` | 代码实测 |
| I-3 | `_build_evidence` 实际填值 | 读 `understanding_projection.py:122-149` | `summary=evidence.get("summary")`（:141，文本片段）；`collection_status=CollectionStatus.PRESENT`（:142）；**未传 `digest_expected`/`digest_match`** → 默认 `None` | 代码实测 |
| I-4 | `_classify` 判定边界 | 读 `understanding_projection.py:100-119` | `resolution_status∈NON_SUCCESS`→`UNKNOWN`；`evidence` 非 Mapping 或为空→`UNKNOWN`；否则 RESOLVED+非空 evidence mapping→`STATIC_INFERENCE` | 代码实测 |
| I-5 | `_stable_id` 派生规则 | 读 `understanding_projection.py:74-80` | 纯内容派生 sha256，**不含 index/序号** → 相同输入必得相同 ID | 代码实测 |
| I-6 | `project_record` 的 projection_id | 读 `understanding_projection.py:195-205` | `projection_id=_stable_id("proj",{object_type,path,symbol,line,status,reasons})` → **两条相同 record 共享同一 projection_id** | 代码实测 |
| I-7 | `project_records` 统计与基数 | 读 `understanding_projection.py:348-371` | `items=[project_record(r) for r in records]`（:358）后 `items.extend(_detect_conflicts(...))`（:359）→ **`projected_items = len(records) + len(conflicts)`**；`static_inference` 按 item 计（:364）；`verified_fact=0`（:368） | 代码实测 |
| I-8 | `parser_contract` 空 evidence fallback | 读 `parser_contract.py:94-121` | `adapt_graph_v1`：`evidence = evidence or {"graph_node_id": item.get("id")}`（:121）→ 空 evidence 被填成非空 dict，使 `_classify` 判为"有 evidence" | 代码实测 |
| I-9 | `parser_contract` 必填校验 | 读 `parser_contract.py:67-91` | `validate_record`：12 必填；`evidence` 必须非空 dict（:87-88），但因 I-8 fallback 总能通过 | 代码实测 |
| I-10 | `scanner` degradation / 文件 sha256 / manifest / test_plan | 读 `scanner.py:400-453, 462, 495, 1098-1103, 1108` | `_degradation_metadata`（:400）返回 `{degraded,reason,method,resolution_status,diagnostics_ref}`；`row` 含 `sha256`（文件级，:462）与 `analysis_degradation`（:495）；manifest（:1108）剥离 `_captured_text` 但**保留 `sha256` 与 `analysis_degradation`**；`test_plan.json`（:1098-1103）静态 3 项骨架（T001-T003, `PLAN_ONLY_NOT_EXECUTED`） | 代码实测 |
| I-11 | `TestSpecification.complete` 派生 | 读 `understanding_model.py:390-415` | `__post_init__` 由必填字段（target/purpose/preconditions/identity_and_data_requirements/steps/expected_result/cleanup_strategy/evidence_requirements）派生 `incomplete_reasons`，并与调用方传入的 `incomplete_reasons` 合并；`@property complete`（:413-415）= `not self.incomplete_reasons` | 代码实测 |
| I-12 | 硬阻断（VERIFIED_FACT / PASS） | 读 `understanding_model.py:278-279, 323-337` | `ConclusionRecord` 构造拒 `VERIFIED_FACT`（:278-279）；`ExecutionRecord` 仅允许 `NOT_RUN`（:332-333） | 代码实测 |

> **三种状态声明（强制区分）**：
> - **设计已修订**：✅ 本报告（REVIEW-04）完成。
> - **源码已实现**：❌ 否。S1-C 未实施；`understanding_projection` 仍仅在 `usit/` 内孤立存在（I-1 BLOCKER）。
> - **测试已执行**：❌ 否。本工单 READ-ONLY，未运行任何测试；下文引用的任何"测试"均指 REVIEW-02 §F / S1-B 既有报告中的**历史证据**，已标注来源与范围，非本次执行。

---

## 1. R1–R8 逐项处理结果

### R1 — 完整性伪代码与混合记录示例矛盾 → 已修订

**REVIEW-03 矛盾**：§A.2 把 `item(R1)`（RESOLVED+真实 summary）标为 `complete=True`；但 §H.2 伪代码第 263-264 行 `if ctx.summary_verification != "VERIFIED": reasons.append("summary_not_verified")`，而本阶段 `summary_verification` 恒为 `NOT_CHECKED` → `item(R1)` 实际必为 `False`。§A.2 与 §H.2 **自相矛盾**。

**修订**：
1. 依据工单要求 #2：**`summary_verification=NOT_CHECKED` 时，所有受该禁令约束的条目必须 `complete=False`**。REVIEW-03 §A.2 的 `item(R1)=True` 是错误示例，已删除/修正。
2. 修正后的 §A.2 混合示例：`item(R1)`（RESOLVED+真实 summary 但摘要未验证）→ `complete=False`，原因含 `summary_not_verified`（及 `evidence_not_meaningful`，因无授权摘录证明，见 R3）。
3. 将 `summary_verification` 的判定**锚定到真实模型事实**而非虚构字段（见 R2）：由 `EvidenceReference.trust_level==NOT_VERIFIED` 且 `digest_match is None` 且本构建无授权验证协议 → 链接层注解 `summary_verification="NOT_CHECKED"`，恒触发 `summary_not_verified`。
4. 引入 `item_context` / `file_context` 分离（见 §3 + 要求 #1），确保单条失败不株连同文件其他独立条目。

### R2 — 文件结论优先级不完整 → 已修订

**REVIEW-03 矛盾**：§C.1 未显式处理"降级但有有效/有意义记录"这一输入；且 `summary_verification`（§A.2 / §E.3）被当作 `EvidenceReference` 字段，但源码（I-2）证明 **`EvidenceReference` 无此字段**——属层级错置，导致 §H.2 伪代码引用了不存在的字段。

**修订**：
1. 重载文件结论优先级，显式覆盖"降级但有有效记录"（见 §4 决策表）：降级且无有意义记录 → `DEGRADED_PARSE`；降级但 ≥1 条有意义 RESOLVED 记录 → `PARTIAL_UNDERSTANDING`（降级仅作 `file_degraded=True` 标记与原因，不降级整体结论）。
2. 明确 `summary_verification` 是 **S1-C 链接层注解**（落在 `understanding_linkage.json` 的 `evidence_refs[]` 条目上，非冻结的 `EvidenceReference` dataclass），由真实模型事实派生。REVIEW-03 §E.3 "新增字段（非 S1-A 原生）" 的描述修正为"链接层注解，不写入 `EvidenceReference`"。
3. 所有受"摘要未验证"禁令约束的条目 `complete=False`，不得为求 `True` 而放宽（要求 #2）。

### R3 — `evidence_meaningful` 规则缺乏可靠性 → 已修订

**REVIEW-03 矛盾**：§D.2 将"非空 `summary` 文本"或"非默认 `locator`"直接等同为"证据真实有效" → 不可审计、属猜测。

**修订（可审计的结构判定，永不猜测 True）**：
```
classify_evidence_meaning(evidence: Mapping, record: Mapping, manifest_paths: set) -> "True"|"False"|"UNKNOWN":
    keys = set(evidence.keys())
    # (a) 可证明的否定：仅有图节点引用，无任何语义摘录
    if keys <= {"graph_node_id"}:
        return "False"
    # (b) 需要本构建不具备的证明：授权摘录留存溯源标记
    provenance_ok = bool(evidence.get("excerpt_verified")) and (record_path in manifest_paths)
    if provenance_ok:
        return "True"
    # (c) 有语义键（summary/locator 存在）但不可证明摘录真实 -> 不得断言有效
    return "UNKNOWN"
```
- 本构建**无任何记录**携带 `excerpt_verified` → 所有真实记录落 `"False"`（graph_node_id-only）或 `"UNKNOWN"`（有 summary 但未证明），**永不返回 `"True"`**。
- 另设纯结构性可观测标志 `evidence_has_semantic_keys = not (keys <= {"graph_node_id"})`（仅作信息标注，**不是**成功判定）。
- 条目 `complete` 的 `evidence_not_meaningful` 原因在 `classify != "True"`（即 False 或 UNKNOWN）时追加。因摘要禁令已使所有条目 `False`，该原因叠加但不改变结论（见 §3）。

### R4 — 文件级 SHA-256 与片段摘要字段语义边界不清 → 已修订

**REVIEW-03 矛盾**：§E.2/E.3 允许 `digest_expected = row['sha256']`（文件级哈希填入证据引用）。但 `EvidenceReference.digest_expected`（I-2）无任何"文件级 SHA-256"字段契约；其唯一约束（:250-251）是 `digest_match=True` 须有 `digest_expected`，语义为"被比对对象的期望摘要"——对证据引用而言比对对象是**摘录本身**，不是整文件。

**修订（要求 #4）**：
1. **禁止**把文件级 SHA-256 填入 `EvidenceReference.digest_expected`（无契约证明）。
2. 文件级扫描摘要改记在**独立链接层元数据** `understanding_linkage.json` 的 `files[].scan_file_sha256`（透传 manifest `row['sha256']`，I-10），与 `EvidenceReference` 解耦。
3. `digest_match` 恒为 `None`（本阶段禁止重读源码比对）。
4. `EvidenceReference.summary` 始终为**文本片段**，绝不填文件哈希。
5. S1-C 映射表修正见 §2。

### R5 — 证据统计分母不完整 → 已修订

**REVIEW-03 矛盾**：§D.1 分母笼统写作"全部 evidence"，未计入 `EvidenceReference` **构造失败**数，也无"原始引用数"。

**修订（要求 #5）**：统计分四层，分母逐指标显式给出（见 §5）：
- `N_raw`：原始候选证据引用数（应产生 `EvidenceReference` 的记录数）。
- `N_construct_ok`：构造成功数；`N_construct_fail`：构造抛错数（**不得静默丢弃**，记入 `construction_failures[]` 并附原因）。
- 各状态 `valid / invalid / unknown_or_not_checked` 三数 + **适用分母**（如 `path_valid` 分母=`N_construct_ok` 中 `relative_path` 非空者；`diagnostic_index_valid` 分母=`N_construct_ok` 中含 `diagnostics_ref` 者）。
- 禁止用单一"有效率"掩盖某状态失败。

### R6 — 条目级与文件级异常混用 → 已修订

**修订（要求 #1）**：显式分离 `item_context`（逐条）与 `file_context`（整文件，对所有条目一致施加，但不株连同文件他条）（见 §3）：
- **逐条影响条目完整性的条件**（item_context，独立计算，失败不传染）：
  1. `TestSpecification` 派生缺失（target/purpose/preconditions/identity_and_data_requirements/steps/expected_result/cleanup_strategy/evidence_requirements）；
  2. 证据引用不存在或无效（`missing_or_invalid_evidence`）；
  3. 证据不可审计为有效（`evidence_not_meaningful`）；
  4. 摘要未验证（`summary_not_verified`，本阶段恒命中）；
  5. 该记录 `resolution_status∈NON_SUCCESS`（`non_success_resolution`）；
  6. 该记录 `orphan`（file_path 不在 manifest，`orphan_record`）；
  7. 该记录 `diagnostics_ref` 越界/非法（`invalid_diagnostic_reference`）。
- **file_context**（整文件一致施加，属真实共享事实，非"株连"）：`file_degraded` → `degraded_evidence`；`file_has_conflict` → `conflict_pending`。
- 关键保证：条目 A 的 item 级失败（如缺失字段）**只影响 A**，B 仍按自身条件独立判定；file_context 因是全局共享事实，对所有条目一致成立，不属于"污染"。

### R7 — 文件结论枚举不一致 → 已修订

**REVIEW-03 矛盾**：§C.1 枚举 8 项，但 §J S8 使用了 **`DISCOVERY_ONLY_NO_RECORDS`**（不在枚举内）→ 枚举不自洽。

**修订（要求 #6）**：统一枚举为 **8 值**（见 §4）：`NO_SEMANTIC_CONTENT / NO_RECORDS / DEGRADED_PARSE / UNKNOWN_RESOLUTION / INSUFFICIENT_EVIDENCE / CONFLICT_ONLY / PARTIAL_UNDERSTANDING / UNKNOWN`。`DISCOVERY_ONLY_NO_RECORDS` 并入 `NO_RECORDS`（以 `reasons` 区分"无符号"与"有符号无记录"），全报告统一引用此枚举。

### R8 — 重复记录、共享 projection_id 与唯一统计口径不清 → 已修订

**REVIEW-03 矛盾**：§B 说明重复共享 projection_id，但未定义"重复关联"的指向规则、自指/循环禁止与"不丢原始记录"的硬保证。

**修订（要求 #7）**：精确定义四元组关系（见 §5 + §7）：
- **原始记录 `raw_records`**：文件内 parser-contract 记录列表，**含完全重复**（长度=真实条数）。
- **重复关联 `duplicate_of`**：指向**首次出现**那条记录的确定性稳定键 `rec:{projection_id}:{first_index}`；后续相同内容记录的 `duplicate_of` = 该键。**禁止自指**（自身 `first_index`≠自身）、**禁止成环**（直接指向首个，无链式传递）。
- **唯一 `projection_id`**：按 `projection_id` 去重后数量；重复记录折叠为同一 ID。
- **投影条目数 `projected_items`（文件）** = `len(raw_records) + conflict_items(文件)`（与 I-7 一致）。
- **不丢原始记录**：`record_keys[]` 列出全部原始记录稳定键（含重复）；`projection_ids[]` 逐条给出（重复共享同 ID）；`duplicate_groups` 仅作关联标注，不删除任一侧。

---

## 2. 统一字段 / schema 规格

### 2.1 `EvidenceReference`（真实模型，`understanding_model.py:197-214`，S1-C 不改写）

| 字段 | S1-C 取值 | 说明 |
|---|---|---|
| `summary` | `evidence.get("summary")`（文本片段） | 仅持有，不验证；绝不填文件哈希 |
| `collection_status` | `PRESENT`（S1-A `_build_evidence:142`） | 片段在 artifact 存在；与 `trust_level` 正交 |
| `trust_level` | `NOT_VERIFIED` | 构造强制（:207） |
| `content_present` | `True` | 扫描时曾读取，不代表当前仍有效 |
| `digest_expected` | `None` | **禁止**填文件级 sha256（R4） |
| `digest_match` | `None` | 禁止重读，不得计算 |
| `source_id` / `relative_path` / `line` / `locator` | 来自 record / source_location | 结构字段，供五状态校验 |

> `EvidenceReference` **无 `summary_verification` 字段**（I-2）。该值作为链接层注解存在（§2.3），不落入此冻结 dataclass。

### 2.2 `understanding_linkage.json`（`usit-understanding-linkage-v1`）— 修订后

| 路径 | 类型 | 说明（相对 REVIEW-03 的变更） |
|---|---|---|
| `schema_version` | str | `usit-understanding-linkage-v1` |
| `files[]` | array | 每文件一条 |
| `files[].file_key` | str | = manifest `path` |
| `files[].scan_file_sha256` | str\|None | **新增**（R4）：透传 manifest `row['sha256']`；独立元数据，不写 `digest_expected` |
| `files[].analysis_degradation` | obj | 透传 scanner 行（I-10） |
| `files[].file_degraded` | bool | **新增**：`analysis_degradation.degraded`，file_context 标志 |
| `files[].file_conclusion` | str | §4 统一枚举之一 |
| `files[].reasons[]` | str[] | 结论原因（区分无符号/有符号无记录/降级等） |
| `files[].record_keys[]` | str[] | **全部**原始 record 稳定键（含重复，不丢，R8） |
| `files[].projection_ids[]` | str[] | 对应 projection_item 的 `projection_id`（重复共享同 ID） |
| `files[].evidence_ref_keys[]` | str[] | 关联证据引用键 |
| `files[].duplicate_groups` | obj | `{projection_id: [原始record索引列表]}`（R8，无自指/环） |
| `files[].orphan_record` | bool | 任一 record.file_path 不在 manifest |
| `files[].has_conflict` | bool | 冲突记录存在 |
| `files[].complete_status` | bool | 文件级 AND 汇总（§3）；零条目时 False |
| `projection_items[]` | array | 来自 `project_records().items`（纯引用 `projection_id`） |
| `evidence_refs[]` | array | 每条含 §5 五状态 + 链接层注解：`summary_verification="NOT_CHECKED"`、`evidence_has_semantic_keys`、`evidence_meaningful`、`scan_file_sha256`（R2/R3/R4） |
| `stats` | obj | 见 §5（含 `N_raw/N_construct_ok/N_construct_fail`） |

### 2.3 `understanding_test_plan_candidate.json`（`usit-understanding-testplan-candidate-v1`）

| 路径 | 类型 | 说明 |
|---|---|---|
| `schema_version` | str | `usit-understanding-testplan-candidate-v1` |
| `derived_from` | str | 关联 `understanding_linkage.json` 的 `file_key` |
| `items[]` | array | 每条目对应一个 projection_item/record |
| `items[].item_key` | str | `(file_key, qualified_name or record_key)` |
| `items[].evidence_ref_keys[]` | str[] | 关联证据 |
| `items[].degradation_aware` | bool | 是否感知降级（file_context） |
| `items[].complete` | bool | **条目级**（§3）；本阶段因 `summary_not_verified` 恒 `False` |
| `items[].incomplete_reasons[]` | str[] | 非空则 `complete=False` |
| `items[].execution` | obj | `ExecutionRecord()` 默认 **NOT_RUN**（I-12） |
| `items[].evidence_meaningful` | str | `"True"\|"False"\|"UNKNOWN"`（R3） |

> **隔离保证**：两个产物均为**新增独立文件**；原 `test_plan.json` / `scan_manifest.json` / `knowledge_graph.json` / `report.md` **schema 与内容不变**（I-10 已证 `symbols[]/calls[]` 未变，test_plan 静态 3 项骨架不变）。

---

## 3. 完整性伪代码（item_context / file_context 分离）

```python
def item_complete(
    spec: TestSpecification,
    ev: EvidenceReference | None,
    link: EvidenceLinkAnnotation,     # 链接层注解（summary_verification 等）
    ctx: ItemContext,                 # 逐条上下文
) -> tuple[bool, list[str]]:
    # (1) TestSpecification 派生的必填缺失（I-11）
    reasons = list(spec.incomplete_reasons)
    # (2) 证据引用不存在或无效（item_context）
    if ev is None or not (ev.reference_exists and ev.path_valid and ev.line_valid_structural):
        reasons.append("missing_or_invalid_evidence")
    # (3) 证据不可审计为有效（R3，item_context）
    if link.evidence_meaningful != "True":          # False 或 UNKNOWN
        reasons.append("evidence_not_meaningful")
    # (4) 摘要未验证禁令（R2/要求#2，链接层注解恒 NOT_CHECKED）
    if link.summary_verification != "VERIFIED":      # 本阶段恒命中
        reasons.append("summary_not_verified")
    # (5)-(7) 其余 item_context 条件（逐条，互不传染）
    if ctx.record_non_success:
        reasons.append("non_success_resolution")
    if ctx.orphan_record:
        reasons.append("orphan_record")
    if ctx.diagnostic_index_invalid:
        reasons.append("invalid_diagnostic_reference")
    # file_context：整文件一致施加（真实共享事实，非株连）
    if ctx.file_degraded:
        reasons.append("degraded_evidence")
    if ctx.file_has_conflict:
        reasons.append("conflict_pending")
    return (len(reasons) == 0), dedupe(reasons)

# 文件级：仅汇总，不反向改写条目（要求#1）
file.complete_status = bool(file_items) and all(item.complete for item in file_items)
```

**要点**：
- 因 `link.summary_verification != "VERIFIED"` 本阶段**恒真** → 所有受约束条目 `complete=False`（R1 修正，要求 #2）。
- `evidence_not_meaningful`、`non_success_resolution`、`orphan_record`、`invalid_diagnostic_reference` 均为**逐条**计算；条目 A 命中不影响条目 B。
- `file_degraded` / `file_has_conflict` 属 file_context，对所有条目一致成立（降级/冲突是该文件证据源的真实属性），不构成"单条失败株连同文件"。

---

## 4. 文件结论决策表（统一枚举，互斥，命中即停）

**枚举（R7 统一为 8 值）**：`NO_SEMANTIC_CONTENT` / `NO_RECORDS` / `DEGRADED_PARSE` / `UNKNOWN_RESOLUTION` / `INSUFFICIENT_EVIDENCE` / `CONFLICT_ONLY` / `PARTIAL_UNDERSTANDING` / `UNKNOWN`。

**优先级（自上而下，首次命中即停，确定性）**：

| 序 | 条件 | `file_conclusion` | 典型 `reasons` |
|---|---|---|---|
| 0 | `size_bytes==0` 或内容为空 | `NO_SEMANTIC_CONTENT` | `empty_or_no_semantic_content` |
| 1 | 该文件 parser-contract 记录数 == 0 | `NO_RECORDS` | `no_parser_contract_records`（区分 `no_symbols` / `symbols_no_records`） |
| 2 | 全部记录 `resolution_status∈NON_SUCCESS` 或 evidence 全空 | `UNKNOWN_RESOLUTION` | `all_records_non_success_or_no_evidence` |
| 3 | `has_conflict` 且**无**有意义 RESOLVED 记录 | `CONFLICT_ONLY` | `conflict_pending` |
| 4 | `file_degraded` 且**无**有意义 RESOLVED 记录 | `DEGRADED_PARSE` | `degraded_evidence` |
| 5 | 有 RESOLVED 记录但**均无** `evidence_meaningful=="True"` | `INSUFFICIENT_EVIDENCE` | `missing_or_empty_evidence` |
| 6 | ≥1 条 `evidence_meaningful=="True"` 的 RESOLVED 记录 | `PARTIAL_UNDERSTANDING` | 含 `file_degraded`（若降级）/`conflict_pending`（若有冲突）标记 |
| 7 | 兜底 | `UNKNOWN` | `unknown` |

> **覆盖要求 #6 的 7 类输入**：空文件→0；无符号→1；有符号无记录→1（reason 区分）；降级但有有效记录→6（`file_degraded` 标记，**不**降为 DEGRADED_PARSE）；全部未知→2；冲突（无有意义）→3（有有意义→6）；部分理解→6。
> **互斥保证**：上表顺序即优先级，每文件至多落入一种结论。

---

## 5. 统计口径（R5：四层 + 逐指标分母）

| 维度 | 统计 | 适用分母 | 验收期望 |
|---|---|---|---|
| 原始引用 | `N_raw` | — | 应产生 `EvidenceReference` 的记录数（含重复） |
| 构造成功 | `N_construct_ok` | — | 未抛错的 `EvidenceReference` 数 |
| 构造失败 | `N_construct_fail` | — | 抛错数（记入 `construction_failures[]`，**不静默丢**） |
| `reference_exists`（valid） | `source_id`+`relative_path` 非空数 | `N_construct_ok` | 分别报 valid/invalid |
| `path_valid`（valid） | `relative_path` 命中 manifest `files[].path` 数 | `N_construct_ok` 中 `relative_path` 非空者 | valid/invalid |
| `line_valid`（structurally_present） | `line` 为正整数数 | `N_construct_ok` | 仅标"结构上合法"，绝不标"已验证行范围" |
| `diagnostic_index_valid`（valid） | `diagnostics_ref` 为非负且 `< len(analysis_diagnostics)` 数 | `N_construct_ok` 中含 `diagnostics_ref` 者 | valid/invalid |
| `summary_verification`（NOT_CHECKED） | 注解 == `VERIFIED` 数 | `N_construct_ok` | **恒 0**，全部 `NOT_CHECKED` |
| 语义推断 | `project_records().stats.static_inference` | — | 仅来自 RESOLVED+evidence record（I-7） |
| 执行状态 | `NOT_RUN` 数 / `PASS` 数 | — | 全部 NOT_RUN；PASS 恒 0（I-12） |
| 投影基数 | `total_records` / `projected_items` / `unique_projection_ids` / `duplicate_groups` | — | `projected_items = total_records + conflicts`（I-7，R8） |

> 每个状态须同时报告 `valid / invalid / unknown_or_not_checked` 三数，**禁止**合并为单一"有效率"。

---

## 6. 至少 12 个正负例逐条预期

> `complete` 列标注 **[当前阶段=False]** 表示受摘要未验证禁令（R1/要求#2）强制为 False；`相对判定` 列给出"若摘要已验证"时该条目相对其他规则的成败，用于证明规则非平凡恒假。

| # | 输入场景 | `file_conclusion` | 条目 `complete` | 条目 `incomplete_reasons`（除恒有的 `summary_not_verified`） | 性质 |
|---|---|---|---|---|---|
| E1 | 正常解析 + 真实 summary（无摘录证明） | `PARTIAL_UNDERSTANDING` | [当前=False] | `evidence_not_meaningful`(UNKNOWN) | 正（正确识别部分理解） |
| E2 | 正常解析 + 仅 `graph_node_id` | `INSUFFICIENT_EVIDENCE` | [当前=False] | `evidence_not_meaningful`(False) | 负（不得误判为理解） |
| E3 | UNRESOLVED（解析降级） | `UNKNOWN_RESOLUTION` | [当前=False] | `non_success_resolution` | 负（不得 STATIC_INFERENCE） |
| E4 | 空文件（`size_bytes==0`） | `NO_SEMANTIC_CONTENT` | N/A（无条目） | — | 正（不标已理解） |
| E5 | 0 条记录（无符号） | `NO_RECORDS` | N/A | reason=`no_symbols` | 正 |
| E6 | 0 条记录（有符号无记录） | `NO_RECORDS` | N/A | reason=`symbols_no_records` | 正（枚举统一 R7） |
| E7 | 降级 + 有意义记录 | `PARTIAL_UNDERSTANDING` | [当前=False] | `degraded_evidence`（`file_degraded`） | 正（R2：降级但有有效记录不降为 DEGRADED_PARSE） |
| E8 | 降级 + 无有意义记录 | `DEGRADED_PARSE` | [当前=False] | `degraded_evidence` | 负（不得误判） |
| E9 | 全部 NON_SUCCESS | `UNKNOWN_RESOLUTION` | [当前=False] | `non_success_resolution` | 负 |
| E10 | 冲突 + 无有意义记录 | `CONFLICT_ONLY` | [当前=False] | `conflict_pending` | 负（冲突不得静默消解） |
| E11 | 冲突 + 有意义记录 | `PARTIAL_UNDERSTANDING` | [当前=False] | `conflict_pending` | 正（保留冲突标记） |
| E12 | 孤立 record（file_path 不在 manifest） | 视其他记录而定 | [当前=False] | `orphan_record` | 负（不得谎称已扫描） |
| E13 | 引用失效（diagnostics_ref 越界） | 依记录 | [当前=False] | `invalid_diagnostic_reference` | 负 |
| E14 | 路径无效（relative_path 不在 manifest） | 依记录 | [当前=False] | `missing_or_invalid_evidence` | 负（不得标 valid） |
| E15 | 行号无效（line 非正整数） | 依记录 | [当前=False] | `missing_or_invalid_evidence`（line 仅 struct） | 负（绝不标"已验行范围"） |
| E16 | TestSpecification 缺必填字段 | 依文件 | [当前=False] | 派生缺失字段名（target/purpose/…） | 负（不得伪造完整） |
| E17 | 重复记录 ×2（完全相同） | 依内容 | [当前=False] | 同对应单条 | 见 R8：不丢、不循环、共享 ID |

---

## 7. 一致性自证（同一组示例逐条推演）

取 **§A.2 三记录混合示例**（文件 `app.py`，3 条 record）与 **E7/E8 降级对**、**E17 重复**作为同一组输入，验证字段/规则/统计/示例自洽：

**输入 α**：`app.py` 含 `R1`(RESOLVED+真实summary,无摘录证明)、`R2`(RESOLVED+仅graph_node_id)、`R3`(UNRESOLVED)。

1. **字段/构造**：
   - `R1/R2` 的 `_build_evidence`（I-3）成功 → `N_construct_ok=3`，`N_construct_fail=0`；`summary` 为文本片段，`digest_expected=None`，`digest_match=None`。
   - `evidence_meaningful`（R3）：`R1` 有 summary 但无 `excerpt_verified` → `"UNKNOWN"`；`R2` 仅 `graph_node_id` → `"False"`；`R3` 无 evidence → 无引用（视为构造无 evidence，不计入 `evidence_refs` valid）。
2. **文件结论（§4）**：有 RESOLVED 记录但均非 `"True"` → 序 5 `INSUFFICIENT_EVIDENCE`？否——`R3` 是 NON_SUCCESS，`R1/R2` 是 RESOLVED 但无有意义 → 序 2（全部非成功？否，R1/R2 是 RESOLVED 非 NON_SUCCESS）→ 跳过序2；序3 无冲突；序4 未降级；序5 有 RESOLVED 但均无 `"True"` → **`INSUFFICIENT_EVIDENCE`**。✓ 与 E2 一致。
3. **条目 complete（§3）**：三者均含 `summary_not_verified`（恒命中）→ 全部 `False`；`R1` 另加 `evidence_not_meaningful`(UNKNOWN)，`R2` 加 `evidence_not_meaningful`(False)，`R3` 加 `non_success_resolution`。每条独立计算，互不传染。✓ 修正了 REVIEW-03 §A.2 把 R1 误标 True 的矛盾（R1）。
4. **统计（§5）**：`N_raw=3`；`reference_exists` valid=3（都有 source_id+path）；`path_valid` valid=3（在 manifest）；`line_valid` structurally=3；`diagnostic_index_valid` 取决于各自 ref；`summary_verification` VERIFIED=0；`static_inference`（I-7）按 item 计=2（R1,R2 RESOLVED）→ 与"文件结论 INSUFFICIENT_EVIDENCE"不冲突（推断数来自 S1-A 投影，结论来自 S1-C 加严）。✓

**输入 β（降级对）**：`mod.py` 降级(`file_degraded=True`) + 1 条有意义 RESOLVED。
- 序 6 → `PARTIAL_UNDERSTANDING` + `file_degraded=True`（E7 正例）。若改为无有意义 → 序 4 → `DEGRADED_PARSE`（E8）。✓ 同一优先级表覆盖 R2 两种降级情形。

**输入 γ（重复，E17）**：`dup.py` 含 `R_a, R_a, R_b`（R_a 完全相同）。
- `raw_records=3`；`projection_ids=[pid_a,pid_a,pid_b]`（I-5/I-6 共享）；`record_keys=[rec:pid_a:0, rec:pid_a:1, rec:pid_b:2]`（全保留）；`duplicate_groups={pid_a:[0,1]}`（指向首个 index 0，无自指 `1→1`、无环）；`unique_projection_ids=2`；`projected_items=3`（无 conflict）。✓ R8 不丢、不循环、共享 ID。

→ 字段定义、判定规则（§3/§4）、统计口径（§5）、示例（§6）四类在同一组输入下结论一致，无内部矛盾。

---

## 8. 未解决问题、BLOCKER 清单与状态

### 8.1 BLOCKER 清单（继续列为代码修改事项，不得自行接线）

| ID | 描述 | 影响 | 处理要求 |
|---|---|---|---|
| BLOCKER-1 | 投影未接线（I-1：`understanding_projection` 仅自身引用，`cli.py` 未 import） | S1-C 无 `projection_items` 输入 | 实施前须授权接线（独立后处理显式调用 `project_records(parser_contract_records)` 或改 `cli.py`）——属代码修改，超出本 READ-ONLY 工单 |
| BLOCKER-2 | evidence `graph_node_id` fallback（I-8）致 `STATIC_INFERENCE` 过宽 | 无真实摘要也计推断 | S1-C 用 `evidence_meaningful`（R3）加严计划完整性；不修改 S1-A |

### 8.2 未解决问题（Unresolved）

1. BLOCKER-1 采用"独立后处理"还是"改 cli.py 接线"需授权（同 8.1）。
2. `analysis_degradation` 仍为死字段；linkage 透传但未接入图谱/报告渲染。
3. 真实项目验证与性能：`UNKNOWN`（未实测，不冒充）。
4. **Android 端到端自主测试：未实现**，本修订未新增该能力。
5. 当前摘要验证需重读源码，被禁止；若未来授权重读，须单独设计校验协议（`excerpt_verified` 溯源标记即为此预留，非本阶段启用）。

### 8.3 PASS/FAIL/未测试状态（设计验收，非实现验收）

| 项 | 状态 |
|---|---|
| R1–R8 八项矛盾逐条处理（§1） | ✅ **设计已修订（PASS 作为设计交付）** |
| 源码契约证据（§0 I-1–I-12） | ✅ 代码实测坐实 |
| 修正 REVIEW-03 矛盾（§A.2 误标 / 枚举不一 / digest_expected 误用 / summary_verification 层级） | ✅ 已修订 |
| BLOCKER 列出（8.1） | ✅ 已列 |
| **源码已实现** | ❌ **否**（S1-C 未实施） |
| **测试已执行（本次）** | ❌ **否**（READ-ONLY，未运行） |
| 历史测试证据引用 | ⚠️ 仅引 REVIEW-02 §F / S1-B 报告，已标来源与范围，非本次执行 |

---

## 9. 固定门禁（原值保持，全程未变）

| 门禁 | 值 |
|---|---|
| `F1` | `NOT_READY_TO_CLOSE` |
| `P0.8` | `HOLD` |
| `F2` | `STOP` |
| `历史授权连续性` | `UNRESOLVED` |
| `安全信任链` | `BLOCKED / NOT_VERIFIED` |

---

## 10. 最终结论

1. **设计已修订（本次交付）**：REVIEW-04 在 REVIEW-03 基础上，基于 12 项源码实测（I-1–I-12）逐条闭合 R1–R8 八项逻辑矛盾：① `item_context`/`file_context` 分离 + 修正 §A.2 误标 `item(R1)=True` 的矛盾（R1）；② 文件结论优先级补全"降级但有有效记录"并纠正 `summary_verification` 层级错置（R2）；③ `evidence_meaningful` 改为可审计结构判定，永不猜测 True（R3）；④ 禁止文件级 SHA-256 填入 `digest_expected`，改记独立链接层 `scan_file_sha256`（R4）；⑤ 统计分母补全 `N_raw/N_construct_ok/N_construct_fail` 与逐指标分母（R5）；⑥ 条目级/文件级异常分离并列出逐条完整性条件（R6）；⑦ 统一 8 值结论枚举，消除 `DISCOVERY_ONLY_NO_RECORDS` 漂移（R7）；⑧ 明确原始/重复/唯一 projection_id/投影条目数四元组与"不丢/不循环"硬保证（R8）。
2. **源码已实现：否**。S1-C 未进入实施；`understanding_projection` 仍孤立未接线（BLOCKER-1，I-1）。
3. **测试已执行：否**。本工单严格 READ-ONLY，未运行任何实现或测试；所有统计口径、决策表与正负例为**设计规格**，待授权实施后验证。
4. **硬约束全部维持**：`verified_fact=0`、`ExecutionRecord` 全 `NOT_RUN`、无虚构成功；`summary_verification=NOT_CHECKED` 恒为真，受约束条目 `complete=False`（要求 #2，未为求 True 放宽）；`degraded=False` 不代表已理解；不放宽信任门禁；不把 UNKNOWN/AMBIGUOUS/DYNAMIC/EXTERNAL/UNRESOLVED/CONFLICT 升级为成功。
5. **建议**：采纳 REVIEW-04 作为 S1-C 最终设计规格；实施前须**另行授权**处理 BLOCKER-1（接线）与 BLOCKER-2（完整性加严），且不得自行进入 S1-C 实现。本设计规格的"通过"**不等于**源码实现通过或测试通过（验收规则明示）。

---

**STOP**：仅提交设计修订，未修改任何文件。等待人工审查与独立授权；不得自行进入 S1-C 实施。
