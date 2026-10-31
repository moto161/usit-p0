# USIT-S1-A-ENV-02｜JS/TS 测试依赖最小恢复执行报告

- **工单编号**：USIT-S1-A-ENV-02
- **授权范围**：仅恢复测试依赖，不修改 USIT 源码或测试代码
- **执行时间**：2026-10-19 19:30–19:42 (GMT+8)
- **工作区**：`C:\Users\Administrator\WorkBuddy\2026-10-15-22-09-47\usit_p0`
- **权威源**：`C:\Users\Administrator\Desktop\usit_p0`
- **解释器**：`…\Python312\python.exe`（`Python 3.12.10`）

## 1. 执行前核验

| 项 | 结果 |
|---|---|
| 权威源绝对路径 | `C:\Users\Administrator\Desktop\usit_p0` ✓ |
| 工作区绝对路径 | `C:\Users\Administrator\WorkBuddy\2026-10-15-22-09-47\usit_p0` ✓ |
| 权威源 TypeScript 版本 | **6.0.3**（`package.json`，哈希 `9332e97c30d3e53e…`） |
| 权威源 `lib/typescript.js` | 存在，哈希 `569177652966bd52…` |
| 夹具依赖范围 | `conditional-lib`（4 文件）+ `external-lib`（3 文件）= 7 文件 / 715 B |
| 目标目录冲突检查 | 工作区 `node_modules` 与夹具 `node_modules` **均不存在** → 无冲突 |

**PRE 清单**：`reports/USIT-S1-A/ENV02_SOURCE_SHA256.txt`（权威源 147 条）。

## 2. 恢复实施（仅新增，不修改/不覆盖既有源码）

| 操作 | 命令性质 | 结果 |
|---|---|---|
| 新建 `USIT_ROOT/node_modules/typescript/` | 从权威源复制（140 文件 / 23.2 MB） | `cp` rc=0 |
| 新建 `tests/fixtures/javascript_typescript/node_modules/` | 从权威源复制（7 文件 / 715 B） | `cp` rc=0 |

**未执行**：未联网、未 `npm install`、未执行安装脚本、未修改 `package.json`/`package-lock.json`、未修改任何源码或测试、未改 PATH/注册表、未进入 S1-B、未触及其他项目。

## 3. 恢复后哈希核验

采用 Python 逐文件比对（避免 shell 清单工具计数噪声）：

```
CHECKED = 147
MISSING = 0
MISMATCH = 0
TS_PACKAGE_PRESENT = True
```

**结论：147 个文件与权威源逐字节一致。**（期间 shell `find` 清单曾显示 146 条，经 Python 复核确认为清单生成噪声，非文件缺失；`LICENSE.txt` 实测存在且哈希 `a7d00bfd…` 一致。）

证据文件：`ENV02_SOURCE_SHA256.txt`、`ENV02_WORKSPACE_SHA256.txt`、`ENV02_RESTORE_DIFF.txt`。

## 4. 解析器信任校验（实际执行）

来源：`reports/USIT-S1-A/t10-trust-check.stdout.txt`（rc=0）

```
EXPECTED_TYPESCRIPT_VERSION = 6.0.3
verify_ts_lib_trust():
  trusted    : true
  usit_root  : …\usit_p0
  ts_lib     : …\usit_p0\node_modules\typescript
  real_path  : …\usit_p0\node_modules\typescript
  version    : 6.0.3
  reason     : null
parser_availability(fixture):
  available  : true
  node       : …\.workbuddy\binaries\node\versions\22.22.2-6\node.EXE
  version    : 6.0.3
```

恢复前该校验为 `PROJECT_LOCAL_TYPESCRIPT_MISSING`（ENV-01 静态推断），**现已实测为 `trusted: true`**。

## 5. 测试证据（真实退出码）

| # | 命令 | rc | 结果 | 证据 |
|---|---|---|---|---|
| 1 | `-m unittest -v tests.test_parser_contract` | **0** | **Ran 7 tests — OK**（69.1s，含 node 子进程） | `t11-contract-after.*` |
| 2 | `-m unittest -v tests.test_understanding_projection` | 0 | Ran 22 tests — OK | `t12-projection.*` |
| 3 | `-m unittest -v tests.test_understanding_model` | 0 | Ran 34 tests — OK | `t13-model.*` |
| 4 | `-m unittest -v tests.test_schema_adapter` | 0 | Ran 28 tests — OK | `t14-adapter.*` |
| 5 | `-m unittest -v tests.test_graph_validation` | 0 | Ran 19 tests — OK | `t15-graph.*` |
| 6 | `-m usit selftest` | 0 | `RESULT: PASS (6/6)` | `t16-selftest.*` |

**三项 JS/TS 失败用例已全部转 PASS**（`test_parser_contract` 由 `FAILED (failures=3)` → `OK`）。**未修改任何断言或测试代码。**

## 6. 禁改与保护文件核验

对照 S1-A `POST_SHA256.txt`：**65 项全部 `IDENTICAL`，`CHANGED=0`**。

含 `scanner.py`、`parser_contract.py`、`python_resolver.py`、`understanding_model.py`、`schema_adapter.py`、`verification_capture.py`、`snapshot.py`、`cli.py`、`__init__.py`、`test_understanding_model.py`、`test_schema_adapter.py`、`test_verification_capture.py` 等。

证据：`ENV02_FORBIDDEN_CHECK.txt`。

## 7. 文件变更清单

**新增（仅批准范围）**
1. `node_modules/typescript/` — 140 文件（来源：桌面权威源，TS 6.0.3）
2. `tests/fixtures/javascript_typescript/node_modules/` — 7 文件（`conditional-lib`、`external-lib`）
3. 报告产物（`reports/USIT-S1-A/`）：`ENV02_SOURCE_SHA256.txt`、`ENV02_WORKSPACE_SHA256.txt`、`ENV02_RESTORE_DIFF.txt`、`ENV02_FORBIDDEN_CHECK.txt`、`ENV02_test_commands.txt`、`t10`–`t16` 测试输出、本报告

**修改**：无（源码/测试/配置零修改）
**删除**：无

## 8. 问题清单

| # | 问题 | 性质 | 影响 |
|---|---|---|---|
| 1 | 对 `LICENSE.txt` 执行了一次 `cp -p`，而该文件已存在（同来源、同哈希 `a7d00bfd…`） | 操作偏差：字面违反"不得覆盖已有文件" | 无内容差异（哈希一致），但如实记录 |
| 2 | shell `find` 清单曾显示 146 条（缺 1） | 清单工具计数噪声 | 经 Python 复核为 147/0 缺失/0 不匹配，非真实缺失 |
| 3 | `parser_contract` 耗时由 7s 增至 69s | node 子进程实际参与解析 | 预期行为，非缺陷 |

## 9. 回滚状态

**保留现场，未删除任何文件。** 如需回滚，仅可删除本工单明确记录的新建项：
- `node_modules/typescript/`（140 文件，清单见 `ENV02_WORKSPACE_SHA256.txt`）
- `tests/fixtures/javascript_typescript/node_modules/`（7 文件）

删除前须逐项核对清单；任何来源不明或与既有文件混合的内容不得删除。回滚需另行授权（本工单未授权删除）。

## 10. 最终结论

**`PASS-WITH-ISSUES`**

**依据**：验收标准四项全部满足——① 三项 JS/TS 失败用例转 PASS（`test_parser_contract` 7/7 OK）；② 相关回归测试全部通过（22/34/28/19 + selftest 6/6，rc 全 0）；③ 禁改与保护文件 65 项 `IDENTICAL`；④ 变更严格限于批准的依赖目录与本报告。判为 WITH_ISSUES 仅因第 8 节记录的一次覆盖操作偏差与清单噪声，**均为非阻断且不影响功能验收**。

**未修改断言以制造 PASS**；未进入 S1-B；门禁原值不变：
`F1=NOT_READY_TO_CLOSE`｜`P0.8=HOLD`｜`F2=STOP`｜`历史授权连续性=UNRESOLVED`｜`安全信任链=BLOCKED / NOT_VERIFIED`

---

**STOP**：本工单完成，不自动开始 S1-B。
