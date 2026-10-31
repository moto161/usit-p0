# USIT-S1-C-IMPLEMENT-02B-IMPLEMENT-01｜文件级结论层接线与发布验证 执行报告

- 工单：USIT-S1-C-IMPLEMENT-02B-IMPLEMENT-01｜执行时间：2026-10-20（GMT+8）｜工作区：`C:\Users\Administrator\WorkBuddy\2026-10-15-22-09-47\usit_p0`
- 授权范围：修改 `usit/scanner.py`；新建 `tests/test_s1c_wiring.py`；运行合成夹具、相关单测与完整套件。未申请任何范围外修改。
- 设计依据：IMPLEMENT-02B-DESIGN-01 / DESIGN-02（已按其接线点、状态语义、预算与脱敏方案实施）。
- 三态声明：接线已实施✅ / 测试已实跑✅（附命令与结果）/ **未真实项目扫描、未构建安装登录联网、未外部 AI** / BLOCKER-1、BLOCKER-2 **未解除**（门禁条款要求保持原值，机制状态变化待独立审查裁定）。

## 1. 执行前检查（工单要求 1）

| 核对项 | 结果 |
|---|---|
| 工作区路径/清单 | PASS（usit_p0 在位） |
| 关键文件 SHA-256 与工单开始时一致 | **PASS**：scanner.py `a009b8dbb132…`、cli.py `879a3735838e…`、file_understanding.py `71995a09da04…`、parser_contract.py `482400f95d21…`、safety.py `fab3fd0b6da9…`、PRE_SHA256.json `3aaabb4ea052…` —— 逐一相符，无意外变化，未触发 STOP |
| `is_sensitive` 实读（工单要求 5） | PASS：`scanner.py:87-101`——SKIP_NAMES(:24) + `.env.*` 前缀 + SECRET_SUFFIXES(:33 `.pem/.key/.p12/.pfx/.keystore/.jks`) + SENSITIVE_NAME 正则(:27-31，secret/credential/password/token/api_key/…/密钥/密码/…) + 目录段匹配；正反例据此设计（见 §7） |
| tests/test_s1c_wiring.py 预先不存在 | PASS（干净新建） |

## 2. 变更文件与前后 SHA-256

| 文件 | 动作 | PRE sha256 | POST sha256 |
|---|---|---|---|
| `usit/scanner.py` | **MODIFIED**（授权范围内） | `a009b8dbb1329c2de030089ea4b441288812cbd5dbaf5eead4bc6b5c472be416` | `58ba5f25483429ce7482ae1d852deabce6ea0490f2e106c6955c6ff5323b45e3` |
| `tests/test_s1c_wiring.py` | **NEW**（29,284 B） | — | `1b930bb70add4849eea0aef206d34433a8c8383ee4c38d03b6dd90ab4a962b3c` |
| `usit/cli.py` | 未修改 | `879a3735838e…` | `879a3735838e…`（相同） |
| `usit/file_understanding.py` | 未修改 | `71995a09da04…` | `71995a09da04…`（相同） |
| `usit/parser_contract.py` | 未修改 | `482400f95d21…` | `482400f95d21…`（相同） |
| `usit/understanding_projection.py` | 未修改 | `8bb0fb6569a3…` | `8bb0fb6569a3…`（相同） |
| `usit/understanding_model.py` | 未修改 | `4668760b69a0…` | `4668760b69a0…`（相同） |
| `usit/schema_adapter.py` | 未修改 | `c44f60e42f9d…` | `c44f60e42f9d…`（相同） |
| `usit/safety.py` | 未修改 | `fab3fd0b6da9…` | `fab3fd0b6da9…`（相同） |
| `outputs/p0710zxrxf/PRE_SHA256.json` | **未更新**（禁改项） | `3aaabb4ea052…` | `3aaabb4ea052…`（相同） |

## 3. scanner.py 逐项变更（4 处，均按 DESIGN-02）

1. **导入**（:4 `from collections.abc import Mapping`；:10 `from .file_understanding import build_file_understanding, verify_understanding_identities`）——依赖单向无环（file_understanding→understanding_projection/parser_contract，均不反向依赖 scanner）。
2. **新增私有辅助**（:539 `_record_grouping_path`：分组键=`source_location.file_path`，与 `_detect_conflicts`/`file_understanding._record_group_key` 一致；:554 `_build_file_understanding_text`）：敏感路径预过滤（`is_sensitive`，剔除计数 `sensitive_records_excluded`，显式非静默、内容零露面）→ `build_file_understanding` → `verify_understanding_identities(total_input_records=len(kept))` → `json.dumps(ensure_ascii=False,indent=2,sort_keys=True)+'\n'`；**任何失败返回 `(None, {status:FAILED,error_type,error_message})`，永不抛**（序列化在构建步内完成，消除"summary 称 COMPLETE 但文件缺失"窗口）。
3. **构建调用 + 状态**（:1191-1192，插于 :1187 scrub 之后、report 渲染之前，**早于 report.json 写入**）：`summary['understanding_status']` = `{'status':'COMPLETE','bytes':N,'sensitive_records_excluded':K}` 或 `{'status':'FAILED','error_type':…,'error_message':…,'sensitive_records_excluded':K}`。`COMPLETE` 仅表示**已构建并序列化至暂存区**，不解释为最终发布成功；发布真相由 `scan_status.json`（COMPLETE + `published_files` 枚举）承载。
4. **输出**（:1220-1221，插于 `parser_contract.json` 之后、`scan_status.json` 之前）：`if understanding_text is not None: bounded_write(out/'understanding.json',understanding_text)`——**同一暂存区、同一 `bounded_write` UTF-8 字节预算、同一次目录 rename 发布**；缺席即诚实缺席（scan_status 不枚举）。

## 4. 行为验证（冒烟 + 测试证据）

- 真实路径字节级不变原则：`adapt_graph_v1` 及既有输出未触碰；既有 schema（report/scan_manifest/knowledge_graph/test_plan/parser_contract/graph_validation/scan_status）**零字段变更**，仅 summary 增 1 个诊断键 `understanding_status`（无测试断言 summary 精确键集）。
- 发布一致性：`understanding.json` 与全部输出同 stage 同 rename；I-12 实证 `published_files 含 understanding.json ⟺ 文件存在 ⟺ status==COMPLETE`；I-13/I-14/I-15 实证构建失败（FAILED+缺席+其余输出 PUBLISHED+扫描 exit 0）、预算阻断（BLOCKED+published()==[]+stage 清理）、写入失败（INCOMPLETE+stage 清理）。
- 预算：单位=UTF-8 序列化字节；双重检查点（bounded_write 写前累计 + staged 总量 :1246 权威）；**无豁免、未降限额**；B-01 实证精确拟合发布（staged total == limit，边界为 `>` 非 `>=`）；B-02 实证超 1 字节阻断+清理；B-03 实证 150 文件/大体量 evidence_refs 参与预算（bytes==盘尺寸，小预算下阻断前不发布）；B-04 实证序列化失败降级 FAILED 不中止。
- 计数与归属：K1–K8 实证（K1 total==matched+projdiag+unmatched；K2 n_records_total==Σn_records；K3/K4；K5 files==len(files)==manifest 条目；K6 Σby_conclusion；K7 conflict_total==Σcf；K8 可观测部分 Nr==N_raw 每文件成立，projected_items==Nr+cf 由投影套件+装配内 verify 保证）；零记录→NO_RECORDS；空路径项目诊断隔离（真实管线实测为 js_ts `NO_PROJECT_CONFIG_SAFE_DEFAULTS`，正确入 project_diagnostics 且不入 n_records_total）；未匹配记录逐条保留（reason+projection）；投影部分/全部失败 **N_raw 不减**（降级项计数 N_construct_fail/N_unexpected_error）。
- 脱敏：understanding.json 不含 excerpt/凭据值/异常消息/记录 repr（继承 02A 审计结论）；S-01 实测 `sk-live-SECRETVALUE123` 不出现；S-02 实测 5 条敏感路径记录剔除且计数准确、正常记录零误删（正反例基于 :87-101 实际规则）；S-03 实测源码行不出现；S-04 实测 `SECRET_DEBUG_INFO` 不出现。
- 确定性：重复运行 understanding.json 字节一致（实测）。

## 5. 测试命令、退出码与结果（全部实跑）

| # | 命令 | 结果 |
|---|---|---|
| 1 | `python -m unittest tests.test_s1c_wiring -v` | **Ran 25 tests, OK**（25/25 PASS；c02 行内 stderr 为预期 CLI 错误输出，结果 ok） |
| 2 | `python -m unittest tests.test_s1c_wiring tests.test_understanding_projection tests.test_s1c_cases tests.test_parser_contract tests.test_file_understanding tests.test_p072_gates tests.test_safety_gates` | **Ran 174 tests, OK**（121.7s） |
| 3 | `python -m unittest discover -s tests -p "test_*.py" -v`（存档 `/tmp/usit_impl02b_full.log`） | **Ran 572 tests in 582.5s, FAILED (failures=2, skipped=3)**，退出码 1 |

完整套件失败明细（**均为既有越界，非本工单引入**）：
- `test_p0710zxrxf_guard_r2.FrozenHashGuardR2Tests.test_guard_real_repo_authorized_maintenance_tolerated`
- `test_p0710zxrxf_trust_boundary.ZXRXF7FrozenCompatibility.test_zrxf7_hashes_recorded`
- 断言唯一 False = `usit/scanner.py` ≠ PRE 基线（Oct-17 记 `c70a1a4b`；本工单前已漂移至 `a009b8db`，现为 `58ba5f25`）；ZXRXF7_WATCH 其余 6 文件全 MATCH（孤立单文件漂移）；基线早于全部本链工单；**根因会话未证实，不归因、不猜测**；未更新基线、未改守卫、未删/跳/放宽任何既有断言。

跳过 3 项（环境条件性，与基线一致，NOT TESTED）：zb5 permission-denied、zb9 mid-read race、ts_lib junction（需 `USIT_TEST_TSLIB_JUNCTION=1`）。

**与实施前基线对比**：547 项/2 失败/3 跳过 → **572 项/2 失败/3 跳过**（+25 新测全 PASS；失败集、跳过集逐一相同；**零新增失败**）。

## 6. 测试矩阵覆盖对照（工单要求 6）

| 要求 | 测试 | 结果 |
|---|---|---|
| I-12～I-15 发布一致性 | PublishConsistencyTests（4） | PASS |
| K1～K8 计数不变量 | SummaryIdentityTests.test_k1_to_k8_hold_on_real_pipeline | PASS |
| 零记录文件 | test_zero_record_manifest_file_gets_no_records_conclusion | PASS |
| 空路径诊断 | test_empty_path_project_diagnostic_is_isolated + test_real_pipeline_project_diagnostics_have_empty_path_only | PASS |
| 未匹配记录 | test_unmatched_record_is_preserved_with_reason | PASS |
| 投影异常降级 | ProjectionFailureTests（部分/全部，2） | PASS |
| 预算恰好达到上限 | test_b01_budget_exactly_at_limit_publishes | PASS |
| 超过 1 字节 | test_b02_budget_one_byte_over_blocks | PASS |
| 超限后清理 | b01/b02/i14 内 stage 清理 + BLOCKED 标记断言 | PASS |
| 疑似凭据值脱敏 | test_s01_credential_value_in_normal_source_is_not_emitted | PASS |
| 敏感路径 | test_s02/s02b + is_sensitive 正反例（2） | PASS |
| 诊断文本脱敏 | test_s03_diagnostic_and_source_text_not_emitted | PASS |
| 异常消息脱敏 | test_s04_exception_message_not_emitted | PASS |
| 重复运行确定性 | test_repeated_runs_produce_identical_understanding | PASS |
| （附加）CLI 退出码 | test_c01（exit 0+understanding_status）/ test_c02（缺授权 exit 2 且无 understanding.json） | PASS |
| （附加）序列化失败 | test_b04_serialisation_failure_degrades_to_failed | PASS |
| （附加）大型 evidence_refs | test_b03_large_evidence_refs_participates_in_budget | PASS |

## 7. 实施期发现（记录，未扩大范围）

- **F-1（测试技术说明）**：`report.json` 的 `parser_task_audit.elapsed_seconds` 为墙钟计时，且 `runtime_limits.max_output_bytes` 嵌入限额本身——两者使 staged 总字节跨运行波动（位长差）。精确拟合测试以冻结时钟 + 位长补偿（−4）消除，并以 `staged_total == limit` 断言守护推导（推导若失效则测试显式失败，不静默通过）。**understanding.json 本身不受影响（天生确定性）**。
- **F-2（机制状态变化，门禁不变）**：本工单完成后，投影机制已由 scanner 输出链调用（经 `build_file_understanding`），BLOCKER-1 所述"未接线"的条件性事实已改变；按工单要求 **BLOCKER-1 保持不解除**，其正式解除须由独立审查工单在证据齐备后裁定。
- **F-3**：BLOCKER-2（adapt_graph_v1 fallback）机制已于 IMPLEMENT-01 修正；接线后复核（真实扫描 evidence 零 graph_node_id）仍待独立审查，本工单不解除。

## 8. 未解决问题

- U-1（NOT_VERIFIED）`is_sensitive` 模式完备性未审计（既有实现）；普通文件内凭据值不被任何输出 scrub（既有全输出集属性），understanding.json 不携值故不放大——**不声称"安全已验证"**。
- U-2（NOT_VERIFIED）understanding.json 真实项目体量（现可经 `understanding_status.bytes` 直接测量；未跑真实扫描）。
- U-3 2 项既有冻结哈希失败需独立授权处置（修 scanner.py 已在本链完成，但基线再生须显式授权步骤+留痕；根因调查工单未签发）。
- U-4 BLOCKER-1/2 解除证据待独立审查（§7 F-2/F-3）。

## 9. 固定门禁逐项复核（原值保持）

F1=NOT_READY_TO_CLOSE ✅保持｜P0.8=HOLD ✅｜F2=STOP ✅｜历史授权连续性=UNRESOLVED ✅｜安全信任链=BLOCKED/NOT_VERIFIED ✅｜verified_fact=0 ✅（无 VERIFIED_FACT 路径，实测断言）｜ExecutionRecord=NOT_RUN ✅（指 S1-C 生产执行；本工单仅单测/合成夹具）｜summary_verification=NOT_CHECKED ✅｜complete=False ✅｜BLOCKER-1 不解除 ✅（§7 F-2）｜BLOCKER-2 待接线后复核 ✅（§7 F-3）。未因测试通过解除任何门禁。

## 10. 范围遵守核验

仅改 `usit/scanner.py`（授权）+ 新建 `tests/test_s1c_wiring.py`（授权）；cli.py/file_understanding.py/parser_contract.py/understanding_projection.py/understanding_model.py/schema_adapter.py/safety.py/PRE_SHA256.json/既有 schema/既有测试**字节级零变更**（§2 哈希对照）；未真实扫描/构建/安装/登录/联网/外部 AI；未自动修复无关问题；未删除/跳过/放宽既有断言；工作区无残留 `.usit-stage-*`。

## 11. 最终结论：**PASS-WITH-ISSUES**

- 授权范围内完成：接线 4 处变更按 DESIGN-02 落地；新增 25 测试全 PASS；相关回归 174/174；完整套件 572 项零新增失败（失败集/跳过集与基线逐一相同）；发布一致性、预算、归属计数、脱敏、确定性均有实测证据。
- ISSUES（不阻断验收，需记录/后续独立授权）：① 2 项既有越界冻结哈希失败（scanner.py 漂移，禁改项，须独立授权再生基线+根因调查）；② `is_sensitive` 完备性未审计（不声称安全已验证）；③ BLOCKER-1/2 机制状态已变但门禁按条款保持、待独立审查裁定；④ 真实项目体量未测（U-2）。
- 本工单未授权范围外任何操作；完成后 STOP，等待独立审查。
