# USIT — 通用软件理解与智能测试系统

**USIT（Universal Software Intelligence Toolkit）不是单纯的代码扫描器或代码生成助手。** 项目目标是让 AI 基于可追溯证据理解一个既有软件：识别其模块、业务功能、接口、数据模型、权限与身份边界，梳理前后端及数据库关联，发现文档/设计/实现之间的差异，形成可审阅的测试候选；在取得明确授权后，再把测试执行结果、覆盖率和回归证据回流到业务功能与需求追溯链，辅助判断问题并支持后续修复验证。

当前仓库处于持续演进阶段，包含 P0 只读扫描基础、静态业务理解、图谱与证据校验、测试候选规划、测试结果/覆盖率导入、证据交叉验证、需求缺口分析和质量评估等能力。**各能力的成熟度并不相同**：静态推断不等于运行时事实，候选不等于已执行测试，测试通过或代码覆盖率也不等于业务正确。

## 能力地图

| 层次 | 当前能力 | 关键边界 |
|---|---|---|
| 项目发现 | 文件清单、语言/构建标记、符号/导入/调用证据 | 解析深度依语言与解析器而异；不支持的语义保持 UNKNOWN |
| 软件理解 | PRD/接口/数据文档、业务功能视图、前后端调用候选、数据库/权限/身份证据 | 只在明确证据和唯一关联下建立关系；静态链路不是运行时证明 |
| 知识与追溯 | `usit-graph-v1`、图校验、来源证据、哈希绑定、需求追溯 | 图谱有效不代表业务实现正确；冲突必须保留并报告 |
| 测试设计 | 业务测试候选、前后端流程候选、预期结果确认点 | 未确认的预期结果不得伪装成验收标准；候选状态不是 PASS |
| 运行证据 | JUnit / pytest JSON 结果导入；coverage.py JSON / JaCoCo XML 覆盖率导入 | 只导入已有报告，不代替测试执行；映射必须显式审阅并绑定图谱哈希 |
| 验证与质量 | 业务证据交叉验证、验证缺口清单、质量维度评估 | UNKNOWN / NOT_MEASURED / CONFLICT 不得折算为通过或零缺陷 |
| AI 辅助 | 可选本地 Agent2API 网关模型调用基础 | 仅显式调用；不会自动上传项目源码或自主修改目标项目 |

## 环境与安装

- Python 3.10 或更新版本；Python 运行依赖由 `pyproject.toml` 声明（当前固定 `cryptography==50.0.2`、`pytest==8.3.5`），不应再描述为“无第三方依赖”。
- Node.js/npm 仅用于启用 JavaScript/TypeScript Compiler API 解析适配器；版本与 TypeScript 依赖见 `package.json` / `package-lock.json`。纯 Python 基础命令不应因未安装 Node 而被误认为 TS 语义已验证。
- 在仓库根目录执行：

```powershell
python -m pip install -e .
python -m usit selftest
# 启用 JavaScript/TypeScript Compiler API 解析时：
npm ci --ignore-scripts --no-audit --no-fund
```

## Windows 快速开始
1. 安装 Python 3.10 或更新版本（安装时勾选 Add Python to PATH）。
2. 将仓库放到本地目录，例如 `C:\Tools\usit_p0`。
3. 在仓库根目录安装 Python 包并运行自检（见上方“环境与安装”）。
4. 对目标项目执行只读扫描，输出目录和授权记录必须位于目标项目之外：

```powershell
python -m usit scan "C:\path\to\your-project" --out "C:\USIT-Reports\project-001" --authorization-file "C:\USIT-Reports\auth-001.json"
python -m usit validate-graph "C:\USIT-Reports\project-001\knowledge_graph.json" --project-root "C:\path\to\your-project"
```

查看输出目录中的 `report.md`、`report.json`、`scan_manifest.json`、`knowledge_graph.json`、`test_plan.json`、`parser_contract.json`、`graph_validation.json`、`graph_validation.md` 和 `scan_status.json`。

> `scan` 必须提供 `--authorization-file`：授权 JSON 存放在被扫描项目之外，记录项目根路径、输出目录、允许动作、禁止动作和授权起止时间。缺少授权或无法确认边界时返回 `BLOCKED`，不会降级为继续扫描。

## 当前不承诺的能力

- 不会仅凭文件名、模块相似度或 AI 猜测建立业务关系；证据不足时保留 UNKNOWN / UNLINKED / CONFLICT。
- 不会把静态分析、测试候选、测试通过率、行覆盖率或零冲突报告解释为业务正确性证明。
- 结果导入命令只读取已经完成的报告，不执行目标项目测试、不构建、不安装、不调用 Gradle/ADB，也不访问项目数据库或服务。
- 不会在未授权时修改目标项目、自动修复代码或自动部署。任何后续执行/修复能力必须有独立授权、隔离工作副本和回归验证。
- 当前解析器只覆盖明确列出的语言和调用形态；未支持框架、动态路由、运行时反射和复杂依赖注入可能无法完整解析。


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
- `validate-graph` 的图谱输入通过 `safeio.read_bounded_no_follow` 读取，最大 256 MiB；拒绝最终文件组件为符号链接/reparse point、非普通文件或超限输入。该限制不宣称所有父目录组件均无符号链接。
- 单独运行校验器时，默认在图谱旁生成报告；若提供 `--project-root`，报告路径必须在项目根目录之外。已有报告不会被覆盖，可通过 `--out-json` 和 `--out-md` 指定新路径。
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


## Local Agent2API multi-model provider (S2.24 foundation)

USIT can discover and call models exposed by a local Agent2API gateway through its OpenAI-compatible Chat Completions API. The default endpoint is `http://127.0.0.1:3065/v1`; model IDs are discovered dynamically from `GET /v1/models` and are never hard-coded.

Security and scope:
- Only plain-HTTP loopback URLs (`127.0.0.1` or `localhost`) are accepted; remote hosts, HTTPS redirects and URL-embedded credentials are rejected.
- Optional gateway authentication is read from the process environment variable `USIT_AGENT2API_API_KEY`; never put a key in source, command arguments, reports or repository files. If Agent2API authentication is disabled, the gateway may not require a key.
- No project files are uploaded automatically. A model call occurs only when the operator explicitly runs `ai-chat` with a prompt.
- Request/response bodies are not logged; response size, timeout and prompt length are bounded. Streaming, tool calls, automatic task routing and autonomous code modification are not implemented in this foundation.

PowerShell examples:

```powershell
# Optional: only if Gateway Key authentication is enabled in Agent2API.
$env:USIT_AGENT2API_API_KEY = Read-Host "Agent2API gateway key"

# Discover available models; this performs a local GET only.
python -m usit ai-models

# Explicitly test one model with a harmless prompt (replace model ID with one returned above).
python -m usit ai-chat --model "MODEL_ID" --prompt "请只回复：USIT本地网关连通测试成功"
```

The current provider uses `POST /v1/chat/completions` with `stream=false`. Model discovery proves only that the local endpoint returned a model list; a successful `ai-chat` call proves one selected model responded to one explicit prompt. Neither proves the model's reasoning quality or the correctness of its conclusions.

## Business understanding → test-candidate planning

Run the static business-understanding pass and generate evidence-linked test candidates:

```powershell
python -m usit plan-business-tests "C:\\path\\to\\your-project" --out "C:\\USIT-Reports\\business-001" --authorization-file "C:\\USIT-Reports\\auth-business-001.json" --graph "C:\\USIT-Reports\\scan-001\\knowledge_graph.json"
```

The command writes `business_semantics.json` and `business_test_plan.json` to a new output directory outside the target project. It reuses the explicit static-read-only authorization gate. Candidates are derived from discovered PRD features, API endpoints, documented data entities and static discrepancies; each candidate retains source evidence, preconditions, steps, structured `actions`, owner-confirmation `expected_results`, and `evidence_requirements`, plus an explicit `NOT_EXECUTED` status. Exact API-contract candidates are linked to a business function only when exactly one graph function view has the same HTTP method and endpoint path; missing or multiple matches remain unlinked with warnings. The planner does not invent acceptance criteria or expected HTTP status codes: expected-result placeholders remain `OWNER_CONFIRMATION_REQUIRED` until the owner supplies verifiable requirements. `trace-business` carries each linked candidate's preconditions, actions, owner-confirmation expected results, evidence requirements, source references and `NOT_EXECUTED` state into the requirement row and Markdown report; candidate linkage never counts as test execution.

Optional `--graph` accepts an existing `knowledge_graph.json` produced by a completed USIT scan. When supplied, USIT emits `business_knowledge_graph.json`, adding business candidate nodes and exact source-file reference edges. The graph input must be outside the target project and output directory. Only exact relative source-path matches are linked; unmatched records remain listed as unlinked. This is a static evidence anchor, not proof of runtime reachability, implementation, or a complete business flow. No project code, build, test, database, device or network operation is executed.


## S2.1 — API route → handler evidence association (partial)

The business graph records a named route callback when it is a simple identifier or member expression on the same source line. With an existing `knowledge_graph.json`, USIT links the endpoint candidate to a unique function/method symbol only when that symbol is defined in the exact same route file. The relation is a candidate reference (`verified=false`, `verification_status=NOT_VERIFIED`, `confidence=STATIC_INFERENCE`), not runtime proof.

Duplicate same-file symbol names are marked `AMBIGUOUS`. Imported/member handlers are resolved only when the TypeScript Compiler API produces an exact, unique symbol binding (see S2.4); otherwise they remain `UNKNOWN`. Inline callbacks, multiline registrations not represented by the business extractor and dynamic expressions remain `UNKNOWN` or `NOT_ANALYZED`. USIT does not guess across files or match globally by name. This slice does not prove authorization-to-handler flow, handler-to-database flow, or runtime reachability.


## S2.2 — Function-owned call evidence (partial)

For Python AST calls and TypeScript Compiler API calls, USIT records the enclosing named function/method identity as caller metadata on each call edge (caller_symbol_id and caller_location). The edge source remains the containing file node because the current usit-graph-v1 validator requires all relationship sources to be file nodes. Calls outside a named function, anonymous callbacks without a symbol, and other languages without equivalent scope evidence retain file-only attribution. When a route handler is uniquely linked, the business endpoint node also collects only call edges whose caller_symbol_id equals that handler symbol. The original graph-v1 call edges remain file-sourced; the endpoint stores a compact, sorted list of downstream call-site evidence. A bounded static expansion follows only RESOLVED calls whose target is an existing function/method symbol. Expansion is deterministic, stops on unresolved/external targets, detects cycles, and is capped at depth 8 and 100 chains; depth/quantity limits are reported as truncation. This enables route → handler → multi-hop call-evidence inspection without changing graph-v1 semantics; it does not prove runtime execution order, reachability or data flow.


## S2.3 — API handler → direct-call evidence (partial)

When a route handler is uniquely associated with a same-file symbol, the business graph collects only call records whose `caller_symbol_id` exactly matches that handler symbol. Each evidence item preserves the call-site path/line, target symbol/location, resolver method and resolution status. The evidence is sorted deterministically and remains static inference; it is not a recursively expanded call chain, runtime reachability proof, execution order, authorization-flow proof or database data-flow proof.

S2.2 ownership guard: Python lambdas and anonymous JavaScript/TypeScript callbacks are treated as callable-scope boundaries. Because the current graph model has no stable symbol for these anonymous scopes, calls inside them deliberately omit `caller_symbol_id` instead of inheriting a misleading enclosing named-function identity. Assigned named arrow/function expressions retain their declared owner when the symbol is represented in the graph.

## S2.4 — Cross-file imported route-handler binding (partial)

For JavaScript/TypeScript route registrations recognized by the TypeScript Compiler API, USIT inspects the final route callback expression and asks the compiler for its actual symbol. It can link a named import or namespace-member handler to a unique in-project function symbol, including a named re-export hop. The graph retains the route file/line, exact handler expression, resolved target symbol and an evidence chain containing import/re-export/definition locations where available. The business endpoint candidate can then link to that exact target and reuse its direct-call evidence.

This is compiler-symbol resolution, not global name matching. A missing/ambiguous/dynamic binding, an external or type-only declaration, or a target absent from the analyzed graph is not promoted to a resolved business handler. The current tests cover a renamed named import, a namespace import through a named re-export, and a dynamic/unresolved handler that must not be guessed. CommonJS interop shapes beyond the tested cases, arbitrary middleware arrays, callback factories and framework-specific routing wrappers remain outside this slice. The relation remains a candidate static reference (verified=false); it does not prove runtime registration, middleware execution, authorization, or request reachability.

## S2.6 — Function-owned database access evidence (partial)

The business-semantics pass conservatively recognizes single-line SQL string literals passed directly to common Python `.execute()` and JavaScript/TypeScript `.execute()`, `.query()`, or `.raw()` calls. It extracts only a narrow operation (SELECT/INSERT/UPDATE/DELETE), table identifiers, and simple projection/insert/update column lists. Each record keeps the source path/line, parser, `STATIC_INFERENCE`, and `NOT_VERIFIED`.

When projecting into `business_knowledge_graph.json`, USIT associates a database call with a function only if exactly one existing call edge has the same source path and line and carries `caller_symbol_id`. It links a parsed table to a documented entity only on an exact unique table-name match. Resolved endpoint call chains can then include database-access evidence owned by a handler or downstream function.

Dynamic SQL, concatenation, multiline SQL, CTE/UNION statements, unsupported SQL forms, missing/ambiguous caller ownership, and undocumented tables are not guessed. This slice does not prove runtime execution, actual read/write effects, transaction commit, parameter values, field-level data flow, ORM model-to-table mapping, or authorization enforcement. All database links remain candidate/unverified static evidence.


## S2.11 — Documented API contract ↔ code-route reconciliation (partial)

The business-semantics pass now extracts documented API method/path pairs with Markdown source line evidence and compares them with statically extracted code routes using exact HTTP method + path equality. Results are classified as `EXACT_MATCH`, `MULTIPLE_CODE_MATCHES`, `DOCUMENTED_NOT_FOUND_IN_CODE`, or `CODE_ENDPOINT_NOT_DOCUMENTED`; document and code locations are retained. The business graph exposes this alignment list as part of its understanding summary.

This is a contract-alignment signal, not a defect verdict. Router mount prefixes, API gateway rewrites, version prefixes, dynamic route construction, generated routes and incomplete/out-of-scope documents are not expanded or normalized. An unmatched documented route does not prove missing implementation; an undocumented code route does not prove it is invalid. All results remain `STATIC_INFERENCE` / `NOT_VERIFIED`.

## S2.10 — Evidence-backed endpoint execution-path reconstruction (partial)

The business graph now emits `business_understanding.endpoint_execution_paths`, a deterministic per-endpoint summary assembled only from existing explicit evidence: route source, resolved handler, bounded call-chain steps, linked database accesses, route/function authorization indicators, and exact-line identity/resource evidence. Each stage is reported separately (entrypoint, handler, calls, database, authorization, identity/resource), with unresolved/external steps and call-chain truncation retained as unknowns. Every path remains `STATIC_INFERENCE` / `NOT_VERIFIED`.

This is a technical execution-path view, not a claimed business workflow. It does not infer undocumented feature-to-endpoint mappings, runtime branch order, actual request reachability, database effects, permission correctness or successful outcomes. Missing stages remain `UNKNOWN` / not linked, not silently filled by AI. This view is intended to support later function understanding, evidence-based test planning, defect localization and authorized regression—not to replace those stages.

## S2.9 — Identity and resource-ownership evidence (partial)

The business-semantics pass now extracts a deliberately narrow set of identity/resource patterns: recognizable authenticated-identity expressions (for example, `req.user.id`), request-controlled identity inputs (for example, `req.body.user_id`), explicit same-line owner comparisons, and same-line SQL owner predicates whose identity expression occurs in a positional parameter argument after the SQL argument; SQL string literals and trailing expressions are excluded. Records retain source path/line, expression details, `STATIC_INFERENCE`, `NOT_VERIFIED` and an explicit parameter-pattern status.

The business graph projects these as `identity_evidence` candidate nodes. An identity record is attached to a database-access candidate only when source path and line match exactly; endpoint association receives it only through a database access already linked to its resolved bounded handler call chain. The security test planner can then propose verification candidates for request-controlled identity → SQL owner patterns and authenticated-identity → SQL owner patterns. Neither is treated as proof of a vulnerability or proof of correct enforcement.

This is not yet general data-flow/taint analysis. It does not propagate values across lines, variables, parameters, returns, aliases, closures, imports, middleware, ORM abstractions or control-flow branches; it only inspects simple same-line positional SQL-call arguments after the SQL argument; it does not verify driver binding semantics, named-parameter objects, ORM bindings, multiline calls or runtime values. Missing evidence is UNKNOWN, not evidence of safety. No runtime requests, builds, database operations or target-project modifications are performed.

## Security test traceability integration

`trace-business` accepts optional `--security-test-plan security_test_candidates.json`. Security candidates link to a business-function view only when the endpoint node ID matches exactly one view's `endpoint_id`; zero or multiple matches remain unlinked and are listed in `unlinked_security_test_candidate_ids`. The security plan carries `source_business_graph_sha256`; `trace-business` rejects a security plan whose digest differs from the exact graph bytes supplied to the trace command. The security-plan artifact SHA-256 is also retained in the traceability matrix. Linked security candidates appear alongside ordinary test-design candidates in requirement rows and Markdown reports, retaining authorization, database, identity-flow and call-chain evidence. This is design-only: candidates remain `NOT_EXECUTED` and expected results require owner confirmation.

Example:

```powershell
python -m usit trace-business --semantics .\business_semantics.json --graph .\business_knowledge_graph.json --mapping .\feature-function-mapping.json --test-plan .\business_test_plan.json --security-test-plan .\security_test_candidates.json --out .\business-traceability-matrix.json --out-md .\business-traceability-report.md
```

## S2.8 — Authorization-gap analysis → security test candidates (partial)

When `plan-business-tests` receives an existing `knowledge_graph.json` through `--graph`, USIT now also emits `security_test_candidates.json`. The planner reuses endpoint handler associations, bounded call-chain evidence, reachable authorization indicators and function-owned SQL access evidence. Candidate categories include unauthenticated boundary verification, role-boundary verification, cross-user resource isolation, owner/user ID parameter-tampering checks, and alternate-entrypoint authorization review where two endpoints share the same statically linked database-access record but expose different authorization-signal statuses.

Every candidate carries endpoint/source references, available call-chain/auth/database evidence, preconditions, steps, limitations, `STATIC_INFERENCE`, `CANDIDATE_NEEDS_REQUIREMENTS` and `NOT_EXECUTED`. Missing authorization signals never become a vulnerability finding. Owner/IDOR candidates require recognized owner-like columns in the linked SQL evidence; role candidates require a recognized role/admin/permission guard name plus sensitive read/write evidence; alternate-entrypoint candidates require shared database-access identity and differing static authorization statuses.

This is deliberately a test-design aid, not a vulnerability scanner verdict. SQL column names do not prove request parameters or authorization semantics; guard names do not prove correct enforcement; shared static database evidence does not prove a bypass. Requirements, account/role matrix, test data, runtime behavior and deny outcomes remain unverified. The command performs no HTTP requests, builds, tests, database access or project modifications. Security candidates are emitted only when `--graph` is supplied.

## S2.7 — Authorization evidence along endpoint-to-database paths (partial)

USIT records a bounded set of explicit authorization indicators: route middleware registrations (`router.use` and common route methods), named guard/permission calls (for example `authRequired`, `adminRequired`, `requirePermission`, `canAccess`), and simple role/owner comparisons. Each item retains source path/line and remains `STATIC_INFERENCE` / `NOT_VERIFIED`.

A named permission call is attached to a function only when a call edge on the exact same source path and line yields one unique `caller_symbol_id`. An endpoint receives route-middleware candidates from its route file and function-level guard calls only when the owning function is present in the endpoint's already-resolved bounded call chain. This lets reports compare route/service authorization evidence with downstream database access evidence without inventing links.

Important: `DETECTED` means only that a recognized source pattern was found. `NOT_DETECTED_STATIC_SCAN` is not proof of missing authorization. The current extractor does not prove middleware execution order, complete path coverage, correct deny behavior, authorization on every branch, tenant isolation, or runtime reachability. Dynamic middleware registration, wrappers, decorators, custom frameworks, and complex role/ownership conditions may be missed. No vulnerability is confirmed by absence alone.


## S2.12 — API 契约到功能测试候选（partial）

测试规划器现在消费 S2.11 的精确 API 契约对照结果：`EXACT_MATCH` 生成 API 契约核验候选；`DOCUMENTED_NOT_FOUND_IN_CODE` 与 `CODE_ENDPOINT_NOT_DOCUMENTED` 生成接口/文档复核候选；`MULTIPLE_CODE_MATCHES` 生成多路由歧义复核候选。未匹配和多匹配均不是缺陷结论。

当调用方同时提供业务知识图谱时，只有唯一精确 method/path 对应的 endpoint execution path 才会附加到测试候选；候选可携带 route、resolved handler、bounded call-chain、database access、authorization 与 identity/resource 的现有证据引用。所有引用仍为 `STATIC_INFERENCE`，候选仍为 `CANDIDATE_NEEDS_REQUIREMENTS` / `NOT_EXECUTED`。

本阶段不生成未由需求定义的请求体、参数、HTTP 状态码、响应结构或业务预期；运行时请求、构建、数据库操作和项目修改均未执行。图谱缺少或无法唯一关联时保留证据缺口，不按路径/名称猜测。


## S2.13-A — 前端 JSX 交互入口证据

扫描器通过 TypeScript Compiler API 识别有限 JSX/TSX 事件属性（`onClick`、`onSubmit`、`onChange`、`onPress`、`onTap`、`onLongPress`），并在事件属性绑定到唯一命名函数/方法时建立带文件/行号的候选引用。业务理解摘要输出 `frontend_interaction_bindings`。内联回调、动态表达式、歧义符号和外部目标不会被伪装成已解析 handler。

本切片只建立“UI 事件属性 → handler 符号”入口证据，尚未建立 handler → API client → 后端 endpoint → 数据库的完整业务链；不覆盖 Vue/Angular/XML/Jetpack Compose 等其他 UI 语法，不证明事件在运行时触发或页面可达。所有记录保持 `STATIC_INFERENCE` / `NOT_VERIFIED`。


## S2.13-B — 前端 API 调用与后端接口候选关联

TypeScript Compiler API 对有限的 `fetch`、`axios/http/api/client` 常见调用形态提取 HTTP 方法、URL 字面量、调用文件/行号及所属函数符号。只有明确的绝对路径字面量（以 `/` 开头且不含查询/片段/模板变量）才参与精确匹配；动态 URL 保留为未解析候选。与代码端点仅按大写 HTTP 方法 + 原始路径完全相等关联，不做路径归一化或猜测。

当 JSX 事件绑定唯一指向 handler，且通过唯一已解析函数调用边可达某 API 调用点时，业务摘要输出 `frontend_business_chains`。这是静态候选链，不证明 UI 运行时触发、网络请求成功、后端实际路由生效或业务结果正确；深度上限 8、访问符号上限 100。当前只覆盖有限 TS/JS 调用形态，不代表对所有前端框架/客户端封装的支持。


## S2.13-C — 跨前后端业务链测试候选

对唯一精确匹配的 UI 事件 → 命名 Handler → 有界已解析调用链 → HTTP 客户端调用 → 后端 method/path endpoint，进一步关联既有 endpoint execution path（后端 Handler、调用链、数据库访问、授权和身份/资源证据），由业务测试规划器生成 `frontend_backend_flow` 候选。只有 EXACT_MATCH 且 endpoint execution path 唯一时才生成；动态 URL、未匹配或歧义链不生成此类候选。测试步骤覆盖真实页面触发、网络请求、后端执行、数据副作用、授权/资源边界和 UI 状态核对，但全部保持 CANDIDATE_NEEDS_REQUIREMENTS / NOT_EXECUTED。该候选是测试设计线索，不代表运行时功能已验证。


## S2.14 — 业务功能视图与测试覆盖关联

新增 endpoint-centred `business_function_views`，将可证据关联的前端入口、后端 Handler/调用链、数据库访问、已文档化实体关联、授权及身份/资源线索汇总为可审计功能切片。PRD feature 与 endpoint 没有显式关系证据时，标记 `NOT_LINKED_NO_EXPLICIT_FEATURE_ENDPOINT_RELATION`，不按名称或模块猜测。

测试规划器将已生成的 `frontend_backend_flow` 候选关联到对应功能视图，分别报告测试设计关联与执行覆盖：`TEST_DESIGN_CANDIDATE_LINKED` 不代表测试执行，`test_execution_coverage` 固定为 `NOT_MEASURED`。该阶段首次实现时尚未接入真实测试运行结果和覆盖率报告；后续 S2.15–S2.18 已增加结果回流、覆盖率导入、证据交叉验证与需求追溯。


## S2.15 — JUnit 测试结果回流（显式映射）

新增 `ingest-test-results` 命令，导入已经完成的 JUnit XML 测试报告，并通过人工审阅的映射 JSON 将精确 testcase ID 关联到 `business_function_id`。命令不执行测试、不访问目标项目、不推断功能关联。

映射格式：

```json
{
  "schema_version": "usit-test-result-mapping-v1",
  "source_graph_sha256": "<64位业务图原始字节SHA-256>",
  "mappings": [
    {"test_case_id": "tests.PhotoTests::test_upload", "business_function_id": "bf-<graph中的真实ID>"}
  ]
}
```

调用示例：

```bash
python -m usit ingest-test-results --junit junit.xml --mapping reviewed-test-map.json --graph business_knowledge_graph.json --out test_execution_results.json
```

功能状态规则：任一已报告映射用例失败/错误为 `FAILED`；只有所有已映射预期 testcase 都出现在报告且全部通过，才标记 `PASSED`；存在跳过或报告不完整（部分映射 testcase 缺失）且没有已观察失败时标记 `UNKNOWN`；没有任何匹配执行记录时标记 `NOT_EXECUTED`。部分报告会输出 `expected_mapped_test_case_count`、`reported_mapped_test_case_count`、`mapped_case_completeness`、`mapped_cases_missing_from_report` 和 `execution_status_reason`，不再因已出现用例全通过就误判整个功能通过。未映射用例、输入 SHA-256 均保留。该导入器只报告测试执行结果，不计算代码覆盖率；代码覆盖率由 S2.16 的独立导入器处理。JUnit 通过不等于需求完整实现。


## S2.16 — 多测试框架结果与代码覆盖率回流（部分适配）

- `ingest-test-results` accepts completed JUnit XML (including compatible Android instrumentation reports) and pytest-json-report JSON. Testcase IDs must match the reviewed exact mapping; no fuzzy matching or inferred function linkage. Mapping JSON must include `source_graph_sha256`, the SHA-256 of the exact raw business-graph file bytes.
- `ingest-coverage` accepts coverage.py JSON and JaCoCo XML. Function-level coverage is linked only by a reviewed `source_file + line_start + line_end → business_function_id` mapping. Coverage mapping JSON must also bind to the exact graph bytes through `source_graph_sha256`.
- Missing report files/ranges remain `UNKNOWN`; no coverage is not treated as 0%. Coverage is separate from test pass/fail and does not establish requirement completeness or correctness. The importer also rejects duplicate or invalid `business_function_id` entries in the source graph instead of silently collapsing them into a set.
- These commands import existing reports only. They do not install plugins, run tests, invoke Gradle/ADB, or execute project code. Report files are size-bounded and read through no-follow file descriptors; JaCoCo DTD/entity declarations are rejected.

Example mapping:

```json
{
  "schema_version": "usit-coverage-mapping-v1",
  "source_graph_sha256": "<64位业务图原始字节SHA-256>",
  "mappings": [
    {
      "business_function_id": "bf-example",
      "source_file": "app/src/main/java/example/PhotoService.java",
      "line_start": 20,
      "line_end": 48
    }
  ]
}
```

Before writing either mapping file, calculate the hash of the exact graph file (not a re-serialized JSON object). PowerShell: `(Get-FileHash .\\business_knowledge_graph.json -Algorithm SHA256).Hash.ToLower()`; Linux/macOS: `sha256sum business_knowledge_graph.json`. The resulting 64-character digest must be copied into `source_graph_sha256`; changing graph bytes requires regenerating the mapping hash.

Import commands:

```powershell
python -m usit ingest-test-results --junit .\\reports\\junit.xml --mapping .\\test-result-mapping.json --graph .\\business_knowledge_graph.json --out .\\test-execution-results.json
python -m usit ingest-test-results --format pytest-json --junit .\\reports\\pytest-report.json --mapping .\\test-result-mapping.json --graph .\\business_knowledge_graph.json --out .\\pytest-execution-results.json
python -m usit ingest-coverage --format coverage-py-json --coverage .\\reports\\coverage.json --mapping .\\coverage-mapping.json --graph .\\business_knowledge_graph.json --out .\\coverage-results.json
python -m usit ingest-coverage --format jacoco-xml --coverage .\\app\\build\\reports\\jacoco\\testDebugUnitTestCoverage\\testDebugUnitTestCoverage.xml --mapping .\\coverage-mapping.json --graph .\\business_knowledge_graph.json --out .\\jacoco-results.json
```


## S2.17 — 业务功能测试与覆盖证据交叉验证

`verify-evidence` merges an existing `usit-graph-v1` business graph with optional imported test-result and code-coverage artifacts. It emits one evidence row per `business_function_id`, validates artifact schemas and graph SHA-256 identity, and flags unknown function IDs, duplicate IDs, invalid statuses and graph-hash mismatches.

The conclusion vocabulary is deliberately limited. Pass-family conclusions
carry a `_UNAUTHENTICATED` suffix, and every evidence row plus the top-level
result carries a `verification_authenticity` field (currently always
`UNAUTHENTICATED`): a pass here records that the imported report says the
tests passed, it does not prove the tests were actually executed by a trusted
runner. A VALID provenance HMAC authenticates the evidence document's
producer, not the runner, so it does not change this marker.
- `TEST_FAILURE_OBSERVED`: imported runtime report contains at least one failed mapped test.
- `TEST_PASS_AND_CODE_LINES_COVERED_UNAUTHENTICATED`: mapped tests passed and explicitly mapped executable lines are covered.
- `TEST_PASS_WITH_COVERAGE_GAP_UNAUTHENTICATED`: mapped tests passed but mapped code lines are partial or uncovered.
- `TEST_PASS_COVERAGE_NOT_MEASURED_UNAUTHENTICATED`: tests passed but no usable coverage evidence exists.
- `TEST_EVIDENCE_INCONCLUSIVE`: mapped tests were skipped/unknown or the imported report omitted one or more expected mapped testcase IDs.
- `NO_RUNTIME_TEST_EVIDENCE`: no imported runtime test evidence exists.
- `EVIDENCE_CONFLICT`: source graph identity, business-function linkage or testcase completeness metadata conflicts; a `PASSED` row with missing mapped testcases is downgraded to `UNKNOWN` and reported as a conflict.

The evidence row preserves the testcase-level IDs/statuses, expected and reported mapped testcase counts, completeness state and missing testcase IDs. It also preserves, per business function, the coverage report/mapping/graph SHA-256 values, exact source-file line ranges, measured line counts and missing source files. Imported source hashes must be complete lowercase SHA-256 digests; invalid coverage percentages, counts or mapping ranges are recorded as evidence conflicts. `trace-business` carries those fields into each linked requirement row, and its Markdown report lists reported and missing testcase IDs separately. An incomplete report is never treated as a function-level pass.

B1 cross-checks (business-evidence loop): `verify-evidence` also compares, per business function, the test snapshot's source-file hashes against the coverage report's `source_files` hashes over the files that function's own coverage row references — a mismatch adds `TEST_COVERAGE_SOURCE_VERSION_DIVERGENCE` and downgrades that function's conclusion to `EVIDENCE_CONFLICT` (row field `source_version_binding_status`: `MATCHED` / `DIVERGENT` / `NO_OVERLAPPING_SOURCE_FILES` / `NOT_PROVIDED` / `NOT_APPLICABLE`). With `--runtime-events` (an already validated `usit-runtime-evidence-v1` document whose `source_graph_sha256` must match the supplied graph), each attributed runtime event's explicit fields are checked against the function's static view — HTTP method/route, database operation/table, and UI component only on exact equality with a static entrypoint file — producing `runtime_consistency_status` (`MATCHED` / `MISMATCH` / `NOT_CHECKED_*` / `NOT_PROVIDED` / `NOT_OBSERVED` / `NOT_APPLICABLE`) and a `RUNTIME_STATIC_MISMATCH` conflict per differing field. Each row also carries `claim_vs_observed`: what the reports claim (test/coverage status, percent, execution binding) kept separate from what the parent process itself observed (pytest exit code/status from the execution provenance; empty when no provenance is present). In `trace-business`, a function observed only through a correlation shared with other functions stays at `SHARED_CORRELATION_EVENT_OWNERSHIP_UNRESOLVED` and never rises to `TRACEABLE_WITH_RUNTIME_EVIDENCE`, and every row annotates each static stage with its parser scope (`stage_parser_scope`, e.g. `SQL_SINGLE_LINE_LITERAL_ONLY`, `CALL_GRAPH_DEPTH_8`, `JSX_TSX_ONLY`), rendered in the Markdown stage table.

Example:

```powershell
python -m usit verify-evidence --graph .\\business_knowledge_graph.json --test-results .\\test-execution-results.json --coverage-results .\\coverage-results.json --out .\\business-evidence-verification.json
```

Omit either optional result input when that evidence is unavailable. Missing evidence remains `NOT_MEASURED`. A zero-conflict report is not a certification: test pass does not prove requirement completeness or business correctness; line coverage does not prove assertion quality, branch coverage, authorization correctness or end-to-end behavior. Static business-function views remain `STATIC_INFERENCE` / `NOT_VERIFIED`.


## S2.18 — 需求追溯与验证缺口清单

`analyze-gaps` creates a prioritized, reviewable backlog from business semantics, the business graph, optional test-design candidates and optional runtime evidence matrix. It reports:
- PRD feature → business-function traceability gaps when no reviewed explicit mapping exists.
- Documented API contracts not found in the scanned code, undocumented code endpoints and ambiguous route matches.
- Business functions with no linked test design, no runtime test evidence, observed test failures, missing/unknown coverage, partial coverage or evidence conflicts.

The analyzer consumes only explicit `business_function_id` associations. It does not infer a feature-to-endpoint link from names or module similarity. Every entry is `REVIEW_REQUIRED` and `defect_confirmed=false`; a missing static match is not a defect verdict.

Example:

```powershell
python -m usit analyze-gaps --semantics .\\business_semantics.json --graph .\\business_knowledge_graph.json --test-plan .\\business_test_plan.json --evidence-matrix .\\business-evidence-verification.json --traceability .\\business-traceability-matrix.json --out .\\verification-gap-backlog.json
```

The test plan, evidence matrix and traceability matrix are optional. When `--traceability` is supplied, only rows marked `APPROVED_LINK` with an exact feature identity and a business_function_id present in the supplied graph can close a requirement traceability gap. The traceability matrix must carry the SHA-256 of the exact graph bytes supplied to `analyze-gaps`; stale or mismatched matrices are rejected. Unreviewed, rejected, targetless or ambiguous mappings do not close gaps. The output records SHA-256 hashes of every supplied input. P0/P1/P2 are triage priorities, not severity determinations. Requirements and acceptance criteria still require owner review.


## S2.19 — 多维度项目质量评估

`assess-quality` combines business semantics, the business graph, optional test-design candidates, optional runtime evidence matrix and optional verification gap backlog into a single traceable report. It deliberately does **not** calculate an aggregate quality score.

Reported dimensions:
- Documented feature traceability: explicitly reviewed feature → business-function links.
- Business-function test design: functions with at least one explicitly linked test candidate.
- Runtime test evidence: functions with determinate PASSED/FAILED evidence, reported separately from pass ratio.
- Test pass ratio: PASSED among functions with determinate PASSED/FAILED status.
- Code coverage measurement: functions with explicit COVERED/PARTIAL/NOT_COVERED status.

It also reports inventory counts, endpoint-contract alignment statuses, gap priority/type counts, evidence conflicts and unknown business-function IDs. Every input artifact's exact-byte SHA-256 is recorded by the CLI.

Example:

```powershell
python -m usit assess-quality --semantics .\\business_semantics.json --graph .\\business_knowledge_graph.json --test-plan .\\business_test_plan.json --evidence-matrix .\\business-evidence-verification.json --gap-backlog .\\verification-gap-backlog.json --out .\\project-quality-assessment.json
```

Optional evidence inputs may be omitted. Missing evidence remains `NOT_MEASURED`; an empty denominator yields `percent=null`, not a fabricated score. A test pass ratio is not a business-correctness score. The report remains bounded by the coverage and limitations of its source artifacts.


### S2.19-B — Markdown 质量报告

The same assessment can optionally produce a human-readable Markdown report alongside the machine-readable JSON artifact:

```powershell
python -m usit assess-quality --semantics .\\business_semantics.json --graph .\\business_knowledge_graph.json --test-plan .\\business_test_plan.json --evidence-matrix .\\business-evidence-verification.json --gap-backlog .\\verification-gap-backlog.json --out .\\project-quality-assessment.json --out-md .\\project-quality-assessment.md
```

Both output paths must be distinct, non-symlink paths that do not already exist. The Markdown report is a presentation of the JSON assessment, not an independent verification result.


## S2.20 — 审阅门控的跨层业务追溯

`trace-business` produces a requirements-to-evidence matrix spanning an explicitly reviewed requirement feature → endpoint-centred business-function view → statically linked UI entrypoint/API/backend handler/call chain/database/authorization/identity evidence → test-design candidate → imported runtime result.

**No automatic feature-to-endpoint guessing is performed.** The only allowed requirement-to-function edge comes from a separate, reviewable mapping manifest. Every mapping decision must include a reviewer attestation, timestamp and rationale. An `APPROVED_LINK` must name an exact existing `business_function_id`; `NEEDS_REVIEW` and `REJECTED_LINK` never create a link. Feature identity is the exact tuple `feature_name + source_path + source_line`; duplicate or unknown identities are rejected.

The manifest is bound to the exact graph bytes using SHA-256. When the graph changes, the manifest must be reviewed again; stale mapping manifests and mismatched runtime-evidence graph hashes are rejected. The reviewer fields are audit metadata, not cryptographic identity authentication.

### 身份到资源归属的数据流证据边界

身份证据提取器除同一行直接模式外，还支持有限的局部变量传播：在同一文件、同一简单语句块内识别直接赋值（例如 `uid = req.user.id`）及简单变量别名，并在 SQL 调用的 owner 条件与参数列表同一行时建立 source→assignment→sink 证据链。此类记录使用 `INTRAPROCEDURAL_LINEAR_ASSIGNMENT`，包含赋值行和 SQL sink 行；原始直接匹配仍标记为 `SAME_LINE_PATTERN`。

该能力不是通用污点分析：不跨函数、文件、模块或异步边界传播；进入/离开显式代码块会清空别名；不解析条件分支汇合、循环、闭包、对象属性、解构、参数传递、ORM 绑定、多行 SQL 或动态 SQL。重新赋值为无法证明来源的表达式会使别名失效。所有结果仍为 `STATIC_INFERENCE` / `NOT_VERIFIED`，只用于生成待审查测试候选，不能证明运行时身份可信、SQL 实际执行、授权正确或存在漏洞。

### S2.30-A — 已解析调用点的实参→形参映射证据

Python AST 与 TypeScript Compiler API 现在会在调用目标唯一且状态为 `RESOLVED` 时，额外记录有限的调用参数映射：`argument_binding_status` 与 `argument_bindings`。每条映射保留调用实参表达式及位置、目标形参名称/索引/种类、形参声明文件及位置。Python 当前覆盖显式位置参数和显式关键字参数；JS/TS 当前覆盖显式位置参数。映射只使用解析器已证明的唯一目标，不按函数名猜测。

遇到 `*args` / `**kwargs`、JS/TS spread、rest 参数、解构形参、超出可映射形参数量或不支持的参数形态时，整体标记 `UNSUPPORTED_ARGUMENT_SHAPE` 并输出空映射，不保留部分映射。非 `RESOLVED` 调用不生成参数映射。

这只是跨函数数据流分析所需的结构化调用证据，不等于身份值已经传播到形参，也不证明运行时调用发生、参数值、控制流或副作用。下一步才会在唯一调用边、实参/形参映射和目标函数内 sink 证据同时齐备时建立有限的跨函数身份→资源归属候选；所有结果继续保持 `STATIC_INFERENCE / NOT_VERIFIED`。 参数映射也已随唯一 `RESOLVED` calls 边写入 `knowledge_graph.json`，并由图级回归测试检查；不支持的参数形态只保留状态，不写入部分映射。 图校验器同时检查映射状态与调用解析状态一致、字段及源码位置完整、参数目标不重复，并拒绝未解析调用上的映射和不支持形态的部分映射。

### Mapping manifest example

```json
{
  "schema_version": "usit-feature-function-mapping-v1",
  "source_graph_sha256": "<SHA-256 of the exact business graph file>",
  "mappings": [
    {
      "feature_name": "查看照片",
      "source_path": "docs/requirements.md",
      "source_line": 12,
      "decision": "APPROVED_LINK",
      "business_function_id": "business-function-view-...",
      "reviewed_by": "reviewer identifier",
      "reviewed_at": "2026-11-01T12:00:00+08:00",
      "rationale": "Explain the approved requirement-to-function relationship and its evidence."
    }
  ]
}
```

Decision values: `APPROVED_LINK`, `REJECTED_LINK`, `NEEDS_REVIEW`. A missing mapping remains `NOT_LINKED_NO_REVIEWED_MAPPING`.

### Generate the matrix

```powershell
python -m usit trace-business --semantics .\\business_semantics.json --graph .\\business_knowledge_graph.json --mapping .\\feature-function-mapping.json --test-plan .\\business_test_plan.json --evidence-matrix .\\business-evidence-verification.json --out .\\business-traceability-matrix.json
```

The test plan and runtime evidence are optional. Missing evidence remains `NOT_MEASURED`; a static relationship remains `STATIC_INFERENCE / NOT_VERIFIED`. This command reads artifacts only and does not run target-project code, tests, builds, database operations or network requests.


### S2.20-B — 生成待审阅映射模板

为避免用户手工计算图谱 SHA-256 并逐项复制需求标识，先生成空白审阅模板：

```powershell
python -m usit init-traceability-template --semantics .\\business_semantics.json --graph .\\business_knowledge_graph.json --out .\\feature-function-mapping.json
```

命令为每项需求生成精确的 `feature_name + source_path + source_line` 标识，自动绑定当前图谱的 SHA-256。所有条目初始为 `NEEDS_REVIEW`，`business_function_id` 为空，审阅者/时间/理由也留空；这份模板**不能直接用于建立链接**。必须由实际审阅者逐项填写并保存后，再运行 `trace-business`。模板输出路径不得已存在。


### S2.20-C — Markdown 追溯审查报告

`trace-business` 可同时输出 JSON 矩阵和 Markdown 审查报告：

```powershell
python -m usit trace-business --semantics .\\business_semantics.json --graph .\\business_knowledge_graph.json --mapping .\\feature-function-mapping.json --test-plan .\\business_test_plan.json --evidence-matrix .\\business-evidence-verification.json --out .\\business-traceability-matrix.json --out-md .\\business-traceability-report.md
```

Markdown 按需求功能展开审阅决策、业务功能/API、各层证据状态、测试候选、运行时状态、源码证据引用和输入哈希。它只是 JSON 矩阵的可读投影，不产生额外验证结论；两个输出路径必须不同且均不得覆盖已有文件。


## S2.21 — 运行时事件证据入口（只校验，不负责采集）

USIT 新增 `usit-runtime-evidence-v1` 事件格式及 `validate-runtime-evidence`。该入口只读取已经由外部测试/观测工具采集的 JSON，不连接 Android 设备，不调用 ADB，不启动应用，不访问服务器或数据库，也不运行目标项目代码。

### 命令

```powershell
python -m usit validate-runtime-evidence --events .\\runtime-events.json --graph .\\business_knowledge_graph.json --out .\\runtime-evidence-validation.json
```

### 事件模型

每个事件包含唯一 `event_id`、带时区的 `timestamp`、`event_type`、`correlation_id`；可选 `parent_event_id` 和显式 `business_function_id`。支持事件类型：`UI_ACTION`、`APP_STATE`、`PERMISSION_RESULT`、`AUTH_CONTEXT`、`HTTP_REQUEST`、`HTTP_RESPONSE`、`BACKEND_SPAN`、`DATABASE_CHANGE`。Android 页面、控件、权限和身份上下文可由外部适配器映射到这些类型；服务端请求/响应、后端跨度和数据库变更也使用同一关联 ID 模型。

事件仅允许白名单元数据字段（如页面/组件标识、操作类型、HTTP 方法、路由模板、状态码、数据库操作、权限结果）。禁止请求/响应正文、密码、令牌、Cookie、凭证、个人身份信息和原始数据库值。未知字段一律拒绝，不会被静默写入报告。

### 证据解释边界

- `RUNTIME_OBSERVED` 仅表示输入中存在运行时事件，不等于测试通过、业务正确或需求已满足。
- `PARENT_LINKED_EVENTS_OBSERVED` 要求至少存在两类事件和显式父子事件关联；仅有相同 correlation ID 的阶段共现仍不足以建立父子链。
- 每个 correlation 汇总额外输出 `parent_topology_status`（`LINEAR_CHAIN`、`BRANCHED`、`MULTI_ROOT`、`PARTIAL_LINKAGE`、`NO_EXPLICIT_LINKS`、`CYCLE_DETECTED`）、根/叶/孤立事件、分支父事件、循环事件和父子链接覆盖比例。拓扑标签只描述输入中声明的 parent_event_id 结构，不代表真实调用因果或完整业务流程。
- `observed_event_ids_by_timestamp` 仅供审阅排序，不会转换为因果边；缺失 parent_event_id 的事件不会被自动补链。
- 未关联业务功能的事件不会按页面名称、API 路由或时间邻近自动猜测归属。
- 图谱 SHA-256 不一致、未知业务功能 ID、缺失父事件、跨关联父子关系、父子时间倒置和父事件循环均保留为冲突。
- 完整 UI→HTTP→后端→数据库链不是每项业务功能的强制模板；缺失阶段表示未观察，不自动认定为缺陷。
- 输出报告记录事件文件与图谱哈希；输入和输出均不得是符号链接，输出不得覆盖已有文件。
- 事件 JSON 与业务图谱 JSON 使用严格解析器；任意嵌套层级的重复 JSON 键均拒绝，防止同一字段被静默覆盖。


### 将运行时观察回填到需求追溯矩阵

完成事件结构校验后，可把校验报告传给 `trace-business`：

```powershell
python -m usit trace-business --semantics .\\business_semantics.json --graph .\\business_knowledge_graph.json --mapping .\\feature-function-mapping.json --test-plan .\\business_test_plan.json --evidence-matrix .\\business-evidence-verification.json --runtime-evidence .\\runtime-evidence-validation.json --out .\\business-traceability-matrix.json --out-md .\\business-traceability-report.md
```

新增的 `runtime_observation` 阶段与 `test_execution` 分开记录：前者只表示事件已观察并通过显式业务功能 ID 关联；后者仍只来自已导入的测试执行报告。需求未获审阅映射时，即使运行事件存在，也不会把事件自动关联到该需求。图谱哈希不匹配会拒绝回填；事件验证报告内部存在冲突时，追溯行保留 `EVIDENCE_CONFLICT`，不把冲突事件标成可信观察。 追溯矩阵另行保留 `runtime_evidence_conflicts`、`runtime_evidence_conflict_count` 和 `unknown_runtime_function_ids`；冲突报告列出冲突类型、事件 ID、业务功能 ID 等原始字段，未知功能 ID 不会被静默丢弃，也不会被关联到图谱中的其他功能。若冲突没有可精确定位的业务功能 ID，则按全局冲突处理。`trace-business` 的所有 JSON 输入均通过有界 no-follow 文件描述符读取，拒绝符号链接、非普通文件和超出各输入上限的文件。


## S2.23 — Android 原生应用事件适配器（离线）

新增 `usit/android_event_adapter.py` 与 `adapt-android-events`。它把 Android Instrumentation / UI Automator / Espresso 等测试端导出的结构化 JSONL 记录转换为统一的 `usit-runtime-evidence-v1`。本阶段只实现文件适配，不包含 ADB 连接、设备控制、APK 安装、应用启动或 Android SDK 编译。

### 输入格式

每条有效记录必须以 `USIT_ANDROID_EVENT ` 开头，后面跟一个 JSON 对象。普通 logcat 行会被忽略，不会复制到输出。示例：

```text
USIT_ANDROID_EVENT {"event_id":"evt-001","timestamp":"2026-11-01T12:00:01+08:00","event":"screen_opened","correlation_id":"flow-001","business_function_id":"bf-photo-view","screen_id":"PhotoDetail","app_state":"RESUMED"}
USIT_ANDROID_EVENT {"event_id":"evt-002","timestamp":"2026-11-01T12:00:02+08:00","event":"ui_action","correlation_id":"flow-001","parent_event_id":"evt-001","business_function_id":"bf-photo-view","screen_id":"PhotoDetail","control_id":"openButton","action":"tap","result":"accepted"}
USIT_ANDROID_EVENT {"event_id":"evt-003","timestamp":"2026-11-01T12:00:03+08:00","event":"permission_result","correlation_id":"flow-001","parent_event_id":"evt-002","permission":"android.permission.READ_MEDIA_IMAGES","granted":true}
USIT_ANDROID_EVENT {"event_id":"evt-004","timestamp":"2026-11-01T12:00:04+08:00","event":"http_request","correlation_id":"flow-001","parent_event_id":"evt-002","http_method":"GET","route_template":"/api/photos/{id}"}
USIT_ANDROID_EVENT {"event_id":"evt-005","timestamp":"2026-11-01T12:00:05+08:00","event":"http_response","correlation_id":"flow-001","parent_event_id":"evt-004","status_code":200}
```

支持事件：`screen_opened`、`ui_action`、`permission_result`、`auth_context`、`http_request`、`http_response`、`backend_span`、`database_change`。事件类型映射为统一模型中的 APP_STATE、UI_ACTION、PERMISSION_RESULT、AUTH_CONTEXT、HTTP_REQUEST、HTTP_RESPONSE、BACKEND_SPAN、DATABASE_CHANGE。

### 转换与校验命令

```powershell
python -m usit adapt-android-events --input .\\android-events.jsonl --graph .\\business_knowledge_graph.json --session-id android-run-001 --captured-at 2026-11-01T12:00:00+08:00 --out .\\runtime-events.json
python -m usit validate-runtime-evidence --events .\\runtime-events.json --graph .\\business_knowledge_graph.json --out .\\runtime-evidence-validation.json
```

适配器只接受白名单字段；未知字段（包括 token、密码、请求正文、Cookie 等）直接拒绝。Android 事件 JSONL 与业务图谱 JSON 均使用严格解析器，任意嵌套层级出现重复 JSON 键都会拒绝，避免同一字段被静默覆盖。业务功能 ID 必须已存在于输入图谱；不按 Activity、页面名或控件名猜测业务归属。事件 ID 必须唯一，时间戳必须带时区。输入和输出不得为符号链接，输出不得覆盖已有文件。

文件输入通过 `safeio.read_bounded_no_follow` 使用 no-follow 文件描述符读取：在读取前检查常规文件类型和大小上限，流式读取时再次强制字节上限，并在读取后复核文件身份、大小及时间戳。Android JSONL、业务图谱和运行时事件 JSON 不再先执行无上限 `read_bytes()` 再检查大小。该机制约束最终文件组件；不应将其描述为操作系统沙箱或来源认证。

### 采集端接入约定

Android 测试适配器应在明确的测试/观测授权下发出结构化事件，优先使用不含个人信息的 screen_id、control_id、route_template 与 opaque actor_ref。每个 UI 测试动作由采集端生成 event_id 和 correlation_id；父事件关系必须由采集端真实掌握后再写入 parent_event_id。禁止把完整 logcat、HTTP headers/body、访问令牌、用户手机号/邮箱或数据库原始值当作事件属性提交。

适配器生成的文件可直接交给统一校验器，再通过 `trace-business --runtime-evidence` 回填业务追溯。转换成功只代表格式适配成功；校验通过只代表事件结构有效；两者都不代表 Android 功能测试通过或业务正确。

运行时追溯按事件中显式、已在图谱中存在的 `business_function_id` 关联到业务功能视图，不按页面、路由、时间或名称猜测。追溯矩阵保留每个 correlation 的事件时间排序、采集端声明的父子边、拓扑状态（如 `LINEAR_CHAIN` / `BRANCHED` / `NO_EXPLICIT_LINKS`）、根/叶/孤立事件及父子关联覆盖比例，便于从需求行下钻复核原始运行观察。时间排序仅是审阅顺序；父子边仍是采集端声明，不构成真实调用因果证明。缺少这些可选字段的旧版校验报告仍可读取，但拓扑显示为 `NOT_PROVIDED`，不补造关联。


### Android 测试端最小事件发射器示例

仓库提供 `examples/android-test/UsitEventEmitter.kt`，可复制到目标应用的 `src/androidTest` 测试源集。它只把白名单元数据写入 logcat，输出前缀为 `USIT_ANDROID_EVENT `，不采集或输出 HTTP headers/body、密码、令牌、Cookie、用户资料或数据库原始值。

测试代码示意：

```kotlin
val flowId = java.util.UUID.randomUUID().toString()
val screenEvent = UsitEventEmitter.screenOpened(
    correlationId = flowId,
    screenId = "PhotoDetail",
    businessFunctionId = "bf-photo-view"
)
val tapEvent = UsitEventEmitter.uiAction(
    correlationId = flowId,
    screenId = "PhotoDetail",
    controlId = "openButton",
    action = "tap",
    result = "accepted",
    businessFunctionId = "bf-photo-view",
    parentEventId = screenEvent
)
```

示例只定义采集端接口，不代表已在某个真实 Android 项目编译或运行。将受控 logcat 导出为 UTF-8 文件后，交给前述 `adapt-android-events` 离线转换。CI 当前验证 Python 适配器与证据链，不会伪称已验证 Android SDK 编译、设备兼容性或真实页面操作。

## S2.25 — Multi-model task orchestration

USIT now supports explicit role-based parallel calls to local Agent2API models. The registry schema is `usit-ai-model-registry-v1`; task roles are manually assigned and are never inferred from model names or provider metadata.

### Initialize and configure a local model registry

The registry JSON is read with a bounded no-follow file descriptor (1 MiB maximum). Symlink/reparse-point and non-regular-file inputs are rejected; this protects the final file component and does not claim to validate every parent directory component.

```powershell
python -m usit ai-init-registry --out .\\usit-models.local.json
```

Edit the generated JSON and replace the placeholder model ID with exact IDs returned by `python -m usit ai-models`. Set `enabled` to `true` only after manually verifying that model. Assign one or more roles:
- `architecture_analysis`
- `defect_analysis`
- `test_design`
- `independent_review`
- `general_analysis`

The template disables the placeholder model by default. The registry is local configuration and should not be committed with personal credentials.

### Run an explicit multi-model task

```powershell
python -m usit ai-ensemble --config .\\usit-models.local.json --task architecture --prompt "分析以下已脱敏的软件架构材料，区分证据、推断和未知项：……" --max-models 3 --out .\\architecture-ensemble.json
```

Supported tasks: `architecture`, `defect`, `test_design`, `review`, `general`. Only enabled models explicitly assigned the corresponding role are selected. Up to eight models may be configured; each invocation is bounded by timeout, token and prompt-size limits. Model calls run concurrently, while output ordering follows registry order for reproducibility.

The result distinguishes `SUCCEEDED`, `PARTIAL` and `FAILED`, retains each model's independent answer and usage metadata, and does not merge disagreement into a fabricated consensus. Gateway exception text and credentials are not copied into result records. Optional output files are new files only; existing paths and symlinks are rejected.

### Important evidence boundary

This stage executes model inference only. It does not read project files, run builds/tests, change source code, or prove model conclusions. Supply only material you have deliberately reviewed and sanitized. Model role assignments are operator assertions, not benchmarked capability claims. A future evidence-fusion stage must compare model claims against source/runtime evidence and preserve disagreements rather than deciding truth by majority vote.



## S2.26 — 多模型主张审查与证据融合（离线）

新增 `usit/ai_claim_review.py` 与 `review-ai-claims`。本阶段把经过人工整理的模型主张、证据目录和审阅记录转为可追溯比较报告；**不自动解析自由文本、不自动读取项目文件、不调用模型、不运行项目测试**。

### 输入账本

输入使用 `usit-ai-claim-ledger-v1` JSON，包含：
- `claims`：每条主张的唯一 `claim_id`、`model_id`、`task`、原文 `claim_text`、可选的人工指定 `comparison_group_id`、证据引用、uncertainty、可选自报 confidence 和带时区生成时间。
- `evidence_catalog`：唯一 `evidence_id`、证据类型、来源文件/报告 SHA-256 和精确 locator。目录只用于验证引用是否存在，不代表 USIT 已独立读取或验证证据内容。
- `reviews`：可选人工审阅决策，必须包含 claim_id、decision、reviewed_by、reviewed_at、rationale 和引用的 evidence_refs。审阅身份字段是审计记录，不是身份认证。

### 命令

```powershell
python -m usit review-ai-claims --input .\\ai-claim-ledger.json --out .\\ai-claim-review.json
```

### 比较与状态规则

- 只有账本中明确相同的 `comparison_group_id` 才会被放在一起比较；不做语义相似度或名称猜测。
- 文本在空白归一化后完全相同，只标记 `TEXT_IDENTICAL_MULTI_MODEL_ALIGNMENT_NOT_VERIFICATION`；不同文本标记 `DIVERGENT_TEXT_REQUIRES_HUMAN_REVIEW`。
- 每条主张保留独立模型来源、原文、时间、uncertainty、自报 confidence、证据引用和审阅记录。
- 证据引用只校验是否存在于输入目录；未知引用显式标记。没有证据引用不等于反证。
- 人工决策 `SUPPORTS_CLAIM` 只形成 `REVIEWED_SUPPORT_CANDIDATE`，不证明主张为真；`CONTRADICTS_CLAIM`、`NEEDS_MORE_EVIDENCE` 和 `CONFLICTING_EVIDENCE` 分别保留。
- 所有主张的 `truth_status` 固定为 `NOT_ESTABLISHED`，`claim_status` 固定为 `MODEL_HYPOTHESIS`；不使用多数投票，不自动生成 `VERIFIED_FACT`。
- 输出绑定输入账本 SHA-256；输入最大 10 MiB，最多 5,000 条主张及 20,000 条证据目录记录。拒绝符号链接和覆盖既有输出文件。

### 示例账本

```json
{
  "schema": "usit-ai-claim-ledger-v1",
  "evidence_catalog": [
    {
      "evidence_id": "source-001",
      "evidence_type": "SOURCE_CODE",
      "source_sha256": "<64 lowercase hex characters>",
      "locator": "src/photo.py:42"
    }
  ],
  "claims": [
    {
      "claim_id": "claim-model-a-001",
      "model_id": "exact-model-id-a",
      "task": "defect",
      "claim_text": "Photo access checks the owner identity.",
      "comparison_group_id": "photo-owner-check",
      "evidence_refs": ["source-001"],
      "uncertainty": ["Static evidence only; runtime behavior unknown."],
      "confidence": 0.7,
      "generated_at": "2026-11-01T12:00:00+08:00"
    }
  ],
  "reviews": []
}
```

账本必须由操作人员或经审阅的结构化适配流程提供。不要把模型自由文本直接复制成“已验证证据”；模型自报 confidence 不代表校准概率。


## S2.27-A — AI 主张证据绑定校验（本地只读）

`verify-evidence-bindings` 将 `usit-ai-claim-ledger-v1` 中的证据目录引用与本地真实 artifact 字节核对。它不执行项目代码、构建或测试，不联网，也不读取凭证。

### 证据目录扩展字段

每个 `evidence_catalog` 条目保留既有字段，并可增加：
- `artifact_path`：相对于 `--evidence-root` 的 POSIX 相对路径；禁止绝对路径、路径穿越和符号链接。
- `SOURCE_CODE`：`locator` 必须为 `artifact_path:line`，校验 UTF-8 文件哈希与行号存在。
- `TEST_RESULT`：当前仅支持 JUnit XML；`locator` 必须为 `artifact_path#test_case_id`。可选 `expected_test_status`（PASSED/FAILED/SKIPPED）用于核对报告中的真实 testcase 结果。

既有条目没有 `artifact_path` 时兼容保留，但状态为 `UNVERIFIED_REFERENCE`，不能因为 ID 存在于目录就算验证通过。

账本输入通过有界 no-follow 文件描述符读取，最大 10 MiB；拒绝最终文件组件为符号链接/reparse point、非普通文件或超限文件。

### 运行

```powershell
python -m usit verify-evidence-bindings --input .\\ai-claim-ledger.json --evidence-root .\\evidence --out .\\ai-evidence-bindings.json
```

### 状态与限制

- `ARTIFACT_HASH_MATCH_AND_LOCATOR_VALID`：精确字节哈希匹配，且当前支持的定位符有效。
- `STALE`：实际 artifact SHA-256 与目录中的 `source_sha256` 不一致。
- `MISSING`：引用文件不存在。
- `INVALID_LOCATOR`：源码行或 JUnit testcase 定位无效。
- `CONFLICT`：目录声明的预期测试结果与 JUnit testcase 实际结果不一致。
- `UNKNOWN_REFERENCE` / `UNVERIFIED_REFERENCE`：未知 ID、缺少 artifact 或暂不支持的证据类型。

即使所有绑定均为 `ALL_BINDINGS_RESOLVED_NOT_AUTHENTICATED`，也只证明字节与定位符符合提供的目录声明；不证明来源可信、审阅者身份真实、代码语义支持主张或业务功能正确。所有主张的 `truth_status` 固定为 `NOT_ESTABLISHED`，不会提升为 `VERIFIED_FACT`。报告为新文件，不覆盖已有结果。


## S2.27-B — SOURCE_CODE 与 USIT 扫描快照交叉绑定

在 S2.27-A 的原始 artifact 字节校验之上，`SOURCE_CODE` 证据可选增加两个字段：
- `snapshot_manifest_path`：相对于 `--evidence-root` 的 `scan_manifest.json` 路径。
- `snapshot_manifest_sha256`：该 manifest 原始字节的 SHA-256。

验证器读取真实 manifest 字节，核对其 SHA-256、`schema_version=usit-manifest-v1`、精确文件相对路径以及 manifest 中记录的文件 SHA-256 是否与已读取源码字节一致。可选 `snapshot_file_path` 用于 manifest 中的路径与 `artifact_path` 不同的受控场景；默认等于 `artifact_path`。

快照状态：
- `MATCHED_SCAN_MANIFEST_ENTRY`：manifest 字节摘要、schema、精确路径和源码文件摘要均匹配。
- `MANIFEST_MISSING` / `MANIFEST_STALE` / `MANIFEST_INVALID`：manifest 缺失、摘要不匹配或格式/schema 无效。
- `FILE_NOT_IN_MANIFEST` / `MANIFEST_DUPLICATE_PATH` / `FILE_HASH_MISMATCH`：manifest 中没有唯一匹配路径，或记录的源码摘要与真实字节不一致。
- `NOT_PROVIDED`：未提供快照 manifest 身份；源码字节本身即使匹配，也不能声称已绑定到 USIT 扫描快照。
- `INCOMPLETE_DECLARATION` / `INVALID_DECLARATION`：manifest 路径与摘要字段不完整或摘要格式非法。

该检查仍不是数字签名、可信时间戳或来源认证；攻击者若能同时替换 manifest 和调用方声明的摘要，仍可构造一致但不可信的输入。因此报告明确保留 provenance 限制，不提升 `truth_status`。新增合成测试覆盖 manifest 成功绑定、manifest 摘要过期、manifest 内文件摘要不匹配和 manifest 缺失。


## S2.27-C: detached scan attestation (core API)

The optional provenance layer is implemented in `usit.scan_attestation` using the pinned `cryptography==50.0.2` Ed25519 provider.

- `create_scan_attestation(...)` signs the exact manifest bytes and binds the snapshot ID, UTC timestamp string, scanner version/revision, manifest path, and manifest SHA-256.
- `verify_scan_attestation(...)` validates the signature using the public key embedded in the sidecar, then checks that exact key against a caller-supplied independent trust mapping (`key_id -> 32-byte raw public key`).
- A valid signature from a key absent from the trust mapping is reported as `ATTESTATION_SIGNATURE_VALID_UNTRUSTED_KEY`, never trusted.
- The verifier reports freshness as `NOT_ASSESSED`; timestamps are signer-provided metadata, not trusted timestamps.
- The read-only `verify-scan-attestation` CLI verifies a detached sidecar against independently provisioned public keys.
- `verify-evidence-bindings` now accepts optional `snapshot_attestation_path` on `SOURCE_CODE` entries and repeatable `--trusted-public-key` arguments. The sidecar's signed manifest path must match `snapshot_manifest_path`; an optional catalog `expected_snapshot_id` enforces caller policy.
- Evidence reports keep `snapshot_binding_status` separate from `attestation_status`; claim summaries include `snapshot_provenance_status` and a per-source `snapshot_provenance` list so multiple manifests are never collapsed into one identity.
- Legacy entries without sidecars remain compatible and report `ATTESTATION_NOT_PROVIDED`. A valid signature without an independently supplied key remains `ATTESTATION_SIGNATURE_VALID_UNTRUSTED_KEY`; it is not trusted.
- `verify-evidence-bindings` exits 0 only when artifact bindings resolve and any declared scan-manifest/attestation checks are acceptable. Legacy entries without a manifest or sidecar remain compatible; a declared but stale manifest, invalid/untrusted sidecar, or failed snapshot policy returns exit 2. Inspect the JSON report for the separate binding, snapshot, and attestation statuses.
- Scanner auto-signing, trust-store persistence/enrollment/revocation, OS-backed private-key custody, and automated key lifecycle remain pending.
- Private keys must be kept outside the project and evidence artifacts. Tests use ephemeral keys only.

Install the project dependencies with `python -m pip install .`. Synthetic tests: `python -m unittest tests.test_scan_attestation -v`.


### Verify a detached scan attestation

Use `usit verify-scan-attestation --manifest scan_manifest.json --attestation scan_attestation.json --trusted-public-key <external-public-key.pem> --out attestation-report.json`. Repeat `--trusted-public-key` for additional trusted keys. Public keys may be raw 32-byte Ed25519 data or PEM Ed25519 public keys. An optional `--expected-snapshot-id` applies an explicit snapshot-identity policy. Exit code 0 means the signature is valid under a supplied trusted key; exit code 2 means verification or trust policy did not pass. Keep trust keys outside the evidence bundle and provision them independently; this CLI does not manage enrollment or revocation.

The CLI reads the manifest through a bounded no-follow descriptor (50 MiB maximum), the detached attestation through a bounded no-follow descriptor (64 KiB maximum), and each trusted public-key file through a bounded no-follow descriptor (16 KiB maximum). The optional `plan-business-tests --graph` input is also bounded to 50 MiB and read without following a final-component symlink/reparse point. These controls apply to the final file component; they do not claim that every parent directory component is free of symlinks, nor do they provide an OS sandbox or provenance authentication. Regression tests cover oversized manifest/key inputs and symlinked attestation/key paths.

## S2.30-B — Python identity source ownership and one-edge parameter propagation

- Python identity-source records now receive owner_symbol_id only when the source line is inside an AST-parsed function with a valid end_lineno; nested functions select the innermost containing function. Syntax-invalid Python, module-level statements, and non-Python files retain owner_resolution_status=UNKNOWN.
- A simple direct assignment such as uid = request.user.id records source_variable=uid. The business graph accepts this owner only when the exact symbol ID exists and its source path matches the evidence path.
- For an endpoint-reachable call edge, identity evidence propagates only when the edge is RESOLVED, argument_binding_status=EXACT_STATIC_MAPPING, and the explicit argument expression equals the source variable or identity expression. The output records caller/callee symbols, callsite, argument and parameter declaration.
- Output field: identity_parameter_propagation_evidence; status is CALL_ARGUMENT_TO_PARAMETER_STATIC_MAPPING, confidence STATIC_INFERENCE, verification NOT_VERIFIED.
- This stage stops at the callee parameter. It does not yet prove that the parameter reaches a SQL/resource sink, does not propagate through return values/fields/collections/closures, and does not infer JavaScript/TypeScript function ownership. Unsupported or ambiguous cases remain unlinked.

## S2.30-C — 多跳身份参数静态传播（实现中，等待 CI）

- 在 S2.30-B 单跳实参→形参映射基础上，沿唯一 `RESOLVED` 调用边和 `EXACT_STATIC_MAPPING` 参数绑定继续传播身份表达式；每一跳必须满足前一跳形参名与下一跳实参表达式精确相等。
- 输出逐跳传播记录、深度和完整传播链；仅处理已关联 endpoint 调用链中的符号，要求身份来源 owner 明确为 `RESOLVED`。
- 对同一调用位置多目标、重复/歧义参数绑定、动态/不支持参数形态、缺失形参名、循环回访均不继续传播；不按函数名猜测。
- 所有结果仍为 `STATIC_INFERENCE` / `NOT_VERIFIED`；不证明运行时执行、变量值、SQL sink 到达或资源归属正确。
- 新增两跳合成回归用例，验证 `uid → owner_id → user_id` 及完整链条。当前提交待 CI 验证。

## S2.30-D — 身份形参到 SQL owner binding 关联（实现中，等待 CI）

- SQL owner predicate 检测到后，额外提取 SQL 字符串之后的绑定实参；仅接受单个裸标识符或被单元素 list/tuple 包裹的裸标识符，复合表达式不参与跨函数匹配。
- 将该绑定标识符与多跳传播记录的最终 callee symbol、形参名进行精确匹配；同时要求 SQL sink 证据的函数 owner 已解析且属于 endpoint 的有界可达符号集合。
- 输出 `identity_sql_sink_evidence` / execution path 的 `identity_sql_sink_bindings`，保留身份来源 ID、SQL sink ID、资源字段、形参和完整传播链。
- 这只证明静态参数映射到 SQL owner predicate 的绑定实参，不证明 SQL 驱动实际绑定语义、查询执行、过滤结果或运行时授权有效；状态仍为 `STATIC_INFERENCE` / `NOT_VERIFIED`。
- 新增 extractor 与两跳图关联回归测试；当前提交等待 CI。

## S2.28 — Coverage mapping bound to real source bytes

Coverage import now requires an explicit absolute `--project-root` and checks each reviewed mapping against the actual source file beneath that root. Each mapping entry should include `source_file_sha256`, the lowercase SHA-256 of the exact UTF-8 source-file bytes. USIT reads the source without following symlinks/reparse points, rejects unsafe path components, compares the digest, and verifies that `line_end` does not exceed the decoded source line count.

Example mapping entry:

```json
{
  "business_function_id": "bf-photo-upload",
  "source_file": "app/photo_service.py",
  "line_start": 40,
  "line_end": 78,
  "source_file_sha256": "<64 lowercase hex SHA-256 of the exact source file>"
}
```

Import an already completed report (USIT does not run the test suite):

```powershell
python -m usit ingest-coverage --coverage .\\coverage.json --mapping .\\coverage-mapping.json --graph .\\business_knowledge_graph.json --project-root "C:\\path\\to\\the\\exact\\source\\tree" --format coverage-py-json --out .\\coverage-results.json
```

A business-function coverage result remains measured only when every mapped source file has matching bytes and every mapped range is within the actual file. Otherwise its coverage status is downgraded to `UNKNOWN` and the per-range reason is retained: `SOURCE_BINDING_NOT_CHECKED`, `SOURCE_FILE_MISSING`, `SOURCE_HASH_MISMATCH`, `SOURCE_ENCODING_UNSUPPORTED`, or `SOURCE_LINE_RANGE_OUT_OF_BOUNDS`. `SOURCE_BYTES_MATCHED` means the supplied mapping digest matches the bytes read from the supplied project root; it is not a digital signature, trusted provenance claim, proof of mapping correctness, or proof of business behavior. The coverage report, mapping, business graph and actual source-file digests remain separately visible in the result.

The `--project-root` must be the exact source tree that produced the coverage report. Do not reuse a mapping after source edits without reviewing and updating its source digest. Test execution and test-source provenance remain separate evidence dimensions; a JUnit/pytest PASS is not proof that the test implementation itself is current or adequate.



### S2.29 — 测试用例到真实测试源码位置的证据绑定

```json
{
  "schema_version": "usit-test-result-mapping-v1",
  "source_graph_sha256": "<sha256 of exact business graph bytes>",
  "mappings": [
    {
      "test_case_id": "tests.PhotoTests::test_upload",
      "business_function_id": "bf-photo-upload",
      "test_source_file": "tests/test_photo.py",
      "test_source_line": 42,
      "test_source_file_sha256": "<64 lowercase hex SHA-256 of exact test source bytes>"
    }
  ]
}
```

```powershell
usit ingest-test-results --junit .\artifacts\junit.xml --mapping .\test-result-mapping.json --graph .\business_knowledge_graph.json --project-root C:\\work\\my-project --out .\artifacts\test-results.json
```

- `test_source_file` 必须是相对于 `--project-root` 的项目内路径；拒绝绝对路径、盘符路径、路径穿越及符号链接/reparse point。
- `test_source_line` 是映射声明的测试源码位置；当前验证器核对其为正整数且不超过真实文件行数。它不证明该行必然定义或执行了对应 testcase。
- `test_source_file_sha256` 必须与项目根目录中读取的真实文件原始字节 SHA-256 完全一致。
- 导入结果逐 testcase 保留 `test_source_binding_evidence`。状态包括 `TEST_SOURCE_BYTES_MATCHED`、`TEST_SOURCE_HASH_MISMATCH`、`TEST_SOURCE_FILE_MISSING`、`TEST_SOURCE_LINE_OUT_OF_BOUNDS`、`TEST_SOURCE_BINDING_NOT_CHECKED` 等。
- 业务证据验证器再次检查匹配状态、声明/实际摘要相等性、文件定位字段和行号范围，并单独输出 `test_source_binding_status`；源码未绑定/未核验不会被伪装成已验证。
- 测试报告中的 PASSED/FAILED 是报告执行结果；测试源码绑定是独立维度。SHA-256 相等仅证明本次读取字节与 mapping 声明一致，不证明来源真实性、测试充分性、测试与报告执行进程的因果关系或业务正确性。
- mapping 声明了测试源码定位但未提供 `--project-root` 时保持 `TEST_SOURCE_BINDING_NOT_CHECKED`；旧 mapping 未声明源码定位时为 `TEST_SOURCE_BINDING_NOT_PROVIDED`。两种状态均不能声称测试源码位置已核验。


## Controlled pytest execution and source-snapshot evidence (S2.30 first implementation)

The run-pytest command is the first controlled execution path. Unlike ingest-test-results, it actually invokes pytest, but only after a separate execution authorization file is supplied.

The authorization JSON is read with `safeio.read_bounded_no_follow` (16 KiB maximum); the final file component must be a regular non-reparse file and is read through the opened descriptor. This avoids an unbounded read and final-component symlink substitution between a path pre-check and file read. Parent-directory components are not claimed to be symlink-free.

```powershell
python -m usit run-pytest "C:\path\to\clean-git-project" --evidence-parent "C:\USIT-Evidence" --authorization-file "C:\USIT-Evidence\test-execution-authorization.json" --pytest-arg "tests/"
```

Authorization file schema (usit-test-execution-authorization-v1):

```json
{
  "schema_version": "usit-test-execution-authorization-v1",
  "project_root": "C:/path/to/clean-git-project",
  "evidence_parent": "C:/USIT-Evidence",
  "allowed_actions": ["run_pytest_on_git_snapshot"],
  "acknowledges_project_code_execution": true,
  "approval_ref": "REPLACE_WITH_REVIEWED_APPROVAL_REFERENCE",
  "authorized_at": "2026-11-03T00:00:00Z",
  "expires_at": "2026-11-03T01:00:00Z"
}
```

The authorization must be created and reviewed by the operator; USIT does not auto-authorize execution. The optional `runner_user` field, when present, must match the `--runner-user` value passed to run-pytest; any mismatch refuses the run. The project must be a clean Git worktree: tracked modifications and untracked files are refused. Git submodules, symlinks and special files in the archived source are refused. USIT stages the committed HEAD with git archive, executes pytest in that separate staging tree, and preserves the exact archived source under the run's source_snapshot/ directory. The evidence bundle includes the JUnit report, source manifest, provenance record, runner plugin, pytest-declared source file/line/qualified symbol/nodeid properties, a runtime-import manifest, preserved source files, pre/post snapshots, logs, final gate report, and a hash-verified evidence manifest. The runtime-import manifest records file-backed project modules observed in sys.modules, their staged relative paths and SHA-256 hashes, and modules unexpectedly loaded from outside the staged snapshot. A project module loaded from the original worktree, an unreadable module source, a missing runtime manifest, or a hash/path mismatch fails the execution gate. The importer independently validates the runtime-import artifact against the provenance digest, evidence manifest, and source manifest. These checks establish byte/path consistency only; they do not authenticate the runner or prove absence of deliberate runtime tampering.

Import the generated report against the preserved snapshot (not the current checkout):

```powershell
python -m usit ingest-test-results --junit "C:\USIT-Evidence\RUN\junit.xml" --mapping "C:\USIT-Evidence\reviewed-test-mapping.json" --graph "C:\USIT-Evidence\business_knowledge_graph.json" --project-root "C:\USIT-Evidence\RUN\source_snapshot" --provenance-record "C:\USIT-Evidence\RUN\execution-provenance.json" --source-manifest "C:\USIT-Evidence\RUN\source-manifest.json" --evidence-dir "C:\USIT-Evidence\RUN" --out "C:\USIT-Evidence\RUN\test-results.json"
```

Important limits:
- This is not an OS sandbox. It does not block project test code from accessing the network, host resources, or any credentials the OS makes available through other channels. The child process receives a reduced environment and no inherited API-key variables, but this is not a security boundary.
- UID isolation (optional, Linux only): pass `--runner-user <name>` to run-pytest (or `runner_user` to `run_controlled_pytest`) to run the test process as a dedicated low-privilege POSIX user instead of the parent user. Prerequisites: the parent process must run as root, the user must already exist on the machine (one-time setup, e.g. `sudo useradd --system --no-create-home --shell /usr/sbin/nologin usit-runner`; USIT never creates users), and the authorization file must name the same user in its optional `runner_user` field. The isolated test process cannot write the evidence directory, the HMAC key file, or the original project tree. The staged source tree is additionally read-only for isolated runs (source files 0444, directories 0555, including the staging root; scripts keep their execute bit), so a test that tries to modify or create files in the tree gets PermissionError; only the JUnit report, the runtime-imports manifest, and the HOME/TMPDIR directories inside staging stay writable. Test suites that legitimately need a writable source tree during the run should use a non-isolated run. It can still read world-readable files, reach the network, and consume host resources, so this is not a sandbox and `provenance_authenticity_status` stays `UNAUTHENTICATED`. Any refusal (non-POSIX platform, non-root parent, unknown user, uid 0, authorization mismatch, staging ownership failure, staging read-only hardening failure) aborts the run instead of silently falling back to the parent UID.
- No dependencies are installed by the runner. Pytest and project dependencies must already be available in the selected Python environment.
- Dirty/untracked worktrees are refused instead of silently omitted; ignored files are not part of the Git archive. The test process runs against committed HEAD only.
- A passing test outcome does not establish business correctness. The report, source snapshot, source symbol, business mapping, and semantic expected-result checks remain separate dimensions.
- Provenance and runtime-import records are currently UNAUTHENTICATED. Runtime-import matching proves that recorded project modules resolve to files whose bytes match the preserved Git snapshot; it is not a cryptographic runner identity and cannot defeat a malicious test process that forges or suppresses evidence. Do not promote these states to authenticated or VERIFIED.
- Tests that modify a source file fail the execution gate. The original archived snapshot is still retained as evidence.
