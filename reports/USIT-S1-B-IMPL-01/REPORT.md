# USIT-S1-B-IMPL-01｜Python AST 降级可追溯性最小修复＋回归验收 执行报告

- **工单编号**：USIT-S1-B-IMPL-01
- **执行时间**：2026-10-19 20:26–20:37 (GMT+8)
- **工作区绝对路径**：`C:\Users\Administrator\WorkBuddy\2026-10-15-22-09-47\usit_p0`（沿用当前工作区，**未切换副本**）
- **执行器**：WorkBuddy｜**实际执行模式**：授权范围内最小修改＋测试＋取证
- **解释器**：`…\Python312\python.exe`（`Python 3.12.10`）
- **授权依据说明**：用户在消息开头明确声明"用户已明确批准本工单实施"并要求"现在开始执行"；工单正文末尾仍保留 `PENDING_USER_APPROVAL` 模板字样。二者并存，**本执行依据用户显式批准语句**，此歧义如实记录于第 6 节。

## 1. PRE 基线、初始状态与 PRE/POST 核验

**前置检查（全部满足，未触发 STOP）**

| 项 | 结果 |
|---|---|
| 工作区绝对路径 | `…\2026-10-15-22-09-47\usit_p0`，确认未切换 |
| Python 版本 | `Python 3.12.10` |
| 前置工单 `S1-B-PRE` 报告 | 存在（REPORT.md 10005 B、PRE/POST 清单齐全） |
| 基线连续性 | `usit/scanner.py` PRE 值 `c70a1a4baf3a6a17…` 与 S1-B-PRE 基线一致 |
| 未提交修改/冲突 | 无（文件哈希可复现） |

**初始测试结果（修改前）**：引用 S1-B-PRE 本工作区实测——`test_python_resolver` 6/6、`test_usit` 14/14、`test_understanding_projection` 22/22、`test_graph_validation` 19/19、`test_parser_contract` 7/7、`selftest` 6/6，**全部 rc=0，无既有失败**。PRE 哈希已证明基线连续，故不存在"原有失败"需区分。

**PRE/POST 哈希核验**

| 文件 | PRE | POST | 判定 |
|---|---|---|---|
| `usit/scanner.py` | `c70a1a4baf3a6a17…` | `12f688261feea6cb…` | **已修改（授权范围内）** |
| `tests/test_s1b_degradation.py` | — | `ac7f77fe1c8e9272…` | **新增（授权范围内）** |
| 其余 63 项（含保护文件） | — | 不变 | **63 项 `IDENTICAL`，`CHANGED=0`** |

证据：`PRE_SHA256.txt`、`POST_SHA256.txt`、`PRE_POST_DIFF.txt`、`PROTECTED_CHECK.txt`。

## 2. 实际修改文件及每项改动原因

**修改 1：`usit/scanner.py`**（83950 → 86198 字节，1188 行）

| 改动 | 位置 | 原因 |
|---|---|---|
| 新增 `_degradation_metadata(row, lang)` | `analyze_file` 之前 | 集中计算 row 级降级元数据，避免在 `analyze_file` 内散落语义 |
| 在 `return row` 前挂载 `row['analysis_degradation']` | `analyze_file` 末尾 | 以**新增可选字段**方式暴露降级信息，**不动 `symbols[]`/`calls[]`** |

字段语义（与真实执行路径一致）：

| 场景 | degraded | reason | method | resolution_status | diagnostics_ref |
|---|---|---|---|---|---|
| Python AST 成功 | `False` | `None` | `python-ast` | `RESOLVED` | `None` |
| Python 语法错误→正则回退 | `True` | `PYTHON_SYNTAX_ERROR` | `regex-candidate` | `UNKNOWN` | 指向真实诊断索引 |
| 语义解析器不可用（如 JS/TS 文本候选） | `True` | `SEMANTIC_PARSER_UNAVAILABLE` | `regex-candidate` | `UNKNOWN` | 指向真实诊断索引 |
| 不支持语言 | `True` | `UNSUPPORTED_LANGUAGE_NO_SEMANTIC_PARSER` | `discovery-only` | `UNKNOWN` | 索引或 `None` |

**关键约束落实**：① 该字段仅表示**分析方法状态**，不表示"每行结论均已验证"（docstring 明示，AST 成功仍可能有 unresolved/低置信调用）；② 未数值化 `confidence`（Python 字符串量纲与 JS/TS 数值量纲不一致，未强行统一）；③ `diagnostics_ref` 为诊断列表索引，**不依赖临时绝对路径**，确定性成立。

**未修改**：`symbols[]`、`calls[]` 字段结构与 schema 版本；`understanding_model.py`、`understanding_projection.py`、图谱验证器、其他解析器与业务模块；任何依赖、配置、数据库、凭据、环境变量、PATH；历史报告。

**修改 2（新增）**：`tests/test_s1b_degradation.py`（134 行 / 5665 字节）

## 3. 逐项测试命令、真实退出码与结果

统一前缀：`PYTHONDONTWRITEBYTECODE=1 python -B`，工作目录=项目根。

| # | 命令 | rc | 测试数 | 状态 |
|---|---|---|---|---|
| 1 | `-m unittest -v tests.test_s1b_degradation` | **0** | **10 tests — OK** | **PASS**（新增） |
| 2 | `-m unittest -v tests.test_python_resolver` | 0 | 6 tests — OK（12.3s） | PASS |
| 3 | `-m unittest -v tests.test_usit` | 0 | 14 tests — OK（38.0s） | PASS |
| 4 | `-m unittest -v tests.test_understanding_projection` | 0 | 22 tests — OK | PASS |
| 5 | `-m unittest -v tests.test_understanding_model` | 0 | 34 tests — OK | PASS |
| 6 | `-m unittest -v tests.test_schema_adapter` | 0 | 28 tests — OK | PASS |
| 7 | `-m unittest -v tests.test_graph_validation` | 0 | 19 tests — OK | PASS |
| 8 | `-m unittest -v tests.test_parser_contract` | 0 | 7 tests — OK（71.2s） | PASS |
| 9 | `-m usit selftest` | 0 | `RESULT: PASS (6/6)` | PASS |

**无 FAIL、无 ERROR、无 SKIP**；原始 stdout/stderr 存于 `t30`–`t38` 文件。所有命令以当前模块结构为准，均真实存在并执行成功（**无 NOT_RUN、无替代命令**）。

## 4. 新增测试覆盖矩阵（A–I 与兼容性）

| 项 | 场景 | 验证方式（非仅验证字段存在） | 结果 |
|---|---|---|---|
| A | 正常 AST 解析 | `degraded=False`、`method='python-ast'` 与实际路径一致 | PASS |
| B | Python 语法错误 | `reason='PYTHON_SYNTAX_ERROR'`；诊断 `code`/`severity`/行号可追溯 | PASS |
| C | 语义解析器不可用 | `reason='SEMANTIC_PARSER_UNAVAILABLE'`，且 `resolution_status != 'RESOLVED'` | PASS |
| D | 降级符号与 row 级诊断关联 | 断言降级符号仍具 `name`/`line`，且 row 级 `degraded=True` 可标识 | PASS |
| E | 未降级不误报 | 干净文件 `degraded=False` 且诊断为空 | PASS |
| F | `diagnostics_ref` 可解析 | 断言为 `int`、在范围内、指向条目 `code=='PYTHON_SYNTAX_ERROR'` | PASS |
| G | 既有结构兼容 | 断言 symbol 键集合恰为 `{name,kind,line,evidence}`；call 含 `callee/line/method/confidence/resolved` | PASS |
| H | 确定性 | 两次分析 `symbols`/`calls`/`analysis_degradation` 全等 | PASS |
| I | 非成功不计成功 | 降级 `resolution_status != 'RESOLVED'`；调用状态非 `RESOLVED` | PASS |
| 附 | 不支持语言 | `reason='UNSUPPORTED_LANGUAGE_NO_SEMANTIC_PARSER'`、`method='discovery-only'` | PASS |

## 5. 保护文件变化、偏差与风险清单

| # | 项 | 说明 |
|---|---|---|
| 1 | 保护/禁改文件 | **63 项 `IDENTICAL`，`CHANGED=0`**；含 `cli.py`、`snapshot.py`、`verification_capture.py`、`understanding_model.py`、`understanding_projection.py`、`parser_contract.py`、`python_resolver.py`、`test_parser_contract.py` 等 |
| 2 | **授权文本歧义（偏差）** | 工单末尾保留 `PENDING_USER_APPROVAL`，与用户开头"已明确批准"并存；依据用户显式批准执行，如实记录 |
| 3 | PRE 清单首次生成超时 | 逐个 `stat` 调用导致 SIGTERM；改用批量 `sha256sum` 重做，**未产生错误数据** |
| 4 | 风险：字段语义被误读 | `resolution_status='RESOLVED'` 仅指分析方法成功，非"结论已验证"；已写入 docstring，但下游仍需按此解读 |
| 5 | 风险：`degraded=True` 覆盖面 | 不支持语言也标 `True`（因无语义解析）；若下游期望"仅回退才 True"，需另行约定 |
| 6 | 未做性能实测 | 新增字段为 O(1) 计算，但**未实测耗时**（标记 UNKNOWN） |

**无意外修改、无越权操作、无自动回滚、无清理现场证据。**

## 6. 尚未解决的问题、未执行项目与后续限制

1. **符号级标记仍未落地**：本次按授权只加 row 级字段；`symbols[]` 条目本身仍无 `method`/`confidence`（保旧 schema）。下游若只看单个符号仍不可辨识——需后续阶段并另行授权。
2. **报告层未逐文件展示降级**：`_render_markdown` 未改（授权范围外）。
3. **未测项（UNKNOWN）**：新增字段对大文件/大工程的耗时影响；`analysis_degradation` 在完整 `scan_project` 端到端产物中的分布（未做端到端扫描取证）。
4. **未执行（NOT_RUN）**：无（工单列出的 8 类回归与新增测试全部实际执行）。
5. **安全信任链仍 `BLOCKED / NOT_VERIFIED`**；**不代表具备 Android 端到端自主测试能力**。

## 7. 固定安全门禁核验（原值保持）

- `F1=NOT_READY_TO_CLOSE`
- `P0.8=HOLD`
- `F2=STOP`
- `历史授权连续性=UNRESOLVED`
- `安全信任链=BLOCKED / NOT_VERIFIED`

**测试全绿不代表安全信任链已验证，也不代表 USIT 具备 Android 端到端自主测试能力。**

## 8. 变更取证与回滚

**变更清单**：修改 1 个（`usit/scanner.py`）、新增 1 个（`tests/test_s1b_degradation.py`）、报告产物（`reports/USIT-S1-B-IMPL-01/`）。**无删除。**

**回滚方案（提出，未执行）**：移除 `analyze_file` 中 3 行（2 行注释 + 1 行赋值）与 `_degradation_metadata` 函数；删除 `tests/test_s1b_degradation.py`。因未触及既有字段，回滚风险低。**回滚执行须另行授权**，本工单未授权删除任何内容。

## 9. 最终结论

**`PASS`**

**证据依据**：① 新增专用测试 10/10 PASS（覆盖 A–I 全部场景，且验证字段值与实际执行路径相符，非仅验证字段存在）；② 工单规定的 8 项回归**全部实际执行且 rc=0**（6/14/22/34/28/19/7 + selftest 6/6），无 FAIL/SKIP；③ 变更严格限于授权文件，`symbols[]`/`calls[]` 与 schema 版本未变（有 G 项测试证明）；④ PRE/POST 哈希证明 63 项保护/禁改文件 `IDENTICAL`、`CHANGED=0`；⑤ 未数值化 confidence、未弱化或删除断言、未越权修改。

判为 `PASS` 而非 `PASS-WITH-ISSUES` 的理由：工单验收项全部满足，第 5 节所列均为**已记录的说明与后续限制**，不构成本阶段未通过项。

---

**STOP**：本工单完成，不自动进入 S1-C、S1-D 或 Android 测试实施。等待你审阅并另行授权。
