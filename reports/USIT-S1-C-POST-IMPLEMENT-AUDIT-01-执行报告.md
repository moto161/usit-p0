# USIT-S1-C-POST-IMPLEMENT-AUDIT-01｜实施后独立审计与接线准入评估 执行报告

- **工单编号**：USIT-S1-C-POST-IMPLEMENT-AUDIT-01
- **执行时间**：2026-10-20（GMT+8）
- **工作区绝对路径**：`C:\Users\Administrator\WorkBuddy\2026-10-15-22-09-47\usit_p0`
- **执行模式**：**严格 READ-ONLY**。禁止修改源码/测试/配置/基线/既有报告；禁止接线；禁止真实项目扫描/构建/登录/外部网络。
- **审计对象**：`USIT-S1-C-IMPLEMENT-01` 的实施结果（投影机制修正 + 独立测试）。
- **审计依据**：IMPLEMENT-01 报告、REVIEW-09、READINESS-01，及本次对源码/测试的**独立实读**（非仅摘要）。
- **门禁状态**：本工单为只读审计，**不解除任何门禁或 BLOCKER**；`verified_fact=0`、`ExecutionRecord=NOT_RUN` 等原值见 §9。

> 说明：本工单允许**运行单元测试**作为只读核查证据（不改源码/基线）；未运行任何真实项目扫描/构建/登录/网络。

---

## 1. 执行前保护（STOP 条件核对）

| 核对项 | 结果 |
|---|---|
| 工作区路径/文件清单 | ✅ `usit_p0` 存在 |
| 当前源码哈希 vs IMPLEMENT-01 报告 POST 哈希 | ✅ **全部一致**（见 §2），无本次审计之外的新变更 |
| 是否有无法区分的用户改动 | ✅ 无；哈希与 IMPLEMENT-01 结束时逐一相符 |
| 是否需要覆盖/回滚/清理/修改 | ❌ 否；全程只读，未触发 STOP |

---

## 2. 核查文件与哈希（当前 == IMPLEMENT-01 POST，证明无审计外变更）

| 文件 | 当前 sha256 | 与 IMPLEMENT-01 报告 |
|---|---|---|
| `usit/understanding_projection.py` | `8bb0fb6569a318357bd3079a923f980c52bd0d085354380e8347b89a33ecb226` | ✅ 一致 |
| `usit/parser_contract.py` | `482400f95d21df37d655b7e951490d35bcee6d98d04a8dd4640484a83314dd87` | ✅ 一致 |
| `tests/test_understanding_projection.py` | `010c1ae35482d31882a467c7728c436d9290dc3a3525321b3c66517efd6c5ac5` | ✅ 一致 |
| `tests/test_parser_contract.py` | `6739d743fbd31fa82ab8658039c9a38c2903fb5904335047f07aa13c112af71e` | ✅ 一致 |
| `tests/test_s1c_cases.py` | `f080f09e…`（14377 字节） | ✅ 与报告记录字节数一致 |
| `usit/understanding_model.py` | `4668760b…1f690e7` | ✅ 未改 |
| `usit/schema_adapter.py` | `c44f60e4…6a4def44` | ✅ 未改 |
| `usit/scanner.py` | `a009b8dbb1329c2de030089ea4b441288812cbd5dbaf5eead4bc6b5c472be416` | ✅ 未改（= 报告 NOT MODIFIED 值） |
| `usit/cli.py` | `879a3735…38b8d9` | ✅ 未改 |

命令：`sha256sum <files>`（当前工作区）。

---

## 3. A. 计数实现独立审计（代码级推导，不以测试通过替代）

### 3.1 核查的实际代码位置
- `project_records`：`understanding_projection.py:416-484`
- 逐记录循环 + 异常捕获：`:442-453`
- `_intended_record_category`：`:366-388`
- `_degraded_projected_item`：`:391-413`
- 批次级前置校验：`:436-437`
- 冲突生成（循环外）：`:455` `conflicts = _detect_conflicts(projected, records)`
- `project_record`：`:152-248`；非 Mapping 短路 `:158`
- `_build_evidence`：`:122-149`（`:125-126` 非候选返回 None）
- `_detect_conflicts`：`:251-298`（冲突条件 `:276` `len(entries)>1 and len(distinct)>1`）

### 3.2 每条原始记录恰好一个终态（互斥 + 穷尽）
循环体（`:442-453`）每次迭代：`try project_record` 成功走 `else`、抛错走 `except`；两个分支各自**恰好一次** `category_counts[...] += 1` 且**恰好 append 一个 item**。无 `continue`/条件跳过/重复处理。故：
- 每条原始记录 → 恰好 1 个投影项 ⇒ `Nr = len(projected) − len(conflicts) = len(records) = N_raw`（1:1，无丢计）。
- 每条原始记录 → 恰好 1 次计数递增（无双计）。

### 3.3 五类异常/边界路径审计（双计/漏计专项）

| 路径 | 代码行为 | 归属终态 | 双/漏计 |
|---|---|---|---|
| 候选构造异常 | `except`(`:445`)：`intended==_CONSTRUCT_OK` → `_CONSTRUCT_FAIL`(`:447`) +1 | `N_construct_fail` | 无双计（仅一分支一增） |
| 非候选异常 | `except`：`intended==_EVIDENCE_NONE` → `_UNEXPECTED_ERROR` +1 | `N_unexpected_error` | 正确**不**计入 `N_evidence_none`（互斥保持） |
| 非 Mapping 记录 | `project_record:158` 返回（不抛）→ `else` → `_NOT_ATTEMPTED` | `N_not_attempted` | 无漏计 |
| 批次级异常（非序列入参） | `:436-437` 循环前 `raise TypeError`，无 ProjectionResult | 不计入任何逐记录计数 | 正确（不伪装） |
| 冲突生成异常 | `_detect_conflicts` 在 `:455`（循环外、try 之外） | 上抛为批次错误 | 正确（不伪装） |

补充：`for record in records` 的迭代器 `__next__` 在 try 之外，迭代器异常按批次错误上抛，不计入逐记录。`except Exception`（非 BaseException）——KeyboardInterrupt/SystemExit 上抛，合理。

### 3.4 独立推导的恒等式（纯代码推导）
记基础计数 na=N_not_attempted, ok=N_construct_ok, fail=N_construct_fail, none=N_evidence_none, ue=N_unexpected_error；`N_candidate=ok+fail`(`:475`)，`N_non_candidate=none`(`:476`)：
1. `N_raw = N_candidate + N_non_candidate + N_not_attempted + N_unexpected_error` = (ok+fail)+none+na+ue = na+ok+fail+none+ue = N_raw ✅
2. `N_candidate = N_construct_ok + N_construct_fail` ✅（定义）
3. `N_non_candidate = N_evidence_none` ✅（定义）
4. 简化式 `N_raw = N_construct_ok + N_construct_fail + N_evidence_none`：仅当 na==0 且 ue==0 成立 ✅
5. `proj = Nr + cf`：`projected_items = n_raw + len(conflicts)`，`conflict`(stat)=len(conflicts)=cf，`Nr=n_raw` ⇒ `projected_items = Nr + conflict` ✅

### 3.5 关键独立结论
- `project_record`(`:152-248`) 与 `_build_evidence`(`:122-149`) 对**任意输入均不抛错**（`_classify` 纯判断；`_build_evidence` 用硬编码安全值构造 `EvidenceReference`；`ConclusionRecord`/`UncertaintyRecord` 均用安全值）。故生产链路中 `except` 分支**永不进入**，`N_construct_fail`/`N_unexpected_error` 为**实测结构性 0**（机制存在、仅注入可达），非虚构未知。

**A 结论：PASS**（互斥穷尽、五路径无双/漏计、恒等式代码级成立、计数诚实）。

---

## 4. B. fallback 信任边界审计

### 4.1 实际代码位置
- `_classify_graph_evidence`：`parser_contract.py:95-119`
- `adapt_graph_v1`：`:122-161`；evidence 透传 `:135,:159`；source_node 回退 `:137-140`；状态校验 `:141-144`；诊断 `:145-147`

### 4.2 信任边界逐项
| 检查项 | 证据 | 结论 |
|---|---|---|
| 真实 evidence 原样保留 | `:119` 返回 `dict(raw)`，`:159` 原样输出；实测 valid_v1.json 4 记录字节级不变、`validate_record` 全过 | PASS |
| 诊断可追溯 | `:146-147` 追加 `{"code":<missing/empty/malformed_evidence:type>,"graph_node_id":<id>}` | PASS |
| 无合成字段被误当真证据 | `graph_node_id` **仅**出现在 diagnostic(`:147`)，**从不**进入 evidence 字段(`:159`)；下游 `_build_evidence` 只读 `record["evidence"]`（缺失/损坏时为 `{}`→非候选→UNKNOWN） | PASS |
| 异常不吞/不转成功 | `_classify_graph_evidence` 全类型不抛；缺失/损坏 evidence → `{}` → 下游 UNKNOWN（实测 missing-evidence 节点全 non-success，`N_evidence_none=1`） | PASS |
| 状态不升级 | `:141-144` 非 RESOLUTION_STATUSES → "UNKNOWN" | PASS |

### 4.3 观测（非阻断）
- `adapt_graph_v1` 仍假设 graph 的 node/edge 本身是 Mapping（`:132 node.get("id")`、`:135 item.get(...)`）；若 graph 结构含非 Mapping 节点会抛 `AttributeError` 并**上抛**（既不吞也不转成功）。此为**既有行为**（原版同样如此），非 IMPLEMENT-01 引入；真实 scanner 产出的 node/edge 均为 dict。属接线设计需保证的输入前提（见 §8）。

**B 结论：PASS**（证据信任边界正确，无伪造、无吞异常、无升级为成功；graph 结构健壮性为既有观测项）。

---

## 5. C. 测试真实性审计

### 5.1 C1–C21 一一对应
- 专用模块 `tests/test_s1c_cases.py`；`_CASES` 表(`:87-160`) 含 **21 条** C1–C21，每条 = (编号, 描述, builder 输入, 预期计数, 设计注记)。
- `test_each_case_matches_expected_projection_counts`(`:164-168`) 逐条 `assertEqual(actual, expected)`（subTest 定位到 case）。
- 预期计数为**硬编码**（源自 REVIEW-09 §8 设计推导），**非**从实现输出回填 ⇒ 无循环自证。

### 5.2 C1–C21 对照表（审计实测复算，全部 PASS）
| # | 输入 | Nr|Nc|Nnc|Nok|Nfail|Nnone|Nna|Neu|cf|proj | 审计结果 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| C1 | RESOLVED/非空dict | 1|1|0|1|0|0|0|0|0|1 | PASS |
| C2 | RESOLVED/仅graph_node_id | 1|1|0|1|0|0|0|0|0|1 | PASS |
| C3 | NON_SUCCESS/fallback | 1|1|0|1|0|0|0|0|0|1 | PASS |
| C4 | 空文件→0 | 0|0|0|0|0|0|0|0|0|0 | PASS |
| C5 | 0记录无符号 | 0|…|0 | PASS |
| C6 | 0记录有符号无记录 | 0|…|0 | PASS |
| C7 | RESOLVED+降级 | 1|1|0|1|0|0|0|0|0|1 | PASS |
| C8 | NON_SUCCESS+降级 | 1|1|0|1|0|0|0|0|0|1 | PASS |
| C9 | NON_SUCCESS/fallback | 1|1|0|1|0|0|0|0|0|1 | PASS |
| C10 | RESOLVED+NON_SUCCESS同身份 | 2|2|0|2|0|0|0|0|1|3 | PASS |
| C11 | RESOLVED+orphan | 1|1|0|1|0|0|0|0|0|1 | PASS |
| C12 | RESOLVED+diag越界 | 1|1|0|1|0|0|0|0|0|1 | PASS |
| C13 | RESOLVED+path∉manifest | 1|1|0|1|0|0|0|0|0|1 | PASS |
| C14 | RESOLVED+line非正 | 1|1|0|1|0|0|0|0|0|1 | PASS |
| C15 | RESOLVED+TestSpec缺必填 | 1|1|0|1|0|0|0|0|0|1 | PASS |
| C16 | 2×RESOLVED相同(重复) | 2|2|0|2|0|0|0|0|0|2 | PASS |
| C17 | NON_SUCCESS+RESOLVED不同身份 | 2|2|0|2|0|0|0|0|0|2 | PASS |
| C18 | RESOLVED/evidence非Mapping | 1|0|1|0|0|1|0|0|0|1 | PASS |
| C19 | RESOLVED非Mapping+NON_SUCCESS候选 | 2|1|1|1|0|1|0|0|0|2 | PASS |
| C20 | 2×NON_SUCCESS不同身份+降级 | 2|2|0|2|0|0|0|0|0|2 | PASS |
| C21 | 2×NON_SUCCESS同身份异内容 | 2|2|0|2|0|0|0|0|1|3 | PASS |

`file_conclusion`/分支列：**NOT_VERIFIED**（设计层未实现；`S1CDesignOnlyColumnsNotImplementedTests:267-273` 断言输出无 `file_conclusion`/`branch` 键，未谎报）。

### 5.3 真实性质疑项核查
| 质疑 | 核查 | 结论 |
|---|---|---|
| 仅验证自身假设/循环 | 预期值硬编码自设计，非实现回填；黑箱 `assertEqual(实现输出, 独立预期)` | 无循环，PASS |
| 缺负向断言 | `test_simplified_identity_invalid_when_not_attempted_present`(`:273-279` assertNotEqual)、`test_duplicate_case_emits_no_conflict`、`test_different_identity_same_content_is_not_conflict`、`test_non_mapping_records_are_not_attempted`(N_candidate==0/N_evidence_none==0)、异常降级项非成功/无 conclusion | 负向充分，PASS |
| 异常注入是否真触发目标分支 | `mock.patch.object(understanding_projection,"project_record",side_effect=flaky)` 替换模块全局（`project_records:444` 调用解析到该全局）⇒ 真实 except 分支 + 真实分类/递增代码以真实异常执行；候选→`N_construct_fail=1`、非候选→`N_unexpected_error=1`（测试通过即为分支真触发证据） | PASS |
| 共享实现伪独立 | 各 case builder 内联独立、无共享可变 fixture | 无伪独立 |

### 5.4 观测（非阻断）
- **投影输入冗余**：C1/C7/C11/C12/C13/C15（均 `[_resolved()]`）、C3/C8/C9（`[_non_success()]`）、C4/C5/C6（`[]`）投影行为相同；其差异在文件级（orphan/diag/manifest/TestSpec/降级/空文件vs无符号），属 NOT_VERIFIED 设计层。投影计数 PASS 为同一断言的重复，透明但未独立覆盖不同投影路径。
- **注入路径生产不可达**：`construct_fail`/`unexpected_error` 仅注入可达（生产 project_record 不抛），测试证明处理逻辑、生产为结构性 0（已在 §3.5/IMPLEMENT-01 披露）。

**C 结论：PASS**（C1–C21 一一对应、无循环、负向充分、注入真触发目标分支；冗余与注入可达性为已披露观测）。

---

## 6. D. 两项冻结哈希失败归因及证据

### 6.1 失败项
- `test_p0710zxrxf_guard_r2.FrozenHashGuardR2Tests.test_guard_real_repo_authorized_maintenance_tolerated`
- `test_p0710zxrxf_trust_boundary.ZXRXF7FrozenCompatibility.test_zrxf7_hashes_recorded`
- 断言信息：`{'usit/graph.py': True, 'usit/scanner.py': False, 'usit/safeio.py': True, ...}`（唯一 False = scanner.py）。

### 6.2 证据（只读复核）
- 守卫基线 `outputs/p0710zxrxf/PRE_SHA256.json`（**文件时间 Oct 17 17:19**，早于本工单 Oct-20 会话）记录 `usit/scanner.py = c70a1a4baf3a6a17cb7ec0ed4748db7194d70e3008ab32f0ac05e3e55f797912`。
- 当前 `usit/scanner.py = a009b8db…2be416` ⇒ **已漂移**。
- 其余 6 个监视文件（graph.py/safeio.py/4 测试）当前哈希与基线**逐一相符** ⇒ 孤立单文件漂移。
- 监视集 `ZXRXF7_WATCH`（`test_p0710zxrxf_trust_boundary.py:79-89`）= {graph.py, scanner.py, safeio.py, test_graph_validation.py, test_p073_budget_race.py, test_p0710z_snapshot_trust.py, test_p0710zrf_descriptor_binding.py}，**不含** IMPLEMENT-01 改动的 5 个文件。
- scanner.py 当前哈希 == IMPLEMENT-01 报告的 "NOT MODIFIED" 值，且不在其改动集内。

### 6.3 归因结论
"既有失败、非本工单引入"结论**证据充分**：
1. 基线时间（Oct-17）早于会话（Oct-20）——基线先于本工单存在；
2. scanner.py 已从该基线漂移，且 IMPLEMENT-01 未改 scanner.py（不在改动集、当前哈希=报告值）；
3. 孤立单文件漂移（6/7 相符），符合"某前序授权会话改了 scanner.py 但未刷新冻结基线"的典型基线陈旧，而非本工单扩散性破坏。

**残留严谨性说明（非阻断）**：初始哈希批次未捕获"工单前 scanner.py 哈希"，故"scanner.py 在 IMPLEMENT-01 开始前已为 a009b8db"依赖于"不在改动集 + 无编辑动作 + 基线早于会话"的间接证据链，而非捕获的前置哈希。证据强度足够支撑归因，但此项标 **NOT_VERIFIED（前置哈希未捕获）**以完全透明。

**D 结论：PASS**（既有失败归因成立，证据充分；未更新基线、未改 scanner.py）。

---

## 7. E. 接线准入评估

### 7.1 现有调用关系（grep 实证）
- `adapt_graph_v1` **已接线**：`scanner.py:8` 导入、`:1059` `parser_contract=adapt_graph_v1(graph)`。
- `understanding_projection`（`project_records`/`project_record`）**未接线**：`usit/` 内除自身模块外无任何导入/调用；`scanner.py`/`cli.py` 均不引用。⇒ BLOCKER-1 仍成立。

### 7.2 输入/输出契约
- 输入：`project_records(records)` 期望「parser-contract 记录 Mapping 序列」。scanner 已构建 `parser_contract['records']`（adapt_graph_v1 + 追加的 js_ts/analysis 诊断记录，均为 dict）。契约**兼容**，但需在接线设计中校验追加诊断记录（`scanner.py:1062-1072`）形状一致。
- 输出：`ProjectionResult(items, stats)`（含 `N_*`/`Nr`）。**当前无消费方**；扫描报告中无既定落点/schema 版本 ⇒ 需设计定义。

### 7.3 接线前仍缺少的条件（准入清单）
1. **file_conclusion / 分支决策层未实现**：REVIEW-07 §4 设计层，`project_records` 不产出（§5.3 NOT_VERIFIED）；仅接投影不会得到文件级结论——**最大缺口**。
2. **接线授权**：修改 `scanner.py`/`cli.py` 需独立授权工单（IMPLEMENT-01 与本审计均不授权）。
3. **冻结基线再生**：`scanner.py` 受 `ZXRXF7_WATCH` 冻结且已漂移（§6）；接线改 scanner.py 会持续触发守卫失败，须在授权下**权威再生** `PRE_SHA256.json`。
4. **端到端验证**：真实项目扫描跑通 `parser_contract→project_records`（当前 `NOT_RUN`）。
5. **输出消费方/schema 落点**：在 report/manifest 中定义投影输出的位置与版本。
6. **追加诊断记录契约校验**：`scanner.py:1062-1072` 记录需满足投影输入契约。

### 7.4 准入结论
- **投影机制本身（A 计数 + B fallback + C 测试）已独立验证为正确、可审计**，满足**进入后续接线设计**的条件。
- **接线实施**仍被上述 6 项条件阻断（尤其 file_conclusion 层缺失 + scanner.py 冻结基线需授权再生 + 端到端未跑）。

**E 结论：READY_FOR_DESIGN_REVIEW（机制侧）/ 接线实施 BLOCKED（待独立授权与 §7.3 条件）**。

---

## 8. 未验证项汇总（NOT_VERIFIED / NOT_RUN）

| 项 | 状态 | 说明 |
|---|---|---|
| `file_conclusion` / 分支决策 | NOT_VERIFIED | 设计层未实现，`project_records` 不产出 |
| 端到端扫描（parser_contract→projection） | NOT_RUN | 未授权、未执行真实扫描 |
| `N_construct_fail`/`N_unexpected_error` 生产路径 | NOT_VERIFIED（生产不可达） | 机制已实现并经注入测试；生产 project_record 不抛，结构性 0 |
| scanner.py 工单前前置哈希 | NOT_VERIFIED | 初始批次未捕获（§6.3），归因靠间接证据链 |
| 跨扫描 duplicate 稳定性 | NOT_VERIFIED | 设计层 UNRESOLVED，本审计未涉及 |
| 追加诊断记录（scanner.py:1062-1072）投影契约符合性 | NOT_VERIFIED | 需接线设计校验 |

---

## 9. 固定门禁复核（原值保持，未因测试通过而解除）

| 门禁 | 值 |
|---|---|
| `F1` | `NOT_READY_TO_CLOSE` |
| `P0.8` | `HOLD` |
| `F2` | `STOP` |
| `历史授权连续性` | `UNRESOLVED` |
| `安全信任链` | `BLOCKED / NOT_VERIFIED` |
| `verified_fact` | `0` |
| `ExecutionRecord` | `NOT_RUN`（未对真实项目执行 S1-C） |
| `summary_verification` | `NOT_CHECKED` |
| 受约束条目 `complete` | `False` |
| `BLOCKER-1`（投影未接线） | **未解除**（§7.1 grep 证实未接线） |
| `BLOCKER-2`（adapt_graph_v1 fallback） | 机制已修正并测试；正式解除待接线后复核，本审计不解除 |

---

## 10. 最终结论

**READY_FOR_DESIGN_REVIEW**

- **A 计数实现：PASS**——逐记录恰一终态（互斥穷尽），五类异常/边界路径无双计漏计，恒等式 (1)–(5) 经代码级独立推导成立；`N_construct_fail`/`N_unexpected_error` 为实测结构性 0（诚实）。
- **B fallback 信任边界：PASS**——真实证据原样保留、诊断可追溯、`graph_node_id` 绝不进入 evidence、无吞异常/无升级成功；graph 结构健壮性为既有观测项。
- **C 测试真实性：PASS**——C1–C21 一一对应、预期硬编码无循环、负向断言充分、异常注入真触发目标分支；冗余与注入可达性已披露。
- **D 失败归因：PASS**——2 项失败为既有、越界（scanner.py 冻结哈希自 Oct-17 基线漂移，非本工单引入），证据充分；未改基线/scanner.py。
- **E 接线准入：READY_FOR_DESIGN_REVIEW**——投影机制满足进入接线**设计**的条件；接线**实施**仍 BLOCKED（§7.3 六项，尤其 file_conclusion 层缺失 + scanner.py 冻结基线需授权再生 + 端到端未跑）。

> **本报告为只读审计结论，不构成接线或源码实施授权。** 未修改任何源码/测试/配置/基线/既有报告；未接线；未解除任何门禁或 BLOCKER；未运行真实项目扫描/构建/登录/网络。固定门禁全原值保持。进入接线设计与实施须另行独立授权，并先满足 §7.3 条件（含在授权下权威再生 `PRE_SHA256.json` 基线）。

---

**审计边界最终声明**：本工单仅授权实施后只读审计与接线准入评估。任何源码修改、接线、基线更新、构建、安装、登录、网络访问或 Android/真实扫描均超出授权范围。
