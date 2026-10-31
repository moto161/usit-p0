# USIT-STAGE-B 完整执行报告

> 阶段 B：业务语义关联与自动测试用例生成能力 — 设计阶段执行报告
> 生成时间：2026-10-30 21:58
> 执行模式：READ-ONLY ANALYSIS + IMPLEMENTATION DESIGN（未执行任何真实测试、未改家庭相册、未构建/安装/登录/联网/调外部 AI）
> 状态：**STOP — 等待独立审查与下一阶段授权**

---

## 1. 现状审计（已完成 ✅）

### 1.1 审查范围

审查了 `usit/` 包下全部 29 个模块，重点核对与阶段 B 相关的 11 个可复用模块及其对外接口：

| 模块 | 审查结论 | 阶段 B 复用方式 |
|------|---------|----------------|
| `scanner.py` | 符号级扫描，产出 parser-contract 记录 | 复用 `discover_files`/`analyze_file` 作为调用链证据源 |
| `graph.py` | 知识图谱，`RELATION_TYPES = defines/imports/calls/references` | 复用边结构作为「功能→API→调用链」关系源 |
| `file_understanding.py` | 文件级结论（`FileConclusion`，冻结 DESIGN-10..14 分支规则）| 复用文件结论作为页面/组件归属证据 |
| `business_semantics.py` | 业务语义（Entity/Feature/Endpoint/Discrepancy/FieldGap）| **主要输入** |
| `understanding_model.py` | 版本化结论模型 | **核心复用**（详见 1.2）|
| `understanding_projection.py` | 只读投影 + 确定性 ID | 复用投影模式 |
| `command_planner.py` | Android 测试命令候选生成 | **前身**，泛化为跨适配器用例级生成器 |
| `schema_adapter.py` | schema 适配，`NON_SUCCESS_STATES` | 复用非成功态约定 |
| `snapshot.py` | 内容哈希捕获（`DescriptorIdentity`/`FileRecord.digest`）| 复用为执行证据内容指纹 |
| `safety.py` | 授权门禁（`ScanAuthorization`/`load_authorization_file`）| **不变** |
| `safeio.py` / `limits.py` | 安全 IO / 预算限制 | 不变 |

### 1.2 关键审计结论：阶段 B 所需模型大部分已存在

审查 `understanding_model.py`（632 行）确认，设计目标 2/4/8 要求的模型**已经建模完成**，无需重造：

| 设计目标要求的模型 | 现有实现 | 结论 |
|-------------------|---------|------|
| 置信度分级（VERIFIED/STATIC_INFERENCE/AI_HYPOTHESIS/UNKNOWN/CONFLICT）| `ConclusionType` enum | ✅ 已存在 |
| 测试结果模型（PASS/FAIL/BLOCKED/NOT_RUN）| `ExecutionStatus` enum | ✅ 已存在（含 NOT_VERIFIED）|
| 测试用例字段 | `TestSpecification` dataclass（specification_id/target/purpose/preconditions/steps/expected_result/evidence_requirements/execution）| ✅ 骨架已存在 |
| 证据溯源（file:line/digest）| `EvidenceReference` | ✅ 已存在 |
| 未知/冲突标记 | `UncertaintyRecord` | ✅ 已存在 |

**关键硬约束（阶段 B 必须继承，不可破坏）：**
- `ConclusionRecord` 硬拒绝构造 `VERIFIED_FACT`（无授权验证协议时）
- `ExecutionRecord` 硬拒绝非 `NOT_RUN` 状态（PASS 需外部验证记录）
- 这三条是冻结设计，阶段 B 只继承、不修改。

### 1.3 能力缺口（阶段 B 要补的 5 项）

1. **关系模型缺失**：entities/features/endpoints/discrepancies 是并列的，无「功能→数据→API→权限→角色→组件」关系图
2. **测试维度缺失**：无「正常/异常/边界/角色权限/数据隔离/跨模块/状态转换/回归」八类维度识别
3. **用例生成缺失**：`command_planner` 只生成 Android Gradle 命令候选，非用例级，不覆盖 HTTP/DB/Web UI
4. **执行适配器缺失**：无统一执行适配器接口
5. **缺陷→测试闭环缺失**：discrepancy/field_gap 未自动转成「影响功能→候选修复→测试用例→回归用例」

---

## 2. 设计方案（已完成 ✅）

### 2.1 总体架构：三层编排（零迁移）

```
L3 测试用例生成层（新增 test_design.py）
L2 业务关系模型层（新增 business_relations.py）
L1 现有基础层（复用，不改）
```

### 2.2 数据模型

**关系模型**（`business_relations.py`，schema `usit-business-relations-v1`）：
- 11 类关系：`feature_requires` / `feature_uses_entity` / `feature_touches_field` / `feature_calls_endpoint` / `endpoint_reads_entity` / `endpoint_writes_field` / `endpoint_requires_role` / `role_can_access` / `component_implements_feature` / `call_chain_reaches` / `scenario_covers_feature`
- 每条关系：`relation_id`（内容派生）+ `source`/`source_kind` + `relation_type` + `target`/`target_kind` + `evidence(file:line)` + `confidence`

**测试用例模型**（`test_design.py`，schema `usit-test-design-v1`）：
- `TestCase`：case_id/feature/dimensions/target/purpose/preconditions/identities(角色)/input_data/steps/expected_result/evidence_requirements/risk_level/related_code/execution_class/execution/unknown_flags
- 三类执行分类：`STATIC_ONLY` / `REQUIRES_TEST_ENVIRONMENT` / `REQUIRES_USER_AUTHORIZATION`（后者绝不自动执行）

**执行适配器**（`executor_adapter.py`）：
- `TestExecutorAdapter` 协议：android/http_api/database/web_ui/desktop，本阶段只交付 `plan()`，`execute()` 留桩

### 2.3 缺陷→测试闭环

```
discrepancy/field_gap → 影响功能 → 候选修复位置(anchors) → 回归用例
```

### 2.4 新增 CLI 子命令

```bash
python -m usit design-test <project> --out <dir> --authorization-file <json>
```
沿用 `safety` 授权门禁，与 `scan`/`understand`/`inspect-android` 完全一致。

---

## 3. 文件变更清单（已核对，如实 ✅）

| 文件 | 变更类型 | 内容 |
|------|---------|------|
| `USIT-STAGE-B-DESIGN-01.md` | **新增** | 阶段 B 设计文档（22 KB）|
| `usit/*.py` | **零变更** | 阶段 B 未新增/修改任何实现代码（已核实：无 business_relations/test_design/executor_adapter）|
| `tests/*.py` | **零变更** | 阶段 B 未新增/修改任何测试 |
| 家庭相册项目 | **零变更** | 未触碰 |

**注**：磁盘上 `business_semantics.py`(21:49)、`cli.py`(21:50) 的时间戳是**阶段 A**（上一轮）的 field_gap 定位 + CLI 摘要改动，非阶段 B 改动。阶段 B（21:52 起）未改任何 `usit/` 代码。

---

## 4. 家庭相册验证案例（设计层面 ✅，未执行）

用 `is_only_self` 案例验证设计能生成 9 类用例，把「仅自己」「指定家庭」「秘密文件」作为**三个独立字段**各自生成、不合并：

| 用例 | 维度 | 角色 | 执行分类 |
|------|------|------|---------|
| 自己发布「仅自己」内容，自己查看 | 正常流程 + 数据隔离 | 自己 | REQUIRES_USER_AUTHORIZATION |
| 其他普通成员尝试查看 | 数据隔离 + 角色权限 | 其他 member | REQUIRES_USER_AUTHORIZATION |
| 指定家庭成员查看 | 数据隔离 + 跨模块 | 指定成员 | REQUIRES_USER_AUTHORIZATION |
| 非指定家庭成员查看 | 数据隔离 | 非指定成员 | REQUIRES_USER_AUTHORIZATION |
| 秘密文件访问（is_secret）| 数据隔离 | 自己/他人/管理员 | REQUIRES_USER_AUTHORIZATION |
| 管理员查看 | 角色权限 | admin | REQUIRES_USER_AUTHORIZATION |
| Feed 查询过滤（is_only_self=0）| 回归影响 | 各角色 | REQUIRES_TEST_ENVIRONMENT |
| 静态资源访问（mediaAuth）| 回归影响 | 各角色 | REQUIRES_TEST_ENVIRONMENT |
| 发布→读取→修改→删除全过程 | 状态转换 | 自己 | REQUIRES_USER_AUTHORIZATION |

`field_gap` 命中的 `is_only_self` 额外触发「回归影响」维度，覆盖 Feed 过滤（contents.js:71/78）、管理员列表（admin.js:32）、静态资源鉴权（mediaAuth.js:17）。

---

## 5. 通用性验收方案（设计层面 ✅，未执行）

两个结构完全不同的合成项目（`tests/fixtures/` 下构造，不依赖真实家庭相册）：

1. **博客系统**（Python/Flask + SQLite）：`is_published`/`author_id`/`role` → 验证权限矩阵
2. **任务管理 CLI**（无 DB、无 HTTP）：todo→doing→done 状态机 → 验证「状态转换」维度识别不依赖 role/is_secret 字段

验收断言：relation_type 集合不因项目而异、权限矩阵只在有权限字段时生成、UNKNOWN 不编造、输出确定性（内容派生 ID）。

---

## 6. 测试命令与真实结果（如实填写）

| 项 | 值 |
|----|----|
| 本阶段新增测试 | 0 个（未实现 L2/L3，故无对应测试）|
| 本阶段运行测试 | 0 次（纯设计阶段，无实现可测）|
| 现有测试套件规模 | 40 个测试文件（阶段 A 遗留，非本阶段产物）|
| 本阶段真实测试结果 | **N/A** |

**说明**：阶段 B 为 READ-ONLY + DESIGN，不实现、不执行。测试方案见设计文档第 7 节（`test_business_relations.py` / `test_test_design.py` / `test_design_universal.py` / `test_executor_adapter.py`），待实现阶段运行。

---

## 7. PASS / FAIL / NOT_RUN（如实填写）

| 测试项 | 状态 |
|--------|------|
| 现状审计 | 完成（非测试，无 PASS/FAIL 语义）|
| 设计方案 | 完成（非测试）|
| 家庭相册验证案例 | **NOT_RUN**（仅设计）|
| 两个合成项目验证 | **NOT_RUN**（仅方案）|
| 所有测试用例 | **NOT_RUN**（无任何测试被声称通过）|

**阶段 B 未声称任何 PASS。** 所有验证均为设计层面，无执行结果。

---

## 8. 未解决问题（3 项，需独立审查决策）

1. **L2 是否允许产出 `AI_HYPOTHESIS` 语义关系**（如"组件 X 实现功能 Y"），还是本阶段只允许 STATIC_INFERENCE/UNKNOWN/CONFLICT？
2. **用例模型**：新建 `TestCase` 包装 `TestSpecification`（避免破坏冻结模型）还是直接扩展 `TestSpecification`？
3. **`execute()` 授权粒度**：按 adapter？按 execution_class？按用例？

---

## 9. 兼容性风险

1. 新增 `TestCase` 与 `understanding_model.TestSpecification` 字段语义重叠，需在实现时确认"复用 vs 包装"，避免两套测试用例模型并存。
2. `AI_HYPOTHESIS` 类语义关联的边界未定，可能影响 L2 的产出范围（见决策点 1）。

---

## 10. 固定门禁状态（如实，未改动 ✅）

| 门禁 | 状态 |
|------|------|
| `safety.py` 授权门禁（`REQUIRED_FORBIDDEN_ACTIONS`）| 未改动 |
| `understanding_model.py` 硬约束（VERIFIED_FACT/PASS 拒绝）| 未改动 |
| `file_understanding.py` 冻结分支规则 | 未改动 |
| `snapshot.py` 内容哈希捕获 | 未改动 |
| 任何历史冻结哈希 / 基线 | 未触碰（经核实：`snapshot.digest` 是扫描时重新捕获的内容哈希，非"需保持的历史冻结哈希"）|

---

## 11. 最终结论

阶段 B 设计完成。架构清晰、零迁移、不触碰任何冻结门禁，三个决策点不阻塞设计但影响实现选择。

**STOP — 等待独立审查与下一阶段授权。**

---

### 附：交付物清单

| 文件 | 说明 |
|------|------|
| `USIT-STAGE-B-DESIGN-01.md` | 阶段 B 设计文档（含架构/数据模型/接口/案例/验收/风险）|
| `USIT-STAGE-B-DESIGN-01-EXECUTION-REPORT.md` | 本执行报告 |
