# USIT-S1-C-IMPLEMENT-02B-DESIGN-01｜文件级结论层接线方案与变更准入设计 执行报告

- 工单：USIT-S1-C-IMPLEMENT-02B-DESIGN-01｜执行时间：2026-10-20（GMT+8）｜工作区：`C:\Users\Administrator\WorkBuddy\2026-10-15-22-09-47\usit_p0`
- 模式：READ-ONLY 设计与影响分析，**不实施代码变更**。仅新增本报告；未修改任何源码/测试/配置/输出基线/冻结哈希文件。
- 前置依据：DESIGN-10/11/12/13/14、IMPLEMENT-02A、POST-IMPLEMENT-02A-AUDIT-01（均已实读，非仅摘要）。
- 标签约定：SOURCE_CONFIRMED（本次实读源码/测试证实）/ DESIGN_DECISION（本工单设计裁定）/ NOT_VERIFIED（缺直接证据）/ FUTURE。**所有推演均为设计，非运行结果**。
- 授权边界：不接线、不实施、不更新 `PRE_SHA256.json`、不解除任何门禁；完成后 STOP 等待独立审查与实施授权。

## 1. 执行信息与只读边界核验

| 核对项 | 结果 |
|---|---|
| 工作区路径与文件清单 | PASS（usit_p0 存在；`usit/file_understanding.py`、`tests/test_file_understanding.py` 在位，与 IMPLEMENT-02A/AUDIT-01 记录一致） |
| 本次审计外新变更 | PASS（未发现；本工单零修改） |
| 读取清单 | IMPLEMENT-02A 报告、POST-IMPLEMENT-02A-AUDIT-01 报告、`usit/scanner.py`（:452-496 行键、:708 scan_project、:1040-1095 parser_contract 构建、:1095-1174 输出块）、`usit/cli.py`（:1-60、:75-150）、`tests/test_p072_gates.py`（:445-489、:84）、失败路径测试断言、冻结基线 `outputs/p0710zxrxf/PRE_SHA256.json` |
| 禁止项遵守 | PASS（未改任何文件、未跑测试套件/真实扫描/构建/安装/登录/联网/外部 AI、未更新基线） |

## 2. 接线位置（唯一推荐接入点）

### 2.1 scanner.py 输出流程实测（SOURCE_CONFIRMED）

`scan_project`（:708）终端成功路径的数据可用时序：

1. `rows`（每文件分析行，键含 `path/size_bytes/readable_text/symbols/analysis_degradation`，:462 + :495）→ `nodes/edges`（**每个文件恒建 file 节点**，:981-982）→ `graph`（:1051）
2. `parser_contract = adapt_graph_v1(graph)`（:1059）→ 追加 js_ts 记录（:1060）→ 追加项目级诊断（:1061-1065，`file_path=''` + `node_type='diagnostic'` + `evidence={'diagnostic':code}`）→ 追加文件级诊断（:1066-1073）→ `record_validation_errors`（:1074-1076）——**records 自此完整**
3. `manifest`（:1108，`files` = rows 去 `_captured_text`，键含 FileContext 全部来源）→ `summary`（:1109-1134）
4. 输出 scrub（:1137，graph/manifest/plan）→ `validate_graph_trusted`（:1144）→ `report` 渲染（:1148）
5. `bounded_write`（:1150-1155，MAX_OUTPUT_BYTES 预算）→ `write_json`（:1159，sort_keys+indent=2）→ 写 `report.json/scan_manifest.json/knowledge_graph.json/test_plan.json`（:1160）→ `parser_contract.json`（:1161）→ `graph_validation.json`（:1162）→ `.md`（:1163-1164）→ `cache/previous_manifest.json`（:1165）
6. `scan_status.json` **最后写**（:1170-1172），`published_files` 用 `out.rglob('*')` 现场枚举此前所有文件

### 2.2 推荐接入点（DESIGN_DECISION）

**唯一推荐：在 scanner.py 内新增一个永不抛错的私有装配函数，两个插入点，均在终端成功块内。**

- **装配点（build）**：插入于 :1137（scrub）之后、:1148（report 渲染）之前。调用 `understanding, understanding_error = _build_file_understanding(manifest, parser_contract)`，随后 `summary['understanding_status'] = {...}`（COMPLETE 或 FAILED+错误类型/消息）。必须在 :1160 写 `report.json` **之前**完成，使状态随 summary 落盘。
- **输出点（emit）**：插入于 :1162（graph_validation.json）之后、:1170（scan_status.json）之前：`if understanding is not None: write_json('understanding.json', understanding)`。必须早于 scan_status.json，使其被 `published_files` 自动枚举（:1172 rglob）。

数据源：scrub **后**的 `manifest['files']` + 完整的 `parser_contract['records']`——敏感文件已在发现期排除（selftest:27 证实 `.env/id_rsa/node_modules` 不入 rows），用 scrub 后 manifest 属纵深防御。

### 2.3 四项风险规避论证（DESIGN_DECISION）

| 风险 | 规避论证 |
|---|---|
| 重复扫描 | 接入点在扫描完成后，零新增文件 I/O；不调用 discover_files/analyze_file |
| 重复投影 | `understanding_projection` 当前在 `usit/` 内**无任何调用方**（历次审计 grep 证实，BLOCKER-1 即此）；接线后 `project_records` 唯一调用发生在 `build_file_understanding` 内部，每文件组恰好一次 |
| 循环依赖 | 方向单一：`file_understanding` → `understanding_projection`/`parser_contract`；`scanner` → `file_understanding`。无环 |
| 覆盖既有文件 | 新文件名 `understanding.json` 与现有 8 个输出零重名；不向 report.json/scan_manifest.json 等加键（仅 summary 增 `understanding_status` 一个诊断键） |

### 2.4 接线伪代码（设计，非实现）

```python
# 插入于 :1137 之后（scrub 已完成）
from .file_understanding import build_file_understanding, verify_understanding_identities
try:
    understanding = build_file_understanding(manifest['files'], parser_contract['records'])
    total_input = len(parser_contract['records'])
    verify_understanding_identities(understanding, total_input)   # K1-K7 自检，失败即抛
    summary['understanding_status'] = {'status': 'COMPLETE'}
except Exception as exc:            # 装配/自检失败：不中止扫描、不伪装成功
    understanding = None
    summary['understanding_status'] = {'status': 'FAILED', 'error_type': type(exc).__name__, 'error_message': str(exc)}
# 插入于 :1162 之后
if understanding is not None:
    write_json('understanding.json', understanding)
```

注：序列化置于 `write_json` 内部既有 try 之外会引入第二类失败面，故装配函数内只做构建+自检；`json.dumps` 失败归入 §4 第 3 类统一处理（在 emit 点包 try，失败置 understanding=None 并回写 summary FAILED——因 report.json 已写，回写意味着 summary 与磁盘不一致，故**最终裁定：emit 点的序列化失败也必须前移至装配点完成**，即装配函数返回已序列化文本或 (None, error)，保证 report.json 写下时状态终态一致。见 §4）。

## 3. 输出契约（understanding.json）

| 契约项 | 裁定（DESIGN_DECISION） |
|---|---|
| 输出路径 | `out/understanding.json`（与既有输出同目录，同 `write_json` 通道：sort_keys、indent=2、UTF-8、参与 MAX_OUTPUT_BYTES 预算） |
| schema 版本 | **`usit-file-understanding-v1` 原样保持，不改**（IMPLEMENT-02A 已冻结并通过审计） |
| 生成条件 | 仅 scan_project 到达终端成功块时生成（被 ScanBlocked/中止的路径不生成——失败路径测试断言 `published()==[]`，见 §6.2） |
| 顶层结构 | `{schema_version, projection_schema_version, generated_from, files[], project_diagnostics[], unmatched_records[], totals{files, by_conclusion, n_records_total, conflict_total, unmatched_total, project_diagnostic_total}}`（DESIGN-12/13/14 冻结） |
| 失败时行为 | 文件**不写**；`summary['understanding_status'].status='FAILED'` 带 error_type/error_message；扫描继续、CLI exit 0；scan_status.json 的 published_files 自然不含该文件（诚实缺席，非伪造） |
| 既有兼容 | 纯加法：不改 report.json/scan_manifest.json/knowledge_graph.json/test_plan.json/parser_contract.json/graph_validation.json/scan_status.json 任何既有字段与 schema 版本；summary 仅增 1 个诊断键（无测试断言 summary 精确键集，实测安全） |
| 消费方策略 | 按 `schema_version` 门控；未知键忽略（前向兼容）；缺失可选字段按缺席不伪造（DESIGN-12 冻结） |
| 与 manifest 关系 | files[] 逐条对应 manifest 有效文件（清单驱动），n_records==该文件 N_raw；冲突合成项只入 conflict_count/projected_items，永不入 n_records |

## 4. 错误与安全分级（四类，均不包装成成功）

| # | 失败类 | 触发面 | 行为裁定（DESIGN_DECISION） | 中止扫描？ | 诊断留存 |
|---|---|---|---|---|---|
| 1 | 单条记录投影失败 | `project_records` 内 per-record except（IMPLEMENT-01 已实现+测试） | 非致命：计入 `N_construct_fail`/`N_unexpected_error`，产降级 ProjectedItem，N_raw 不减 | 否 | understanding.json 内 counts + evidence_refs（失败记录经降级项 projection_id 可追溯，AUDIT-01 §2.3 实测证实） |
| 2 | 文件级构建失败 | `build_file_understanding` 抛 ValueError（manifest 结构无效/重复路径/系统性失配/strict）或 `verify_understanding_identities` 自检失败 | 捕获 → understanding=None + `summary['understanding_status']=FAILED{error_type,error_message}`；文件不写；扫描继续 | **否** | summary（report.json）+ CLI stdout（summary 即 CLI 打印体，cli.py:147） |
| 3 | 序列化失败 | `json.dumps(understanding)` 抛 TypeError（理论上不可达——全纯数据结构；防御性处理） | **前移至装配点**：装配函数内完成 `json.dumps` 得到文本，失败按第 2 类处理；保证 report.json 写下时 understanding_status 为终态，避免“summary 说 COMPLETE 但文件缺失/反之”的不一致 | 否 | 同第 2 类 |
| 4 | 输出写入失败 | `bounded_write` 抛 ScanBlocked（MAX_OUTPUT_BYTES 超预算，:1150-1154） | 与所有既有输出一致：上抛 → 既有 blocked 路径写 `usit-status-*.json` 标记（:218-223）→ CLI exit 2（cli.py:148-149）。**不给 understanding.json 特殊豁免**（避免静默丢输出） | 是（既有语义） | usit-status 标记 + stderr ERROR 行 |

红线（DESIGN_DECISION）：任何失败不得表现为 success；`understanding_status.status` 只有 `COMPLETE`/`FAILED` 二值，无中间态；第 2/3 类失败**不**写半成品 understanding.json（缺席即诚实）。

## 5. 哈希治理（只读复核 + 治理方案）

### 5.1 实测差异（SOURCE_CONFIRMED，本次只读复核）

基线 `outputs/p0710zxrxf/PRE_SHA256.json`（文件时间 Oct-17）：

| 文件 | 基线 | 当前 | 状态 |
|---|---|---|---|
| usit/scanner.py | c70a1a4baf3a… | a009b8dbb132… | **DRIFT**（在 ZXRXF7_WATCH → 2 项守卫失败根因） |
| usit/cli.py | f6d66207e20a… | 879a3735838e… | **DRIFT**（**不在** ZXRXF7_WATCH → 不触发失败） |
| usit/graph.py | eeda9ae8e22a… | eeda9ae8e22a… | MATCH |
| usit/safeio.py | d8d8a42e6cb7… | d8d8a42e6cb7… | MATCH |
| tests/ 4 个监视文件 | — | — | 全 MATCH |

### 5.2 归因边界（NOT_VERIFIED）

- 已证实：漂移事实、监视集内仅 scanner.py 漂移（孤立单文件）、基线早于本会话（Oct-17 < Oct-20）、历次工单（IMPLEMENT-01/02A/AUDIT）均未修改 scanner.py/cli.py（字节复验一致）。
- **未证实**：漂移发生在哪次具体修改/会话。无逐会话哈希链，按工单要求**不归因**，不写成“某次修改导致”。
- cli.py 漂移同样未证实（且不在守卫范围，无失败关联）。

### 5.3 治理方案（DESIGN_DECISION）

1. **独立历史漂移调查**（建议单独只读工单）：枚举 Oct-17 至当前的全部工单执行报告，收集各报告记录的 scanner.py/cli.py 前后哈希，串成哈希链；仅当存在“前值==基线值”的直接链接时方归因；产出漂移时间线报告（只读，不改任何文件）。
2. **基线再生须显式授权**：IMPLEMENT-02B 实施工单内可将“再生 `outputs/p0710zxrxf/PRE_SHA256.json`”列为独立授权步骤，执行时在报告中记录：再生前基线哈希、再生后基线哈希、每个监视文件的前后哈希、再生理由（“因授权实施修改 scanner.py”）；**禁止静默再生、禁止把基线陈旧当作更新授权**。
3. **验收预期管理**：基线再生前，2 项守卫失败（test_p0710zxrxf_guard_r2 / trust_boundary）持续存在且属既有越界；IMPLEMENT-02B 的通过标准**不得**要求其转绿，只要求“零新增失败”。
4. cli.py 漂移：本方案不改 cli.py（无接线必要，§7）；其基线记录陈旧在调查工单中一并记录，不在本工单处置。

## 6. 测试与验收矩阵设计

### 6.1 新增测试（DESIGN_DECISION，全部尚未编写→NOT_RUN）

**U 单元级**（并入 `tests/test_file_understanding.py` 或新模块）：
- U-01 装配函数对合法输入返回 (text, None)，text 可 json.loads 且 schema_version=='usit-file-understanding-v1'
- U-02 装配函数对 manifest 结构非法/重复路径返回 (None, error)，error 含 type/message，**不抛**
- U-03 自检失败（注入矛盾 counts）→ (None, error)
- U-04 summary 状态键形状：COMPLETE 时 `{'status':'COMPLETE'}`；FAILED 时含 error_type/error_message

**I 集成级**（新建 `tests/test_s1c_wiring.py`，仿 `test_p072_gates._SyntheticProject` 模式 + `scan_project(..., force=True, authorization=...)`）：
- I-01 成功扫描合成项目 → `out/understanding.json` 存在；schema v1；`len(files)==len(manifest files)`（清单驱动全覆盖）
- I-02 `totals.files==len(files)`；`Σby_conclusion==totals.files`；`n_records_total==Σfiles.n_records`（K1/K2/K5/K6 真实管线复算）
- I-03 K7：构造含同身份异内容冲突的合成项目（两符号同名不同状态）→ `conflict_total==Σconflict_count`，冲突组每组 1 次
- I-04 K8：抽查文件 `projected_items==n_records+conflict_count`
- I-05 不可读非空文件（写入非 UTF-8 字节 → readable_text=False）→ 该文件 file_conclusion 为 UNKNOWN_RESOLUTION（有 file 节点记录）且**不等于** NO_SEMANTIC_CONTENT；content_status='unreadable'
- I-06 项目级诊断隔离：js_ts 不可用时合成项目无项目诊断→ `project_diagnostics==[]`；用直接 builder 单测覆盖 T-01..T-04（DESIGN-14，已在 02A 覆盖，回归即可）
- I-07 unmatched：真实 scanner 路径不产生（契约保证）；以 02A 既有单测回归覆盖，集成侧标注 NOT_APPLICABLE
- I-08 部分/全部投影失败：集成不可注入（不改 scanner），以 02A 既有注入单测回归；集成侧标注 NOT_APPLICABLE（理由记录）
- I-09 既有输出兼容：report.json/scan_manifest.json/parser_contract.json 等仍生成且既有键在位；`scan_status.json.published_files` 含 'understanding.json'
- I-10 summary 含 `understanding_status.status=='COMPLETE'`
- I-11 敏感内容不泄漏：仿 test_p072_gates:478-489，对 understanding.json 追加同样断言集合

**C CLI 级**（调 `usit.cli.main(argv)` 或 subprocess）：
- C-01 成功扫描 exit 0，stdout 为 summary JSON 且含 understanding_status
- C-02 缺少 authorization → exit 2（cli.py:148-149 既有行为），**且 out 无 understanding.json**（失败不产出）
- C-03 `--out` 内 published_files 含 understanding.json（经 scan_status.json 读取）

**R 回归**：完整套件 `python -m unittest discover -s tests -p "test_*.py" -v`；基线现状 547 项 / 2 既有越界失败 / 3 环境跳过；IMPLEMENT-02B 后允许集 = {同样 2 项}，**零新增失败**；不得删/跳/弱化旧测试。

**E 端到端**（实施工单内执行，需操作员提供 authorization 文件）：
- E-01 对 usit_p0 自身扫描 → understanding.json 全量 K1–K8 复核 + `n_records_total` 与 parser_contract['records'] 分类计数手工对账 + 无 VERIFIED_FACT + evidence_refs 非空
- E-02 体量测量：记录 understanding.json 字节数占 MAX_OUTPUT_BYTES 比例（见 §9 未决 R-2）

### 6.2 量化验收条件（实施工单通过标准，DESIGN_DECISION）

1. I-01..I-11、C-01..C-03、U-01..U-04 全 PASS（实跑，附命令/退出码/原始输出）
2. 完整套件失败集 ⊆ {既有 2 项} 且数量不增
3. 既有输出文件逐一字节兼容（report.json 除新增 understanding_status 一键外不变——用前后 diff 证明）
4. 修改范围 = §7 清单，范围外零变更（sha256 前后对照）
5. 基线再生（若授权）按 §5.3-2 留痕；未授权则不再生且记录 2 项失败为既有

## 7. 范围、预计修改文件与回滚

| 文件 | 动作 | 必要性 | 潜在影响 | 回滚方式 |
|---|---|---|---|---|
| `usit/scanner.py` | 修改（新增 import + 装配函数 ~20 行 + 2 插入点） | 唯一同时持有 manifest/parser_contract/输出管线/预算/终止状态的位置（§2.1） | 新增 1 输出文件 + summary 1 诊断键；触发 ZXRXF7 守卫既有失败持续（§5） | 恢复前映像（实施报告中记录 PRE sha256=a009b8dbb132… 与 unified diff；非 git 仓库，用备份副本回滚） |
| `tests/test_s1c_wiring.py` | 新建 | 集成/CLI 验收（§6.1） | 无（纯新增） | 删除文件 |
| `usit/file_understanding.py` | **不改** | 02A 已实现且审计通过 | — | — |
| `usit/cli.py` | **不改** | 薄封装（:147 直接打印 scan_project 返回值），understanding_status 自动进入 stdout；新增子命令无必要 | — | — |
| `outputs/p0710zxrxf/PRE_SHA256.json` | **不改**（除非实施工单获显式授权并按 §5.3-2 留痕再生） | 治理要求 | — | — |
| 其他 schema/配置/既有测试 | **不改** | — | — | — |

## 8. BLOCKER 解除证据条件（设计阶段不解除）

- **BLOCKER-1（投影未接线）申请解除需齐备**：① scanner.py 接线 diff + 前后哈希；② I-01..I-11 + C-01..C-03 实跑通过证据；③ E-01 真实扫描 K1–K8 对账；④ 完整套件零新增失败；⑤ 基线再生留痕（若授权）。以上齐备后由独立审查工单裁定，本工单仅定义条件。
- **BLOCKER-2（adapt_graph_v1 fallback 过宽）**：机制已在 IMPLEMENT-01 修正（移除合成 graph_node_id + 分类诊断），AUDIT-01 已确认信任边界；**接线后复核证据**：E-01 真实扫描输出中 `parser_contract.json`/`understanding.json` 的 evidence 字段零 `graph_node_id`、缺失/损坏证据记录携 diagnostic 且投影为非成功。齐备后由独立审查裁定。

## 9. 未决事项与风险

- R-1（NOT_VERIFIED）scanner.py/cli.py 历史漂移根因——待 §5.3-1 独立调查；本工单不归因。
- R-2（NOT_VERIFIED）understanding.json 大体量项目下的字节占比未实测（evidence_refs 逐记录投影 ID，AUDIT-01 O-4 已提示大文件可能较长）——E-02 测量；若超预算风险显著，未来须单独授权评估 evidence_refs 口径（schema 决策，不在本工单）。
- R-3（DESIGN_DECISION 记录）NO_RECORDS 在真实 scanner 路径不可达（每文件恒建 file 节点，:981-982）——保留为防御分支，集成测试以直接 builder 覆盖（02A 已覆盖）。
- R-4（DESIGN_DECISION）第 4 类写入失败会因共享预算中止整个扫描——与既有输出语义一致，但意味着 understanding.json 体量直接影响扫描可达性；以 E-02 数据监控。
- R-5（NOT_VERIFIED）`summary['understanding_status']` 新增键对下游消费 report.json 的既有工具的兼容性——无测试断言精确键集（实测），但外部消费方不在本仓库，标记未验证。

## 10. 逐项结论与最终结论

| 项 | 结论 |
|---|---|
| 1. 执行前核对与只读边界 | PASS |
| 2. 接线位置（唯一接入点+顺序+四风险规避） | PASS（SOURCE_CONFIRMED 流程 + DESIGN_DECISION 方案） |
| 3. 输出契约 | PASS（usit-file-understanding-v1 不变；纯加法；失败诚实缺席） |
| 4. 错误与安全分级 | PASS（四类分明；无成功包装；序列化失败前移消除不一致面） |
| 5. 哈希治理 | PASS（只读复核；漂移事实已证实/根因未证实不归因；再生须显式授权留痕） |
| 6. 测试与验收矩阵 | PASS（U/I/C/R/E 全覆盖工单点名场景；全部 NOT_RUN 待实施工单执行） |
| 7. 范围与回滚 | PASS（范围=scanner.py+新测试文件；cli/file_understanding/基线不改） |
| 8. BLOCKER 证据条件 | PASS（条件定义；本工单不解除） |

**最终结论：PASS-WITH-ISSUES**——接线设计可执行、变更范围明确、失败路径全部可审计、验收条件可量化；ISSUES 为须随实施工单携带治理项：① scanner.py/cli.py 历史漂移根因未证实（禁猜，先行独立调查或实施时留痕）；② understanding.json 体量未实测（E-02 前置）；③ 基线再生须显式授权（禁静默）。

**固定门禁复核（原值保持）**：F1=NOT_READY_TO_CLOSE / P0.8=HOLD / F2=STOP / 历史授权连续性=UNRESOLVED / 安全信任链=BLOCKED/NOT_VERIFIED / verified_fact=0 / ExecutionRecord=NOT_RUN / summary_verification=NOT_CHECKED / complete=False / BLOCKER-1 不解除 / BLOCKER-2 待接线后复核。未宣称可信项目理解/完整测试/自动修复。

**STOP**：本工单仅授权只读设计与影响分析并新增本报告；不授权实施/接线/测试执行/基线更新/真实项目操作。设计须经独立审查，再另行签发 IMPLEMENT-02B 实施授权工单。

## 11. 文件修改核验

本工单零修改：`usit/scanner.py`（a009b8dbb132…）、`usit/cli.py`（879a3735838e…）、`usit/file_understanding.py`、`usit/understanding_projection.py`、`usit/parser_contract.py`、`usit/understanding_model.py`、`usit/schema_adapter.py`、`outputs/p0710zxrxf/PRE_SHA256.json`、tests/ 全部、既有报告全部——与工单开始前逐一相符；仅新增本报告。
