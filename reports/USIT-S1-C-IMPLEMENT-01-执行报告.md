# USIT-S1-C-IMPLEMENT-01｜投影机制修正与独立测试实施工单 执行报告

- **工单编号**：USIT-S1-C-IMPLEMENT-01
- **执行时间**：2026-10-20（GMT+8）
- **工作区绝对路径**：`C:\Users\Administrator\WorkBuddy\2026-10-15-22-09-47\usit_p0`
- **执行模式**：经本工单明确授权的**限定范围**源码修改与测试。**不实施 S1-C 全链路接线。**
- **授权修改范围**：仅投影模块 `usit/understanding_projection.py`、图适配模块 `usit/parser_contract.py` 及其专用测试 `tests/test_understanding_projection.py`、`tests/test_parser_contract.py`、新增 `tests/test_s1c_cases.py`。
- **前置依据**：`reports/USIT-S1-C-DESIGN-REVIEW-09-执行报告.md`、`reports/USIT-S1-C-IMPLEMENTATION-READINESS-01-执行报告.md` 及其引用的源码/测试（均已实读，非仅摘要）。

---

## 1. 执行信息与执行前检查（STOP 条件逐条核对）

| STOP 条件 | 核对结果 |
|---|---|
| 工作区真实路径存在 | ✅ `usit_p0` 存在，文件清单与基线一致 |
| Git 状态 | ⚠️ **非 Git 仓库**（`git status` → `fatal: not a git repository`），无法用 git diff；改用 sha256 前后哈希清单核验范围 |
| 文件与基线相符 / 无可区分用户改动 | ✅ 进入工单前无进行中的用户改动；本工单改动全部可追溯（见 §3） |
| 测试可运行 | ✅ Python 3.13.14，纯 stdlib（pyproject 无第三方依赖）；基线 29 项相关测试全 PASS |
| 改动可能破坏既有行为 | ✅ 已核验：真实扫描路径与 `valid_v1.json` 输出**字节级不变**；`project_records` 既有 stats 键语义不变（仅新增键）；完整套件无**新增**失败（§6） |
| 未覆盖/回滚/清理任何用户改动 | ✅ 未执行任何回滚/清理 |

> 执行前已记录源码哈希（§3）。未触发任何 STOP 条件。

---

## 2. 授权边界遵守情况

- ✅ 仅修改投影模块、图适配模块及其专用测试；**未修改** CLI 接线（`cli.py`）、扫描器（`scanner.py`）、配置、数据库、凭据、外部网络或其他业务模块。
- ✅ **未实施 S1-C 全链路接线**：未在 `scanner.py`/`cli.py` 中新增对 `project_records` 的调用；S1-C 仍**未接线**（BLOCKER-1 保持）。
- ✅ 未修改固定门禁，未把设计推演改写为实测结果（设计-only 的 `file_conclusion`/分支列明确保持 NOT_VERIFIED，见 §5）。
- ✅ 未通过放宽校验、伪造字段、改预期值、跳过测试或降低断言来获取通过。

---

## 3. 执行前后文件清单、哈希与差异

> sha256。`PRE` = 工单开始前；`POST` = 工单结束后。`understanding_model.py`/`schema_adapter.py` 前后哈希**完全相同**（未改）；`scanner.py`/`cli.py` **未修改**（越界禁改）。

| 文件 | 范围 | PRE sha256 | POST sha256 | 变更 |
|---|---|---|---|---|
| `usit/understanding_projection.py` | 授权 | `0984a74a62a733e153e7f1bea91f78ef64c0c48fac111843bd450491b9a665f8` | `8bb0fb6569a318357bd3079a923f980c52bd0d085354380e8347b89a33ecb226` | **MODIFIED**（Task B：逐记录分类计数） |
| `usit/parser_contract.py` | 授权 | `3f7a23c8e5c308371f414dafe872147cb56d10c785c1a7d3953f3f3f358d17a4` | `482400f95d21df37d655b7e951490d35bcee6d98d04a8dd4640484a83314dd87` | **MODIFIED**（Task A：fallback 不再伪造证据） |
| `tests/test_understanding_projection.py` | 授权 | `0b58c6b67f51dca50bbac6c8aaf628f057d362f05e5e98b468134433dc8b8f6d` | `010c1ae35482d31882a467c7728c436d9290dc3a3525321b3c66517efd6c5ac5` | **MODIFIED**（新增计数/异常注入测试） |
| `tests/test_parser_contract.py` | 授权 | *(初始哈希批次遗漏，未捕获；变更纯追加)* | `6739d743fbd31fa82ab8658039c9a38c2903fb5904335047f07aa13c112af71e` | **MODIFIED**（追加 `GraphV1EvidenceFallbackTests`） |
| `tests/test_s1c_cases.py` | 授权 | *(不存在)* | `14377` 字节（新建） | **NEW**（C1–C21 专用 fixture/独立测试） |
| `usit/understanding_model.py` | — | `4668760b69a0a6d0e86592897b60375feb76800b20bc845e32f26fcc61f690e7` | `4668760b69a0a6d0e86592897b60375feb76800b20bc845e32f26fcc61f690e7` | **UNCHANGED** |
| `usit/schema_adapter.py` | — | `c44f60e42f9d50f42078743a4fbc9a350124151fd8454a301b6bf36d6a4def44` | `c44f60e42f9d50f42078743a4fbc9a350124151fd8454a301b6bf36d6a4def44` | **UNCHANGED** |
| `usit/scanner.py` | 越界禁改 | — | `a009b8dbb1329c2de030089ea4b441288812cbd5dbaf5eead4bc6b5c472be416` | **NOT MODIFIED** |
| `usit/cli.py` | 越界禁改 | — | `879a3735838e183f41dd08054095b514ffc0e81e2b43776b75a77e25d138b8d9` | **NOT MODIFIED** |

> 披露：`tests/test_parser_contract.py` 的 PRE 哈希未纳入初始批次（批次仅含 5 个核心文件）；其变更为**纯追加**一个测试类（`GraphV1EvidenceFallbackTests`），未改动既有测试。

### 逐项变更说明

**A. `usit/parser_contract.py` — `adapt_graph_v1` fallback 修正**
- 新增 `_classify_graph_evidence(raw)`：把 graph 节点/边的 `evidence` 分为真实（非空 Mapping，原样透传）/ 缺失（`missing_evidence`）/ 空（`empty_evidence`）/ 类型错误（`malformed_evidence:<type>`），**永不合成** `{"graph_node_id": id}`，**永不崩溃**（非 Mapping 不再触发 `AttributeError`）。
- `adapt_graph_v1`：缺失/空/类型错误的 evidence 一律输出 `{}` + 一条 diagnostic（保留失败原因）；节点身份经 `graph_ref` 继续可追溯；`source_node` 路径回退同样经分类器防崩。
- 兼容性：真实路径（所有节点/边均有真实 evidence）与 `valid_v1.json` 输出**字节级不变**（已实测：4 条记录、全部 `validate_record` 通过、无 `graph_node_id` 伪造、diagnostics 均为空）。

**B. `usit/understanding_projection.py` — 逐记录互斥分类计数**
- 新增 `_intended_record_category(record)`：无异常时每条记录恰好归入 `not_attempted`（非 Mapping）/ `construct_ok`（候选：Mapping + 非空 Mapping evidence）/ `evidence_none`（Mapping 但 evidence 非候选）之一。
- 新增 `_degraded_projected_item(...)`：捕获异常时产出**不带 conclusion 的 UNKNOWN 项**（永不升级为成功）。
- 重写 `project_records`：逐记录 `try/except`，异常**仅归属当条记录**并分类为 `construct_fail`（候选构造抛错）或 `unexpected_error`（其他）；批次级 `TypeError`（非序列入参）在循环前抛出，**不伪装成逐记录计数**；`_detect_conflicts` 的失败照常作为批次错误上抛（不计入逐记录）。
- stats **追加**键：`N_raw / N_candidate / N_non_candidate / N_construct_ok / N_construct_fail / N_not_attempted / N_unexpected_error / N_evidence_none / Nr`；既有 8 键语义**不变**（`conflict` 即 `cf`，`projected_items` 即 `proj`）。无任何调用方依赖被破坏（`project_records` 当前仅被测试引用，S1-C 未接线）。

---

## 4. 计数定义与恒等式（B 的落地）

定义（每条原始记录恰好归入一个终态）：
- `N_raw`：冲突合成项追加前的原始输入记录数 = `len(records)`。
- `Nr`：原始记录投影数（每条原始记录→恰好 1 个投影项，故 `Nr == N_raw`）。
- `N_candidate` = `N_construct_ok + N_construct_fail`（候选 = Mapping + 非空 Mapping evidence）。
- `N_non_candidate` = `N_evidence_none`（Mapping 但 evidence 缺失/空/非 Mapping）。
- `N_not_attempted`：记录非 Mapping，`project_record` 顶部短路，不触达 `_build_evidence`。
- `N_construct_fail`：候选但构造抛错被**捕获并归属该记录**。
- `N_unexpected_error`：可归属到单条记录且被捕获分类的其它异常（**非**批次中断）。
- `cf` = `conflict`（额外冲突投影数，每组 1 个）；`proj` = `projected_items` = `Nr + cf`。

恒等式（均由自动化断言验证，见 §5/§6）：
1. `N_raw = N_candidate + N_non_candidate + N_not_attempted + N_unexpected_error`
2. `N_candidate = N_construct_ok + N_construct_fail`
3. `N_non_candidate = N_evidence_none`
4. `N_raw = N_construct_ok + N_construct_fail + N_evidence_none`（仅当 `N_not_attempted==0` 且 `N_unexpected_error==0`）
5. `proj = Nr + cf`

> **关于 `N_construct_fail`/`N_unexpected_error` 的 0**：本工单实现了捕获机制（`try/except` + 分类），因此这两个计数是**实测值**（无注入时为实测 0，非虚构未知）。C1–C21 无注入，故为实测 0；`>0` 路径由专用注入测试证明（§6）。这修正了 REVIEW-09 中"源码无机制→NOT_AVAILABLE"的状态：机制现已存在并可测。

---

## 5. C1–C21 逐项结果（专用 fixture：`tests/test_s1c_cases.py`）

> 可定位专用测试模块，逐项映射 编号/输入/预期/实际。**实测并断言投影计数**；`file_conclusion`/分支属设计层、`project_records` 不产出 → 明确 **NOT_VERIFIED**（见 `S1CDesignOnlyColumnsNotImplementedTests`：断言输出无 `file_conclusion`/`branch` 键）。

| # | 输入（摘要） | N_raw | Nc | Nnc | Nok | Nfail | Nnone | Nna | Neu | cf | proj | 结果 | file_conclusion/分支 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| C1 | 1×RESOLVED/非空dict | 1 | 1 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 1 | **PASS** | NOT_VERIFIED[设计] |
| C2 | 1×RESOLVED/仅{graph_node_id} | 1 | 1 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 1 | **PASS** | NOT_VERIFIED[设计] |
| C3 | 1×NON_SUCCESS/fallback非空 | 1 | 1 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 1 | **PASS** | NOT_VERIFIED[设计] |
| C4 | 空文件→0记录 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | **PASS** | NOT_VERIFIED[设计] |
| C5 | 0记录、无符号 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | **PASS** | NOT_VERIFIED[设计] |
| C6 | 0记录、有符号无记录 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | **PASS** | NOT_VERIFIED[设计] |
| C7 | 1×RESOLVED + 文件降级 | 1 | 1 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 1 | **PASS** | NOT_VERIFIED[设计] |
| C8 | 1×NON_SUCCESS + 文件降级 | 1 | 1 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 1 | **PASS** | NOT_VERIFIED[设计] |
| C9 | 1×NON_SUCCESS/fallback非空 | 1 | 1 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 1 | **PASS** | NOT_VERIFIED[设计] |
| C10 | RESOLVED+NON_SUCCESS 同身份异内容 | 2 | 2 | 0 | 2 | 0 | 0 | 0 | 0 | 1 | 3 | **PASS** | NOT_VERIFIED[设计] |
| C11 | 1×RESOLVED + orphan | 1 | 1 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 1 | **PASS** | NOT_VERIFIED[设计] |
| C12 | 1×RESOLVED + diagnostics越界 | 1 | 1 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 1 | **PASS** | NOT_VERIFIED[设计] |
| C13 | 1×RESOLVED + path∉manifest | 1 | 1 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 1 | **PASS** | NOT_VERIFIED[设计] |
| C14 | 1×RESOLVED + line非正整数 | 1 | 1 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 1 | **PASS** | NOT_VERIFIED[设计] |
| C15 | 1×RESOLVED + TestSpec缺必填 | 1 | 1 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 1 | **PASS** | NOT_VERIFIED[设计] |
| C16 | 2×RESOLVED完全相同（重复） | 2 | 2 | 0 | 2 | 0 | 0 | 0 | 0 | 0 | 2 | **PASS** | NOT_VERIFIED[设计] |
| C17 | NON_SUCCESS+RESOLVED 不同身份 | 2 | 2 | 0 | 2 | 0 | 0 | 0 | 0 | 0 | 2 | **PASS** | NOT_VERIFIED[设计] |
| C18 | 1×RESOLVED/evidence非Mapping | 1 | 0 | 1 | 0 | 0 | 1 | 0 | 0 | 0 | 1 | **PASS** | NOT_VERIFIED[设计] |
| C19 | RESOLVED非Mapping + NON_SUCCESS候选 | 2 | 1 | 1 | 1 | 0 | 1 | 0 | 0 | 0 | 2 | **PASS** | NOT_VERIFIED[设计] |
| C20 | 2×NON_SUCCESS 不同身份 + 降级 | 2 | 2 | 0 | 2 | 0 | 0 | 0 | 0 | 0 | 2 | **PASS** | NOT_VERIFIED[设计] |
| C21 | 2×NON_SUCCESS 同身份异内容 | 2 | 2 | 0 | 2 | 0 | 0 | 0 | 0 | 1 | 3 | **PASS** | NOT_VERIFIED[设计] |

- 21/21 样例投影计数 **PASS**；逐样例恒等式 (1)(2)(3)、`proj=Nr+cf`、适用时 (4) 全部成立（`test_identities_hold_for_every_case`）。
- C10/C21 各产出 **1 个 CONFLICT 项**（`cf=1, proj=3`），冲突为独立合成条目；C16 重复**不**判冲突；`file_conclusion`/分支列全部 NOT_VERIFIED（未实现，未谎报）。
- 边界场景额外覆盖：同身份同内容（重复非冲突）、同身份异内容（冲突）、不同身份同内容（非冲突）、不同状态同身份（冲突）、多冲突组（`cf=2`）、零记录、非 Mapping 记录（`N_not_attempted`）。

---

## 6. 真实执行命令、退出码与测试原始输出

### 6.1 执行前基线（改动前）
```
$ python -m unittest tests.test_understanding_projection tests.test_parser_contract -v
Ran 29 tests in 46.281s
OK
```
退出码 0。

### 6.2 完整测试套件（改动后）
```
$ python -m unittest discover -s tests -p "test_*.py" -v
Ran 513 tests in 428.296s
FAILED (failures=2, skipped=3)
```
退出码 1。原始输出存档：`/tmp/usit_full_suite.log`。

### 6.3 失败分析（2 项，均为**既有**失败，非本工单引入）
- `test_p0710zxrxf_guard_r2.FrozenHashGuardR2Tests.test_guard_real_repo_authorized_maintenance_tolerated`
- `test_p0710zxrxf_trust_boundary.ZXRXF7FrozenCompatibility.test_zrxf7_hashes_recorded`
- 二者断言信息一致：`{'usit/graph.py': True, 'usit/scanner.py': False, 'usit/safeio.py': True, 'tests/test_graph_validation.py': True, 'tests/test_p073_budget_race.py': True, 'tests/test_p0710z_snapshot_trust.py': True, 'tests/test_p0710zrf_descriptor_binding.py': True}` —— **唯一漂移文件为 `usit/scanner.py`**（与 `PRE_SHA256.json` 基线不符）。

**非本工单引入的证明**：
1. 这两个测试只校验 `ZXRXF7_WATCH = {usit/graph.py, usit/scanner.py, usit/safeio.py, tests/test_graph_validation.py, tests/test_p073_budget_race.py, tests/test_p0710z_snapshot_trust.py, tests/test_p0710zrf_descriptor_binding.py}` —— **不含**本工单修改的 5 个文件，故本工单改动**在数学上不可能**影响该校验结果。
2. 唯一漂移的 `usit/scanner.py` 本工单**从未修改**（且属越界禁改项）；其哈希 `a009b8db…` 为进入工单前的原值。
3. PRE 基线中记录的 `parser_contract.py = 3f7a23c8…` 正是本工单开始前的原哈希，证明该基线早于本工单生成，`scanner.py` 漂移**先于**本工单存在。
4. 本工单新增/修改的全部测试在套件中均 `... ok`（计数/异常/C1–C21/fallback 全 PASS）。

> 结论：2 项失败为**既有、越界**（`scanner.py 冻结哈希漂移`），需**独立授权修改 scanner.py 或更新其基线**方可处理，本工单无权亦未处理。

### 6.4 跳过（3 项，均为环境条件性，源码内已标注 NOT TESTED）
- `test_zb5_permission_denied_not_tested` — 宿主不可构造 permission-denied。
- `test_zb9_mid_read_race_not_tested` — 无确定性 mid-read race 注入。
- `test_ts_lib_root_junction_to_outside_is_contained` — 需 `USIT_TEST_TSLIB_JUNCTION=1`。

### 6.5 本工单相关测试结果（全部 PASS）
- `tests/test_understanding_projection.py`：原 20 项 + 新增 `RecordClassificationCountTests`(10) + `ExceptionCaptureTests`(5) 全 PASS。
- `tests/test_s1c_cases.py`：13 项全 PASS（含 C1–C21 数据驱动、恒等式、边界、设计列未实现断言）。
- `tests/test_parser_contract.py.GraphV1EvidenceFallbackTests`：9 项全 PASS（真实透传/缺失/空/非Mapping/缺节点ID/边回退/无效状态/下游非成功）。

---

## 7. 异常路径与边界输入测试证据

- **构造异常注入 → `N_construct_fail`**：候选记录注入 `ValueError` → `N_construct_fail=1, N_construct_ok=0, N_unexpected_error=0, projected_items=1`，降级项非成功、无 conclusion（`test_candidate_construction_failure_counted_as_construct_fail`）。
- **意外异常 → `N_unexpected_error`**：非候选记录注入 `RuntimeError` → `N_unexpected_error=1`，且**不**被重复计入 `N_evidence_none`（互斥）（`test_non_candidate_exception_counted_as_unexpected_error`）。
- **批次级失败不伪装逐记录**：非序列入参抛 `TypeError`（循环前），不产出 `ProjectionResult`（`test_batch_level_failure_is_not_disguised_as_per_record_count`）；`_detect_conflicts` 失败照常上抛（`test_conflict_detection_failure_is_not_a_per_record_count`）。
- **fallback 不伪造证据**：缺失/空/非 Mapping/缺节点 ID/边无路径+源证据损坏，均不崩溃、不合成 `graph_node_id`、经 `project_records` 投影为**非成功**（`GraphV1EvidenceFallbackTests`）。
- **负向/不变量**：非成功态不计入 success；`verified_fact` 恒 0；投影不产出 VERIFIED_FACT；输入不可变。

---

## 8. 兼容性与回归

- `adapt_graph_v1` 真实路径与 `valid_v1.json` 输出**字节级不变**（4 记录、`validate_record` 全过、无伪造、diagnostics 空）。
- `project_records` 既有 8 个 stats 键语义不变，仅**追加** `N_*`/`Nr` 键；`to_dict()` 结构仅多出 stats 子键，既有消费方（当前无生产消费方）不受影响。
- 完整套件**未引入任何新失败**（§6.3 证明 2 项失败既有且越界）；未删除/跳过/降低任何旧测试或断言。

---

## 9. 固定门禁复核（原值保持，未因底层测试通过而解除）

| 门禁 | 值 | 说明 |
|---|---|---|
| `F1` | `NOT_READY_TO_CLOSE` | 保持 |
| `P0.8` | `HOLD` | 保持 |
| `F2` | `STOP` | 保持 |
| `历史授权连续性` | `UNRESOLVED` | 保持 |
| `安全信任链` | `BLOCKED / NOT_VERIFIED` | 保持 |
| `verified_fact` | `0` | 投影不产出 VERIFIED_FACT，保持 0 |
| `ExecutionRecord` | `NOT_RUN` | 指 S1-C 生产执行记录；本工单仅跑**单元测试**、未接线、未对真实项目执行 S1-C，故保持 NOT_RUN |
| `summary_verification` | `NOT_CHECKED` | 保持 |
| 受约束条目 `complete` | `False` | 保持 |
| `BLOCKER-1`（投影未接线） | **未解除** | S1-C 仍未接入 scanner/cli |
| `BLOCKER-2`（adapt_graph_v1 fallback 过宽） | **部分缓解，未正式解除** | fallback 伪造已移除并测试，但正式解除需独立授权与接线后复核 |

> 本工单未宣称系统具备可信项目理解/完整测试/自动修复能力；S1-C 全阶段未完成。

---

## 10. 残余风险与未测试项

1. **既有越界失败**：`usit/scanner.py` 冻结哈希漂移（2 项测试）——需独立授权修改 scanner.py 或更新 `PRE_SHA256.json` 基线；本工单越权禁改，未处理。
2. **设计-only 列 NOT_VERIFIED**：`file_conclusion`/分支决策未实现、未测试（属 REVIEW-07 §4 设计层），未谎报为通过。
3. **S1-C 未接线**：`project_records` 仍未接入生产链路；`N_*` 计数尚未在真实扫描中端到端验证。
4. **跨扫描 duplicate 稳定性**：仍 UNRESOLVED（设计层），本工单未涉及。
5. `tests/test_parser_contract.py` 的 PRE 哈希未纳入初始批次（§3 披露），其变更为纯追加。

---

## 11. 最终结论

**PASS-WITH-ISSUES**

- **Task A（adapt_graph_v1 fallback）**：PASS。移除合成 `graph_node_id` 伪造；区分真实/缺失/空/类型错误证据并保留 diagnostic；非 Mapping 不再崩溃；真实路径字节级不变；9 项独立测试 PASS。
- **Task B（逐记录互斥分类计数）**：PASS。8 个 `N_*` + `Nr` 全部实现，互斥穷尽，恒等式 (1)–(5) 由自动化断言验证；异常仅归属单条记录，批次级失败不伪装；既有 JSON 语义不变。
- **Task C（C1–C21 独立测试）**：PASS。21/21 投影计数 PASS + 六类边界 + 注入异常 + fallback 不伪造；设计-only 的 `file_conclusion`/分支诚实标 NOT_VERIFIED。
- **完整套件**：513 项，2 项**既有越界**失败（scanner.py 冻结哈希，非本工单引入、无权修）、3 项环境跳过；本工单相关测试全 PASS，**零新增失败**。

> **ISSUES（不阻断本工单验收，但需记录/后续独立授权）**：① `scanner.py` 冻结哈希漂移的 2 项既有失败（越界）；② `file_conclusion`/分支未实现（NOT_VERIFIED）；③ S1-C 仍未接线、计数未端到端验证。
>
> 本工单仅授权投影机制修正与独立测试；**未实施 S1-C 全链路接线，未解除任何固定门禁或 BLOCKER**。遇越界需求（如修 scanner.py）已 STOP 并在本报告记录，待独立授权。
