# USIT-S1-C-POST-IMPLEMENT-02A-AUDIT-01｜文件级结论层独立只读审计 执行报告

- **工单编号**：USIT-S1-C-POST-IMPLEMENT-02A-AUDIT-01
- **执行时间**：2026-10-20（GMT+8）
- **工作区绝对路径**：`C:\Users\Administrator\WorkBuddy\2026-10-15-22-09-47\usit_p0`
- **执行模式**：**严格 READ-ONLY 独立审计**。未修改任何文件；未接入 scanner/CLI；未真实扫描/构建/安装/登录/联网/外部 AI；未修改冻结哈希基线。
- **审计对象**：`usit/file_understanding.py`（sha `71995a09…`，21,754B）+ `tests/test_file_understanding.py`（sha `100411412…`，23,292B），即 IMPLEMENT-02A 交付物。
- **基准**：DESIGN-10/11/12/13/14 冻结规格、IMPLEMENT-02A 报告。
- **方法**：重读磁盘源码 + 独立探针实测 + 实跑测试（不以 IMPLEMENT-02A 自述替代）。

---

## 1. 逐项审计结论总表

| # | 审计项 | 结论 |
|---|---|---|
| 1 | 8 值枚举与首次命中分支规则 | **PASS** |
| 2 | 原始记录三向互斥分类 + 项目诊断身份 + unmatched 保留 + 非Mapping/异常 | **PASS** |
| 3 | 计数来源（投影前分组）+ evidence_refs 对失败记录可追溯 | **PASS** |
| 4 | K1–K8 恒等式计算与断言 | **PASS** |
| 5 | 输出契约（字段/三态/枚举/未知字段）+ 统计隔离 + 稳定性 | **PASS** |
| 6 | 异常输入/manifest 重复/路径类型/重复记录/冲突ID/投影异常不致失真 | **PASS** |
| 7 | 冻结哈希核对与失败归因 | **PASS**（既有越界，未引入，根因部分证实） |
| 8 | 实跑测试 | **PASS**（34/34 新测 + 93 相关 + 完整套件零新增失败） |

**总体裁定：文件级结论层符合 DESIGN-10..14 冻结规格，未发现阻断性缺陷。**（问题与严重级别见 §10，均为 INFO/LOW，非缺陷。）

---

## 2. 逐项审计详情（含文件:行号证据）

### 2.1 项 1：8 值枚举与首次命中分支（PASS）
- **枚举**：`FileConclusionType`（`file_understanding.py:71-88`）恰 8 值；**无 `VERIFIED_FACT` 成员**（`:74` 注释明确，实测 `"VERIFIED_FACT" in FileConclusionType.__members__ → False`）；`MEANINGFUL_EVIDENCE` 标注 FUTURE（`:84`）。
- **首次命中有序分支**：`decide_file_conclusion`（`:199-278`）顺序 0→1→2→3→4→6→7→8，`return` 首次命中即互斥。
- **边界实测（探针，非设计推演）**：
  - hypothesis-only（RESOLVED+AI_HYPOTHESIS+候选）→ `UNKNOWN`/分支 8，`resolved_ev_count=0`（AI_HYPOTHESIS 被 `:220-223` 排除：仅 `RESOLVED ∧ STATIC_INFERENCE` 计入）。
  - 不可读非空（有 file 节点记录）→ `content_status=unreadable`（`:157-173`，`size>0 & readable=False`，**非 empty**）→ 分支 0 不命中 → 分支 2 `UNKNOWN_RESOLUTION`；不可读零记录 → 分支 1 `NO_RECORDS`。**均不判 `NO_SEMANTIC_CONTENT`**。
  - 零字节（`size_bytes==0`）→ 分支 0 `NO_SEMANTIC_CONTENT`（`:252-253`，唯一判空条件）。
  - 冲突（同身份异内容）→ 分支 3 `CONFLICT_ONLY`；降级（`file_degraded is True` 且过 2/3）→ 分支 4 `DEGRADED_PARSE`；正常 → 分支 7 `INSUFFICIENT_EVIDENCE`。
- **VERIFIED_FACT 不可达**：无枚举成员 + 无代码路径 + `verify_understanding_identities:501-502` 断言无 `VERIFIED_FACT`。`MEANINGFUL_EVIDENCE` 不可达：`meaningful_count=0` 硬编码（`:224`）⇒ `if meaningful_count>=1`（`:269`）恒假。

### 2.2 项 2：三向分类 + 诊断身份 + unmatched 保留（PASS）
- **分类**：`_categorize_record`（`:300-317`）穷尽互斥——非 Mapping→`unmatched(invalid_or_missing_file_path)`；非空 str 键→`matched_file`(∈manifest) 或 `unmatched(no_matching_manifest_file)`；空键→`project_diagnostic`(确认) 或 `unmatched(empty_path_not_confirmed_project_diagnostic)`；其它→`unmatched(invalid_or_missing_file_path)`。每条记录恰入一类（构建循环 `:411-418`）。
- **诊断身份**：`_is_confirmed_project_diagnostic`（`:292-297`）= `node_type=='diagnostic'` **且** evidence 为 Mapping **且** 含 `diagnostic` 键——**非空路径 alone**；与 DESIGN-14 一致。实测：external 风格空路径记录（无 diagnostic 字段）→ unmatched，未伪装为项目诊断。
- **unmatched 逐条保留**：`_unmatched_entry`（`:347-356`）保留 `file_path/reason/source/projection/identity_basis`；`projection=project_record(record).to_dict()`——非 Mapping 记录经 `project_record` 得 not_attempted 项（不抛错），故投影恒保留。
- **非Mapping/异常输入**：非 Mapping → unmatched（如上）；无效路径类型（`source_location.file_path` 非 str/None）→ `invalid_or_missing_file_path`（实测）。

### 2.3 项 3：计数来源 + evidence_refs 失败可追溯（PASS）
- **n_records/N_raw 来源投影前分组**：`matched_groups` 在分类阶段构建（`:408-418`），`project_records(group)` 在其后调用（`:423`）；`n_records=stats["N_raw"]`（`:208`）= `len(group)`（投影前原始记录数），**非**成功 item 数反推。
- **投影失败不少计 + 可追溯（探针实测）**：
  - 部分失败：2 记录、1 候选抛错 → `N_raw=2, N_construct_fail=1, N_construct_ok=1`，`evidence_refs=2`（**失败记录仍在 evidence_refs**）。
  - 全部失败：2 候选均抛错 → `N_raw=2`（不少计），`evidence_refs=2`（均经降级项投影_id 追溯）。
- **evidence_refs**：`tuple(i.projection_id for i in record_items)`（`:226`），`record_items` = 非 CONFLICT 项（`:213`）；降级项 conclusion_type=UNKNOWN（非 CONFLICT）⇒ **失败记录进入 record_items ⇒ 其 projection_id 在 evidence_refs**。失败记录的投影_id 由 `{category, exc-type, record-repr}` 内容派生（`:398`），确定性且可溯（见 §10-O2）。

### 2.4 项 4：K1–K8 恒等式（PASS）
- **模块自检**：`verify_understanding_identities`（`:472-509`）每次构建均执行，校验 K1（穷尽划分）/K2/K3/K4/K5/K6/K7 + 无 VERIFIED_FACT；K8 为投影层恒等式由投影测试覆盖（函数 docstring `:476-477` 明确）。
- **实测（探针混合输入：a.py 冲突 + b.py 零记录 + c.py 正常 + 1 项目诊断 + 1 未匹配，total_in=5）**：
  - K1：`5 == n_records_total(3)+project_diagnostic_total(1)+unmatched_total(1)` ✓（覆盖**所有**原始输入：每条记录恰入一类）。
  - K2：`n_records_total==Σ files.n_records==3` ✓；K3 `unmatched_total==1`；K4 `project_diagnostic_total==1`。
  - K5：`totals.files==len(files)==3` ✓；K6：`Σ by_conclusion==3` ✓。
  - K7：`conflict_total==Σ files.conflict_count==1` ✓（每文件 `conflict_count`=该文件投影 `conflict` stat=该文件冲突组数；**每文件每冲突组 1 次**）。
  - K8：`len(projection.items)==n_records+conflict_count`（测试 `test_k8_projected_items_equals_nr_plus_cf` + `_detect_conflicts:276-277` 每桶≤1 项）。
- 测试断言：`SummaryIdentityTests.test_k1_k7_on_result` + `test_k8_projected_items_equals_nr_plus_cf`。

### 2.5 项 5：输出契约 + 统计隔离 + 稳定性（PASS）
- **顶层字段**（`:450-458`）：`schema_version / projection_schema_version / generated_from / files / project_diagnostics / unmatched_records / totals`——与 DESIGN-12 §6.1 冻结一致（测试 `test_schema_version_and_top_level_fields`）。
- **文件条目字段**：`FileConclusion.to_dict`（`:129-155`）恰为冻结字段集（测试 `test_file_entry_has_frozen_field_set`）；`conflicting_refs` 明确注释引用 projection_id 非 conclusion_id（`:150-153`）。
- **三态序列化**：`_tri` → `"true"/"false"/"unknown"`（测试 `test_tri_state_serialized_as_strings`）。
- **未知字段策略**：容忍输入多余键（测试 `test_unknown_input_fields_are_tolerated`）；输出仅含冻结字段集（无未定义字段）。
- **统计隔离**：`totals.files=len(files)`、`n_records_total=Σ files.n_records`（仅匹配文件原始记录）；`unmatched_total/project_diagnostic_total` 独立（`:429-436`）——**project_diagnostics/unmatched 不混入文件统计**。
- **稳定性（探针）**：重复构建 JSON 完全一致（确定性）；重复记录 → `N_raw=2, cf=0`（不冲突、不丢）；conflicting_refs 经 sorted-set 去重定序。

### 2.6 项 6：异常输入不致计数失真/不稳定（PASS）
- **manifest 重复路径** → `ValueError`（`:401-402`），阻断（manifest 完整性）。
- **manifest/records 非序列、条目无 path、path 非 str** → `TypeError`（`:387-400`）。
- **路径类型异常**（file_path 非 str/None）→ unmatched（`invalid_or_missing_file_path`），不崩溃。
- **重复记录** → 均计入 `N_raw`（`cf=0`，同指纹不冲突）；evidence_refs 出现重复内容 id（见 §10-O3，非失真）。
- **冲突投影 ID**：`conflicting_refs` 为各方 projection_id（实测 `proj:` 前缀，非 `conc:`），互异（冲突各方内容不同），无歧义。
- **投影构造异常** → `N_construct_fail`/`N_unexpected_error` 归因单条、`N_raw` 不减（项 3 实测）。
- **系统性失配/strict** → `ValueError` 阻断（`:439-448`）。
- 未发现计数失真或结果不稳定。

### 2.7 项 7：冻结哈希核对与失败归因（PASS）
- **当前哈希（只读）**：`scanner.py=a009b8db…`、`cli.py=879a3735…`、`PRE_SHA256.json=3aaabb4e…`——与 IMPLEMENT-02A 结束时**逐一相符**（本审计与 IMPLEMENT-02A 均未改动）。
- **基线 vs 当前**：`PRE_SHA256.json`（Oct-17）记录 `scanner.py=c70a1a4b…`；当前 `a009b8db…` ⇒ **漂移**；其余 6 个 `ZXRXF7_WATCH` 文件（graph/safeio/4 测试）**全部 MATCH** ⇒ 孤立单文件漂移。
- **2 项失败**：`test_p0710zxrxf_guard_r2…maintenance_tolerated`、`test_p0710zxrxf_trust_boundary…zrxf7_hashes_recorded`（均因 scanner.py 哈希 ≠ 基线）。
- **归因（三分，不猜历史原因）**：
  - **本审计/IMPLEMENT-02A 未引入**：已证实——scanner.py 字节级未变、不在两工单改动集、失败集与 IMPLEMENT-02A 完全一致（547/2/3）。
  - **漂移事实 + 孤立性**：已证实（哈希比对 + 6/7 相符）。
  - **具体历史根因会话**：**未证实**——无跨会话 scanner.py 前后哈希链，不归因、不猜测。

### 2.8 项 8：实跑测试（PASS）
- **新增 34 项**：`python -m unittest tests.test_file_understanding -v` → `Ran 34 tests … OK`（34/34 PASS，0 FAIL，0 NOT_RUN）。
- **相关回归（本次实跑）**：`python -m unittest tests.test_file_understanding tests.test_understanding_projection tests.test_s1c_cases tests.test_parser_contract.GraphV1EvidenceFallbackTests` → `Ran 93 tests … OK`。
- **完整套件（本次实跑）**：`python -m unittest discover -s tests -p "test_*.py" -v` → `Ran 547 tests in 422.336s`，`FAILED (failures=2, skipped=3)`，退出码 1；34 项 file_understanding 全 ok；2 失败=§2.7 既有越界冻结哈希；3 跳过=环境条件性（zb5/zb9/ts_lib）。**零新增失败**（与 IMPLEMENT-02A 完全一致）。

---

## 3. 发现的问题与严重级别（均为 INFO/LOW，非阻断缺陷）

| 编号 | 严重 | 描述 | 证据/说明 |
|---|---|---|---|
| O-1 | INFO | 分支 0 仅以 `size_bytes==0` 判空且优先级最高；矛盾输入（size=0 却有记录）会得 `NO_SEMANTIC_CONTENT` 且 `n_records>0` | `:252-253`；符合 DESIGN-12 冻结（分支 0 最高优先）；真实路径空文件无记录，不发生 |
| O-2 | INFO | 失败记录在 evidence_refs 中经**降级项** projection_id 追溯（与"若成功则"的 id 不同，但由 record-repr 内容派生、确定可溯） | `:226,:398`；可追溯性不丢失 |
| O-3 | LOW | 重复记录使 evidence_refs 出现重复内容 id（内容 id 每实例一次） | 内容 id 语义正确（`N_raw` 记实例数）；非失真 |
| O-4 | LOW | `evidence_refs` 取"该文件全部逐记录 item 的 projection_id"（大文件列表较长） | DESIGN_DECISION（冻结字段内填充口径，IMPLEMENT-02A D-3） |
| O-5 | INFO | `project_diagnostics[]/unmatched_records[].source="unknown"`（记录无可核验来源标记） | 符合 DESIGN-14（NOT_VERIFIED，不伪造） |
| O-6 | INFO | 系统性失配判据 `非空路径记录 ∧ ΣN_raw==0`（DESIGN-13 冻结）；真实路径因 scanner `:982` 恒建 file 节点不触发，纯防御 | `:441-448` |
| O-7 | INFO | 系统性判据对每条记录两次调用 `_record_group_key`（微效，非正确性） | `:443` |

> 未发现 FAIL 级问题；无计数失真、无错分类、无 VERIFIED_FACT 泄漏、无不稳定。

---

## 4. 是否符合冻结规格

- **符合 DESIGN-10/11/12/13/14**：8 值枚举与有序分支、`size_bytes==0` 判空、不可读不判空、三态上下文、零记录 `NO_RECORDS`、hypothesis-only→分支 8、项目诊断身份（node_type+diagnostic 字段）、未匹配独立保留、冲突引用 `conflicting_refs`=projection_id、路径 `source_location.file_path` 精确匹配、`n_records/N_raw` 投影前分组、投影失败不少计、K1–K8、`usit-file-understanding-v1` 输出契约、未知字段策略——均落地并经实测/测试验证。
- **IMPLEMENT-02A 自报偏离复核**：D-1（枚举置于新模块）、D-3（evidence_refs 口径）、D-4（source=unknown）与磁盘代码一致，均为已披露的非破坏性设计决策/澄清，不构成规格违背。

---

## 5. 是否建议进入集成阶段

**建议：可作为后续独立授权集成工单（scanner/CLI 接线，IMPLEMENT-02B）的基础**，本模块符合冻结规格、经 34 项单测 + 93 项相关回归 + 完整套件零新增失败验证。**但集成前须在集成设计/授权中处理**（非本模块缺陷）：
1. **冻结哈希基线治理**：`scanner.py` 已漂移（§2.7），接线改 scanner.py 会触发守卫；须在授权下权威再生 `PRE_SHA256.json` 并附审计轨迹。
2. **external/candidate 空路径非诊断实体**：现归 `unmatched_records[]`（承 DESIGN-14 V-1/V-2）；集成时评估是否单列项目级非诊断桶（schema 变更须单独授权）。
3. **evidence_refs 口径**（O-4）：确认大文件下引用范围是否需收敛。
4. **end-to-end**：真实扫描产出并抽查 `understanding.json`（当前未接线、未生成）。

---

## 6. 文件修改核验（READ-ONLY 保持）

- 本审计**未修改任何文件**；`usit/file_understanding.py`（`71995a09…`）、`tests/test_file_understanding.py`（`100411412…`）、`scanner.py`（`a009b8db…`）、`cli.py`（`879a3735…`）、`PRE_SHA256.json`（`3aaabb4e…`）与 IMPLEMENT-02A 结束时逐一相符。
- 仅新增 `reports/USIT-S1-C-POST-IMPLEMENT-02A-AUDIT-01-执行报告.md`。

---

## 7. 固定门禁复核（原值保持，未解除）

`F1=NOT_READY_TO_CLOSE` / `P0.8=HOLD` / `F2=STOP` / 历史授权连续性=`UNRESOLVED` / 安全信任链=`BLOCKED/NOT_VERIFIED` / `verified_fact=0` / `ExecutionRecord=NOT_RUN` / `summary_verification=`NOT_CHECKED` / 受约束条目 `complete=False` / **`BLOCKER-1` 不解除**（仍未接线）/ `BLOCKER-2` 待接线后复核。未因审查通过解除任何门禁。

---

## 8. 结论

**审计结论：PASS（符合冻结规格，无阻断性缺陷）**——8 个审计项全部 PASS；问题均为 INFO/LOW（§3）。实测：新测 34/34、相关回归 93/93、完整套件 547（2 既有越界失败 + 3 环境跳过，零新增失败）。

**集成建议**：本模块可作为独立授权的 scanner/CLI 集成工单基础，但集成前须处理 §5 的 4 项前置（尤其 scanner.py 冻结哈希基线须在授权下再生）。

> **本工单只产出审查报告，不实施修复，不接线，不解除任何门禁。即使审查通过，也必须等待独立授权后才能开始 scanner/CLI 集成。**

---

**审计边界最终声明**：本工单仅授权对 IMPLEMENT-02A 交付物的只读独立审计并新增本报告。未修改任何源码/测试/配置/manifest/冻结哈希基线；未接入 scanner/CLI；未真实扫描/构建/安装/登录/联网/外部 AI。
