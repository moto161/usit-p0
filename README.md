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


## Business understanding → test-candidate planning

Run the static business-understanding pass and generate evidence-linked test candidates:

```powershell
python -m usit plan-business-tests "C:\\path\\to\\your-project" --out "C:\\USIT-Reports\\business-001" --authorization-file "C:\\USIT-Reports\\auth-business-001.json" --graph "C:\\USIT-Reports\\scan-001\\knowledge_graph.json"
```

The command writes `business_semantics.json` and `business_test_plan.json` to a new output directory outside the target project. It reuses the explicit static-read-only authorization gate. Candidates are derived from discovered PRD features, API endpoints, documented data entities and static discrepancies; each candidate retains source evidence, preconditions, steps, limitations and an explicit `NOT_EXECUTED` status. The planner does not invent acceptance criteria or expected HTTP status codes: candidates remain `CANDIDATE_NEEDS_REQUIREMENTS` until the owner supplies verifiable requirements.

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

The business-semantics pass now extracts a deliberately narrow set of identity/resource patterns: recognizable authenticated-identity expressions (for example, `req.user.id`), request-controlled identity inputs (for example, `req.body.user_id`), explicit same-line owner comparisons, and same-line SQL owner predicates whose call expression also contains one of those identity expressions. Records retain source path/line, expression details, `STATIC_INFERENCE`, `NOT_VERIFIED` and `SAME_LINE_PATTERN`.

The business graph projects these as `identity_evidence` candidate nodes. An identity record is attached to a database-access candidate only when source path and line match exactly; endpoint association receives it only through a database access already linked to its resolved bounded handler call chain. The security test planner can then propose verification candidates for request-controlled identity → SQL owner patterns and authenticated-identity → SQL owner patterns. Neither is treated as proof of a vulnerability or proof of correct enforcement.

This is not yet general data-flow/taint analysis. It does not propagate values across lines, variables, parameters, returns, aliases, closures, imports, middleware, ORM abstractions or control-flow branches; it does not resolve actual SQL parameter arrays or prove that a textually co-occurring expression is the bound value. Missing evidence is UNKNOWN, not evidence of safety. No runtime requests, builds, database operations or target-project modifications are performed.

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

测试规划器将已生成的 `frontend_backend_flow` 候选关联到对应功能视图，分别报告测试设计关联与执行覆盖：`TEST_DESIGN_CANDIDATE_LINKED` 不代表测试执行，`test_execution_coverage` 固定为 `NOT_MEASURED`。尚未接入真实测试运行结果、覆盖率报告或需求追踪矩阵。


## S2.15 — JUnit 测试结果回流（显式映射）

新增 `ingest-test-results` 命令，导入已经完成的 JUnit XML 测试报告，并通过人工审阅的映射 JSON 将精确 testcase ID 关联到 `business_function_id`。命令不执行测试、不访问目标项目、不推断功能关联。

映射格式：

```json
{
  "schema_version": "usit-test-result-mapping-v1",
  "mappings": [
    {"test_case_id": "tests.PhotoTests::test_upload", "business_function_id": "bf-<graph中的真实ID>"}
  ]
}
```

调用示例：

```bash
python -m usit ingest-test-results --junit junit.xml --mapping reviewed-test-map.json --graph business_knowledge_graph.json --out test_execution_results.json
```

功能状态规则：有失败/错误为 `FAILED`；全部映射用例通过为 `PASSED`；存在跳过且没有失败为 `UNKNOWN`；没有匹配执行记录为 `NOT_EXECUTED`。未映射用例、映射但报告中缺失的用例、输入 SHA-256 均保留。JUnit 通过不等于需求完整实现；代码覆盖率仍为 `NOT_MEASURED`。当前版本仅支持 JUnit XML，其他测试报告格式和覆盖率报告尚未接入。


## S2.16 — 多测试框架结果与代码覆盖率回流（部分适配）

- `ingest-test-results` accepts completed JUnit XML (including compatible Android instrumentation reports) and pytest-json-report JSON. Testcase IDs must match the reviewed exact mapping; no fuzzy matching or inferred function linkage.
- `ingest-coverage` accepts coverage.py JSON and JaCoCo XML. Function-level coverage is linked only by a reviewed `source_file + line_start + line_end → business_function_id` mapping.
- Missing report files/ranges remain `UNKNOWN`; no coverage is not treated as 0%. Coverage is separate from test pass/fail and does not establish requirement completeness or correctness.
- These commands import existing reports only. They do not install plugins, run tests, invoke Gradle/ADB, or execute project code.

Example mapping:

```json
{
  "schema_version": "usit-coverage-mapping-v1",
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

Import commands:

```powershell
python -m usit ingest-test-results --junit .\\reports\\junit.xml --mapping .\\test-result-mapping.json --graph .\\business_knowledge_graph.json --out .\\test-execution-results.json
python -m usit ingest-test-results --format pytest-json --junit .\\reports\\pytest-report.json --mapping .\\test-result-mapping.json --graph .\\business_knowledge_graph.json --out .\\pytest-execution-results.json
python -m usit ingest-coverage --format coverage-py-json --coverage .\\reports\\coverage.json --mapping .\\coverage-mapping.json --graph .\\business_knowledge_graph.json --out .\\coverage-results.json
python -m usit ingest-coverage --format jacoco-xml --coverage .\\app\\build\\reports\\jacoco\\testDebugUnitTestCoverage\\testDebugUnitTestCoverage.xml --mapping .\\coverage-mapping.json --graph .\\business_knowledge_graph.json --out .\\jacoco-results.json
```


## S2.17 — 业务功能测试与覆盖证据交叉验证

`verify-evidence` merges an existing `usit-graph-v1` business graph with optional imported test-result and code-coverage artifacts. It emits one evidence row per `business_function_id`, validates artifact schemas and graph SHA-256 identity, and flags unknown function IDs, duplicate IDs, invalid statuses and graph-hash mismatches.

The conclusion vocabulary is deliberately limited:
- `TEST_FAILURE_OBSERVED`: imported runtime report contains at least one failed mapped test.
- `TEST_PASS_AND_CODE_LINES_COVERED`: mapped tests passed and explicitly mapped executable lines are covered.
- `TEST_PASS_WITH_COVERAGE_GAP`: mapped tests passed but mapped code lines are partial or uncovered.
- `TEST_PASS_COVERAGE_NOT_MEASURED`: tests passed but no usable coverage evidence exists.
- `TEST_EVIDENCE_INCONCLUSIVE`: mapped tests were skipped/unknown.
- `NO_RUNTIME_TEST_EVIDENCE`: no imported runtime test evidence exists.
- `EVIDENCE_CONFLICT`: source graph identity or business-function linkage conflicts.

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
python -m usit analyze-gaps --semantics .\\business_semantics.json --graph .\\business_knowledge_graph.json --test-plan .\\business_test_plan.json --evidence-matrix .\\business-evidence-verification.json --out .\\verification-gap-backlog.json
```

The test plan and evidence matrix are optional. The output records SHA-256 hashes of every supplied input. P0/P1/P2 are triage priorities, not severity determinations. Requirements and acceptance criteria still require owner review.


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
- `CORRELATED_EVENTS_OBSERVED` 要求至少存在显式父子事件关联；仅有相同 correlation ID 的阶段共现仍不足以证明因果。
- 未关联业务功能的事件不会按页面名称、API 路由或时间邻近自动猜测归属。
- 图谱 SHA-256 不一致、未知业务功能 ID、缺失父事件、跨关联父子关系和父子时间倒置均保留为冲突。
- 完整 UI→HTTP→后端→数据库链不是每项业务功能的强制模板；缺失阶段表示未观察，不自动认定为缺陷。
- 输出报告记录事件文件与图谱哈希；输入和输出均不得是符号链接，输出不得覆盖已有文件。


### 将运行时观察回填到需求追溯矩阵

完成事件结构校验后，可把校验报告传给 `trace-business`：

```powershell
python -m usit trace-business --semantics .\\business_semantics.json --graph .\\business_knowledge_graph.json --mapping .\\feature-function-mapping.json --test-plan .\\business_test_plan.json --evidence-matrix .\\business-evidence-verification.json --runtime-evidence .\\runtime-evidence-validation.json --out .\\business-traceability-matrix.json --out-md .\\business-traceability-report.md
```

新增的 `runtime_observation` 阶段与 `test_execution` 分开记录：前者只表示事件已观察并通过显式业务功能 ID 关联；后者仍只来自已导入的测试执行报告。需求未获审阅映射时，即使运行事件存在，也不会把事件自动关联到该需求。图谱哈希不匹配会拒绝回填；事件验证报告内部存在冲突时，追溯行保留 `EVIDENCE_CONFLICT`，不把冲突事件标成可信观察。
