# USIT-S1-B-FIX-01｜JS/TS 降级诊断引用修复＋回归验收 执行报告

- **工单编号**：USIT-S1-B-FIX-01
- **执行时间**：2026-10-19 21:44–21:52 (GMT+8)
- **工作区绝对路径**：`C:\Users\Administrator\WorkBuddy\2026-10-15-22-09-47\usit_p0`（沿用当前工作区，未切换副本）
- **执行模式**：授权范围内最小修改＋测试＋取证
- **解释器**：`…\Python312\python.exe`（`Python 3.12.10`）
- **前置基线**：`USIT-S1-B-IMPL-01` 的 POST 清单 + `USIT-S1-B-AUDIT-01` 报告

## 1. PRE 核验（基线连续，未触发 STOP）

| 项 | 结果 |
|---|---|
| 工作区绝对路径 | `…\2026-10-15-22-09-47\usit_p0` ✓ |
| `usit/scanner.py` | `12f688261feea6cb…` = IMPL-01 POST，**same=True** |
| `tests/test_s1b_degradation.py` | `ac7f77fe1c8e9272…` = IMPL-01 POST，**same=True** |
| 保护文件（cli / snapshot / verification_capture / understanding_model / understanding_projection / parser_contract / python_resolver） | **全部 same=True** |
| PRE 清单 | `reports/USIT-S1-B-FIX-01/PRE_SHA256.txt`（67 行） |

## 2. 缺陷定位（与 AUDIT-01 一致，本轮精确到行）

| 环节 | 位置 | 行为 |
|---|---|---|
| 写入降级诊断 | `usit/scanner.py:485` | `analyze_file` 对 JS/TS 写 `SEMANTIC_PARSER_UNAVAILABLE` |
| 计算引用 | `usit/scanner.py:495` | `_degradation_metadata` 算出 `diagnostics_ref=0` |
| **覆盖（根因）** | `usit/scanner.py:918` | `_scan_project_impl` 用 `row['analysis_diagnostics']=parsed.get('diagnostics',[])` **整体替换**诊断列表 → ref 悬空 |
| 兜底未覆盖该场景 | `usit/scanner.py:926-928` | 仅在 `not available` 时才 append 诊断；解析器可用（ENV-02 后）时不会补 |

## 3. 实际修改（仅授权文件）

### 修改 1：`usit/scanner.py`（1188 → 1202 行；86198 → 87309 字节）

在 JS/TS 分支结束处（`row['parser']=…` 之后，即 `:931` 之后）新增 **14 行**：

```python
        # S1-B-FIX-01: the JS/TS branch above replaces `analysis_diagnostics`
        # wholesale, which can orphan the `diagnostics_ref` computed by
        # `analyze_file`. Re-materialise the diagnostic that this row's
        # degradation metadata actually claims (never null the reference: that
        # would hide the reason), then recompute the metadata so the reference
        # always resolves to a real, readable entry. Python rows and
        # unsupported-language rows never reach this branch.
        claimed_reason=(row.get('analysis_degradation') or {}).get('reason')
        if claimed_reason=='SEMANTIC_PARSER_UNAVAILABLE':
            diags=row.setdefault('analysis_diagnostics',[])
            if not any(isinstance(d,dict) and d.get('code')=='SEMANTIC_PARSER_UNAVAILABLE' for d in diags):
                diags.append({'code':'SEMANTIC_PARSER_UNAVAILABLE','severity':'UNKNOWN',
                    'message':'Text candidates are not semantic parse results; syntax validity is not measured.'})
        row['analysis_degradation']=_degradation_metadata(row, row['language'])
```

**设计取舍（逐条对应工单要求）**

| 工单要求 | 落实方式 |
|---|---|
| ① 诊断真实存在且 ref 指向它 | 覆盖后**补回** `SEMANTIC_PARSER_UNAVAILABLE` 条目，再重算 ref |
| ② 不得只把引用改 None | 保留原因与证据；测试 `test_a` 显式断言 `ref is not None` |
| ③ 最小改动、不改其他解析器语义 | 补丁位于 JS/TS 分支内，**Python 行与不支持语言行 `continue` 不进入**；未动 `:485`/`:926-928` 语义 |
| ④ 不支持语言保持原语义 | 该分支不处理 `Unknown` 语言行；`test_d` 验证 `UNSUPPORTED_LANGUAGE_NO_SEMANTIC_PARSER` 不变 |
| ⑤ 不接入图谱 / Markdown | 未改 `_render_markdown` 与图装配；`t60` 实证两处均**不含**该字段 |
| ⑥ 越权即 STOP | 未发生（核验见第 6 节） |

### 修改 2（新增）：`tests/test_s1b_fix01_ref.py`

7 个用例，全部基于**真实 `scan_project` 端到端产物**（经 `scan_manifest.json` 序列化往返读取），运行于 `tempfile.TemporaryDirectory`，自动清理。

## 4. 测试与回归（真实退出码）

统一前缀：`PYTHONDONTWRITEBYTECODE=1 python -B`，cwd=项目根。

| # | 命令 | rc | 测试数 | 状态 |
|---|---|---|---|---|
| 1 | `-m unittest -v tests.test_s1b_fix01_ref` | **0** | **7 tests — OK**（67.8s） | **PASS（新增）** |
| 2 | `-m unittest -v tests.test_s1b_degradation` | 0 | 10 tests — OK | PASS |
| 3 | `-m unittest -v tests.test_python_resolver` | 0 | 6 tests — OK | PASS |
| 4 | `-m unittest -v tests.test_usit` | 0 | 14 tests — OK | PASS |
| 5 | `-m unittest -v tests.test_understanding_projection` | 0 | 22 tests — OK | PASS |
| 6 | `-m unittest -v tests.test_understanding_model` | 0 | 34 tests — OK | PASS |
| 7 | `-m unittest -v tests.test_schema_adapter` | 0 | 28 tests — OK | PASS |
| 8 | `-m unittest -v tests.test_graph_validation` | 0 | 19 tests — OK | PASS |
| 9 | `-m unittest -v tests.test_parser_contract` | 0 | 7 tests — OK（52.4s） | PASS |
| 10 | `-m usit selftest` | 0 | `RESULT: PASS (6/6)` | PASS |

**无 FAIL、无 ERROR、无 SKIP**；原始输出存于 `t50`–`t59`。**无 NOT_RUN、无替代命令。**

### 新增测试覆盖矩阵（A–G）

| 项 | 场景 | 验证方式 | 结果 |
|---|---|---|---|
| A | JS/TS 解析器不可用 | `ref is not None` 且可解析、`code=='SEMANTIC_PARSER_UNAVAILABLE'`、状态非 RESOLVED | PASS |
| B | Python 语法错误 | `ref` 有效且指向 `PYTHON_SYNTAX_ERROR` | PASS |
| C | AST 正常 | `degraded=False`、`method='python-ast'`、诊断为空、无虚假降级 | PASS |
| D | 不支持语言 | `UNSUPPORTED_LANGUAGE_NO_SEMANTIC_PARSER` + `discovery-only` + `UNKNOWN` 不变 | PASS |
| E | 序列化往返 | 四文件逐一校验 `ref` 有效且 `code == reason` | PASS |
| F | 确定性 | 两次扫描 `analysis_degradation` 与 `analysis_diagnostics` 全等 | PASS |
| G | 结构兼容 | `calls[]` 含 `resolution_status`/`resolved`；Python `symbols[]` 键集合恰为 `{name,kind,line,evidence}` | PASS |

## 5. 端到端复现取证（修复后，`t60-e2e-after-fix.txt`）

| 文件 | lang | degraded | reason | method | status | ref | **序列化后有效** | ref_code |
|---|---|---|---|---|---|---|---|---|
| `ok.py` | Python | False | `None` | `python-ast` | `RESOLVED` | None | — | — |
| `broken.py` | Python | True | `PYTHON_SYNTAX_ERROR` | `regex-candidate` | `UNKNOWN` | 0 | **True** | `PYTHON_SYNTAX_ERROR` |
| `main.js` | JavaScript | True | `SEMANTIC_PARSER_UNAVAILABLE` | `regex-candidate` | `UNKNOWN` | 0 | **True（修复前 False）** | `SEMANTIC_PARSER_UNAVAILABLE` |
| `notes.xyz` | Unknown | True | `UNSUPPORTED_LANGUAGE_NO_SEMANTIC_PARSER` | `discovery-only` | `UNKNOWN` | None | — | — |

产物分布复核：`knowledge_graph.json` **不含**该字段；`report.md` **不含**该字段 → 未接入图谱/Markdown（符合工单第 5 条）。

## 6. PRE/POST 哈希与保护文件核验

| 项 | 结果 |
|---|---|
| 检查文件数 | 65 |
| SAME | **63** |
| CHANGED | **1 —— `usit/scanner.py`（授权）** |
| ADDED | **1 —— `tests/test_s1b_fix01_ref.py`（授权）** |
| REMOVED | **0** |
| **UNAUTHORIZED_CHANGES** | **0** |

保护/禁改逐项：`cli.py`、`snapshot.py`、`verification_capture.py`、`understanding_model.py`、`understanding_projection.py`、`parser_contract.py`、`python_resolver.py`、`__init__.py`、`test_s1b_degradation.py`、`test_understanding_model.py`、`test_schema_adapter.py`、`test_verification_capture.py` —— **全部 IDENTICAL**。

**未修改**：`symbols[]`/`calls[]` 字段结构与 schema 版本；依赖、配置、数据库、凭据、环境变量、PATH；历史报告。**未自动回滚、未清理现场证据。**

## 7. 偏差、风险与未解决问题

| # | 项 | 说明 |
|---|---|---|
| 1 | 语义副作用（已知） | JS/TS 行现在会带一条 `SEMANTIC_PARSER_UNAVAILABLE` 诊断，可能进入 manifest 的 `unknown_items`（`:1116`）与 parser_contract 记录。回归全绿（含 `test_usit`/`test_parser_contract`），但**未单独统计 unknown_items 增量**（标记 UNKNOWN） |
| 2 | `degraded=True` 覆盖面 | 不支持语言仍标 `True`（IMPL-01 既有语义，本工单未改）；下游应以 `reason` 区分 |
| 3 | 未测项（UNKNOWN） | 大工程耗时影响；真实项目中的字段分布 |
| 4 | 未执行（NOT_RUN） | 无 |
| 5 | 字段仍无生产消费者 | 按工单第 5 条**刻意不接入**图谱/Markdown，留待单独设计 |

## 8. 固定安全门禁（原值保持）

- `F1=NOT_READY_TO_CLOSE`
- `P0.8=HOLD`
- `F2=STOP`
- `历史授权连续性=UNRESOLVED`
- `安全信任链=BLOCKED / NOT_VERIFIED`

测试全绿不代表安全信任链已验证，也不代表 USIT 具备 Android 端到端自主测试能力。

## 9. 变更取证与回滚

**变更清单**：修改 1 个（`usit/scanner.py`，+14 行）、新增 1 个（`tests/test_s1b_fix01_ref.py`）、报告产物若干。**无删除。**

**回滚方案（提出，未执行）**：删除 `scanner.py` 中 FIX-01 新增的 14 行；删除新增测试文件。未触及既有字段，回滚风险低。**回滚执行须另行授权**，本工单未授权删除任何内容。

## 10. 最终结论

**`PASS`**

**证据依据**：① 新增专用测试 **7/7 PASS**，覆盖 A–G，且在**真实端到端产物 + 序列化往返**下验证引用有效（非仅字段存在）；② 工单规定的 9 项测试全部实际执行且 rc=0，无 FAIL/SKIP；③ 端到端复现显示 JS/TS 场景 `ref_valid` 由 `False` 转为 `True`，`ref_code` 与 `reason` 一致；④ 未采用"置 None"规避（测试显式断言不得为 None）；⑤ PRE/POST 核验 63 SAME / 1 CHANGED（授权）/ 1 ADDED / 0 REMOVED，**UNAUTHORIZED_CHANGES=0**；⑥ 未接入图谱与 Markdown，未改 `symbols[]`/`calls[]` 与 schema。

判 `PASS` 而非 `PASS-WITH-ISSUES` 的理由：工单验收项全部满足；第 7 节所列均为已记录的后续限制与未测项，不构成本阶段未通过项。

---

**STOP**：本工单完成，不进入 S1-C / S1-D，不实施 Android 自动测试，不扩大授权范围。等待你审阅并另行授权。
