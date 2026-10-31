# USIT-S1-C-IMPLEMENT-02B-AUDIT-02｜接线后独立验收与计数完整性审计 执行报告

- 工单：USIT-S1-C-IMPLEMENT-02B-AUDIT-02｜执行时间：2026-10-20（GMT+8）｜工作区：`C:\Users\Administrator\WorkBuddy\2026-10-15-22-09-47\usit_p0`
- 模式：**严格 READ-ONLY 静态审计**。未修改任何文件；**未运行任何测试**（工单要求）；未真实扫描/构建/安装/登录/联网/外部 AI；未更新基线；未解除门禁。仅新增本报告。
- 审计对象：IMPLEMENT-02B-IMPLEMENT-01 的 `usit/scanner.py`（POST `58ba5f254834…`）与 `tests/test_s1c_wiring.py`（`1b930bb70add…`）。
- 证据分级：SOURCE_CONFIRMED（本次实读源码）/ TEST-ASSERTED（测试文件中断言存在，本次未执行）/ REPORT-CLAIMED（实施报告声称的运行结果，本次未复跑）/ NOT_VERIFIED。
- 方法：独立重读磁盘代码（非复述执行报告）+ 只读哈希核验 + 测试断言静态定位。

## 1. A. 原始记录计数审计（最高优先级）

### A.1 代码位置（SOURCE_CONFIRMED）

| 功能 | 位置 |
|---|---|
| 记录分组键 | `scanner.py:539-552` `_record_grouping_path`（=`source_location.file_path`，与 `file_understanding._record_group_key`/`_detect_conflicts` 键一致） |
| 接线装配（过滤+构建+校验+序列化） | `scanner.py:554-584` `_build_file_understanding_text` |
| 三向归属（matched/project_diagnostic/unmatched） | `file_understanding.py:407-418` |
| 每文件投影与结论 | `file_understanding.py:422-424`（`project_records(matched_groups[path])` → `decide_file_conclusion`） |
| totals 计算 | `file_understanding.py:429-436` |
| 恒等式校验 K1-K7+无 VERIFIED_FACT | `file_understanding.py:472-506` `verify_understanding_identities` |
| 投影失败计数 | `understanding_projection.py` per-record except → `N_construct_fail`/`N_unexpected_error`（IMPLEMENT-01 已审计机制，本次未重读全文，引用既有审计结论） |

### A.2 各数量计算点沿数据流定位（SOURCE_CONFIRMED）

| 数量 | 计算点 | 是否进入校验/产物 |
|---|---|---|
| 过滤前原始批次数 `len(records)` | `scanner.py:568`（`parser_contract.get('records',[])`）+ :571 循环 | **仅作循环输入；未存储、未送入任何恒等式校验** |
| 敏感判定+排除计数 | `scanner.py:573-574`（`is_sensitive(path)` → `excluded+=1`） | 仅入 `summary['understanding_status']['sensitive_records_excluded']`（:581/:584） |
| 过滤后批次数 `len(kept)` | `scanner.py:576` 累积、:579 校验入参 | 作为 `total_input_records` 送入 verify（**两处**：builder :460 内部 + scanner :579，值相同、重复校验） |
| matched/projdiag/unmatched | `file_understanding.py:411-418`（投影前穷尽互斥） | 入 totals（:432-435）与各自数组 |
| 每文件 N_raw/Nr | `project_records` stats（:423 调用）→ `FileConclusion.n_records/counts` | 入 files[] |
| 冲突 cf | `projection.stats['conflict']` → `conflict_count` | 入 files[] 与 totals.conflict_total（:433） |
| 投影失败数 | per-record except → N_construct_fail/N_unexpected_error → 每文件 counts | 入 files[].counts；**不影响 n_records**（n_records=N_raw 源自投影前分组） |

### A.3 `total_input_records` 口径判定（工单点名问题）

**判定：`total_input_records` 代表过滤后批次 `len(kept)`，不是过滤前原始输入批次。** 证据：`scanner.py:579` `verify_understanding_identities(understanding, total_input_records=len(kept))`；`file_understanding.py:460` 同理（其 `records` 入参即 kept）。过滤前原始数（`len(parser_contract['records'])`）未传入任何校验。

### A.4 完整原始输入的 K1 判定（工单点名问题）

冻结恒等式（DESIGN-14 K1）：`total_input_records = matched_file_raw_records + project_diagnostic_raw_records + unmatched_raw_records`。

- **对完整原始输入（含敏感排除）的推广恒等式**：`len(原始批次) = matched + project_diagnostic + unmatched + sensitive_records_excluded`。
- **是否成立**：**成立（by construction）**——:573-576 的 if/else 互斥（每条原始记录恰入 excluded 或 kept 之一），kept 经 :411-418 穷尽互斥三向归属；无静默丢弃（排除计数显式披露于 summary）。
- **验证覆盖**：`verify_understanding_identities`（:496-499）只验后过滤恒等式；**推广恒等式无生产级断言**。测试层 `test_s1c_wiring.py:309-313`（`test_k1_to_k8_hold_on_real_pipeline`）断言了 `matched+projdiag+unmatched == len(parser_contract records) - excluded`——即推广恒等式的测试级验证存在，**但该次运行 excluded==0（合成项目无敏感记录），断言退化为后过滤恒等式**；excluded>0 的真实管线推广断言不存在（assembly 级 `test_s02`(:452-465) 隐含验证：6 输入 = 1 matched + 0 + 0 + 5 excluded，但未以单一恒等式方程断言）。
- **产物可对账性**：推广恒等式可由现存产物复算——`len(understanding.json 无关)`；实际为 `len(parser_contract.json records)`（未过滤，含敏感路径记录若存在） vs `understanding.json totals` 三项和 + `report.json → understanding_status.sensitive_records_excluded`。understanding.json 的 totals **不含**排除数（v1 schema 冻结字段，未擅自增加——符合边界）。
- **判定：K1（完整原始输入）成立；生产自检覆盖 NOT_VERIFIED（excluded>0 场景）**。不存在"重新定义 total_input_records 掩盖遗漏"——无记录丢失、排除显式计数；缺口在**验证覆盖与显式恒等式化**，不在计数正确性。

### A.5 K2～K8 口径核查（SOURCE_CONFIRMED + TEST-ASSERTED）

| 恒等式 | 生产计算 | 测试断言（未执行） | 判定 |
|---|---|---|---|
| K2 `n_records_total==Σfiles.n_records` | :432 + verify :488-489 | test_s1c_wiring:315-316 | PASS |
| K3 `unmatched_total==len(unmatched_records)` | :434 + verify :492-493 | :318 | PASS |
| K4 `project_diagnostic_total==len(project_diagnostics)` | :435 + verify :494-495 | :319 | PASS |
| K5 `totals.files==len(files)` | :430 + verify :483-484 | :321-322（另 == manifest 条目） | PASS |
| K6 `Σby_conclusion==totals.files` | :431 + verify :485-486 | :324-325 | PASS |
| K7 `conflict_total==Σconflict_count` | :433 + verify :490-491 | :326-327 | PASS |
| K8 `projected_items==Nr+cf` | 投影层（IMPLEMENT-01 机制）；可观测部分 Nr==N_raw 每文件 | :328-334（Nr==N_raw、n_records==N_raw）+ 投影套件 + 装配内 verify | PASS |
| 冲突合成项不双计 | cf 为附加投影；n_records=N_raw；K7 仅计组数 | :326-327 | PASS |
| 投影失败不少计 | N_raw 源自投影前分组；失败仅改 N_construct_fail/N_unexpected_error | test_partial/total_projection_failure(:384-431)：N_raw 不减、失败数和==N_raw | PASS |
| 敏感排除不混入 | 排除记录不进任何类别/数组；excluded 独立计数 | test_s02(:452-465)：unmatched_total==0、n_records_total==1 | PASS |

### A.6 测试静态审查（TEST-ASSERTED，本次未执行）

- `test_k1_to_k8_hold_on_real_pipeline`(:304-336)：K1-K8 全断言 + 无 VERIFIED_FACT；K1 用后过滤口径（excluded==0 退化）。
- `test_zero_record…`(:338-346)、`test_empty_path_project_diagnostic_is_isolated`(:348-358)、`test_unmatched_record_is_preserved_with_reason`(:360-371)：归属边界。
- `test_partial/total_projection_failure…`(:384-431)：失败计数保持。
- `test_s02/s02b`(:452-470)：敏感排除计数与零误删。
- 无测试断言推广恒等式（含 excluded>0）；无测试覆盖"原始批次全敏感"边界（该场景下 :441-448 系统性失配守卫因过滤而不触发，files 全 NO_RECORDS + excluded>0——诚实但守卫域被过滤改变，记为观测 O-3）。

## 2. B. 发布与输出预算

| 项 | 证据 | 判定 |
|---|---|---|
| understanding_status 终态时机 | :1191-1192 构建+状态在 scrub(:1186) 后、report 渲染(:1203)/report.json 写(:1215) **之前**——summary 落盘时状态已终态 | PASS（SOURCE_CONFIRMED） |
| COMPLETE 语义 | 仅"已构建+序列化至暂存区"（:1187-1190 注释 + :581 返回点）；发布真相=scan_status.json(:1230-1232 published_files rglob) | PASS |
| 文件存在性 ⟺ 状态 | emit 条件 `if understanding_text is not None`(:1220) 与 status COMPLETE 同源（:581）；I-12 测试断言 iff 链(:132-145) | PASS（源码+TEST-ASSERTED；运行结果 REPORT-CLAIMED 25/25 OK，本次未复跑） |
| 预算通道 | :1221 经 `bounded_write`(:1205-1210)：`text.encode('utf-8')` 字节累计、写前检查、与全部输出同预算；序列化格式与 write_json 一致(:580) | PASS（SOURCE_CONFIRMED） |
| 无豁免/未降限额 | 未触碰 MAX_OUTPUT_BYTES/safety.py（哈希未变 `fab3fd0b…`）；B-01/B-02 断言边界(:215-250) | PASS |
| 失败后 stage/旧输出 | 预算/写失败→ ScanBlocked/OSError → 既有 except(:791-796 域) rmtree stage + usit-status 标记；understanding.json 不单独落盘；I-14/I-15 断言(:167-198) | PASS（源码+TEST-ASSERTED） |
| 测试运行结果 | 572 项/2 既有失败/3 跳过、新测 25/25、相关回归 174/174——**REPORT-CLAIMED，本工单未运行** | NOT_VERIFIED（未复跑；建议下阶段复跑或由独立审查采信） |

## 3. C. 脱敏与记录保留

| 项 | 证据 | 判定 |
|---|---|---|
| 过滤覆盖所有记录路径 | 进入 build 的唯一输入是 `kept`(:578)；project_diagnostics/unmatched 条目均由 kept 记录在 builder 内生成(:426-427)——无第二条路径 | PASS（SOURCE_CONFIRMED） |
| 过滤先于任何输出构造 | :571-576 过滤 → :578 build；identity_basis/unmatched.file_path 只能携带非敏感路径（s02:452-465 断言 5 敏感路径零露面） | PASS |
| 无误删正常记录 | is_sensitive 负例测试(:438-440：app.py/api.py/pkg/mod.py/README.md/main.go/tests/test_x.py 均 False) + s02b(:467-470 excluded==0) | PASS（TEST-ASSERTED） |
| 排除计数可追溯不泄露 | 仅计数入 summary(:581/:584)；敏感路径不出现在 understanding.json（s02 断言） | PASS |
| 无 excerpt/凭据值/异常消息/repr | 代码链：`_build_evidence` raw_value=None（understanding_projection.py:147）、ConclusionRecord raw_source=None(:224)、降级项仅 type(exc).__name__(:404-409)、repr 仅进 ID 哈希(:398)、to_dict(:319-330) 无证据内容；测试 S-01..S-04(:442-499) | PASS（源码+TEST-ASSERTED；运行结果 REPORT-CLAIMED） |
| is_sensitive 完备性 | 模式集（SKIP_NAMES/SECRET_SUFFIXES/SENSITIVE_NAME/目录段，scanner.py:24-33/:87-101）未审计完备性；普通文件内凭据值不被任何输出 scrub（既有全输出集属性） | **NOT_VERIFIED**（不宣称安全已验证） |

## 4. D. BLOCKER-1/2 与冻结哈希

### D.1 BLOCKER-1（投影未接线）——机制条件已被本链解决；**本工单不解除**

证据（SOURCE_CONFIRMED + TEST-ASSERTED）：
- scanner 实际调用文件级构建：:1191（`_build_file_understanding_text`）→ :578（`build_file_understanding`）→ :423（`project_records` 每文件组一次）；
- 实际写出：:1220-1221 经 bounded_write 入暂存区，随单次 rename 发布；
- 失败路径：:577-584 never-raise + FAILED 状态 + 诚实缺席（测试 I-12..I-15:132-198）。
- **解除建议（仅建议，不改门禁）**：建议独立审查工单在以下条件齐备后裁定：① 补齐 A.4 最小修正（全批次恒等式生产断言 + excluded>0 测试）；② 冻结基线在显式授权下再生并留痕；③ 真实项目端到端验证（当前 ExecutionRecord=NOT_RUN）；④ 完整套件复跑采信。

### D.2 BLOCKER-2（adapt_graph_v1 fallback 过宽）——机制已修正；**保持待复核**

证据：`parser_contract.py:135` 分类、:159 `"evidence": evidence`（仅真实分类证据，**从不合成**）、:147 `graph_node_id` 仅出现在 diagnostics（节点身份经 graph_ref :161 追溯）；测试 `GraphV1EvidenceFallbackTests`（IMPLEMENT-01，既有）。**缺口**：接线后"真实扫描产物 evidence 零 graph_node_id"的端到端证据未独立获取（本工单禁真实扫描）→ 维持待复核，不解除。

### D.3 冻结哈希（SOURCE_CONFIRMED，只读核验）

- 当前：`scanner.py=58ba5f254834…`（接线后，与实施报告 POST 一致）、`cli.py=879a3735838e…`（未变）、`PRE_SHA256.json=3aaabb4ea052…`（**未更新**）。
- 基线（Oct-17）记录 `scanner.py=c70a1a4b…` → **DRIFT**；ZXRXF7_WATCH 其余 6 文件全 MATCH（孤立单文件漂移）。
- 两项历史失败（`test_p0710zxrxf_guard_r2…maintenance_tolerated`、`test_p0710zxrxf_trust_boundary…zrxf7_hashes_recorded`）为同一根因（scanner.py≠基线）；**根因会话未证实，不归因、不猜测**；未更新基线、未将历史失败伪报为通过。

## 5. 问题清单与最小修正建议（仅建议，未实施）

| # | 级别 | 问题 | 最小修正建议 |
|---|---|---|---|
| I-1 | **中** | 推广恒等式（完整原始输入 K1）无生产级断言；唯一测试断言退化为 excluded==0 | 在 `scanner.py:578` 之后加断言：`len(records) == understanding['totals']['n_records_total'] + totals['project_diagnostic_total'] + totals['unmatched_total'] + excluded`（失败即走既有 FAILED 降级，**零 schema 变更**）；新增真实管线测试：patch `adapt_graph_v1` 追加一条敏感路径记录，断言 excluded==1 且四项和==len(parser_contract records) |
| I-2 | 低 | 重复校验：builder :460 与 scanner :579 以相同参数各验一次 | 可删其一（低优先；保留亦无害） |
| I-3 | 低 | "原始记录全部敏感"场景绕过 :441-448 系统性失配守卫（过滤使其非空路径计数为 0） | 守卫计数改用过滤前口径，或在 I-1 断言中间接覆盖（该场景四项和仍成立） |
| I-4 | 信息 | understanding.json totals 不含 sensitive_records_excluded（v1 schema 冻结） | 如需产物自含，须走 schema 变更授权；当前可由 parser_contract.json + report.json 对账（已可行） |
| I-5 | 信息 | 测试运行结果本次未复跑（REPORT-CLAIMED） | 由独立审查或下阶段复跑采信 |

## 6. 逐项结论汇总

| 审计项 | 结论 |
|---|---|
| A.1/A.2 代码位置与计数点 | PASS |
| A.3 total_input_records 口径 | **PASS（判定：=过滤后 len(kept)，非过滤前；已明确记录）** |
| A.4 完整原始输入 K1 | **PASS-WITH-ISSUES**（恒等式成立且无遗漏；生产自检不覆盖 excluded>0，测试断言退化——见 I-1） |
| A.5 K2-K8 口径 | PASS（无双计/少计：冲突附加、失败不减、排除互斥） |
| A.6 测试静态审查 | PASS（断言定位清晰；覆盖缺口=I-1） |
| B 发布与预算 | PASS（源码证实；运行结果未复跑标 NOT_VERIFIED） |
| C 脱敏与保留 | PASS（is_sensitive 完备性 NOT_VERIFIED） |
| D.1 BLOCKER-1 | 机制条件已解决；**不解除**（解除建议见 §4.1，待独立审查） |
| D.2 BLOCKER-2 | 机制已修正；**保持待复核**（端到端证据未独立获取） |
| D.3 冻结哈希 | PASS（漂移事实证实、孤立、根因未证实不归因、未更新基线） |

## 7. 最终结论：**PASS-WITH-ISSUES**

- 接线实现与冻结规格一致：计数逻辑无双计/少计（冲突附加、投影失败不减、敏感排除互斥且显式计数）；发布状态语义一致（COMPLETE=暂存区构建成功、发布真相=scan_status）；预算走既有 bounded_write 无豁免；脱敏链闭合（is_sensitive 完备性除外，NOT_VERIFIED）。
- ISSUES：① I-1（中）推广恒等式缺生产级断言 + excluded>0 测试退化——**建议在接线被认可为完成前补齐**（最小修正零 schema 变更）；② 测试运行结果未复跑；③ BLOCKER-1/2 按门禁条款保持，待独立审查；④ 冻结基线待显式授权再生。
- **下一阶段结论**：机制层面**可进入下一阶段**（独立审查/门禁裁定/基线治理/端到端验证），但建议以 I-1 补齐作为接线完成的收尾条件之一。本工单不实施修复、不接线、不解除门禁、不更新基线。

## 8. 文件修改核验

本工单零修改：`usit/scanner.py`(58ba5f25…)/`usit/cli.py`(879a3735…)/`usit/file_understanding.py`(71995a09…)/`usit/parser_contract.py`(482400f9…)/`outputs/p0710zxrxf/PRE_SHA256.json`(3aaabb4e…)/tests/既有报告均与工单开始前相符；仅新增本报告。**未运行任何测试**（静态审计）。
