# USIT-S1-C-IMPLEMENT-02A｜文件级结论层实现与单元测试 执行报告

- **工单编号**：USIT-S1-C-IMPLEMENT-02A
- **执行时间**：2026-10-20（GMT+8）
- **工作区绝对路径**：`C:\Users\Administrator\WorkBuddy\2026-10-15-22-09-47\usit_p0`
- **执行模式**：限定范围代码实施 + 单元测试（依据 DESIGN-10/11/12/13/14 冻结规格）。
- **前置规格**：DESIGN-10/11/12/13/14、IMPLEMENT-01、POST-IMPLEMENT-AUDIT-01。
- **范围声明**：仅实现文件级结论层与输出构建**纯逻辑模块**及其测试；**未接入** scanner/CLI；**未**触发真实扫描；**未**生成真实项目的 `understanding.json`。

---

## 1. 实际修改文件与前后 SHA-256

**纯加法实施：仅新建 2 个文件，7 个既有文件（含 scanner/cli/schema/PRE_SHA256）字节级零改动。**

### 1.1 新建文件（POST）
| 文件 | 大小 | SHA-256 |
|---|---|---|
| `usit/file_understanding.py` | 21,754 B | `71995a09da04427c028282cfeba17c6b12ebdbcae97796adc2998af7c455ce5b` |
| `tests/test_file_understanding.py` | 23,292 B | `100411412e216b13cac40616ab0ec29e1dcec062252fde3d3b9441d9772f0b00` |

### 1.2 既有文件（PRE == POST，均未修改）
| 文件 | SHA-256（前后一致） |
|---|---|
| `usit/understanding_projection.py` | `8bb0fb65…33ecb226` |
| `usit/parser_contract.py` | `482400f9…314dd87` |
| `usit/understanding_model.py` | `4668760b…1f690e7` |
| `usit/scanner.py` | `a009b8db…2be416` |
| `usit/schema_adapter.py` | `c44f60e4…6a4def44` |
| `usit/cli.py` | `879a3735…38b8d9` |
| `outputs/p0710zxrxf/PRE_SHA256.json` | `3aaabb4e…7775b910` |

> 因零既有文件修改，无需逐文件"必要性"说明（符合授权边界：禁改 scanner/cli/既有 schema/冻结基线——均未触碰）。

---

## 2. 新模块接口、数据流与分支实现

### 2.1 模块 `usit/file_understanding.py`
- **类型**：`FileConclusionType`（8 值枚举，**无 VERIFIED_FACT**；`MEANINGFUL_EVIDENCE` 为 FUTURE 占位、不可达）；`FileContext`（三态：`size_bytes/readable_text/has_symbols/file_degraded`，`None`=未知）；`FileConclusion`（冻结字段契约 + `to_dict()`）。
- **常量**：`FILE_UNDERSTANDING_SCHEMA = "usit-file-understanding-v1"`。
- **核心函数**：
  - `decide_file_conclusion(file_path, file_context, projection) -> FileConclusion`：有序分支（0 empty > 1 no_records > 2 all_non_success|all_evidence_empty > 3 conflict_only > 4 degraded > 6 meaningful[FUTURE] > 7 insufficient > 8 unknown），首次命中互斥。
  - `build_file_understanding(manifest_files, records, *, strict=False) -> dict`：清单驱动构建。
  - `verify_understanding_identities(result, *, total_input_records)`：K1–K7 自检 + 无 VERIFIED_FACT。

### 2.2 数据流
```
manifest_files(scan_manifest['files']) + records(parser-contract 批)
  -> 校验 manifest/records 为序列；建 manifest 路径集 + 每文件 FileContext
  -> 逐条分类(投影前, 穷尽互斥): _categorize_record
        matched_file(键=source_location.file_path 精确匹配 manifest rel)
        project_diagnostic(空路径 ∧ node_type=='diagnostic' ∧ 'diagnostic' in evidence)
        unmatched(其余: no_matching_manifest_file / invalid_or_missing_file_path / empty_path_not_confirmed_project_diagnostic)
  -> 每 manifest 文件: project_records(该组) -> decide_file_conclusion
  -> project_diagnostics[] / unmatched_records[](保留逐条投影+reason+identity_basis)
  -> totals{files,by_conclusion,n_records_total,conflict_total,unmatched_total,project_diagnostic_total}
  -> 阻断校验(strict / 系统性失配) -> verify_understanding_identities(K1-K7+无VERIFIED_FACT)
  -> understanding dict(usit-file-understanding-v1)
```

### 2.3 关键实现点（对应工单 A/B/C/D）
- **覆盖**：文件列表=清单驱动；每清单文件恰 1 结论（含零记录）；记录不生成文件条目。
- **匹配**：`source_location.file_path` 与 manifest rel **精确匹配**（无大小写/分隔符/绝对-相对归一）；分组键与 `_detect_conflicts`（`understanding_projection._source_location`）一致。
- **身份判定**：项目级诊断须 `node_type=='diagnostic'` ∧ evidence 含 `diagnostic` 字段（**非空路径 alone**）；身份不明空路径 → unmatched（不伪装、不丢弃）。
- **分支红线**：仅 `size_bytes==0` 判空；非空不可读→`unreadable`（绝不 `NO_SEMANTIC_CONTENT`）；上下文三态不默认否定；零记录→`NO_RECORDS`；`RESOLVED+AI_HYPOTHESIS`→`UNKNOWN`/分支8（`resolved_ev_count` 排除 AI_HYPOTHESIS）；`MEANINGFUL_EVIDENCE`/`excerpt_verified` FUTURE 不可达；冲突引用输出 `conflicting_refs`（=projection_id，注明非 conclusion_id）。
- **计数**：`n_records/N_raw` 来自投影前分组；单条投影失败仍计入 `N_raw`+`N_construct_fail`；冲突合成项附加（`projected_items==Nr+cf`），不入 `n_records_total`。

---

## 3. 原始记录分类、失败保留与汇总恒等式验证证据

- **分类穷尽互斥**：每条记录恰入 matched/project_diagnostic/unmatched（`_categorize_record`），测试 `test_file_list_is_manifest_driven_not_record_driven`/`test_non_mapping_record_is_unmatched`/`test_empty_path_non_diagnostic_is_unmatched…` 验证。
- **投影失败不少计**：`test_projection_failure_still_counted_in_n_raw`（注入异常→`N_raw=1,N_construct_fail=1,n_records_total=1`）；`test_projection_failure_among_successes_counts_both`（`N_raw=2,N_construct_fail=1,N_construct_ok=1`）。
- **K1–K8**：`SummaryIdentityTests.test_k1_k7_on_result`（K1 穷尽划分 `5==3+1+1`；K2 `n_records_total==3==Σn_records`；K3/K4；K5 `files==3==len(files)`；K6 `Σby_conclusion==3`；K7 `conflict_total==1==Σcf`；无 VERIFIED_FACT）；`test_k8_projected_items_equals_nr_plus_cf`（`len(proj.items)==n_records+conflict_count`）。模块内 `verify_understanding_identities` 对 K1–K7 fail-fast 自检（每次构建均执行，本轮全部通过）。

---

## 4. 测试命令、退出码、PASS/FAIL/NOT_RUN 与失败详情

### 4.1 新增单元测试（`tests/test_file_understanding.py`）
```
$ python -m unittest tests.test_file_understanding -v
Ran 34 tests in 0.022s
OK
```
**34/34 PASS，0 FAIL，0 NOT_RUN**（全部实际运行）。覆盖工单 10 类：①全覆盖+零记录；②零字节/不可读有·零记录/上下文缺失（含"不可读不判空"与"分支由记录定"两测）；③hypothesis-only 分支8；④项目诊断与空路径普通记录隔离（含缺 diagnostic 字段）；⑤非空未匹配路径；⑥非Mapping/无效路径/投影异常（含失败仍计数）；⑦同身份异内容冲突 + 引用 ID 类型（`proj:` 非 `conc:`）；⑧原始记录计数来自分组；⑨K1–K8；⑩序列化字段/schema 版本/三态/未知字段容忍。每项均含明确输入、预期、负向断言。

### 4.2 相关回归
```
$ python -m unittest tests.test_understanding_projection tests.test_s1c_cases tests.test_parser_contract tests.test_file_understanding
Ran 100 tests in 46.493s
OK
```
**100/100 PASS**（投影计数/异常注入/C1–C21/parser_contract 含 scanner 集成/file_understanding）。

### 4.3 完整套件
```
$ python -m unittest discover -s tests -p "test_*.py" -v
Ran 547 tests in 420.187s
FAILED (failures=2, skipped=3)     # 退出码 1
```
- **547** 项（IMPLEMENT-01 后基线 513 + 本工单新增 34）。
- **2 failures**（见 §5，均为既有越界）；**3 skipped**（环境条件性：zb5 permission-denied、zb9 mid-read race、ts_lib junction）。
- 本工单新增/相关测试全 PASS，**零新增失败**。

---

## 5. 完整回归与冻结哈希守卫情况

**2 项失败均为既有、越界（非本工单引入）：**
- `test_p0710zxrxf_guard_r2.FrozenHashGuardR2Tests.test_guard_real_repo_authorized_maintenance_tolerated`
- `test_p0710zxrxf_trust_boundary.ZXRXF7FrozenCompatibility.test_zrxf7_hashes_recorded`
- 二者断言 `usit/scanner.py` 哈希与 `outputs/p0710zxrxf/PRE_SHA256.json`（Oct-17 记录 `c70a1a4b…`）不符；当前 `scanner.py=a009b8db…`（已漂移）。

**归因（区分既有漂移 / 本次引入 / 无法确认）：**
- **既有漂移（非本次引入）**：`scanner.py` 本工单**未修改**（§1.2 复验 `a009b8db…` 与 IMPLEMENT-01 结束时一致；不在本工单改动集）；漂移先于本工单（DESIGN-11/POST-AUDIT-01 已核实：基线 Oct-17 早于会话、其余 6 监视文件相符、孤立单文件漂移）。
- **本次引入**：无（失败集与 IMPLEMENT-01 完整套件完全一致：2 项同名失败；测试数 513→547、失败 2→2、跳过 3→3）。
- **无法确认**：漂移的**具体根因会话**仍无直接证据（维持"待调查"，不归因）。
- **未修改**冻结哈希基线、未改 `scanner.py`、未改守卫；未为求全绿而规避。

---

## 6. 未解决问题、偏离冻结规格的内容及原因

| # | 项 | 类型 | 说明 |
|---|---|---|---|
| D-1 | `FileConclusionType` 枚举置于新模块 `file_understanding.py` 而非 `understanding_model.py` | 偏离（DESIGN-12 建议位置） | 原因：零既有文件修改、降低风险、无需必要性论证；影响：枚举自 `usit.file_understanding` 导入，非破坏性；集成时可再迁居 |
| D-2 | 系统性失配阻断的边界澄清 | 澄清（非偏离） | DESIGN-13"非空路径记录 ∧ ΣN_raw==0 → 阻断"按原样实现；澄清：仅在"清单文件零匹配记录 且 存在非空路径未匹配记录"时触发（真实 scanner 路径因 :982 恒建 file 节点而不触发），纯防御 |
| D-3 | `evidence_refs` 填充口径 | DESIGN_DECISION（冻结字段内） | 冻结字段已定义；填充取"该文件全部逐记录 item 的 projection_id"（完整可追溯证据基）；可按后续审查细化 |
| D-4 | `project_diagnostics[]/unmatched_records[]` 的 `source` | NOT_VERIFIED | 记录无可核验来源标记，按 DESIGN-14 保守置 `"unknown"`，不伪造 |
| U-1 | external/candidate 空路径非诊断实体归 unmatched（标签语义/数量） | 未决（承 DESIGN-14 V-1/V-2） | 交集成工单评估是否单列项目级非诊断桶 |
| U-2 | 模块未接线 | 范围外 | 本阶段仅纯逻辑+测试；集成须独立授权 |
| U-3 | 冻结哈希根因 | 未决 | 待调查，不归因、不更新基线 |

---

## 7. 固定门禁复核（原值保持）

| 门禁 | 值 |
|---|---|
| `F1` | `NOT_READY_TO_CLOSE` |
| `P0.8` | `HOLD` |
| `F2` | `STOP` |
| `历史授权连续性` | `UNRESOLVED` |
| `安全信任链` | `BLOCKED / NOT_VERIFIED` |
| `verified_fact` | `0`（无 VERIFIED_FACT 路径；实测断言） |
| `ExecutionRecord` | `NOT_RUN`（未对真实项目执行） |
| `summary_verification` | `NOT_CHECKED` |
| 受约束条目 `complete` | `False` |
| `BLOCKER-1`（投影未接线） | **不解除**（本阶段纯逻辑，未接线） |
| `BLOCKER-2`（adapt_graph_v1 fallback） | 仍待后续接线复核 |

未宣称系统具备可信项目理解/完整测试/自动修复；未生成真实 `understanding.json`；未因新增测试通过解除任何门禁。

---

## 8. 结论

### **PASS-WITH-ISSUES**

- **授权范围内完成且通过**：新建 `usit/file_understanding.py`（8 值枚举/有序分支/三态上下文/记录三向归属/项目诊断身份判定/未匹配保留/计数失败不少计/K1–K8 自检/`usit-file-understanding-v1` 序列化）+ `tests/test_file_understanding.py`（34 项全 PASS，含 10 类要求与负向断言）。相关回归 100/100 PASS。完整套件 547 项，**零新增失败**。
- **ISSUES（不阻断本工单验收，需记录/后续处理）**：
  1. **2 项既有越界失败**（`scanner.py` 冻结哈希守卫）——非本工单引入、非本工单可修（禁改 scanner.py）；须独立授权处理基线/扫描器。
  2. **偏离/澄清**：`FileConclusionType` 枚举位置（D-1，非破坏性）；`evidence_refs` 填充口径（D-3）；`source=unknown`（D-4，NOT_VERIFIED）；external/candidate 空路径实体归 unmatched（U-1）。
- **范围遵守**：仅新建 2 文件；scanner/cli/既有 schema/PRE_SHA256 零改动；未接线、未真实扫描、未构建/安装/登录/联网/外部 AI。
- **下一步**：提交本报告，等待独立审查后再决定是否签发 scanner/CLI 集成工单。

---

**STOP**：本工单授权仅限文件级结论层与输出构建模块及其测试。未接入 scanner/CLI、未真实扫描、未更新冻结哈希、未解除任何门禁。完成后提交本执行报告，等待独立审查与后续集成授权。
