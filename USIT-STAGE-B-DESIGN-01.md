# USIT-STAGE-B-DESIGN-01｜业务语义关联与自动测试用例生成能力

> 阶段 B 设计文档 · READ-ONLY ANALYSIS + IMPLEMENTATION DESIGN
> 作者：USIT 主开发 · 生成：2026-10-30
> 状态：**STOP — 等待独立审查与下一阶段授权**

---

## 0. 执行模式声明

本阶段为**只读分析 + 实现设计**。已完成以下工作，**未执行**任何真实项目测试、未修改家庭相册、未构建、未安装、未登录、未联网、未调用外部 AI：

| 动作 | 状态 |
|------|------|
| 审查现有工程与模块 | ✅ 已完成 |
| 形成架构设计与数据模型 | ✅ 本文件 |
| 修改家庭相册代码 | ⬜ 未执行（本阶段不允许）|
| 执行真实测试 / 构建 / 安装 / 登录 / 联网 | ⬜ 未执行 |
| 调用外部 AI | ⬜ 未执行 |
| 更新历史冻结哈希 / 解除门禁 | ⬜ 未触碰（无此需求）|

---

## 1. 现状审计

### 1.1 现有模块清单与接口（可复用资产）

| 模块 | 职责 | 阶段 B 复用方式 |
|------|------|----------------|
| `scanner.py` | 符号级扫描（symbols/calls/imports），产出 parser-contract 记录 | 复用其 `discover_files`、`analyze_file`、parser-contract 记录，作为调用链证据源 |
| `graph.py` | 知识图谱（nodes/edges，`RELATION_TYPES = defines/imports/calls/references`）| 复用边结构，作为「功能→API→调用链」关系源 |
| `file_understanding.py` | 文件级结论（`FileConclusion`、冻结分支规则）| 复用文件结论，作为页面/组件归属证据 |
| `business_semantics.py` | 业务语义提取（Entity/Feature/Endpoint/Discrepancy/FieldGap）| **本阶段主要输入**：entities/features/endpoints/discrepancies/field_gaps |
| `understanding_model.py` | 版本化结论模型（`ConclusionType`、`TestSpecification`、`ExecutionRecord`、`ExecutionStatus`、`EvidenceReference`、`UncertaintyRecord`）| **核心复用**：已含测试规格、执行记录、置信度分级 |
| `understanding_projection.py` | 只读投影（parser-contract → 统一结论）| 复用投影模式与确定性 ID 约定 |
| `command_planner.py` | Android 测试命令候选生成（`Candidate`，`execution_status=NOT_EXECUTED`）| **前身**：泛化为跨适配器的用例级生成器 |
| `schema_adapter.py` | schema 适配（`NON_SUCCESS_STATES`）| 复用非成功态约定 |
| `snapshot.py` | 内容哈希捕获（`DescriptorIdentity`/`FileRecord.digest`）| 复用作为执行证据的内容指纹 |
| `safety.py` | 授权门禁（`ScanAuthorization`/`load_authorization_file`/`authorization_record`）| **不变**，所有命令沿用同一门禁 |
| `safeio.py` / `limits.py` | 安全 IO / 预算限制 | 不变 |

### 1.2 关键结论：阶段 B 需要的模型几乎已存在

审查 `understanding_model.py` 发现，阶段 B 设计目标要求的**置信度分级、测试结果模型、测试规格、证据引用**已经建模完成：

- `ConclusionType`：`VERIFIED_FACT / STATIC_INFERENCE / AI_HYPOTHESIS / UNKNOWN / CONFLICT`（目标 2 的置信度分级 ✅）
- `ExecutionStatus`：`NOT_RUN / PASS / FAIL / BLOCKED / NOT_VERIFIED`（目标 8 的测试结果模型 ✅）
- `TestSpecification`：`specification_id / target / purpose / preconditions / identity_and_data_requirements / steps / expected_result / cleanup_strategy / evidence_requirements / execution / evidence_refs`（目标 4 的测试用例字段 ✅ 已有骨架）
- `EvidenceReference`：`relative_path / line / symbol / digest`（目标 2 的证据溯源 ✅）
- `UncertaintyRecord`：`missing_evidence / conflicting_conclusion_ids / minimum_resolution`（目标 5 的 UNKNOWN/CONFLICT 标记 ✅）

**阶段 B 不需要重新造这些轮子，而是在其上增加一层「业务关系模型」和「测试维度识别 + 用例生成」的编排逻辑。**

### 1.3 现有能力缺口（阶段 B 要补的）

| 缺口 | 说明 |
|------|------|
| 关系模型缺失 | `business_semantics` 产出的是**并列的** entities/features/endpoints/discrepancies，没有把它们连成「功能→数据→API→权限→角色→组件」的**关系图** |
| 测试维度缺失 | 没有从功能自动识别「正常/异常/边界/角色权限/数据隔离/跨模块/状态转换/回归」八类维度 |
| 用例生成缺失 | `command_planner` 只生成 Android Gradle 命令候选，不生成「用例级」测试方案，也不覆盖 HTTP API / DB / Web UI |
| 执行适配器缺失 | 无统一执行适配器接口 |
| 缺陷→测试闭环缺失 | discrepancy/field_gap 没有自动转成「影响功能→候选修复→测试用例→回归用例」 |

---

## 2. 设计方案

### 2.1 总体架构：三层编排

```
┌─────────────────────────────────────────────────────────────┐
│  L3 测试用例生成层（新增 test_design.py）                      │
│    功能 → 测试维度识别 → 用例生成 → 风险分级 → 执行类型标注      │
└──────────────────────────┬──────────────────────────────────┘
                           │ 消费
┌──────────────────────────▼──────────────────────────────────┐
│  L2 业务关系模型层（新增 business_relations.py）               │
│    feature → requirement → entity → field → endpoint →        │
│    call_chain → permission → role → component → test_scenario │
│    每条关系带 source/file/line/relation_type/confidence        │
└──────────────────────────┬──────────────────────────────────┘
                           │ 消费
┌──────────────────────────▼──────────────────────────────────┐
│  L1 现有基础层（复用，不改）                                    │
│    business_semantics + scanner + graph + understanding_model  │
│    + command_planner + safety(授权门禁) + snapshot             │
└─────────────────────────────────────────────────────────────┘
```

**关键设计决策**：新增两个模块 `business_relations.py`（L2）和 `test_design.py`（L3），**只读消费** L1 的产出，**不改任何 L1 模块**。这保证了现有接口、数据格式、冻结设计、授权门禁全部不变。

### 2.2 业务关系模型（L2，目标 1/2）

#### 2.2.1 关系类型枚举

```python
RELATION_TYPES = {
    "feature_requires",      # 功能 → 需求条目
    "feature_uses_entity",   # 功能 → 数据表
    "feature_touches_field", # 功能 → 字段
    "feature_calls_endpoint",# 功能 → API
    "endpoint_reads_entity", # API → 数据表
    "endpoint_writes_field", # API → 字段
    "endpoint_requires_role",# API → 角色权限
    "role_can_access",       # 角色 → 对象级权限
    "component_implements_feature", # 页面/组件 → 功能
    "call_chain_reaches",    # 调用链 → 目标符号
    "scenario_covers_feature", # 测试场景 → 功能
}
```

#### 2.2.2 关系记录数据结构

```python
@dataclass(frozen=True)
class BusinessRelation:
    relation_id: str          # 内容派生确定性 ID（复用 _stable_id）
    source: str               # 源节点标识（feature 名 / entity 名 / endpoint path）
    source_kind: str          # feature | entity | field | endpoint | role | component
    relation_type: str        # 上述 RELATION_TYPES 之一
    target: str               # 目标节点标识
    target_kind: str
    evidence: tuple[EvidenceReference, ...]  # 复用 understanding_model
    confidence: ConclusionType                # VERIFIED/STATIC_INFERENCE/AI_HYPOTHESIS/UNKNOWN/CONFLICT
    provenance: str           # 哪条规则生成（可追溯）
```

**证据与置信度规则（目标 2，严格）：**
- 关系来自 `business_semantics` 已带 `file:line` 的证据 → `STATIC_INFERENCE`（因为它是静态提取，不是执行验证）
- 关系是「由字段名/路径名**推断**出的语义关联」→ `AI_HYPOTHESIS`，且必须标记 `limitations`
- 无法从证据确定的关联 → `UNKNOWN`，不猜测
- 两个证据来源对同一关系给出矛盾结论 → `CONFLICT`，保留双方

**注意**：沿用 L1 的硬约束——`VERIFIED_FACT` 在无授权验证协议下**永远不产出**（`understanding_model.ConclusionRecord` 已硬编码拒绝）。

### 2.3 测试维度识别（L3，目标 3）

对每个 feature，按下列八类维度自动识别候选测试方向。识别依据是**证据**（字段类型、权限字段、是否跨表、是否有状态字段、是否被 discrepancy 命中），不是硬编码：

| 维度 | 识别信号（证据驱动） | 示例（家庭相册） |
|------|---------------------|-----------------|
| 正常流程 | feature 存在且 status=DONE | 发布照片成功 |
| 异常流程 | endpoint 有参数校验/400 分支 | 类型错误 / 标题缺失 |
| 边界条件 | 字段有长度/枚举/可空约束 | 超大文件、空 title |
| 角色权限 | entity 有 role/is_core/权限字段 | admin vs member vs 非成员 |
| 数据隔离 | 有 is_secret/is_only_self/family_ids 等可见性字段 | 秘密文件仅自己可见 |
| 跨模块关联 | feature 引用多个 entity 或 endpoint | 上传→存储→Feed 展示 |
| 状态转换 | 有 status/生命周期字段 | 发布→修改→删除 |
| 回归影响 | discrepancy/field_gap 命中的功能 | is_only_self 缺失波及 Feed/管理员/静态资源 |

**维度识别输出**：`feature → [dimension, evidence]` 列表，每条维度带触发它的证据（file:line + 字段名）。

### 2.4 测试用例生成（L3，目标 4/6）

#### 2.4.1 用例数据结构（复用 `TestSpecification` 并扩展）

```python
@dataclass(frozen=True)
class TestCase:
    case_id: str                     # 确定性 ID
    feature: str                     # 关联功能
    dimensions: tuple[str, ...]      # 覆盖的测试维度
    target: str                      # 被测对象（endpoint path / 页面组件 / 数据表）
    purpose: str                     # 测试目标
    preconditions: tuple[str, ...]   # 前置条件（账号、数据、环境）
    identities: tuple[str, ...]      # 账号角色要求（admin/member/非成员/自己/他人）
    input_data: tuple[str, ...]      # 输入数据（用占位符，不编造真实值）
    steps: tuple[str, ...]           # 操作步骤
    expected_result: str | None      # 预期结果；无法确定时 = None + UNKNOWN
    evidence_requirements: tuple[str, ...]  # 证据要求（接口返回/DB记录/文件存在）
    risk_level: str                  # LOW/MEDIUM/HIGH/CRITICAL
    related_code: tuple[str, ...]    # 关联代码位置（file:line）
    execution_class: str             # STATIC_ONLY / REQUIRES_TEST_ENVIRONMENT / REQUIRES_USER_AUTHORIZATION
    execution: ExecutionRecord       # 复用，初始 NOT_RUN
    unknown_flags: tuple[str, ...]   # 需求缺验收标准/权限不清/代码不可追踪 的标记
```

#### 2.4.2 三类执行分类（目标 6）

| 分类 | 判定依据 | 能否自动执行 |
|------|---------|-------------|
| `STATIC_ONLY` | 仅靠静态分析即可验证（如字段引用存在性、鉴权中间件存在性）| 可（仍走授权门禁）|
| `REQUIRES_TEST_ENVIRONMENT` | 需编译/单测/DB 客户端/模拟器 | 需授权 + 环境 |
| `REQUIRES_USER_AUTHORIZATION` | 需真实账号、真实数据、联网、修改 | **绝不自动执行** |

**硬约束**：任何用例的 `execution.status` 初始一律 `NOT_RUN`；只有通过授权门禁 + 执行适配器真实执行后，才由执行层写入 `PASS/FAIL/BLOCKED`。静态生成阶段**永远不产 PASS**（沿用 `ExecutionRecord` 的硬拒绝）。

### 2.5 通用执行适配器接口（目标 7）

```python
class TestExecutorAdapter(Protocol):
    """跨平台测试执行适配器接口。只定义契约，不实现执行。"""
    adapter_kind: str          # android | http_api | database | web_ui | desktop | generic

    def supported(self, case: TestCase) -> bool: ...
    def requires_authorization(self, case: TestCase) -> str: ...
    def plan(self, case: TestCase) -> ExecutorPlan: ...   # 生成执行计划（非执行）
    def execute(self, case: TestCase, plan: ExecutorPlan) -> ExecutionRecord: ...  # 未来，授权后
```

**本阶段只交付接口定义 + `plan()`（执行计划生成）**，`execute()` 留空桩，等待后续授权。这样：
- Android 适配器：映射到 `command_planner` 现有候选
- HTTP API 适配器：映射到 curl/请求结构（不真正发请求）
- Database 适配器：映射到 SQL 断言（不真正连库）
- Web UI / 桌面应用：留扩展位

### 2.6 缺陷→测试闭环（目标 9）

```python
discrepancy / field_gap
   → 影响功能（feature 反向查 relation）
   → 候选修复位置（field_gap.anchors 已有 file:line）
   → 生成回归用例（覆盖该功能的正常 + 权限 + 数据隔离维度）
   → 修复后重跑回归用例
```

具体：`contents.is_only_self` 的 `field_gap` 会生成一组回归用例——自己可见、他人不可见、指定家庭可见、非指定家庭不可见、管理员可见、Feed 过滤、静态资源鉴权——**这正是需求里列的家庭相册验证案例**。

### 2.7 数据模型汇总（新增 schema）

```
business_relations schema:  usit-business-relations-v1
  { relations: [BusinessRelation], relation_count }

test_design schema:         usit-test-design-v1
  { test_cases: [TestCase], dimensions: {...}, execution_class_counts: {...},
    closed_loop: [{discrepancy_id, impacted_features, regression_cases}] }
```

---

## 3. 模块边界与接口定义

### 3.1 新增模块

```
usit/business_relations.py   # L2 业务关系模型
usit/test_design.py          # L3 测试维度识别 + 用例生成 + 闭环
usit/executor_adapter.py     # 执行适配器接口 + plan()（不含 execute 实现）
```

### 3.2 对外入口（新增 CLI 子命令）

```bash
# 只读：生成业务关系 + 测试用例设计（不执行任何测试）
python -m usit design-test <project> --out <dir> --authorization-file <json>
```

产出文件：
- `<out>/business_relations.json`
- `<out>/test_design.json`

沿用 `safety.load_authorization_file` + `canonical_project` + `canonical_output` 门禁，与 `scan`/`understand`/`inspect-android` 完全一致。

### 3.3 不改动的冻结资产（STOP 边界）

| 资产 | 处理 |
|------|------|
| `understanding_model.py` 的硬约束（VERIFIED_FACT/PASS 拒绝）| **不改**，直接复用 |
| `file_understanding.py` 冻结分支规则 | **不改** |
| `safety.py` 授权门禁 / `REQUIRED_FORBIDDEN_ACTIONS` | **不改**，不解除任何门禁 |
| `snapshot.py` 内容哈希捕获 | **不改**（不存在"历史冻结哈希"需更新）|
| `business_semantics.py` / `scanner.py` / `graph.py` 现有接口 | **不改**，只读消费 |
| `command_planner.py` | **不改**，作为 Android 适配器的底层 |

**结论：本设计不触碰任何冻结文件、基线、授权机制或安全门禁，无需 STOP 报备。**

---

## 4. 迁移方案

- **零迁移**：所有 L1 模块保持不变，新增 L2/L3 模块为纯增量。
- 新增 CLI 子命令 `design-test`，不影响现有 `scan`/`understand`/`inspect-android`。
- 现有测试全部保持通过（新增模块不 import 时不被加载，无副作用）。

---

## 5. 家庭相册验证案例（目标 4 验收）

用 `is_only_self` 案例验证设计能否生成「把『仅自己』『指定家庭』『秘密文件』作为独立语义」的用例：

| 用例 | 维度 | 角色 | 执行分类 |
|------|------|------|---------|
| 自己发布「仅自己」内容，自己查看 | 正常流程 + 数据隔离 | 自己 | REQUIRES_USER_AUTHORIZATION |
| 其他普通成员尝试查看「仅自己」内容 | 数据隔离 + 角色权限 | 其他 member | REQUIRES_USER_AUTHORIZATION |
| 指定家庭成员查看 | 数据隔离 + 跨模块 | 指定成员 | REQUIRES_USER_AUTHORIZATION |
| 非指定家庭成员查看 | 数据隔离 | 非指定成员 | REQUIRES_USER_AUTHORIZATION |
| 秘密文件访问（is_secret）| 数据隔离 | 自己/他人/管理员 | REQUIRES_USER_AUTHORIZATION |
| 管理员查看 | 角色权限 | admin | REQUIRES_USER_AUTHORIZATION |
| Feed 查询过滤（is_only_self=0）| 回归影响 + 数据隔离 | 各角色 | REQUIRES_TEST_ENVIRONMENT |
| 静态资源访问（mediaAuth）| 回归影响 | 各角色 | REQUIRES_TEST_ENVIRONMENT |
| 发布→读取→修改→删除全过程 | 状态转换 | 自己 | REQUIRES_USER_AUTHORIZATION |

**关键**：`is_only_self`、`is_secret`、`family_ids`（指定家庭）作为**三个独立字段**各自生成用例，不合并。`field_gap` 命中的 `is_only_self` 会额外触发「回归影响」维度，把 Feed 过滤（contents.js:71/78）、管理员列表（admin.js:32）、静态资源鉴权（mediaAuth.js:17）都纳入回归。

---

## 6. 通用性验收（目标 5）

用**两个结构完全不同的合成项目**验证能力不硬编码家庭相册字段/文件名/业务规则。合成项目在 `tests/fixtures/` 下构造，不依赖真实家庭相册。

**合成项目 1：博客系统（Web + SQL）**
- 语言：Python(Flask) + SQLite；文档：需求.md + 数据库.md + API.md
- 表：`posts`(is_published/author_id)、`comments`、`users`(role)
- 业务：发布/评论/权限（作者可改、访客只读）
- 验证点：识别 `is_published` 与 `author_id` 的权限语义，生成作者/访客/管理员的权限矩阵用例

**合成项目 2：任务管理 CLI（无 Web、无 DB）**
- 语言：Go 或 Rust 风格；文档：spec.md
- 无数据库、无 HTTP API，只有命令行 + 本地 JSON 状态文件
- 业务：任务状态机（todo→doing→done）、优先级
- 验证点：识别「状态转换」维度（无权限字段场景），验证维度识别不依赖 role/is_secret 等字段存在

**验收断言（确定性 + 证据追溯）**：
1. 关系模型对两个项目都能产出，且 relation_type 集合一致（不因项目而异）
2. 权限矩阵只在有权限字段（role/is_published/author_id）时生成，任务管理器不生成权限用例（证明不硬编码）
3. 每条关系/用例带证据 + 置信度，UNKNOWN 时明确标记、不编造 expected_result
4. 输出确定性：同一输入两次运行产生相同 case_id/relation_id（内容派生 ID）

---

## 7. 测试方案（对阶段 B 实现本身的测试）

| 测试文件 | 覆盖 |
|---------|------|
| `tests/test_business_relations.py` | 关系模型：feature→entity→endpoint→role 链路、证据绑定、置信度分级、CONFLICT 检测 |
| `tests/test_test_design.py` | 八类维度识别、用例生成、三类执行分类、未知状态不编造、缺陷→回归闭环 |
| `tests/test_design_universal.py` | 两个合成项目通用性验收（上节）|
| `tests/test_executor_adapter.py` | 适配器接口契约、plan() 生成、execute 留桩 |

**测试命令**（本阶段为设计，实际测试待实现后运行）：
```bash
python -m unittest tests.test_business_relations tests.test_test_design \
    tests.test_design_universal tests.test_executor_adapter
```

---

## 8. 执行报告（本阶段实际状态）

- **现状审计**：✅ 完成（见第 1 节）
- **设计方案**：✅ 完成（见第 2-5 节）
- **文件变更清单**：**本阶段 0 个文件变更**（仅新增本设计文档 `USIT-STAGE-B-DESIGN-01.md`）
- **数据模型**：✅ 定义（第 2.2/2.4/2.7 节）
- **接口示例**：✅ 定义（第 3 节）
- **家庭相册案例**：✅ 设计（第 5 节）
- **两个合成项目验证**：✅ 方案设计（第 6 节，实际验证待实现后执行）
- **测试命令与真实结果**：N/A（本阶段不实现、不执行）
- **PASS/FAIL/NOT_RUN**：全部 `NOT_RUN`（无任何测试被声称通过）
- **未解决问题**：
  1. `execute()` 需要后续单独授权才能实现（当前仅接口 + plan）
  2. `AI_HYPOTHESIS` 类语义关联（如"组件 X 实现功能 Y"）需确认是否允许在 L2 产出，还是全部标 `STATIC_INFERENCE`/`UNKNOWN`
  3. 用例 `input_data` 的占位符规范（避免编造真实测试数据）
- **兼容性风险**：新增模块与 `understanding_model.py` 的 `TestSpecification` 字段可能有语义重叠，需在实现时确认是"复用"还是"包装"，避免两套测试用例模型并存
- **固定门禁状态**：`safety.py` 授权门禁、`understanding_model.py` 的 VERIFIED_FACT/PASS 硬拒绝、`file_understanding.py` 冻结规则——**全部保持原样，未改动**
- **最终结论**：设计完成，架构清晰、零迁移、不触碰冻结门禁。**STOP，等待独立审查。**

---

## 附：需要独立审查确认的三个决策点

1. **L2 是否允许产出 `AI_HYPOTHESIS` 语义关系**（如组件→功能归属），还是本阶段只允许 `STATIC_INFERENCE`/`UNKNOWN`/`CONFLICT`？
2. **用例模型**：复用 `understanding_model.TestSpecification`（扩展）还是新建 `TestCase`（包装）？建议新建 `TestCase` 包装 `TestSpecification`，避免破坏冻结模型，但需审查确认。
3. **`execute()` 的授权边界**：确认后续阶段授权粒度（按 adapter？按 execution_class？按用例？）。

以上三点不阻塞设计交付，但影响实现阶段的具体选择。
