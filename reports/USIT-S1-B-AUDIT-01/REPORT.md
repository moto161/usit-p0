# USIT-S1-B-AUDIT-01｜降级元数据语义与端到端传播只读取证报告

- **工单编号**：USIT-S1-B-AUDIT-01
- **执行时间**：2026-10-19 21:09–21:20 (GMT+8)
- **工作区绝对路径**：`C:\Users\Administrator\WorkBuddy\2026-10-15-22-09-47\usit_p0`
- **执行模式**：严格只读（仅检查、读取、临时目录安全实验）
- **解释器**：`…\Python312\python.exe`（`Python 3.12.10`）

## 1. PRE 检查（基线核验通过，未触发 STOP）

| 项 | 结果 |
|---|---|
| 工作区绝对路径 | `…\2026-10-15-22-09-47\usit_p0` ✓ |
| `S1-B-IMPL-01/REPORT.md` | 存在（10099 B） |
| `usit/scanner.py` | 存在（86198 B） |
| `tests/test_s1b_degradation.py` | 存在（5665 B） |
| **当前哈希 vs IMPL-01 POST** | `usit/scanner.py` `12f68826…`、测试 `ac7f77fe…`、保护文件（cli/snapshot/verification_capture/understanding_model/understanding_projection/parser_contract）**全部 MATCH** |

**POST 核验（工单结束时）**：59 个源码/测试文件 **MATCH=59，MISMATCH=0** → **本工单零修改**。

## 2. A. 生产消费者检索

**`analysis_degradation` 出现位置**

| 位置 | 性质 |
|---|---|
| `usit/scanner.py:495` | **唯一生产代码出现**（赋值） |
| `tests/test_s1b_degradation.py`（多处） | 仅测试消费 |

**结论**：该字段在生产代码中**只写不读**，无任何下游业务/报告/图谱消费者。

**`resolution_status` 的既有消费者**（均消费 **calls[] 级别**，非新增 row 级字段）：
- `association_benchmark.py:40/49/54/58/65/73/82` —— 按 RESOLVED/UNKNOWN/AMBIGUOUS 判定关联失效场景
- `benchmark.py:26/54` —— 调用状态统计
- `graph.py:325-409` —— 图边校验，`:342` 明确"resolved must be true exactly when resolution_status is RESOLVED"
- `js_ts_invalidation.py:41`、`parser_contract.py:71`（必填字段）

**A 项风险判定**：新增 row 级 `resolution_status='RESOLVED'` **不会**被现有消费者误当作业务/调用/验证成功，因为**没有代码读取 `analysis_degradation`**。既有消费者读的是 calls[] 层状态，未受影响。

## 3. B. 传播路径（实测）

| 产物 | 是否含 `analysis_degradation` | 证据 |
|---|---|---|
| `scan_manifest.json` | **是** | 端到端实验实测（`files[]` 每行均带该字段） |
| `knowledge_graph.json` | **否** | 实测 |
| `report.md` | **否** | 实测（含既有"调用解析状态（UNKNOWN 不计为成功）"行，但不含新字段） |

传播链：`analyze_file` → row → manifest `files[]`（`scanner.py:1035` 复制整行，仅剔除 `_captured_text`）→ **止于 manifest**；未进入图谱节点/边与 Markdown 报告。

## 4. C. 四场景端到端实验（临时目录，已自动清理）

实验脚本：`t42-e2e-fixed.txt`；内容用 `chr(10)` 构造并以 `write_bytes` 写入，避免换行被破坏。所有输出写入 `tempfile.TemporaryDirectory`，退出后自动清理（实测工作区无 `usit-s1b-*`/`usit-diag-*` 残留）。

| 文件 | lang | degraded | reason | method | resolution_status | symbols | diagnostics_ref | 序列化后有效 |
|---|---|---|---|---|---|---|---|---|
| `ok.py` | Python | **False** | `None` | `python-ast` | `RESOLVED` | `['S','h','r']` | `None` | —（无诊断，符合） |
| `broken.py` | Python | **True** | `PYTHON_SYNTAX_ERROR` | `regex-candidate` | `UNKNOWN` | `['broken']`（正则） | `0` | **True**（指向 `PYTHON_SYNTAX_ERROR`） |
| `main.js` | JavaScript | **True** | `SEMANTIC_PARSER_UNAVAILABLE` | `regex-candidate` | `UNKNOWN` | `[]` | `0` | **False（悬空）** |
| `notes.xyz` | Unknown | **True** | `UNSUPPORTED_LANGUAGE_NO_SEMANTIC_PARSER` | `discovery-only` | `UNKNOWN` | `[]` | `None` | —（无诊断，符合） |

**语义准确性判定**：四场景的 `degraded`/`reason`/`method`/`resolution_status` **均与实际执行路径相符**——合法 Python 不误报降级；语法错误明确记录回退；不支持语言不伪装成功。

## 5. D. `diagnostics_ref` 有效性（**发现缺陷**）

- Python 路径：`ref=0` 指向 `PYTHON_SYNTAX_ERROR` 诊断，**序列化往返后仍有效** ✓
- **JS/TS 路径**：`ref=0` 但 `analysis_diagnostics` 在最终产物中为空 → **`ref_valid=False`，引用悬空**

**根因**：`analyze_file` 写入 `SEMANTIC_PARSER_UNAVAILABLE` 诊断并据此计算 `ref=0`；随后 `_scan_project_impl`（`scanner.py:859` 附近）以 JS/TS 解析结果**覆盖** `row['analysis_diagnostics']`（空列表），导致索引指向已失效位置。

**影响**：`reason`/`method`/`resolution_status` 仍正确（主要语义未受损），但 `diagnostics_ref` 在 JS/TS 场景不可安全使用；下游若不校验直接索引会越界或读到错误诊断。

## 6. E. 确定性、兼容性与非成功状态

| 项 | 结果 |
|---|---|
| 重复扫描确定性 | **True**（两次 `scan_project` 的 `analysis_degradation` 完全一致） |
| `symbols[]` 结构兼容 | 键集合仍为 `{name,kind,line,evidence}`，未变（IMP L-01 测试 G 已断言，本轮端到端亦一致） |
| `calls[]` 结构兼容 | 未变 |
| 非成功状态误计成功 | **未发现**：降级场景 `resolution_status='UNKNOWN'`；报告仍含"调用解析状态（UNKNOWN 不计为成功）" |
| 旧消费者兼容 | 新字段为可选，旧消费者忽略即可；实测 8 项回归在 IMPL-01 中已全绿 |

## 7. F. 不支持语言 `degraded=True` 的语义评估（只给证据与建议，未改代码）

**证据**：`notes.xyz`（`lang='Unknown'`）→ `degraded=True`、`reason='UNSUPPORTED_LANGUAGE_NO_SEMANTIC_PARSER'`、`method='discovery-only'`。

**评估**：
- 合理面：从未承诺语义解析的语言，其结果与"完整解析"确实不同，标记可避免被当作正常解析结果。
- 语义风险：`degraded` 字面含"退化/回退"意味，而该语言**从未有过**可解析状态，标 `True` 可能被下游误读为"本来能解析但退化了"。
- **建议（不改代码）**：① 在文档中明确 `degraded=True` 覆盖"回退 + 不支持"两类，靠 `reason` 区分；② 或后续阶段引入独立状态（如 `unsupported`）与 `degraded` 并列；③ 下游判断应以 `reason` 为准，而非仅看 `degraded`。

## 8. 偏差与意外

| # | 偏差 | 性质与处理 |
|---|---|---|
| 1 | **初次端到端实验中 `ok.py` 被标 `PYNTHON_SYNTAX_ERROR`** | **我的实验脚本问题，非代码缺陷**：heredoc 中字面 `\n` 写入时未正确解析（`read_text` 返回 `class S:/n`，正斜杠），文件内容**真的**非法。改用 `chr(10)` 构造后结果正确。已如实记录，未修改任何代码"修复"该假象 |
| 2 | PRE 批量比对两次因 `awk`/路径分隔符与 `* ` 前缀产生假差异 | 工具噪声；改用直接哈希比对与统一分隔符后确认 **59/59 MATCH** |
| 3 | 实验脚本中 `r1['summary']` 触发 `KeyError` | 我的脚本用错键（scan_project 返回结构非预期）；不影响已取得的结论，未重试该子项 |

## 9. 尚未解决的问题与限制

1. **`diagnostics_ref` 在 JS/TS 场景悬空**（第 5 节）——需后续修复（重算或改用稳定引用）。
2. **字段无生产消费者、未进入图谱与 Markdown 报告**——当前只有 manifest 承载，下游价值未兑现。
3. **未测项（UNKNOWN）**：大工程下字段计算耗时；真实业务项目中的分布（本轮仅临时目录合成样本）。
4. **未执行（NOT_RUN）**：无（工单要求的检查项全部实际执行）。

## 10. 固定安全门禁（原值保持）

`F1=NOT_READY_TO_CLOSE`｜`P0.8=HOLD`｜`F2=STOP`｜`历史授权连续性=UNRESOLVED`｜`安全信任链=BLOCKED / NOT_VERIFIED`

## 11. 最终结论

**`PASS-WITH-ISSUES`**

**证据依据**：
- **通过部分**：四场景语义**全部准确**（合法 Python `degraded=False`；语法错误/解析器不可用/不支持语言均明确标记且不伪装成功）；重复扫描**确定性成立**；`symbols[]`/`calls[]` 与 schema **未变**，旧消费者兼容；非成功状态**未被误计为成功**；PRE/POST 哈希证明 59/59 MATCH、**本工单零修改**。
- **ISSUES（实质未通过项）**：① **`diagnostics_ref` 在 JS/TS 场景序列化后悬空**（`ref_valid=False`），下游直接使用有越界/误读风险；② 字段**无生产消费者**且未进入图谱与 Markdown 报告，端到端传播止于 manifest，下游价值尚未兑现。

判为 `PASS-WITH-ISSUES` 而非 `PASS`：上述两项为真实缺陷/限制；而非 `FAIL`：核心语义、确定性与兼容性均成立，且未发生任何未授权变更。

---

**STOP**：只读检查结束，未修改任何代码，未进入 S1-C/S1-D，未实施 Android 自动测试。等待你审阅并另行授权。
