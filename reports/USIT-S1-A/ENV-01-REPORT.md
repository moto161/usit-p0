# USIT-S1-A-ENV-01｜JS/TS 测试环境只读取证报告

- **工单编号**：USIT-S1-A-ENV-01（只读调查，禁止实施修复）
- **执行时间**：2026-10-19 18:55–19:05 (GMT+8)
- **工作区**：`C:\Users\Administrator\WorkBuddy\2026-10-15-22-09-47\usit_p0`
- **调查对象**：`tests.test_parser_contract` 中 3 个 JS/TS 相关失败
- **调查边界**：仅读取文件、列目录、计算哈希、读已有日志、无副作用版本查询。**未安装/卸载、未联网、未复制依赖、未生成缓存、未构建、未执行被调查项目代码、未修改环境变量/PATH/注册表、未进入 S1-B。**

## 1. 工具版本与环境（实测）

| 工具 | 结果 | 状态 |
|---|---|---|
| Python | `Python 3.12.10`（`…\Python312\python.exe`，S1-A 实测） | VERIFIED_FACT |
| Node | `v22.22.2`（`…\.workbuddy\binaries\node\versions\22.22.2-6\node`，`node --version` rc=0） | VERIFIED_FACT |
| npm | 静态定位于 `…\node\versions\22.22.2-6\npm`；**未运行**（避免缓存副作用） | NOT_RUN |
| 期望 TypeScript 版本 | `EXPECTED_TYPESCRIPT_VERSION = "6.0.3"`（`usit/js_ts_parser.py:21`） | VERIFIED_FACT |
| 工作区 TypeScript 包 | **缺失**（`node_modules` 与 `node_modules/typescript` 均不存在） | VERIFIED_FACT |

## 2. PRE 状态保护结果

| 检查 | 结果 |
|---|---|
| 工作区绝对路径 | `…\2026-10-15-22-09-47\usit_p0`，rc=0 |
| S1-A 报告目录 | 存在，含 PRE/POST/差异/禁改核验/6 组测试输出 |
| `ENV-01-REPORT.md` 是否已存在 | **不存在**（`No such file`），可安全创建，未覆盖任何文件 |
| 调查前/后哈希核验 | `diff` 完整比对（含 `package-lock.json`）**rc=0，零变化** |
| 关键文件抽样 | `scanner.py c70a1a4b…`、`parser_contract.py 3f7a23c8…`、`understanding_model.py 4668760b…`、`js_ts_parser.py b38addce…`、`test_parser_contract.py 9ecbe198…`，均与 POST 一致 |

**未发现文件哈希变化、缺失或证据矛盾 → 未触发 STOP。**

## 3. A. 三个失败用例逐项分析

原始证据：`reports/USIT-S1-A/t04-contract.stderr.txt`（rc=1，`Ran 7 tests`，`FAILED (failures=3)`）。

| # | 测试名 | 源文件:行 | 夹具 | 预期 | 实际 | 归类 |
|---|---|---|---|---|---|---|
| 1 | `test_javascript_typescript_semantic_fixture_and_jvm_honest_fallback` | `tests/test_parser_contract.py:29` | `tests/fixtures/javascript_typescript`（28 个源文件）+ `expected.json` 的 30 项 relation | `js['status']=='PASS'`、TP/FP/FN=(20,0,0)、全部 `matched` | `status=='FAIL'`，`relation_results` 中 `actual` **全为 `None`**，`checks.present=False` | 解析器未产出语义记录（非断言写错） |
| 2 | `test_javascript_typescript_associations_recompute_after_source_changes` | `tests/test_parser_contract.py:52` | 同上（8 个变更场景） | `result['status']=='PASS'`，8/8 通过 | `status=='FAIL'`；8 场景中 `baseline-link`、`same-name-added-explicit-import` 失败，其余"通过"仅因期望本就是 NOT_RESOLVED；失败项 `diagnostic="CALL_NOT_FOUND"` | 同上 |
| 3 | `test_fixture_benchmark_metrics_keep_unsupported_languages_unmeasured` | `tests/test_parser_contract.py:140` | `usit.benchmark.run_fixture_benchmark()` | `js['status']=='PASS'` | `js['status']=='FAIL'` | 同根因（同一 benchmark 入口） |

**`CALL_NOT_FOUND` / `actual=None` 的精确产生位置**：`usit/js_ts_invalidation.py:27-30`
```python
def _actual(call, *, path=None, file_present=True):
    if call is None:
        return {"file_present": file_present, "status": None, "resolved": False, "target": None,
                "diagnostic": "CALL_NOT_FOUND" if file_present else "TEST_FILE_ABSENT", "evidence": []}
```
即：`analyze_js_ts_project` 未返回任何 `calls` → `js_ts_invalidation.py:16-18` 的 `_call()` 查找落空 → `_actual()` 输出 `CALL_NOT_FOUND`。**这是"解析器无输出"的下游表现，不是断言逻辑错误，也不是外部工具崩溃。**

## 4. B. JS/TS 解析调用链（静态阅读，含行号）

```
tests/test_parser_contract.py:26-27   → usit.benchmark.run_fixture_benchmark()
tests/test_parser_contract.py:35-37   → usit.js_ts_parser.analyze_js_ts_project(fixture, discover_files(fixture))
tests/test_parser_contract.py:45-46   → usit.js_ts_invalidation.run_js_ts_invalidation_benchmark()
usit/js_ts_invalidation.py:12-13      → analyze_js_ts_project(root, discover_files(root))
usit/js_ts_invalidation.py:16-18      → _call() 从 result['files'][path]['calls'] 查找 callee
usit/js_ts_invalidation.py:27-30      → call is None ⇒ CALL_NOT_FOUND
usit/js_ts_parser.py:63-136           → verify_ts_lib_trust()：依赖定位与信任校验
usit/js_ts_parser.py:84               → ts_lib = USIT_ROOT / "node_modules" / "typescript"
usit/js_ts_parser.py:90-91            → 不存在 ⇒ reason="PROJECT_LOCAL_TYPESCRIPT_MISSING", trusted=False
usit/js_ts_parser.py:110-111          → 必需文件 package.json、lib/typescript.js
usit/js_ts_parser.py:134-135          → 版本不符 ⇒ UNEXPECTED_TYPESCRIPT_VERSION
usit/js_ts_parser.py:142-163          → parser_availability() 基于 verify_ts_lib_trust
usit/js_ts_parser.py:168/234          → analyze_js_ts_project 通过 supervisor.run_subprocess 调用适配器
```

**因果链**：工作区 `node_modules/typescript` 缺失 → `verify_ts_lib_trust` 返回 `PROJECT_LOCAL_TYPESCRIPT_MISSING`、`trusted=False` → 解析器不可用 → `files[].calls` 为空 → `_call()` 落空 → `CALL_NOT_FOUND` → 断言 `PASS` 失败。

**注意（对 S1-A 报告的修正）**：S1-A 报告将根因表述为"夹具目录缺 `node_modules`"。本轮精确定位显示：解析器依赖位于 **USIT 项目根** `node_modules/typescript`（`js_ts_parser.py:84`），**项目根缺失才是解析器不可用的直接原因**；夹具目录 `node_modules` 缺失是**第二个独立因素**（影响被测 fixture 的外部依赖场景）。

## 5. C. 夹具与权威源差异

| 项 | 工作区 | 桌面权威源 | 差异 |
|---|---|---|---|
| 夹具绝对路径 | `…\2026-10-15-22-09-47\usit_p0\tests\fixtures\javascript_typescript` | `C:\Users\Administrator\Desktop\usit_p0\tests\fixtures\javascript_typescript` | — |
| `tsconfig.json` | 存在，`da919ec278283909…` | 存在，`da919ec278283909…` | **一致** |
| `expected.json` | 存在，`340ef3745952c976…` | 存在，`340ef3745952c976…` | **一致** |
| `src/` | 28 个文件 | 28 个文件 | 一致 |
| 夹具 `node_modules` | **缺失** | 存在（含 `conditional-lib`、`external-lib`） | **工作区缺** |
| **USIT 根 `node_modules/typescript`** | **缺失** | **存在，版本 6.0.3**，`lib/typescript.js` 9144216 字节 | **工作区缺（关键）** |

权威源**可访问**，以上内容均为实测，未猜测。**未把权威源文件复制进工作区。**

## 6. D. 运行环境结论

- node **可用**（v22.22.2）→ "外部工具不可用"**不成立**。
- 缺失的是 **TypeScript 6.0.3 包**（解析器依赖），且权威源存在同版本可复核副本。
- 工作区经 `find -maxdepth 3 -name node_modules` 确认**无任何 node_modules**。

## 7. E. 失败归因与置信

| 结论 | 状态 | 依据 |
|---|---|---|
| 工作区 `USIT_ROOT/node_modules/typescript` 缺失 | **VERIFIED_FACT** | `ls` 实测 |
| 解析器依赖定位在该路径 | **VERIFIED_FACT** | `js_ts_parser.py:84` |
| 权威源存在 6.0.3 且必需文件完整 | **VERIFIED_FACT** | `package.json` 版本 + `lib/typescript.js` 实测 |
| `verify_ts_lib_trust` 返回 `PROJECT_LOCAL_TYPESCRIPT_MISSING` | **STATIC_INFERENCE** | 代码 `:90-91` + 缺失事实推导（未运行该函数，因禁止执行项目代码） |
| 三个失败由该缺失导致 | **STATIC_INFERENCE** | 上述调用链推导 |
| 夹具配置与权威源一致 | **VERIFIED_FACT** | 哈希比对 |

**四问回答**

1. **缺少 node_modules 是否为充分原因**：分两层。对"解析器不可用"——**USIT 根 `typescript` 缺失是充分原因**。对"30 项 relation 全部匹配"——还需夹具 `node_modules`（`conditional-lib`/`external-lib`），故**完整恢复需补两处**。
2. **是否存在其他独立故障**：是——夹具 `node_modules` 缺失（独立）。未发现代码缺陷；node 可用故非工具问题。
3. **新增 `understanding_projection.py` 是否可能通过导入链影响**：**不可能（VERIFIED_FACT）**。`test_parser_contract.py:7-10,26,35,45` 导入清单为 `parser_contract`、`scanner`、`synthetic_scan`、`python_resolver`、`benchmark`、`js_ts_parser`、`js_ts_invalidation`，**不含**新增模块；且既有文件哈希全部 `IDENTICAL`。
4. **尚未获得运行时验证的结论**：① 恢复依赖后 3 个用例是否转 PASS；② node v22.22.2 与 TS 6.0.3 兼容性；③ 从权威源复制后是否通过 `verify_ts_lib_trust` 的 reparse/重定向检查；④ 30 项 relation 是否全部匹配。（均为 NOT_RUN/UNKNOWN）

## 8. 恢复方案（只提出，未执行）

| 方案 | 文件范围 | 依赖来源与版本核验 | 副作用 | 回滚 | 验收 | 额外授权 |
|---|---|---|---|---|---|---|
| **1. 从已验证来源恢复到独立测试环境** | 工作区 `node_modules/typescript`（6.0.3）+ 夹具 `node_modules`（conditional-lib、external-lib） | 桌面权威源；核验 `package.json` version==6.0.3、`lib/typescript.js` 存在 | 新增大量文件；占用磁盘 | 删除新增 `node_modules` 目录 | 3 个用例 PASS；禁改文件哈希不变；`node --version` 不变 | **需写入授权 + 复制授权**（本工单未授予） |
| **2. 使用解析器降级路径** | 不改代码 | 不恢复依赖，接受 JS/TS 为 UNKNOWN | 测试仍 FAIL；若要"跳过"需改测试（**禁止**绕过断言） | — | 断言无法验证（测试期望 PASS，不接受降级） | 不推荐；改测试需另行授权且违反"不得绕过失败" |
| **3. 独立登记为环境阻断项（推荐当前默认）** | **零文件变更** | — | 无 | 无需回滚 | 维持 S1-A `PASS-WITH-ISSUES`；本失败记为 `ENV-BLOCKED` | 无需新授权 |

**推荐**：**方案 3**（当前默认，零变更、零风险，符合只读边界）；若你希望测试转绿，再单独授权**方案 1**，并先完成版本与完整性核验（尤其是 `verify_ts_lib_trust` 的 reparse/路径校验，避免引入不可信依赖）。**方案 2 不推荐**——它会掩盖能力验证且需改动测试。

## 9. 文件变更清单与哈希核验

- 调查期间：**0 变更**（未创建、修改、删除任何文件；未复制依赖；未生成缓存）。
- 本轮唯一新增：**本报告** `reports/USIT-S1-A/ENV-01-REPORT.md`（创建前已确认不存在）。
- 调查前/后全量哈希比对：**rc=0，零变化**（含 `usit/*.py`、`tests/*.py`、`pyproject.toml`、`package.json`、`package-lock.json`、`README.md`、`AGENTS.md`、`PILOT-READONLY-PROTOCOL.md`）。

## 10. 每项检查状态

| 检查项 | 状态 |
|---|---|
| 工作区路径与 S1-A 报告存在性 | PASS |
| `ENV-01-REPORT.md` 不存在可创建 | PASS |
| 调查前后哈希核验 | PASS（rc=0） |
| 三个失败用例定位（文件/行/诊断位置） | PASS |
| JS/TS 调用链静态追踪 | PASS |
| 夹具与权威源差异（权威源可访问） | PASS |
| node 版本查询 | PASS |
| npm 版本查询 | **NOT_RUN**（未运行以避免缓存副作用） |
| 解析器运行时行为（verify_ts_lib_trust 实际返回） | **NOT_RUN**（禁止执行项目代码） |
| 恢复后测试是否转绿 | **UNKNOWN / NOT_RUN** |
| node 22.22.2 与 TS 6.0.3 兼容性 | **UNKNOWN** |
| 实施任何修复 | **NOT_RUN**（本工单禁止） |

## 11. 最终结论

**`COMPLETE_WITH_ISSUES`**

**依据**：只读诊断目标完成——根因已由可复核证据定位为**工作区 `USIT_ROOT/node_modules/typescript` 缺失**（辅以夹具 `node_modules` 缺失这一独立因素），权威源存在同版本（6.0.3）可复核副本，node 可用，新增投影模块经导入清单排除影响，调查前后哈希零变化。判为 WITH_ISSUES 的原因：① 前轮 S1-A 报告的根因表述需修正为"项目根 typescript 缺失为主因"；② 恢复效果、node/TS 兼容性、信任校验结果均未获运行时验证（NOT_RUN/UNKNOWN）；③ 修复本身未执行且需另行授权。

**本工单为只读调查，不构成实施授权。** 未安装/复制/恢复 `node_modules`，未更改 S1-A 代码，未开始 S1-B。
