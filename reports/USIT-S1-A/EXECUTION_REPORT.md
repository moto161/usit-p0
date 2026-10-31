# USIT-S1-A 执行报告｜统一理解模型只读投影

- **工单编号**：USIT-S1-A-AUTH-01
- **工作区**：`C:\Users\Administrator\WorkBuddy\2026-10-15-22-09-47\usit_p0`
- **执行时间**：2026-10-19 18:12–18:22 (GMT+8)
- **执行模式**：授权范围内实施（仅 S1-A）
- **解释器**：`C:\Users\Administrator\AppData\Local\Programs\Python\Python312\python.exe`（实测 `Python 3.12.10`，`--version` rc=0）

## 1. 实际执行动作

1. PRE 核验：路径、`.git` 元数据（**不存在，未初始化**）、解释器、`usit selftest` 入口（`__main__.py` → `cli.main`）、PRE SHA-256 清单。
2. 新增 `usit/understanding_projection.py`（只读投影模块）。
3. 新增 `tests/test_understanding_projection.py`（专用测试，22 项）。
4. 创建 `reports/USIT-S1-A/` 并写入报告产物。
5. 运行授权清单中的 6 项测试，记录命令、时间、退出码、stdout/stderr。
6. POST SHA-256 复算、逐文件差异、禁改与保护文件核验。

**未执行**：未进入 S1-B/C/D；未修改任何既有源码/测试/配置/凭据/历史证据；未构建、安装、部署、联网、登录；未初始化 Git、未提交/暂存/恢复；未追认 FIX-01/FIX-02。

## 2. PRE/POST 清单与差异

| 文件 | 用途 |
|---|---|
| `reports/USIT-S1-A/PRE_SHA256.txt` | PRE 基线（66 行，含 4 行头部） |
| `reports/USIT-S1-A/POST_SHA256.txt` | POST 基线（68 行，头部与 PRE 同构） |
| `reports/USIT-S1-A/DIFF_filelist.txt` | 路径级差异 |
| `reports/USIT-S1-A/DIFF_hash.txt` | 哈希级差异 |
| `reports/USIT-S1-A/FORBIDDEN_CHECK.txt` | 63 项逐文件核验 |

**差异结果**：仅两行新增，无修改行、无删除行。

```
> 0984a74a62a733e153e7f1bea91f78ef64c0c48fac111843bd450491b9a665f8  usit/understanding_projection.py
> 0b58c6b67f51dca50bbac6c8aaf628f057d362f05e5e98b468134433dc8b8f6d  tests/test_understanding_projection.py
```

## 3. 禁改文件与保护文件核验

**12 项禁改文件全部 `IDENTICAL`，`CHANGED=0`**（总计 63 项既有文件 `IDENTICAL`）：

| 文件 | 状态 | SHA-256 |
|---|---|---|
| `usit/scanner.py` | IDENTICAL | `c70a1a4baf3a6a17…` |
| `usit/parser_contract.py` | IDENTICAL | `3f7a23c8e5c30837…` |
| `usit/python_resolver.py` | IDENTICAL | `3739ab9af7fd5cbc…` |
| `usit/understanding_model.py` | IDENTICAL | `4668760b69a0a6d0…` |
| `usit/schema_adapter.py` | IDENTICAL | `c44f60e42f9d50f4…` |
| `usit/verification_capture.py`（保护） | IDENTICAL | `976a4eb3866842cb…` |
| `tests/test_verification_capture.py`（保护） | IDENTICAL | `719e646182d15d83…` |
| `usit/snapshot.py`（保护） | IDENTICAL | `f7ae3187297f6982…` |
| `usit/cli.py`（保护） | IDENTICAL | `879a3735838e183f…` |
| `usit/__init__.py` | IDENTICAL | `07585303836d8d87…` |
| `tests/test_understanding_model.py` | IDENTICAL | `9a21893062898904…` |
| `tests/test_schema_adapter.py` | IDENTICAL | `4f59cd699ca9b547…` |

## 4. 测试证据（授权清单 6 项）

命令统一前缀：`PYTHONDONTWRITEBYTECODE=1 python -B`，工作目录=项目根。

| # | 命令 | 退出码 | 结果 | 证据文件 |
|---|---|---|---|---|
| 1 | `-m unittest -v tests.test_understanding_projection` | **0** | **Ran 22 tests — OK**（新增专用测试） | `t01-projection.stdout/stderr.txt` |
| 2 | `-m unittest -v tests.test_understanding_model` | **0** | Ran 34 tests — OK | `t02-model.*` |
| 3 | `-m unittest -v tests.test_schema_adapter` | **0** | Ran 28 tests — OK | `t03-adapter.*` |
| 4 | `-m unittest -v tests.test_parser_contract` | **1** | **Ran 7 tests — FAILED (failures=3)** | `t04-contract.*` |
| 5 | `-m unittest -v tests.test_graph_validation` | **0** | Ran 19 tests — OK | `t05-graph.*` |
| 6 | `-m usit selftest` | **0** | `RESULT: PASS (6/6)` | `t06-selftest.*` |

时间记录见 `test_commands.txt`。

## 5. t04 失败分析（既有失败，非本次引入）

失败用例：
- `test_javascript_typescript_semantic_fixture_and_jvm_honest_fallback`
- `test_javascript_typescript_associations_recompute_after_source_changes`
- `test_fixture_benchmark_metrics_keep_unsupported_languages_unmeasured`

**根因**：三个用例均依赖 JS/TS 语义解析；其夹具需要项目内 TypeScript 包。实测：
- 工作区 `tests/fixtures/javascript_typescript/` **仅有** `expected.json`、`src`、`tsconfig.json`，**缺 `node_modules`**；
- 桌面权威源同名目录**存在** `node_modules`（副本创建时被排除）；
- 失败详情中 `relation_results` 的 `actual` 全为 `None` / `CALL_NOT_FOUND`，即解析器未产出记录。

**非本次引入的证据**：① 本次仅新增两个文件，禁改文件 63 项全部 `IDENTICAL`；② 新增模块与 JS/TS 解析无任何导入关系；③ 该套件在 FIX-01/FIX-02 历史记录中未见运行（历史 4 套件为 model/adapter/graph/selftest）。

**状态**：既有失败，环境依赖缺失，`NOT_VERIFIED` 的 JS/TS 解析器可用性现已取得实测证据——**在工作区不可用**。

## 6. 投影能力边界

- 复用 `understanding_model` 契约：`ConclusionRecord.__post_init__` 与 `validate()` **硬阻断 `VERIFIED_FACT`**（"requires a trusted external verification protocol; none authorized"），故静态解析**只能**映射为 `STATIC_INFERENCE`，结构性无法冒充运行时验证。
- `EvidenceReference` 拒绝调用方自授信（`source_trusted`/`execution_verified`/`TRUSTED` 构造即抛错）。
- 非成功态 `UNRESOLVED/AMBIGUOUS/DYNAMIC/EXTERNAL/UNKNOWN` → `UNKNOWN`，`is_success=False`，不计入成功。
- 证据缺失/空 → `UNKNOWN` + `UncertaintyRecord`，不伪造路径/行号/符号。
- 同身份异内容 → `CONFLICT`，保留双方，不自动择一。
- `confidence` 保持 `None`（按授权要求不数值化、不照搬 JS/TS 量纲）。
- 确定性：ID 由 SHA-256 内容派生；重复投影输出完全一致（已测）。

## 7. 偏差与问题清单

| # | 问题 | 性质 | 处理 |
|---|---|---|---|
| 1 | t04 三项 JS/TS 用例失败 | 既有（缺 node_modules） | 如实报告，未修改夹具/未装依赖（需另行授权） |
| 2 | POST 清单首版头部少 1 行导致比对误报 `__init__.py` 缺失 | 本轮脚本缺陷 | 已重新生成同构清单并修正；`__init__.py` 实测存在且哈希一致 |
| 3 | JS/TS 解析器在工作区不可用 | 环境依赖缺失 | 记录为实测证据，不推断历史 |

## 8. 固定门禁复核（原值不变）

- `F1=NOT_READY_TO_CLOSE`
- `P0.8=HOLD`
- `F2=STOP`
- `历史授权连续性=UNRESOLVED`
- `安全信任链=BLOCKED / NOT_VERIFIED`

本阶段结果**不解除任何门禁**，不追认 FIX-01/FIX-02。

## 9. 最终结论

**`PASS-WITH-ISSUES`**

**依据**：核心目标达成——新增投影能力交付、新增专用测试 **22/22 PASS**、授权回归 5 项中 4 项通过、禁改与保护文件 63 项 `IDENTICAL`（无越权变更）、无删除/无未授权修改。但 `tests.test_parser_contract` 存在 **3 项失败**（既有、因工作区缺 `node_modules` 导致 JS/TS 解析器不可用），故按授权"相关既有回归测试全部 PASS"字面未完全满足，不判 `PASS`；失败非本次引入且未发生未经授权变更，故不判 `FAIL`。

**后续需用户决定**：是否授权补齐 JS/TS 夹具依赖（node_modules），或将该既有失败另行立项处理。

**STOP**：本阶段结束，不自动进入 S1-B。
