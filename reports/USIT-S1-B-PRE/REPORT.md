# USIT-S1-B-PRE｜Python AST 降级可追溯性只读取证报告

- **工单编号**：USIT-S1-B-PRE
- **执行模式**：严格只读｜当前阶段 S1-B 前置评估｜**仅授权检查，不授权修改**
- **执行时间**：2026-10-19 19:53–20:00 (GMT+8)
- **工作区**：`C:\Users\Administrator\WorkBuddy\2026-10-15-22-09-47\usit_p0`
- **解释器**：`…\Python312\python.exe`（`Python 3.12.10`）

## 1. 检查范围与实际执行命令

**检查对象**：`usit/scanner.py`（`_python_analysis`、`analyze_file`、`_regex_symbols`、`_intra_file_calls`、`_framework_candidates`、`_render_manifest`/报告）、`usit/python_resolver.py`、`usit/benchmark.py`、`usit/parser_contract.py`、相关测试源码。

**实际执行命令**（均为只读/不修改工作区）：

| # | 命令 | rc | 结果 |
|---|---|---|---|
| 1 | `python -B -m unittest -v tests.test_python_resolver` | 0 | Ran 6 tests — OK（9.9s） |
| 2 | `python -B -m unittest -v tests.test_usit` | 0 | Ran 14 tests — OK（32.2s） |
| 3 | `python -B -m unittest -v tests.test_understanding_projection` | 0 | Ran 22 tests — OK |
| 4 | `python -B -m unittest -v tests.test_graph_validation` | 0 | Ran 19 tests — OK |
| 5 | `python -B -m unittest -v tests.test_parser_contract` | 0 | Ran 7 tests — OK（51.7s） |
| 6 | `python -B -m usit selftest` | 0 | `RESULT: PASS (6/6)` |

`usit selftest` 经源码确认使用 `tempfile.TemporaryDirectory`（`usit/cli.py:15`），全部写入临时目录并自动清理，**不修改工作区**，故判定为安全可执行。

**未执行**：任何修改/新建/删除源码、测试、配置、依赖、数据库的操作；格式化、自动修复、代码生成、安装、升级、联网、清理；未进入 S1-B 实现、S1-C、S1-D 或 Android 实施。

## 2. 降级路径表

| # | 触发条件 | 代码位置 | 当前行为 | 输出状态 | 证据充分性 | 风险 |
|---|---|---|---|---|---|---|
| 1 | Python AST 解析失败（SyntaxError/ValueError） | `scanner.py:340-343` | 返回正则符号 + 正则导入 + **空 calls** | 符号条目**无**降级标记 | 部分（row 级有，符号级无） | **降级符号与 AST 符号结构不可区分** |
| 2 | 语法错误诊断记录 | `scanner.py:419-422` | 二次 `ast.parse` 写入 `analysis_diagnostics=[{code:'PYTHON_SYNTAX_ERROR',severity:'ERROR',line,message}]` | row 级诊断，含行号与异常类型 | **充分**（且有测试断言） | 二次解析有性能浪费 |
| 3 | 非 Python 且无语义解析器 | `scanner.py:428-430` | `SEMANTIC_PARSER_UNAVAILABLE`，`severity='UNKNOWN'` | row 级诊断 | 充分 | 未标记到符号级 |
| 4 | 正则提取符号 | `scanner.py:319-326` | 输出 `{name,kind,line,evidence}` | **无 `method`/`confidence`/`resolution_status`** | **不足** | 低置信度结果可能冒充完整解析 |
| 5 | AST 成功路径符号 | `scanner.py:347/350` | 输出 `{name,kind,line,evidence}` | 同样无 method/confidence | 不足 | 与降级符号同形 |
| 6 | AST 调用解析 | `scanner.py:360/364/366` | 含 `method='python-ast'`、`confidence='high'/'low'`、`resolved` | 调用层有标记 | 充分 | — |
| 7 | 正则调用候选 | `scanner.py:388` | `method='regex-candidate'`、`confidence='low'`、`resolved=False`、`resolution_status='UNKNOWN'` | 明确 UNKNOWN | 充分 | — |
| 8 | 框架候选 | `scanner.py:397` | `verified=False`、`candidate=True` | 明确候选 | 充分 | — |
| 9 | 未知项汇总 | `scanner.py:1057` | `unknown_items` 单列含诊断的 row | 未知项被单列 | 充分 | — |
| 10 | 单文件 Python 解析超时 | — | **不存在单文件超时机制**；仅有总扫描 deadline（`scanner.py:668` MAX_SCAN_SECONDS）与子进程 supervisor timeout（`:755`） | 超时仅整体/子进程级 | 不适用 | 大文件解析无独立超时保护 |
| 11 | 能力声明注入 | `scanner.py:433-436` | 每 row 注入 `parser{name,version,level,supported,unsupported,degradation}` | row 级 | 充分 | — |

**判断标注**
- 第 1/4/5 项：**VERIFIED_FACT**（代码实测可见字段）。
- 第 2/3/6/7/8/9/11 项：**VERIFIED_FACT**。
- 第 10 项：**STATIC_INFERENCE**（未见单文件超时代码；未做耗时实测）。

## 3. 测试覆盖表

| 测试 | 实际结果 | 覆盖内容 | 未覆盖边界 |
|---|---|---|---|
| `tests/test_parser_contract` | **PASS** (7/7, rc=0) | `:80`、`:122` 明确断言 `analysis_diagnostics[0]['code']=='PYTHON_SYNTAX_ERROR'` | 无专门断言"降级符号不得与 AST 符号同形"；无符号级 `method` 断言 |
| `tests/test_python_resolver` | **PASS** (6/6, rc=0) | 解析状态（external/unresolved/ambiguous/dynamic）、resolved 调用有定义与导入证据 | 无"AST 失败后正则降级"的符号级断言 |
| `tests/test_usit` | **PASS** (14/14, rc=0) | P0_3 关系：`test_python_calls_resolved`、`:75` 仅导入的调用不宣称 resolved、`test_graph_has_calls_and_references` | 无语法错误文件的端到端降级场景 |
| `tests/test_graph_validation` | **PASS** (19/19, rc=0) | 缺失证据、`UNKNOWN` 状态不计成功 | — |
| `tests/test_understanding_projection` | **PASS** (22/22, rc=0) | 非成功态保真、证据缺失降 UNKNOWN、确定性 | 不覆盖 scanner 降级 |
| `usit selftest` | **PASS** (6/6, rc=0) | 扫描输出、增量、输出目录守卫 | 不覆盖降级 |

**未覆盖边界（NOT_RUN / 无测试）**：① 语法错误文件经 `analyze_file` 后符号是否带降级标记；② 降级符号与 AST 符号的可区分性；③ 单文件解析耗时/超时；④ 报告逐文件展示降级。

## 4. 最小修复建议（**只提出，不实施**）

**核心问题**：降级在 **row 级**已被记录且充分（有诊断、有测试），但 **符号级**无任何方法/置信度/状态标记，导致下游只看 `symbols[]` 时无法区分 AST 结果与正则降级结果。

**预计改动文件**
1. `usit/scanner.py` —— 在 `analyze_file`（`:417-437`）根据 `analysis_diagnostics` 与 `parser.level` 为 row 追加 **row 级可选** `analysis_degradation`：`{degraded: bool, reason, method, resolution_status, diagnostics_ref}`。**不改 `symbols[]`/`calls[]` 内部结构**（保持旧 schema 兼容）。
2. 新增专用回归测试（未命名，待授权时确定）。

**不推荐**：直接改 `_regex_symbols` 返回结构（会破坏旧 schema 与既有消费者，回归风险中高）。

**兼容性风险**：新增字段为可选 → 旧消费者可忽略；`sort_keys=True`（`:1086`）保证确定性；历史报告可读性保持。

**置信度处理**：**不数值化**。`Python` 侧为字符串 `'high'/'low'`（`:360/366`），JS/TS 侧为数值 `0.9/0.0`（`js_ts_adapter.cjs:770`）→ **量纲不一致（VERIFIED_FACT）**，故 `confidence` 暂不落地数值，仅保留 `method` 与 `resolution_status`。

**回归范围**：`tests.test_usit`（P0_3/P0_4 与调用统计相关）、`tests.test_deterministic_output`、`tests.test_parser_contract`、`tests.test_graph_validation`、`usit selftest`。

**验收标准**：① 语法错误 row 的 `analysis_degradation.degraded==True` 且 `reason=='PYTHON_SYNTAX_ERROR'`；② AST 成功 row `degraded==False`；③ 旧字段零变更；④ 输出确定性保持；⑤ 保护文件哈希不变；⑥ 上述回归全绿。

**回滚方法**：仅删除本阶段新增的 `analysis_degradation` 赋值代码行与新增测试文件；因不改既有字段，回滚风险低。

## 5. PRE/POST 文件哈希核验

| 项 | 结果 |
|---|---|
| PRE 清单 | `reports/USIT-S1-B-PRE/PRE_SHA256.txt` |
| POST 清单 | `reports/USIT-S1-B-PRE/POST_SHA256.txt` |
| PRE vs POST 差异 | **`diff` 无输出（rc=0），完全一致** |
| 受保护/关键文件 | **65 项 `IDENTICAL`，`CHANGED=0`** |
| 抽样 | `scanner.py`、`cli.py`、`snapshot.py`、`verification_capture.py`、`python_resolver.py`、`parser_contract.py`、`test_parser_contract.py`、`test_python_resolver.py`、`test_verification_capture.py` 全部 IDENTICAL |

**本工单唯一新增**：`reports/USIT-S1-B-PRE/` 下报告产物（PRE/POST 清单、测试输出、本报告）。**未修改任何源码、测试、配置或依赖。**

## 6. 发现与结论

1. 降级**已被识别并记录**，且在 row 级证据充分（含 code/severity/line/message），并有测试断言（`test_parser_contract.py:80/122`）——优于此前评估。
2. 但**符号级信息不对称**仍是实质缺口：正则降级符号与 AST 符号字段结构相同（`{name,kind,line,evidence}`），下游无法区分 → 这正是 S1-B 需修复的核心。
3. 报告层已展示"调用解析状态（UNKNOWN 不计为成功）"与方法分布（`_render_markdown:1113`），但**不逐文件展示降级诊断**。
4. `confidence` 量纲在 Python（字符串）与 JS/TS（数值）间不一致 → 支持"暂不数值化"的既有决策。
5. 单文件 Python 解析无独立超时（仅有总扫描 deadline）。

## 7. 最终结论

**`PASS-WITH-ISSUES`**

**依据**：本工单目标（查明降级如何被识别、记录、传播、验证）**已完成**——降级路径 11 项全部定位到文件与行号，6 组只读测试**实际执行且全部通过（rc=0）**，PRE/POST 哈希证明**零修改**。判为 WITH_ISSUES 的原因：证明确认了**待修复的实质缺口**（符号级无 `method`/`confidence`/`resolution_status`，导致降级与 AST 结果不可区分；`confidence` 量纲不一致；报告不逐文件展示降级），这些是 S1-B 实施的输入而非本工单的失败。

**未修改断言以制造 PASS**；未把 `UNKNOWN`/`DYNAMIC`/`AMBIGUOUS`/`UNRESOLVED` 或降级结果计为已验证成功；未进入 S1-B 实现。

**固定门禁原值不变**：
`F1=NOT_READY_TO_CLOSE`｜`P0.8=HOLD`｜`F2=STOP`｜`历史授权连续性=UNRESOLVED`｜`安全信任链=BLOCKED / NOT_VERIFIED`

---

**STOP**：本工单完成，等待你审阅报告及另行授权；**不得自动开始 S1-B 实现**。
