# USIT-S1-C-DESIGN-01｜下一阶段目标与验收方案设计（READ-ONLY 方案设计）

- **工单编号**：USIT-S1-C-DESIGN-01
- **执行时间**：2026-10-19 22:13 (GMT+8)
- **工作区绝对路径**：`C:\Users\Administrator\WorkBuddy\2026-10-15-22-09-47\usit_p0`
- **执行模式**：**严格只读方案设计** —— 未修改任何源码/测试/配置/既有报告；未运行实现性变更；未进入实施、未执行 Android 测试、构建、安装、登录、网络或项目写入
- **解释器**：`Python 3.12.10`（仅用于只读核验，未执行任何新增逻辑）
- **设计依据**：本报告 A 项所有"数据流/消费关系"均来自对当前工作区源码的直接阅读与 grep 实测（见"实际检查依据"），非历史转述或 AI 臆测

---

## 0. 实际检查依据（Inspection Basis）

| # | 检查对象 | 方法 | 直接观察结果 | 可信状态 |
|---|---|---|---|---|
| I-1 | `usit/understanding_projection.py`（14183 B） | 全文件阅读 | `project_records(records)` → `ProjectionResult`；消费 **parser-contract 记录**（字段：language/file_path/parser_name/source_location{file_path,line,column}/node_type/qualified_name/relation_type/resolution_status/confidence/evidence/diagnostics）；`VERIFIED_FACT` 硬阻断；非成功→`UNKNOWN`；`_detect_conflicts` 保留双方；纯函数、内容派生 ID、确定性 | 代码实测（直接观察） |
| I-2 | `usit/understanding_model.py` | 全文件阅读 | `ConclusionRecord` 构造即拒绝 `VERIFIED_FACT`（:278-279, :316-320）；`EvidenceReference` 拒绝 `source_trusted`/`execution_verified`/`TRUSTED`（:223-224）；`ExecutionRecord` 仅允许 `NOT_RUN`（:332-337）；`TestSpecification.complete` 由真实字段推导（:390-411） | 代码实测（直接观察） |
| I-3 | `usit/scanner.py`（1202 行） | grep + 区间阅读 | `row['analysis_degradation']` **仅**在 :495（及 FIX-01 补丁 :945）赋值，生产后无生产代码读取（I-4 复核）；`_degradation_metadata` 定义于 :400；`row['analysis_diagnostics']` 在 :478/:485/:488 生产，JS/TS 分支 :918 整体覆盖、`parsed.get('diagnostics',[])` | 代码实测（直接观察） |
| I-4 | `analysis_degradation` 消费者 | grep `usit/*.py` | 全仓库**无任何生产模块读取** `analysis_degradation`（唯一出现为 scanner.py:495/:945 赋值、及 test 文件）。与 S1-B-AUDIT-01 结论一致 | 代码实测（直接观察） |
| I-5 | `scanner.py:1108` 产物装配 | 区间阅读 | `scan_manifest.json` 的 `files[]` = 每行除 `_captured_text` 外**所有键**，故 **manifest 含 `analysis_degradation` 与 `analysis_diagnostics`** | 代码实测（直接观察） |
| I-6 | `scanner.py:1160` 产物装配 | 区间阅读 | `knowledge_graph.json` 由 nodes/edges 装配；`graph.py:325` 仅读 **edge 级 `resolution_status`**，不读 row 级 `analysis_degradation` → **图谱不含降级字段** | 代码实测（直接观察） |
| I-7 | `scanner.py:1130` / `:1176` | 区间阅读 | `report.md` 的 `unknown_items` 仅含每行列 `analysis_diagnostics`（不含 `analysis_degradation`）；`_render_markdown` 未改 → **报告不含降级字段** | 代码实测（直接观察） |
| I-8 | `scanner.py:1098-1103` | 区间阅读 | `test_plan.json` = 静态 3 项骨架（T001/T002/T003，`schema_version=usit-test-plan-v1`，`execution_policy=PLAN_ONLY_NOT_EXECUTED`），**未引用扫描结果/降级/证据/理解结果** | 代码实测（直接观察） |
| I-9 | `scanner.py:1059-1076` + `parser_contract.py:94-125` | 区间阅读 | `parser_contract.json` 由 `adapt_graph_v1(graph)` + JS/TS records + `analysis_diagnostics` 转 record 装配；其 record schema **与 `understanding_projection` 输入 schema 完全一致** | 代码实测（直接观察） |
| I-10 | `understanding_projection` 接线 | grep `usit/*.py` | **除自身外无任何模块 import/调用** `understanding_projection`/`project_records` → 投影步骤从未被扫描流水线触发 | 代码实测（直接观察） |

---

## A 项：现有 S1-A/S1-B 数据流图与真实消费关系

### A.1 数据流图（ASCII）

```
                         ┌─────────────────────────────────────────────┐
                         │  scan_project (scanner.py:708)  [READ-ONLY]  │
                         └─────────────────────────────────────────────┘
                                           │
        ┌──────────────────────────────────┼───────────────────────────────────┐
        │ analyze_file (per row)           │ _scan_project_impl 聚合            │
        │  - symbols[]/imports[]/calls[]   │  - nodes/edges (knowledge_graph)   │
        │  - analysis_diagnostics (row级)  │  - parser_contract (records)       │
        │  - analysis_degradation (S1-B,   │  - inventory / test_assets         │
        │    row级, :495)                  │                                   │
        └──────────────────────────────────┴───────────────────────────────────┘
                                           │
              ┌────────────────────────────┼─────────────────────────────────┐
              ▼                            ▼                                 ▼
   ┌──────────────────────┐   ┌────────────────────────┐      ┌─────────────────────────┐
   │ scan_manifest.json   │   │ knowledge_graph.json   │      │ parser_contract.json    │
   │ files[] = 整行复制   │   │ 仅 edge.resolution_   │      │ records[] = 图边 +      │
   │ ✅含 analysis_       │   │   status (graph.py:325)│      │ JS/TS + diagnostics    │
   │   degradation/diag   │   │ ❌不含 row级降级字段  │      │ ✅是 understanding_     │
   │ ✅含 symbols/calls   │   │                        │      │   projection 的输入!    │
   └──────────────────────┘   └────────────────────────┘      └─────────────────────────┘
              │                            │                                 │
              │ (unknown_items 仅诊断)      │                                 │ (但 projection 从未被调用)
              ▼                            ▼                                 ▼
   ┌──────────────────────┐   ┌────────────────────────┐      ╳  understanding_projection
   │ report.md            │   │ test_plan.json         │        (S1-A 模块，孤立，未接线)
   │ ❌不含 analysis_     │   │ ❌静态 3 项骨架，与    │      ╳  project_records(records)
   │   degradation        │   │  扫描/降级/证据零连接  │
   └──────────────────────┘   └────────────────────────┘
```

### A.2 字段真实消费关系（实测结论）

| 字段 | 生产者 | manifest | 图谱 | 报告 | test_plan | 生产消费者 | 可信状态 |
|---|---|---|---|---|---|---|---|
| `symbols[]` / `calls[]` | scanner analyze_file | ✅ | ✅(edges) | 统计 | — | graph/benchmark/association_benchmark | 实测存在 |
| `calls[].resolution_status` | scanner | ✅ | ✅(edge 级) | 统计 | — | graph.py:325、benchmark.py:26/54、association_benchmark.py:40-82、js_ts_invalidation.py:41、parser_contract.py(必填) | 实测存在 |
| `analysis_diagnostics`（row 级） | scanner :478/:485/:918 | ✅ | ❌ | ✅(unknown_items) | — | 仅 test；FIX-01 补丁回写 | 实测存在 |
| **`analysis_degradation`（row 级，S1-B）** | scanner **:495 / :945** | ✅ | ❌ | ❌ | ❌ | **无任何生产消费者**（I-4） | 实测存在但"死字段" |
| `parser_contract.records` | scanner :1059 | — | — | — | — | **应被 projection 消费但未被调用**（I-9/I-10） | 实测存在 |
| `understanding_projection` 输出 | **从未运行**（I-10） | ❌ | ❌ | ❌ | ❌ | 无 | **未接线** |

**A 项结论（PASS-WITH-ISSUES 作为现状描述，非本工单失败）**：S1-B 新增的降级元数据当前是**只在 manifest 中存活的"死字段"**，既无生产消费者、也未进入图谱/报告/测试计划；S1-A 的投影能力已具备但**完全未接入流水线**；`parser_contract.json` 已天然携带 projection 所需输入却未被投影。这正是 S1-C 要闭合的断点。

---

## B 项：S1-C 唯一核心目标与非目标

### B.1 核心目标（唯一）

**建立"可验证关联层"（Linkage Layer）**：把扫描结果（symbols/calls/diagnostics）、分析降级状态（row 级 `analysis_degradation`）、证据引用（`EvidenceReference`，内容派生 + 可选 digest）、项目理解结果（`understanding_projection` 输出）**串成一条可审计的链路**，并据此**充实 `test_plan.json`**，使其每个条目都能回溯到真实扫描证据、并诚实反映降级/未知状态。

**为何提升"可验证性"而非仅增加信息**：
- 现状下，`test_plan.json` 是静态骨架，与扫描证据零连接 → 下游测试生成无从判断"哪些文件是降级解析、哪些结论是 UNKNOWN、哪些证据缺失"。
- S1-C 让**每个理解结论/计划条目必须携带 `evidence_refs` 或显式 `incomplete_reasons`**；降级/未知文件**不得生成 `STATIC_INFERENCE` 结论或"已理解"的计划条目**；`VERIFIED_FACT` 计数恒为 0（信任链未解）。
- 可验证性体现于：确定性（同输入→同 linkage ID）、可审计（每条目可回溯 path/line/diagnostic/schema 版本）、诚实（非成功态不伪装成功）。

### B.2 非目标（明确排除）

| # | 非目标 | 理由 |
|---|---|---|
| 1 | 实现 Android 端到端自主测试 | 超出信任链与授权；本工单为设计，且 G 项明示不具备 |
| 2 | 改动 scanner 核心解析/分析逻辑 | S1-A/B 成果保持不变，S1-C 仅为关联层 |
| 3 | 产出 `VERIFIED_FACT` 或 `ExecutionRecord.PASS` | 信任链 `BLOCKED/NOT_VERIFIED`，硬阻断未解除 |
| 4 | 执行任何测试/构建/安装/网络 | READ-ONLY 设计边界 |
| 5 | 数值化 `confidence` 或统一跨语言量纲 | S1-B 已决策暂缓；非 S1-C 范围 |
| 6 | 符号级降级标记（symbols[] 加 method） | S1-B 限制-1，需单独授权 |

---

## C 项：最小设计方案

### C.1 输入（Input）

- `scan_manifest.json` 的 `files[]`（每行含 `analysis_degradation`、`analysis_diagnostics`、`symbols`、`calls`、`sha256`、`path`、`language`）。
- `parser_contract.json` 的 `records[]`（已是 `understanding_projection` 适配好的输入格式）。
- 可选：`knowledge_graph.json`（仅用于 edge 级 `resolution_status` 交叉校验）。

### C.2 输出（Output，均为**新增产物，不改动既有 artifact schema**）

1. `understanding_linkage.json`（`schema_version=usit-understanding-linkage-v1`）：
   - 每行映射：`{path, language, analysis_degradation (透传/引用), projection_item (来自 project_records), evidence_refs[]}`。
   - 顶层 `stats`（复用 `ProjectionResult.stats`：static_inference / unknown / conflict / ai_hypothesis / **verified_fact=0**）。
2. **充实后的 `test_plan.json`**（保持 `usit-test-plan-v1` 与 `PLAN_ONLY_NOT_EXECUTED`，保留既有 T001–T003）：
   - 新增 `understanding_section` 与按文件/按结论的 `items`，每条目携带 `evidence_refs` + `degradation_aware` 标志 + `incomplete_reasons`（沿用 `TestSpecification.complete` 派生逻辑）。

### C.3 证据引用（Evidence Reference）

- 复用 `understanding_model.EvidenceReference`；从扫描证据填充 `relative_path`(=row.path)、`line`(=diagnostic.line 或 source_location.line)、`symbol`、`locator`、`summary`。
- `content_present=True`（行级文本确被读取）；`digest_expected = row['sha256']`，`digest_match` 由行 sha 比对得出（确定性，不需要重读文件）。
- **强制**：`source_trusted=False`、`execution_verified=False`、`trust_level=NOT_VERIFIED`、**永不置 `TRUSTED`**（构造级拒绝，I-2）。

### C.4 降级处理（Degradation Handling）

- 每个 linkage 条目透传该行 `analysis_degradation`。
- `degraded=True` 或 `resolution_status != RESOLVED` 的文件：**不得**产生 `STATIC_INFERENCE` 结论；其计划条目必须标 `requires_manual_semantic_review=True` 或在 `incomplete_reasons` 含 `'degraded_evidence'`。
- `diagnostics_ref` 失效（越界/指向空）的条目：计划条目**不得**声明"证据有效"，必须标 `evidence_ref_invalid=True` + `incomplete_reasons`。

### C.5 冲突处理（Conflict Handling）

- 直接复用 `understanding_projection._detect_conflicts` 与 `understanding_model.make_conflict`：同一逻辑标识（path+qualified_name）出现不同内容 → 产出 `CONFLICT` 结论并保留双方，**不自动裁决**（与 S1-A 语义一致）。

### C.6 兼容策略（Compatibility）

- **纯新增**：新模块 `usit/understanding_linkage.py` + 新测试；**不修改** `scanner.py` 写入路径、`symbols[]`/`calls[]`、图谱/报告 schema。
- 作为**独立后处理步骤**运行（输入为已生成的 artifact 目录），而非侵入 `scan_project` 写逻辑 → 回滚零影响、保持只读契约。
- 对**缺失 `analysis_degradation` 的旧 schema 行**（S1-B 之前形状）优雅降级：视为 `provenance=UNKNOWN` 并在 `limitations` 标注，不崩溃。

---

## D 项：≥5 个可复现验收场景

| # | 场景 | 输入 | 预期输出 | 失败条件 | 证据（验证方法） |
|---|---|---|---|---|---|
| S1 | 正常解析（ok.py, Python AST 成功） | row: language=Python, 无 diagnostic, analysis_degradation.degraded=False | projection→`STATIC_INFERENCE`；linkage 条目 `evidence_refs` 含有效 path/line；plan 条目无 `requires_manual_semantic_review` | 该文件被标 degraded，或结论为 UNKNOWN，或 evidence_refs 为空 | 断言 `projection_item.conclusion_type=='STATIC_INFERENCE'` 且 `analysis_degradation.degraded is False`；断言 evidence_ref.content_present=True（JSON 往返） |
| S2 | 解析降级（broken.py, Python 语法错误） | row: analysis_diagnostics=[PYTHON_SYNTAX_ERROR], analysis_degradation.degraded=True, reason=PYTHON_SYNTAX_ERROR, ref=0 | projection→`UNKNOWN`（无语义成功证据）；plan 条目 `requires_manual_semantic_review=True` + `incomplete_reasons` 含 `degraded_evidence` | 该文件产生 `STATIC_INFERENCE`，或 plan 条目未标降级 | 断言 `conclusion_type=='UNKNOWN'`；断言 plan 条目 `requires_manual_semantic_review is True`；断言 ref 指向的 diagnostic.code=='PYTHON_SYNTAX_ERROR' |
| S3 | 证据引用失效（main.js, JS/TS） | row: language=JavaScript, analysis_degradation.reason=SEMANTIC_PARSER_UNAVAILABLE, ref=0（FIX-01 后有效） | ref_valid=True，ref_code==reason；plan 条目 evidence 有效 | **若** ref 越界/指向空 → plan 条目必须 `evidence_ref_invalid=True` 且不得声明证据有效 | 序列化往返后断言 `diagnostics_ref` 解析为 `SEMANTIC_PARSER_UNAVAILABLE`；构造一个"诊断被清空"的负例断言 `evidence_ref_invalid` 触发 |
| S4 | 未知状态（notes.xyz, 不支持语言） | row: language=Unknown, analysis_degradation.degraded=True, reason=UNSUPPORTED_LANGUAGE_NO_SEMANTIC_PARSER, diagnostics_ref=None | projection→`UNKNOWN`；plan 条目 `discovery_only=True`，无任何语义声明 | 该条目产生 STATIC_INFERENCE 或声称已解析 | 断言 `reason=='UNSUPPORTED_LANGUAGE_NO_SEMANTIC_PARSER'` 且 `resolution_status=='UNKNOWN'`；断言无 `degraded=False` 误报 |
| S5 | 旧 schema 兼容（无 analysis_degradation 字段的行） | row: **不含** `analysis_degradation`（S1-B 前形状），含 symbols/calls | linkage 不崩溃；该条目 `provenance='UNKNOWN'` + `limitations` 标注缺降级元数据；仍产出有效 evidence_refs（来自 symbols/calls） | 抛异常、或静默当作已验证、或 symbols[]/calls[] 旧键解析失败 | 断言函数对缺字段行返回合法 `ProjectedItem`；断言 `symbols` 键集合仍为 `{name,kind,line,evidence}`；断言 `verified_fact==0` |
| S6 | 确定性 & 非成功不计成功（补充） | 同 S1 样本两次运行 | 两次 `projection_id`/`conclusion_id` 全等；`stats.verified_fact==0`，`success_counted` 不含 UNKNOWN/CONFLICT | 两次 ID 不同，或 verified_fact>0 | 断言两次序列化 JSON 字节一致；断言 `stats['verified_fact']==0` |

> 每个场景的证据均为**对产物字段的断言**（JSON 往返读取），非信任声明；不将 `STATIC_INFERENCE`/`UNKNOWN` 计为已验证。

---

## E 项：测试分层与验收门槛

| 层级 | 范围 | 是否本设计实施 | 验收门槛 |
|---|---|---|---|
| 单元测试 | `usit/understanding_linkage.py` 纯函数（evidence 构建、降级映射、plan 充实、冲突传播） | ✅ 建议新增 | 每个函数有等价类/边界用例；确定性断言；`verified_fact` 恒 0 |
| 端到端产物验证 | 在 `tempfile.TemporaryDirectory` 跑 `scan_project`（fixtures）→ 再跑 linkage → 断言 `understanding_linkage.json`/`test_plan.json` 字段（JSON 往返） | ✅ 建议新增（复用 S1-B 的 e2e 手法） | S1–S6 全部可复现；rc=0；无 FAIL/SKIP |
| 真实项目验证 | 对实际业务仓库跑完整链路 | ⛔ **未授权 / UNKNOWN** | 真实项目字段分布与性能未实测；标记为 UNKNOWN，不冒充已验证 |
| 未测试事项 | 大工程下 digest/投影耗时；Android 路径（明确未实现） | ⛔ | 标记 UNKNOWN |

**阶段成功门槛（明确，非"测试全绿"）**：
1. `verified_fact` 计数 **永远 = 0**（信任链未解，硬约束）。
2. 所有非成功态（UNKNOWN/AMBIGUOUS/DYNAMIC/EXTERNAL/UNRESOLVED/CONFLICT）**不得**升级为 `STATIC_INFERENCE` 或被计划条目宣称"已理解"。
3. 每个计划条目**必须**至少携带 1 个 `evidence_refs` 元素 **或** 显式 `incomplete_reasons`（沿用 `TestSpecification.complete` 派生）。
4. 降级/未知文件**必须**在计划中以 `requires_manual_semantic_review` 或 `incomplete_reasons` 显式标出。
5. **既有 artifact 零回归**：`scan_manifest.json`/`knowledge_graph.json`/`report.md` 结构不变；旧 schema 行可处理。
6. 确定性：同输入 → 同 linkage ID（内容派生）。

> 门槛 1–4 保证"可验证性提升"是**结构性强制**而非靠测试数量堆出来——直接回应边界 #5（不能仅以加字段或测试全绿作为成功标准）。

---

## F 项：变更候选文件、授权前置、回滚与风险

| # | 项 | 内容 |
|---|---|---|
| 1 | **变更候选（新增）** | `usit/understanding_linkage.py`（新关联层模块）；`tests/test_s1c_linkage.py`（新增测试）；产物 `understanding_linkage.json` 与充实后的 `test_plan.json` |
| 2 | **变更候选（可选，非必须）** | 仅在"希望扫描后自动生成 linkage"时，于 `cli.py` 增加**可选的**后处理调用（默认关，不改动 `scanner.py` 写入路径）。**最小方案建议先不做，保持独立后处理** |
| 3 | **不改动** | `scanner.py` 解析/写入逻辑、`symbols[]`/`calls[]`、图谱/报告 schema、`understanding_projection.py`、`understanding_model.py` |
| 4 | **授权前置条件** | ① 用户明确批准 S1-C 实施；② 维持固定门禁原值（F1/P0.8/F2/连续性/信任链）；③ 不触网/不执行/不改信任链；④ `understanding_linkage.py` 不得绕过 `EvidenceReference`/`ConclusionRecord` 的构造级拒绝 |
| 5 | **回滚方案** | 删除新增模块、新增测试、生成产物即可；因纯新增且为后处理，**对既有 artifact 零影响**；回滚风险低（须另行授权执行删除） |
| 6 | **风险** | R1：若充实 `test_plan.json` 时覆盖既有 T001–T003 → 必须保留并合并；R2：digest 计算开销（可用行级已有 sha256，零额外读盘，开销低）；R3：误将 `STATIC_INFERENCE` 当作"已验证" → 由门槛 1–2 强制拦截；R4：跨 artifact 路径相对化（manifest 用 `path` 相对 project_root，evidence 引用需统一相对路径） |

---

## G 项：目前仍不具备的能力（明确清单，不暗示已实现）

| 能力 | 状态 | 说明 |
|---|---|---|
| **Android 端到端自主测试** | ⛔ **未实现 / 未设计可执行** | `test_plan.json` 仅建议"人工复核后再运行构建/测试"（T001/T002/T003，`PLAN_ONLY_NOT_EXECUTED`）；无任何 Android 构建/运行/模拟器/设备能力；S1-C 不新增该能力 |
| `VERIFIED_FACT` / 可信外部验证协议 | ⛔ 未授权 | `understanding_model` 构造级硬阻断；`verified_fact` 恒为 0 |
| `ExecutionRecord.PASS` | ⛔ 未授权 | 仅允许 `NOT_RUN`；无外部执行验证协议 |
| 真实项目 ground-truth 度量 | ⛔ `NOT_MEASURED` | scanner `parser_quality.ground_truth_metrics` 全 0（:1094-1096） |
| 跨语言 `confidence` 数值统一 | ⛔ 暂缓 | Python 字符串量纲 vs JS/TS 数值量纲不一致（S1-B 决策） |
| 符号级降级标记（symbols[] 加 method） | ⛔ 未做 | S1-B 限制-1，需单独授权 |
| 报告层逐文件降级展示 | ⛔ 未做 | S1-B 限制-2，授权范围外 |
| 安全信任链 | ⛔ `BLOCKED / NOT_VERIFIED` | 全程原值不变；S1-C 设计**不解除**该状态 |

---

## H 项：收口结论、门禁与建议

### 设计核验小结（PASS/FAIL/UNKNOWN）

| 项 | 判定 |
|---|---|
| A 项数据流图（基于 I-1~I-10 实测） | ✅ PASS（字段消费关系已用代码阅读+ grep 坐实） |
| B 项核心目标/非目标定义 | ✅ PASS（目标聚焦"可验证关联"，非堆信息） |
| C 项最小设计（输入/输出/证据/降级/冲突/兼容） | ✅ PASS（复用既有硬阻断模块，纯新增，不改 schema） |
| D 项 ≥5 场景（实给 6） | ✅ PASS（覆盖正常/降级/引用失效/未知/旧 schema/确定性） |
| E 项测试分层与门槛 | ✅ PASS（含 6 条结构性强制门槛，回应边界 #5） |
| F 项变更/授权/回滚/风险 | ✅ PASS |
| G 项能力清单（尤其 Android） | ✅ PASS（如实标注未实现，未暗示） |

### 固定门禁（原值保持，全程未变）

| 门禁 | 值 |
|---|---|
| `F1` | `NOT_READY_TO_CLOSE` |
| `P0.8` | `HOLD` |
| `F2` | `STOP` |
| `历史授权连续性` | `UNRESOLVED` |
| `安全信任链` | `BLOCKED / NOT_VERIFIED` |

### 未解决问题（Unresolved）

1. **S1-A 投影未接线**是 S1-C 实施的前提断点——本设计为闭合它提出"独立后处理"路径，但**实施是否采用该路径需另行授权**。
2. `analysis_degradation` 当前为死字段；若 S1-C 仅做关联层而不接入报告/图谱展示，其下游可见价值仍有限（与 S1-B-AUDIT-01 ISSUE-2 同源）——是否同时接入报告层属后续决策。
3. 真实项目验证与性能为 UNKNOWN，未实测。

### 最终建议（Recommendation）

- **建议将 S1-C 定位为"关联层 + 计划充实"一步到位的最小可验证增量**，而非一次铺开（不碰图谱/报告渲染、不动 scanner 写入路径），以把回归面压到最小并保留只读契约。
- **实施前必须另行授权**；本工单仅提交设计，未修改任何文件。
- 建议验收以本报告 E 项 6 条结构性门槛为准，而非"测试全绿"数量。

---

**STOP**：仅提交设计，未修改任何文件。等待你与 ChatGPT 审查本报告并另行授权 S1-C 实施。
