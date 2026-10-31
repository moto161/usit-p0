# USIT-S1-C-IMPLEMENT-02B-DESIGN-02｜发布一致性、输出预算与证据脱敏专项修订 执行报告

- 工单：USIT-S1-C-IMPLEMENT-02B-DESIGN-02｜执行时间：2026-10-20（GMT+8）｜工作区：`C:\Users\Administrator\WorkBuddy\2026-10-15-22-09-47\usit_p0`
- 性质：READ-ONLY 设计修订，前置 = IMPLEMENT-02B-DESIGN-01。仅新增本报告；零修改源码/测试/配置/基线。
- 标签：SOURCE_CONFIRMED（本次实读证实）/ DESIGN_DECISION（本工单裁定）/ NOT_VERIFIED（缺直接证据）/ FUTURE。**所有推演均为设计，非实测结果；所有新测试均 NOT_RUN。**
- 授权边界：不接线/不实施/不跑测试/不真实扫描/不构建/不安装/不登录/不联网/不外部 AI/不更新 `PRE_SHA256.json`/不解除门禁；完成后 STOP。

## 1. 执行信息与证据基础

实读范围：`usit/scanner.py`（:10-11 导入、:87 `is_sensitive`、:200-229 状态标记、:510-536 scrub、:708-796 发布编排、:1040-1174 输出块）、`usit/safety.py`（:12-18 常量、:28 ScanBlocked）、`usit/file_understanding.py`（:285-356 分类/条目构建）、`usit/understanding_projection.py`（:122-149 `_build_evidence`、:152-248 `project_record`、:319-330 `to_dict`、:391-413 降级项）、`usit/understanding_model.py`（:198-210 EvidenceReference、:261-271 ConclusionRecord）、`tests/test_p072_gates.py`（:418-448 预算/写入失败测试、:460-489 published_files/scrub 断言）、`tests/test_p073_budget_race.py`（:40-125 预算测试模式）。只读边界核验 PASS（本工单零修改）。

## 2. P0-1 发布一致性

### 2.1 发布架构实测（SOURCE_CONFIRMED）

`scan_project`（:708-796）的真实发布模型是 **staged-then-rename**，不是逐文件直接落盘：

1. 读输入（带守卫）→ 失败即 `_write_scan_status(requested_out.parent, exc)` + raise（:768-770）
2. 建暂存目录 `stage=.usit-stage-*`（位于 **final_out 同级**，:771 → 保证同文件系统）
3. `_scan_project_impl(root, stage, …)`（:773）——**全部输出写入 stage**，写入顺序（:1160-1172）：
   `report.json` → `scan_manifest.json` → `knowledge_graph.json` → `test_plan.json` → `parser_contract.json` → `graph_validation.json` → `graph_validation.md` → `report.md` → `cache/previous_manifest.json` → **［新] `understanding.json`** → `scan_status.json`（**最后**，:1170-1172 用 `out.rglob('*')` 现场枚举此前所有文件为 `published_files`）
4. 权威预算检查 staged 总量（:774-776）+ 截止时间检查（:777-778）
5. 发布：final_out 存在→备份 `.preserved-<ms>` + rename（:780-784）；`stage.rename(final_out)`（:785，**单次目录 rename**）；rename 失败→恢复备份（:786-788）
6. 任何异常→rmtree stage（:792-794）+ `_write_scan_status` + raise（:791-796）

### 2.2 状态模型（DESIGN_DECISION）

| 状态 | 定义 | 可观察性 |
|---|---|---|
| BUILD_COMPLETE（内部） | stage 内全部输出（含 understanding.json 或 understanding_status=FAILED）写完 | 不可直接观察；仅经 rename 后成为 PUBLISHED |
| PUBLISHED | final_out 存在 + `scan_status.json` status=COMPLETE + `published_files` 枚举 `understanding.json` | **唯一可宣称 understanding.json 已发布的状态** |
| BLOCKED | ScanBlocked（含 MAX_OUTPUT_BYTES_EXCEEDED）→ parent 落 `usit-status-*.json` status=BLOCKED；stage 已清；final_out 为旧值或不存在 | 无任何发布宣称 |
| INCOMPLETE | 非 ScanBlocked 异常（如 OSError 写入失败，:437-448 既有测试）→ usit-status status=INCOMPLETE | 同上 |

`understanding_status`（summary 内）保持 **二值**：`COMPLETE`（文件在 stage 内）/ `FAILED`（文件缺席+error_type/error_message）。不引入第三值——发布真相由 `scan_status.published_files` 承载，构建真相由 `understanding_status` 承载，二者同 stage 同 rename，不会出现可观察的不一致。

### 2.3 一致性不变量（DESIGN_DECISION，全部可断言）

- **I-1**：`'understanding.json' in scan_status.published_files` ⟺ 该文件存在于 final_out（同 stage 写入、:1170 现场枚举、随后自身才写）
- **I-2**：`summary.understanding_status=='COMPLETE'` ⟺ understanding.json 在同一 stage 存在；`=='FAILED'` ⟺ 缺席。两者随同一次 rename 可见/不可见
- **I-3**：不存在 stage 之外的 understanding.json 写入路径（接线只经 `write_json`/`bounded_write`，:1159）
- **I-4**：不存在部分目录发布路径（发布是单次目录 rename，:785；无逐文件 publish）
- **I-5**（本单新增）：敏感路径记录在分类前剔除并按计数入 summary——`unmatched_records[].file_path` 永不可能携带敏感路径（详见 §4）
- **I-6**：预算/写入失败路径上 understanding.json 与全部输出一起不可见（stage 整体丢弃，:792-794；`published()==[]`，既有断言模式 :86/:431-444）

一致性来自**发布模型本身**（stage+单次 rename+终态标记），**不依赖 try/except 逐文件兜底**——满足工单"不得只用 try/except 代替一致性设计"。

### 2.4 失败场景逐条分析（DESIGN_DECISION）

| 场景 | 实际状态序列 | understanding.json | 终态 |
|---|---|---|---|
| 构建失败（ValueError/自检失败） | stage 内其余输出正常写；summary FAILED；不写该文件 | 缺席 | PUBLISHED（其余输出）+ summary FAILED；扫描 exit 0 |
| 序列化失败 | 构建步骤内预序列化即失败→同上（无"summary 说 COMPLETE 但文件缺失"窗口） | 缺席 | 同上 |
| 预算超限（in-flight，:1153） | 该文件 write_bytes 前 raise→stage 部分写→except rmtree+usit-status | 不可见 | BLOCKED，published()==[] |
| 预算超限（staged，:774） | 全 stage 写完→总量检查 raise→rmtree+usit-status | 不可见 | BLOCKED，published()==[] |
| 文件写入失败（OSError） | write_bytes raise→stage 清理→usit-status INCOMPLETE（既有测试 :437-448） | 不可见 | INCOMPLETE，published()==[] |
| 进程硬中断（kill） | stage 可能残留 `.usit-stage-*`（**既有缺口**，仅异常路径有清理测试 :432）；final_out 不变 | 不可见 | 无终态标记（硬中断无 except）；重试见下 |
| 重试（force） | final_out→`.preserved-<ms>` 备份→新 stage rename（:780-788） | 新值或按 FAILED 缺席 | PUBLISHED；旧备份留存待人工 |
| 重试（无 force） | OUTPUT_CONFLICT ScanBlocked（:781-782） | 不动旧值 | BLOCKED（fail-closed） |

非原子性说明（DESIGN_DECISION 记录，均为**既有全输出集属性、非 understanding 特有**）：① 单文件 `write_bytes`（:1155）非原子，但截断只可能发生在 stage 内，rename 不可达故永不发布；② 硬中断遗留 stage 目录惰性存在、后续运行不清理（建议记录，不扩大范围）；③ :783-785 备份窗口内 final_out 瞬时缺席（既有）；④ Windows 目录 rename 的原子性属平台层既有风险。understanding.json 因同 stage 同 rename，不比现有 8 个输出更脆弱。

## 3. P0-2 输出预算

### 3.1 机制实测（SOURCE_CONFIRMED）

- 常量：`MAX_OUTPUT_BYTES = 268_435_456`（256 MiB，`safety.py:18`）；`MAX_FILES=5000`、`MAX_FILE_BYTES=1MiB`、`MAX_TOTAL_READ_BYTES=256MiB`（:12-14）。
- 单位：**UTF-8 序列化字节数**——`bounded_write` 先 `text.encode('utf-8')` 再累计（:1151-1152）。
- 检查点 A（in-flight，:1149-1155）：每文件写前累计，`> MAX` 即 raise，**write_bytes 不可达**（超限文件从不落盘，但同 stage 先写文件已写——无害，stage 终将丢弃）。
- 检查点 B（权威，:774-776）：`actual=sum(f.stat().st_size for f in stage.rglob('*'))`，`> MAX` → ScanBlocked('MAX_OUTPUT_BYTES_EXCEEDED')，**staged results were not published**。
- 参与计算的全部输出：stage 内一切文件——9 个既有输出 + `cache/previous_manifest.json` + **新 `understanding.json`**；`usit-status-*.json` 写于 parent（不在 stage、不计入——既有属性）。
- 检查与写入的差异：A 为写前累计拦截（合成期），B 为写后盘尺寸总计（发布前）；二者同常量、同语义；B 权威。

### 3.2 understanding.json 预算方案（DESIGN_DECISION）

- **不降低限额、不设豁免**：与所有输出同一常量、同一 `bounded_write`、同一 staged 检查。
- **可执行预检**：构建步骤即完成序列化（见 §5.1），返回文本与其 UTF-8 长度；summary 增记 `understanding_status.bytes`（审计与 E-02 体量测量用，零 schema 影响——该键为本工单自有诊断键，非冻结字段）。
- **超限处理**：A 点拦截（该文件不写）或 B 点拦截（全 stage 弃）→ usit-status BLOCKED → CLI exit 2；与既有输出语义完全一致。
- 既有测试已覆盖 patch=1 的阻断+清 stage+BLOCKED 标记（test_p072_gates:424-435）；新测试补精确边界（下）。

### 3.3 预算测试设计（全 NOT_RUN）

- **B-01 精确满足**：先跑一次合成项目得 staged 总字节 T（确定性输出），`patch.object(scanner,'MAX_OUTPUT_BYTES',T)` 重跑 → 断言 COMPLETE、`published_files` 含 understanding.json（证明 `>` 非 `>=`，边界正确）。
- **B-02 超 1 字节**：patch 为 T-1 → ScanBlocked MAX_OUTPUT_BYTES_EXCEEDED、`published()==[]`、`.usit-stage-*` 已清、usit-status BLOCKED（镜像 :424-435）。
- **B-03 大型 evidence_refs**：合成项目含大量符号（如 2000 个函数→数千条记录→evidence_refs 长大）；正常预算 → COMPLETE 且文件在（证明参与预算且无豁免）；同项目 patch 小预算 → 阻断前不发布（证明大体量下走同一安全路径）。
- **B-04 序列化失败**：patch 构建函数返回含不可序列化对象 → 构建步骤捕获 → `understanding_status=FAILED`、扫描 COMPLETE exit 0、understanding.json 缺席（证明第 3 类失败不中止、不伪装）。

## 4. P1-3 脱敏链

### 4.1 全路径追踪（SOURCE_CONFIRMED）

| 阶段 | 位置 | 携带内容 | 进入 understanding.json？ |
|---|---|---|---|
| 记录构造 | :1059-1076（adapt_graph_v1 + js_ts + 诊断追加） | evidence 含 **excerpt（源码行）**、path、line；诊断含 code | 见下行 |
| scrub | :1137（graph/manifest/plan，路径模式 `is_sensitive` :87）；**parser_contract 不 scrub** | 路径级剔除 | parser_contract.json 既有即携 excerpt（既有属性，非本单引入） |
| 投影取值 | `_build_evidence` :122-149 | 仅 locator/summary/path/line/symbol；**`raw_value=None`(:147)**；不拷 excerpt | ✅ 无 excerpt |
| 结论内容 | `project_record` :206-226 | `content`=node_type+symbol+path+line+status 组合；**`raw_source=None`(:224)** | ✅ 无源码文本 |
| 降级项 | `_degraded_projected_item` :391-413 | reasons/limitations 仅 `type(exc).__name__`（**无异常消息** :404-409）；`repr(record)` 仅进 ID 哈希（:398，不序列化） | ✅ 无消息/无 repr |
| 序列化面 | `ProjectedItem.to_dict` :319-330 | 仅 projection_id/object_type/resolution_status/conclusion_type/is_success/reasons/limitations/conclusion_id/evidence_count | ✅ 无证据内容 |
| 文件条目 | FileConclusion.to_dict | file_path、枚举、branch、reasons（静态串）、计数、evidence_refs/conflicting_refs（**投影 ID 哈希**） | ✅ 仅路径+标识符+枚举+计数+哈希 |
| 诊断条目 | `_project_diagnostic_entry` :335-344 | file_path=''、qualified_name、diagnostic_code（均为 **code 串**，:1063/:1070）、source='unknown'、projection(to_dict) | ✅ 无诊断消息文本（PYTHON_SYNTAX_ERROR 消息仅异常类型名 :478） |
| 未匹配条目 | `_unmatched_entry` :347-356 | file_path、reason（枚举）、source、projection(to_dict)、identity_basis{node_type,has_diagnostic_field,file_path}（:324-332，**无证据内容**） | ✅ 仅路径+枚举+哈希 |
| 序列化/写入 | :1159 json.dumps(ensure_ascii=False) → stage → 单次 rename | — | 同现有输出链 |

**结论（SOURCE_CONFIRMED）**：understanding.json 不含源码 excerpt、不含凭据值、不含异常消息、不含记录 repr；内容面仅为**路径、标识符（符号名/诊断 code）、枚举/状态串、计数、ID 哈希**——内容暴露面**严格弱于** parser_contract.json/knowledge_graph.json（二者携 excerpt）。

### 4.2 发现的缺口与修复（DESIGN_DECISION）

- **缺口 G-1**：manifest 在 :1137 被 scrub，而 records 不 scrub——若敏感路径记录存在（发现期排除 + scrub 双保险的正常路径下不出现，但防御纵深要求），会以 `no_matching_manifest_file` 落入 `unmatched_records[].file_path`，使敏感路径经由新输出露面。
- **修复**：接线装配步骤在分类前以 `is_sensitive`（scanner.py:87，零新依赖）对 records 按分组键路径预过滤；被剔除者**不进入任何输出数组**（含 unmatched_records），仅在 summary 记 `understanding_status.sensitive_records_excluded=N`——显式计数、可审计、非静默丢弃（满足 DESIGN-13/14"不得静默丢弃"与"不得泄漏"的交叉约束：丢弃动作留计数，内容零露面）。
- **缺口 G-2（NOT_VERIFIED，不声称安全已验证）**：`is_sensitive` 的模式覆盖面本身（哪些路径算敏感）为既有实现，本工单只读未审计其完备性；普通文件内的凭据**值**不被任何输出 scrub（既有全输出集属性），understanding.json 不携值故不放大该风险，但"安全已验证"不予宣称。

### 4.3 脱敏测试设计（全 NOT_RUN）

- **S-01 普通源码含疑似凭据值**：合成 `a.py` 含 `API_KEY = "sk-live-SECRETVALUE123"` → 扫描后 understanding.json 全文 **不得含** `sk-live-SECRETVALUE123`（断言序列化文本 grep）；符号名 `API_KEY` 可出现（标识符，非值）。
- **S-02 敏感路径记录剔除**：向装配函数直接注入一条分组路径为敏感名（如 `.env`）的记录 + 正常 manifest → `sensitive_records_excluded==1`，输出无该路径任何踪影（files/unmatched/project_diagnostics 三处均无）。
- **S-03 诊断文本**：含语法错误的 `bad.py`（如 `def f(:`）→ understanding.json 不得含该文件任何源码行；诊断侧仅可出现 code 串 `PYTHON_SYNTAX_ERROR`（消息仅类型名 :478）。
- **S-04 异常信息**：patch `project_record` 抛 `ValueError("SECRET_DEBUG_INFO")` → 输出文本不得含 `SECRET_DEBUG_INFO`；仅可出现 `construct_fail:ValueError`；且 repr 仅进 ID 哈希不可逆（:398）。

## 5. 修订后的接线方案（对 DESIGN-01 的增量）

### 5.1 调用点与装配函数（DESIGN_DECISION）

保留 DESIGN-01 两插入点，装配函数签名修订为**返回已序列化文本**（消除序列化失败的不一致窗口）：

```python
# 插入于 :1137（scrub）之后、:1148（report 渲染）之前——早于 :1160 写 report.json
understanding_text, understanding_error = _build_file_understanding_text(manifest, parser_contract)
#   内部：records 经 is_sensitive 预过滤(计数) → build_file_understanding →
#         verify_understanding_identities → json.dumps；任一步失败即 (None, error)，永不抛
status = {'status': 'COMPLETE', 'bytes': len(understanding_text.encode('utf-8'))} \
         if understanding_text is not None else \
         {'status': 'FAILED', 'error_type': ..., 'error_message': ..., 'sensitive_records_excluded': N}
summary['understanding_status'] = status
# 插入于 :1161（parser_contract.json）之后、:1170（scan_status.json）之前
if understanding_text is not None:
    bounded_write(out / 'understanding.json', understanding_text)
```

### 5.2 错误分类（四类，不变 + 敏感剔除审计位）

| 类 | 行为 | 中止 |
|---|---|---|
| 1 单条投影失败 | project_records 内计数+降级项（既有实现） | 否 |
| 2 构建/自检失败 | (None, error) → summary FAILED → 文件缺席 → 扫描继续 exit 0 | 否 |
| 3 序列化失败 | 并入类 2（构建步内完成序列化） | 否 |
| 4 写入失败 | OSError→INCOMPLETE / 预算→ScanBlocked BLOCKED（既有语义，stage 全弃） | 是 |
| 审计位 | 敏感路径记录剔除计数 `sensitive_records_excluded`（仅计数，零内容露面） | 否 |

### 5.3 兼容性与回滚（不变，重申）

纯加法：不改 `usit-file-understanding-v1` 冻结字段、不改 5 个既有输出 schema、不改 `PRE_SHA256.json`；summary 仅增 `understanding_status` 一键（无测试断言精确键集）。回滚=恢复 scanner.py 前映像（PRE sha256 `a009b8dbb132…`）+ 删新测试文件（非 git 仓库）。

### 5.4 测试矩阵增量（在 DESIGN-01 的 U/I/C/R/E 上追加，全 NOT_RUN）

- I-12（一致性）成功扫描：`'understanding.json' in published_files` ⟺ 文件存在 ⟺ `understanding_status=='COMPLETE'`
- I-13（一致性）构建失败（patch 构建函数抛）：文件缺席 + summary FAILED + 其余输出 PUBLISHED + exit 0
- I-14（一致性）预算阻断（patch 小）：`published()==[]` + stage 清 + BLOCKED + 无 understanding.json
- I-15（写入失败，patch `Path.write_bytes` OSError）：INCOMPLETE + stage 清 + 无 understanding.json（镜像 :437-448）
- B-01..B-04（§3.3）、S-01..S-04（§4.3）
- R：全套件失败集 ⊆ {既有 2 项 scanner.py 冻结哈希}；E-01/E-02 不变（E-02 现可直接读 `understanding_status.bytes`）

## 6. 未决事项

- U-1（NOT_VERIFIED）`is_sensitive` 模式完备性未审计（既有实现，只读范围外）。
- U-2（NOT_VERIFIED）硬中断遗留 `.usit-stage-*` 的自动清理（既有缺口，非 understanding 特有；建议记录不扩范围）。
- U-3（NOT_VERIFIED）Windows 目录 rename 原子性（平台层既有风险）。
- U-4（DESIGN_DECISION 待实施验证）B-01 的 staged 总字节确定性（sort_keys+固定输入下预期确定，实施时以两次运行互证）。
- U-5（NOT_VERIFIED）understanding.json 真实项目体量（E-02；现可用 `understanding_status.bytes` 直接测量）。

## 7. 逐项结论与最终结论

| 项 | 结论 |
|---|---|
| P0-1 发布一致性 | **PASS**（staged-then-rename 架构实测；状态模型 BUILD_COMPLETE/PUBLISHED/BLOCKED/INCOMPLETE 语义明确；I-1..I-6 不变量可断言；八类失败场景逐条分析；一致性源于发布模型而非 try/except） |
| P0-2 输出预算 | **PASS**（单位/参与面/双检查点/检查-写入差异实测；understanding.json 同常量同通道无豁免；预检=构建步序列化+bytes 审计；B-01..B-04 覆盖精确满足/超1字节/大型 evidence_refs/序列化失败） |
| P1-3 脱敏链 | **PASS-WITH-ISSUES**（全路径追踪证实 understanding.json 不含 excerpt/凭据值/异常消息/repr，内容面弱于既有输出；缺口 G-1 已设计修复（敏感路径预过滤+显式计数）；G-2 is_sensitive 完备性 NOT_VERIFIED，不声称安全已验证） |
| 4. 修订接线方案 | **PASS**（装配函数返回序列化文本；错误四类+敏感剔除审计位；兼容性/回滚不变；测试矩阵增补 I-12..I-15/B-*/S-*） |
| 5. 哈希与授权边界 | **PASS**（scanner.py 历史漂移根因维持未证实裁定；不更新基线；实施须独立授权） |

**最终结论：PASS-WITH-ISSUES**——三个缺口均有可执行、可测试的设计修订；ISSUES=① `is_sensitive` 模式完备性未审计（G-2，不声称安全已验证）② 既有 stage 遗留/Windows rename 原子性为全输出集既有属性（记录不扩范围）③ 体量待 E-02 实测。设计可直接用于 IMPLEMENT-02B 实施授权工单的输入。

**固定门禁复核（原值保持）**：F1=NOT_READY_TO_CLOSE / P0.8=HOLD / F2=STOP / 历史授权连续性=UNRESOLVED / 安全信任链=BLOCKED/NOT_VERIFIED / verified_fact=0 / ExecutionRecord=NOT_RUN / summary_verification=NOT_CHECKED / complete=False / BLOCKER-1 不解除 / BLOCKER-2 待接线后复核。未宣称可信项目理解/完整测试/自动修复/安全已验证。

**STOP**：本工单仅授权只读设计修订并新增本报告；不授权实施/接线/测试执行/基线更新/真实项目操作；`PRE_SHA256.json` 未更新（复验 `3aaabb4e…` 不变）；等待独立审查后另行签发 IMPLEMENT-02B 实施授权工单。

## 8. 文件修改核验

零修改：`usit/scanner.py`（a009b8dbb132…）、`usit/cli.py`（879a3735838e…）、`usit/file_understanding.py`（71995a09…）、`usit/understanding_projection.py`（8bb0fb65…）、`usit/parser_contract.py`（482400f9…）、`usit/understanding_model.py`（4668760b…）、`usit/schema_adapter.py`（c44f60e4…）、`usit/safety.py`、`outputs/p0710zxrxf/PRE_SHA256.json`（3aaabb4e…）、tests/ 与既有报告全部与工单开始前相符；仅新增本报告。
