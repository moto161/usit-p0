# USIT P0 — 通用软件理解与智能测试基础引擎

当前版本：0.1.0（Python 标准库，无第三方依赖）。P0目标是安全地扫描项目、识别语言/构建标记、用启发式提取符号和导入、建立带证据路径/行号的轻量知识图谱，并生成测试计划与 JSON/Markdown 报告。

## Windows 快速开始
1. 安装 Python 3.10 或更新版本（安装时勾选 Add Python to PATH）。
2. 解压本工程，例如 `C:\Tools\usit_p0`。
3. 打开 PowerShell，在工程目录执行：
   - `python -m usit selftest`
   - `python -m usit scan "C:\path\to\your-project" --out "C:\USIT-Reports\project-001" --authorization-file "C:\USIT-Reports\auth-001.json"`
   - `python -m usit validate-graph "C:\USIT-Reports\project-001\knowledge_graph.json" --project-root "C:\path\to\your-project"`
4. 查看输出目录中的 `report.md`、`report.json`、`scan_manifest.json`、`knowledge_graph.json`、`test_plan.json`、`parser_contract.json`、`graph_validation.json`、`graph_validation.md` 和 `scan_status.json`。

> `scan` 需要 `--authorization-file`：一份存放在被扫描项目之外的 JSON 授权记录，必须包含项目根路径、输出目录、允许动作、禁止动作与授权起止时间。缺少授权即返回 `BLOCKED`，不会降级为继续扫描。

## P0.7.2 只读试点门禁与资源限制

`PILOT-READONLY-PROTOCOL.md` 中的每条限制都由程序强制执行，而非仅文档约定：

| 限制 | 实现位置 | 超限行为 |
|---|---|---|
| 授权与路径门禁 | `usit/safety.py` `canonical_project` / `canonical_output` / `ScanAuthorization` | `BLOCKED`，不启动 |
| 符号链接 / junction / reparse point | `usit/safety.py` `is_reparse_point` / `reject_reparse_components`，`usit/scanner.py` `discover_files` | `BLOCKED` |
| 文件数 5,000、单文件 1 MiB、累计读取 256 MiB | `usit/scanner.py` `discover_files` | 在读取任何内容之前 `BLOCKED` |
| 总时长 600 秒 | `usit/scanner.py` `deadline` 检查点 | `BLOCKED` |
| 单个解析任务 45 秒、并发 1 | `usit/limits.py` `ParserTaskSupervisor` / `ConcurrencyGuard` | 子进程被杀并验证终止；进程内任务中止扫描并如实标记不可取消 |
| 输出 256 MiB | `usit/scanner.py` `bounded_write` 与暂存区总量检查 | 不发布结果 |
| 失败关闭 | `usit/limits.py` `verify_safety_limits` | 任一上限被改动即 `BLOCKED` |
| 终端状态 | `usit/scanner.py` `scan_status.json` 与外部 `usit-status-*.json` | 失败/部分结果绝不输出貌似完整的图谱 |

运行 `python -m unittest discover -s tests -p "test_*.py"` 可复现全部门禁与资源限制测试；`tests/test_p072_gates.py` 使用故障注入与可控模拟解析器验证超时取消与资源释放，不依赖真实等待。


## 安全边界
- 只读取目标项目；不修改目标项目，不执行其中的代码/构建脚本/测试，不安装依赖，不联网。
- 输出目录必须在目标项目目录之外。
- 默认跳过 `.git`、依赖缓存、构建产物、虚拟环境、`.env*`、疑似密钥/凭据命名文件、私钥/证书类扩展名；不跟随符号链接；单文件超过 1 MiB 跳过。
- 扫描输出包含文件路径、符号和导入证据，使用前应按项目数据等级妥善保管。

## P0能力与限制
- 支持识别常见 Python、JS/TS、Java/Kotlin、C/C++、C#、Go、Rust、PHP、Ruby 等扩展名。
- Python 使用标准库 AST，并仅将显式导入且唯一指向项目内定义的调用标为 `RESOLVED`；同名冲突、动态调用和无法证明的调用会标为 `AMBIGUOUS`、`DYNAMIC`、`UNRESOLVED` 或 `UNKNOWN`。循环导入只做静态索引，不执行模块。
- 其他语言的符号/调用仍是启发式候选；调用状态为 `UNKNOWN`，不宣称与 Python 具备相同解析精度。
- `validate-graph` 校验 `usit-graph-v1` 结构、端点、证据、路径和调用解析状态，输出 JSON/Markdown 诊断；不自动修复或删除关系。旧版无 edge ID 的图谱保持兼容，报告中使用数组序号定位关系。
- 单独运行校验器时，默认在图谱旁生成报告；若提供 `--project-root`，报告路径必须在项目根目录之外。已有报告不会被覆盖，可通过 `--out-json` 和 `--out-md` 指定新路径。
- `validate-graph` 默认不把图谱 JSON 内可编辑的 `source_line_counts` 当作可信证据；没有外部可信快照时，行号范围结论会标为 `UNKNOWN/UNVERIFIED`。只有显式传入 `--allow-untrusted-embedded-line-counts` 才启用较弱的内嵌提示校验。
- `inspect-android --force` 必须在授权记录的 `allowed_actions` 中显式包含 `overwrite_existing_output`；Android 与业务语义适配器对遍历、文件数、单文件大小、累计读取字节和总时长执行失败关闭限制。超限不会继续输出貌似完整的分析结果。
- `verification_capture.capture_run` 的命令超时默认/上限为 600 秒，stdout+stderr 合计最多保留 16 MiB；超限以 `FAIL` 记录，证据清单仍需完整校验。该模块仍要求调用方先验证真实授权；`approval_ref` 只是审计引用，不是授权凭证，当前不允许将其直接接入未设门禁的自动修复入口。
- 构建识别基于标记文件；不提供完整调用图或编译器语义。
- 增量只分类新增/修改/未变/删除；P0仍全量轻量分析。
- 测试计划只生成建议，不执行测试，也不代表 PASS。
- 尚未接入豆包/WorkBuddy API；不会上传源码。后续应通过显式适配层实现模型调用，并提供脱敏、授权、审计和关闭开关。

## P0.4 parser contract and fixture benchmark

- `usit-parser-result-v1` is emitted additively as `parser_contract.json`; `knowledge_graph.json` remains `usit-graph-v1`.
- Python uses the standard-library AST and conservative import/call evidence. JavaScript/TypeScript and Java/Kotlin are `DISCOVERY_ONLY`; textual candidates always remain `UNKNOWN`. Their syntax correctness is `NOT_MEASURED` because no semantic parser is installed.
- Run the checked-in independent fixtures with `python -m usit benchmark-fixtures --out C:\\USIT-Reports\\fixture-results.json`. The command is static only, never imports or executes fixture code, and refuses to overwrite an existing result.
- Scan reports distinguish test assets (`test_execution_status=NOT_RUN`) from static associations and coverage evidence (`UNKNOWN`).
- Check test-to-symbol evidence invalidation with `python -m usit benchmark-associations --out C:\\USIT-Reports\\association-results.json`; the command only mutates disposable synthetic fixtures and rejects overwriting an existing result.
