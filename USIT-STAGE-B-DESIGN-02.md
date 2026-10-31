# USIT-STAGE-B-DESIGN-02｜独立审查意见落实与接口契约修订

> 阶段 B 设计修订 · READ-ONLY ANALYSIS + DESIGN REVISION
> 作者：USIT 主开发 · 生成：2026-10-30
> 上游：USIT-STAGE-B-DESIGN-01（PASS-WITH-ISSUES）+ 独立审查结论
> 状态：**STOP — 等待独立审查与下一阶段授权**（未授权 IMPLEMENT-01）

---

## 0. 执行模式声明

本工单为**只读分析 + 设计修订**。已完成的动作、未执行的动作如实记录：

| 动作 | 状态 |
|------|------|
| 审查 DESIGN-01 与独立审查意见 | ✅ 已完成 |
| 核对冻结模型字段（understanding_model / business_semantics）| ✅ 已完成 |
| 修订设计、补齐接口契约 | ✅ 本文件 |
| 修改 `usit/*.py` / `tests/*.py` | ⬜ 未执行（工单禁止）|
| 创建实现模块（business_relations/test_design/executor_adapter）| ⬜ 未执行 |
| 运行真实测试 / 构建 / 安装 / 登录 / 联网 / 外部 AI | ⬜ 未执行 |
| 更新历史冻结哈希 / 解除门禁 | ⬜ 未执行 |

**基线同步说明（非本工单变更）**：本工单开工前，为让 DESIGN-02 有真实设计基线，已将 Desktop 副本中的阶段 A 成果复制进正式工作区（详见执行报告 §文件变更清单）。此同步不覆盖任何已存在文件、不触碰 S1-C 冻结基线 `scanner.py=58ba5f25…`。

---

## 1. 审查意见裁定 → 设计落实（总览）

独立审查对三个待决策问题给出裁定，DESIGN-02 按裁定落地：

| 决策 | 裁定 | DESIGN-02 落实位置 |
|------|------|-------------------|
| L2 是否允许 AI_HYPOTHESIS | 允许，严格隔离 | §3.4 置信度分级规则 |
| 用例模型 | 复用 TestSpecification，外层包装 TestCase | §5.1 用例模型 |
| execute() 授权粒度 | 按单个用例授权，执行前再校验 | §7.3 执行授权契约 |

---

## 2. 关系模型修订（回应审查问题 1/2/3）

### 2.1 节点类型与关系类型分离（问题 1）

DESIGN-01 的关系类型 `feature_uses_entity` 等**同时混合了节点语义和关系语义**，导致无法表达 CLI/桌面/Android 的命令、组件、状态、事件、数据存储等关系。

DESIGN-02 将**节点类型**与**关系类型**彻底分离：

#### 2.1.1 节点类型（`NODE_KINDS`，可扩展注册）

```python
# 节点类型是"这个实体是什么"，与"它和别的东西是什么关系"无关。
NODE_KINDS = {
    # 语义层（所有项目通用）
    "feature",        # 业务功能
    "requirement",    # 需求条目
    "entity",         # 数据表/集合/持久化对象
    "field",          # 字段/属性/列
    "endpoint",       # API 端点 / HTTP 路由
    "role",           # 用户角色
    "permission",     # 权限/可见性规则
    # 结构与交互层（跨项目类型）
    "component",      # 页面/UI 组件/Widget
    "module",         # 代码模块/包
    "command",        # CLI 命令 / 入口函数
    "state",          # 状态 / 生命周期阶段
    "event",          # 事件 / 消息 / 广播
    "store",          # 数据存储（文件/DB/缓存/内存）
    "symbol",         # 代码符号（函数/类/方法）
    "test_scenario",  # 测试场景
}
```

**注册机制**：`NODE_KINDS` 是**可扩展注册表**，未来 Android 的 `activity`/`fragment`、桌面的 `window`、Web 的 `page`/`route` 可作为新节点类型注册，**不需要修改关系模型本身**。新增节点类型不要求现有项目产出。

#### 2.1.2 关系类型（`RELATION_TYPES`，稳定集合）

```python
# 关系类型是"两个节点之间是什么语义关联"，与节点类型无关。
# 稳定集合：不随项目类型变化，新增须走版本升级，不得静默修改。
RELATION_TYPES = {
    # 溯源关系
    "derives_from",       # 派生自（feature → requirement）
    "documented_by",      # 被记录于（node → 文档）
    # 结构关系
    "contains",           # 包含（module → symbol / entity → field）
    "implements",         # 实现（component/symbol → feature）
    "uses",               # 使用（feature → entity/store/symbol）
    "reads",              # 读（endpoint/command → entity/field/store）
    "writes",             # 写（endpoint/command → entity/field/store）
    "invokes",            # 调用（feature/command → endpoint/symbol）
    # 权限关系
    "requires_role",      # 需要角色（endpoint/feature → role）
    "grants_access",      # 授予访问（role → permission/entity）
    "restricted_by",      # 受限于（entity/field → permission）
    # 流转关系
    "transitions_to",     # 状态转换（state → state）
    "emits",              # 发出（feature/command → event）
    "handles",            # 处理（symbol/command → event）
    # 测试关系
    "covers",             # 覆盖（test_scenario → feature）
}
```

**关键规则（问题 1 落实）**：
- 节点类型与关系类型**正交**：任何节点类型理论上可参与任何语义上合理的关系类型，组合由证据决定，不由类型系统预设。
- 关系类型集合**稳定**：不要求每个项目产出全部关系；任务管理 CLI 不产出 `requires_role`/`grants_access` 是合法的，不是缺失。
- 新增关系类型须走 schema 版本升级（`usit-business-relations-v2`），不得静默修改 v1 语义。

### 2.2 关系记录数据结构（含证据追溯）

```python
@dataclass(frozen=True)
class BusinessRelation:
    relation_id: str          # 内容派生确定性 ID（_stable_id）
    source_id: str            # 源节点标识
    source_kind: str          # NODE_KINDS 之一
    relation_type: str        # RELATION_TYPES 之一
    target_id: str            # 目标节点标识
    target_kind: str          # NODE_KINDS 之一
    evidence: tuple[EvidenceReference, ...]   # 复用 understanding_model（17 字段）
    confidence: ConclusionType                # 见 §3.4
    provenance: str           # 生成规则 ID（可追溯到哪条规则产出）
    rationale: tuple[str, ...] = ()           # AI_HYPOTHESIS 时的推断依据
    limitations: tuple[str, ...] = ()         # 限制条件
    digest: str | None = None                 # 源文件内容哈希（失效判定用，见 §8）
```

### 2.3 证据验证规则（回应问题 2：不能机械按来源赋值）

**DESIGN-01 的核心缺陷**：它默认"`business_semantics` 里有 file:line → 关系成立 → STATIC_INFERENCE"，这是**机械按来源赋值**，把"有定位证据"当成了"证据支持该关系"。

DESIGN-02 改为**证据↔关系对应性校验**，每条关系必须能回答："这条证据是否真的支撑 `source -[relation_type]-> target` 这个断言？"

```python
def _validate_relation_evidence(relation: BusinessRelation) -> ConclusionType:
    """
    返回该关系应得的置信度。核心原则：
    - file:line 只证明"这里有定位"，不证明"这行代码支撑该关系语义"。
    - 必须核对证据内容（symbol/context/summary）与关系断言是否对应。
    """
    if not relation.evidence:
        return ConclusionType.UNKNOWN          # 无证据 → 不确定，不生成确定性关系

    # 逐条证据核对"是否支撑该关系"
    for ev in relation.evidence:
        # 1. 证据必须可定位（relative_path 或 source_id 至少一个）
        if ev.relative_path or ev.source_id:
            # 2. 证据语义必须与关系类型对应（见下表）
            if _evidence_supports(ev, relation.relation_type):
                return ConclusionType.STATIC_INFERENCE
    return ConclusionType.UNKNOWN              # 有定位但语义不对应 → UNKNOWN，不冒充分事实
```

**证据↔关系对应表（每种关系类型要求的最小证据）**：

| relation_type | 最小证据要求 | 不满足时 |
|---------------|-------------|---------|
| `derives_from` | 需求文档行 + 功能名同时出现 | UNKNOWN |
| `reads` / `writes` | SQL/代码行含该 entity/field 的引用（非注释）| UNKNOWN |
| `invokes` | 调用链边（graph `calls`）或路由行含 endpoint | UNKNOWN |
| `requires_role` | 鉴权中间件行（authRequired/adminRequired）| UNKNOWN |
| `grants_access` / `restricted_by` | 权限字段 + 判断逻辑行 | UNKNOWN |
| `implements` | 组件/符号定义行 + 功能名（同名或注释引用）| AI_HYPOTHESIS（见下）|
| `transitions_to` | 状态字段 + 状态赋值/转换行 | UNKNOWN |

**关键结论（问题 2 落实）**：
- 有 `file:line` 但无法证明"该行支撑该关系语义" → **`UNKNOWN`**，不是 STATIC_INFERENCE。
- 只有证据内容与关系断言**逐条对应**时，才标 `STATIC_INFERENCE`。
- `implements`（组件→功能）这类语义关联，即使有符号名相似，本质仍是**推断**，归 `AI_HYPOTHESIS`。

---

## 3. 置信度分级规则（回应审查裁定 1 + 问题 2）

### 3.1 五级置信度（复用 `ConclusionType`，不新建）

```python
VERIFIED_FACT      # 禁止设计阶段产生（沿用冻结硬约束）
STATIC_INFERENCE   # 有可定位证据，且证据内容与断言逐条对应
AI_HYPOTHESIS      # 有推断依据，但非静态事实；必须记录 rationale + limitations
UNKNOWN            # 证据不足，不生成确定性关系
CONFLICT           # 多个来源互相矛盾，保留双方，不选边
```

### 3.2 各级别的生成规则（严格）

| 级别 | 生成条件 | 禁止条件 |
|------|---------|---------|
| `STATIC_INFERENCE` | 证据可定位 + 证据语义与断言对应（§2.3 表）| 证据仅靠字段名/路径名相似 |
| `AI_HYPOTHESIS` | 有推断依据（如符号名相似、注释提及），但无法静态证实 | 不得冒充 STATIC_INFERENCE；必须带 rationale |
| `UNKNOWN` | 无充分证据 | 不得生成确定性关系、不得生成确定性测试预期 |
| `CONFLICT` | 两来源对同一关系/断言给出矛盾结论 | 不得自动选边 |
| `VERIFIED_FACT` | 无（本阶段禁止）| 永远不产出 |

### 3.3 AI_HYPOTHESIS 的强制字段（审查裁定 1 落实）

任何 `AI_HYPOTHESIS` 关系**必须**携带：
- `rationale`：推断依据（"组件名 `AlbumScreen` 与功能名 `相册` 相似"）
- `provenance`：生成规则 ID
- `limitations`：限制条件（"未验证组件实现逻辑，仅为命名推断"）

**且 AI_HYPOTHESIS 关系不得作为确定性测试预期的依据**（见 §6.4）。

### 3.4 与 business_semantics 的衔接（关键修订）

DESIGN-01 假设 business_semantics 的 `VERIFIED_FACT` 可直接复用。**这是错的**：business_semantics 用 `CONF_VERIFIED="VERIFIED_FACT"` 标记"从文档读到的实体/字段/功能"，违反"禁止设计阶段产生 VERIFIED_FACT"。

DESIGN-02 明确：**business_semantics 的产出在本阶段一律降级对待**：

| business_semantics 原置信度 | DESIGN-02 重新映射 |
|----------------------------|-------------------|
| `VERIFIED_FACT`（读自文档）| `STATIC_INFERENCE`（有定位证据，但非执行验证）|
| `STATIC_INFERENCE` | `STATIC_INFERENCE`（若证据对应）或 `AI_HYPOTHESIS`（若仅命名推断）|
| `CONFLICT` | `CONFLICT`（保留）|

**注意**：此映射只影响 L2/L3 消费 business_semantics 时的置信度解释，**不改 business_semantics.py 本身**（它是阶段 A 成果，本阶段禁止改代码）。若后续要修正 business_semantics 的 `VERIFIED_FACT` 误用，须另开工单、单独授权。

---

## 4. 八类测试维度修订（回应问题 3）

DESIGN-01 的维度识别有"重复和误判风险"：仅凭"存在 400 分支"就认定异常流程已识别、仅凭字段名含 `secret` 就认定数据隔离。这是把**触发信号**当成了**确定性结论**。

DESIGN-02 为每个维度定义**四要素**：触发条件、必要证据、排除条件、置信度。

| 维度 | 触发条件（候选信号）| 必要证据（确认为该维度）| 排除条件 | 置信度 |
|------|-------------------|----------------------|---------|--------|
| 正常流程 | feature 存在且 status=DONE | 该 feature 有对应 endpoint/command/symbol | 无实现证据 | STATIC_INFERENCE / UNKNOWN |
| 异常流程 | endpoint 有校验/错误返回分支 | 分支行确实含错误处理（400/throw/return error）| 仅参数存在无处理 | STATIC_INFERENCE / UNKNOWN |
| 边界条件 | 字段有长度/枚举/可空约束 | 约束值来自文档或 schema（非猜测）| 约束仅靠字段名猜 | UNKNOWN |
| 角色权限 | entity 有 role 字段 或 endpoint 有 authRequired | 鉴权中间件/角色判断行真实存在 | 仅有 role 列无判断逻辑 | STATIC_INFERENCE / UNKNOWN |
| 数据隔离 | 有可见性字段（is_secret 等）| 可见性字段**确实被过滤逻辑引用**（SQL WHERE/JOIN）| 字段名含 secret 但零引用 | STATIC_INFERENCE / UNKNOWN |
| 跨模块关联 | feature 引用多个 entity/endpoint | 关系图里存在 ≥2 条跨节点关系 | 单节点 | STATIC_INFERENCE |
| 状态转换 | 有 status/生命周期字段 | 状态字段有赋值/转换语句 | 仅字段存在无转换 | UNKNOWN |
| 回归影响 | discrepancy/field_gap 命中 | discrepancy 有 file:line 证据 | 无证据 | STATIC_INFERENCE |

**关键规则（问题 3 落实）**：
- 字段名、路径名、400 分支**只能触发候选假设**，不能直接形成确定性维度结论。
- 每个维度结论必须标注置信度；证据不足时标 `UNKNOWN`，不得升级为"已识别"。
- 「数据隔离」维度：仅凭 `is_secret` 字段名存在 → 候选；必须核实该字段**被过滤逻辑引用**（这正是 business_semantics 的 `field_gap`/`documented_field_unimplemented` 检测能提供的证据）→ 才确认。

---

## 5. 测试用例模型修订（回应审查裁定 2 + 架构要求 1）

### 5.1 用例模型：TestCase 包装 TestSpecification（裁定 2 落实）

DESIGN-02 采用 **`TestCase` 外层包装 `TestSpecification`**，不修改冻结模型、不复制其字段：

```python
@dataclass(frozen=True)
class TestCase:
    """业务级测试用例，包装冻结的 TestSpecification。"""
    case_id: str                      # 确定性 ID（内容派生）
    spec: TestSpecification           # 复用冻结模型（14 字段，原样持有，不复制）
    # ---- 业务维度（TestSpecification 没有、业务层新增）----
    feature: str                      # 关联功能
    dimensions: tuple[str, ...]       # 覆盖的测试维度（§4 八类）
    risk_level: str                   # LOW/MEDIUM/HIGH/CRITICAL
    execution_class: str              # STATIC_ONLY / REQUIRES_TEST_ENVIRONMENT / REQUIRES_USER_AUTHORIZATION
    # ---- 证据二分（架构要求 1 落实，见 §5.2）----
    requirement_evidence: tuple[EvidenceReference, ...] = ()
    implementation_evidence: tuple[EvidenceReference, ...] = ()
    expected_result_confidence: ConclusionType = ConclusionType.UNKNOWN
    oracle_status: str = "NO_ORACLE"  # VERIFIED_ORACLE / INFERRED_ORACLE / NO_ORACLE
    # ---- 可追溯闭环（架构要求 2 落实，见 §8）----
    relation_ids: tuple[str, ...] = ()   # 反向追溯到 BusinessRelation
    source_digests: tuple[str, ...] = () # 源文件哈希，失效判定
```

**字段映射规则**：
- `TestSpecification` 已有的字段（target/purpose/preconditions/steps/expected_result/evidence_requirements/execution/...）**全部原样由 spec 持有**，TestCase 不重复定义。
- TestCase 只新增"业务维度 + 证据二分 + 追溯"三类字段，与 TestSpecification 无重叠。
- `input_data` 问题：DESIGN-01 有 `input_data` 字段，但 TestSpecification 用 `identity_and_data_requirements` 表达账号/数据要求。**DESIGN-02 删除 TestCase.input_data**，统一用 spec.identity_and_data_requirements 表达，且规范为占位符（见 §5.3）。

### 5.2 证据二分：requirement_evidence vs implementation_evidence（架构要求 1 落实）

审查指出："代码允许普通成员访问某资源，不代表业务规则允许"。DESIGN-02 强制每个用例区分两类证据：

| 字段 | 含义 | 来源 |
|------|------|------|
| `requirement_evidence` | 业务规则/需求依据（"业务上应该怎样"）| 需求文档、PRD 行 |
| `implementation_evidence` | 当前代码实现依据（"代码现在怎样"）| 源码行、路由、SQL |

**关键规则**：
- 两者**独立**：业务规则允许 ≠ 代码实现允许，反之亦然。两者不一致时是 `CONFLICT` 关系，不是用例错误。
- 没有 `requirement_evidence` 时，可生成**探索性测试**（execution_class 仍可标注），但 `oracle_status` 必须为 `NO_ORACLE`，且 `expected_result` 必须为 None（不编造判定标准）。

### 5.3 expected_result 与 oracle_status（架构要求 1 落实）

```python
# oracle_status 三态：
#   VERIFIED_ORACLE  — 有明确业务规则证据，可给出确定性 expected_result
#   INFERRED_ORACLE  — 预期结果从实现推断，置信度 STATIC_INFERENCE 或 AI_HYPOTHESIS
#   NO_ORACLE        — 无可靠业务依据，expected_result 必须为 None，不编造 PASS/FAIL
```

**硬规则**：
- `expected_result` 非空 **当且仅当** `oracle_status in (VERIFIED_ORACLE, INFERRED_ORACLE)`。
- `oracle_status == NO_ORACLE` 时，`expected_result` 强制 None，`expected_result_confidence == UNKNOWN`。
- 不得因"存在 admin 角色"就默认"管理员可访问所有内容"——这是未经证据的推断，必须标 `AI_HYPOTHESIS` 或 `UNKNOWN`（见 §6 家庭相册案例修正）。

---

## 6. 家庭相册案例修订（回应问题 4）

DESIGN-01 的家庭相册用例表有**未经证明的预期**，例如"管理员查看""指定家庭可见""静态资源鉴权"被默认成了确定性预期。DESIGN-02 逐条修正：

### 6.1 三个字段独立建模（保持，正确）

`is_only_self`（仅自己）、`is_secret`（秘密）、`family_ids`（指定家庭）作为**三个独立字段**，各自生成用例，不合并。这是正确的，保留。

### 6.2 未经证据的预期 → 降级（问题 4 落实）

| DESIGN-01 用例 | 问题 | DESIGN-02 修正 |
|---------------|------|---------------|
| "管理员查看"（默认 admin 可见所有）| 无证据证明 admin 可访问 | oracle_status=INFERRED_ORACLE（仅 admin.js 有 `is_secret=0` 过滤的证据）或 NO_ORACLE |
| "指定家庭可见" | 依赖 family_ids 的 content_families 关联，需核实 | 仅当 content_families 关联代码被证实，才 STATIC_INFERENCE |
| "静态资源访问（mediaAuth）" | 需 mediaAuth.js 的鉴权规则证据 | mediaAuth.js 确实存在 `is_secret=1` 判断（已证实），可 STATIC_INFERENCE；但 `is_only_self` 未实现，故该用例是"回归影响"而非"现状验证" |

**关键修正原则**：每个用例的 `requirement_evidence` 和 `implementation_evidence` 必须分别填实，缺一即标 `NO_ORACLE` 或 `INFERRED_ORACLE`，**不得默认确定性预期**。

### 6.3 具体案例重写（示例）

以"管理员查看"为例，DESIGN-02 的用例形态：

```python
TestCase(
    case_id="case_...",
    spec=TestSpecification(
        specification_id="spec_...",
        target="contents 列表（admin）",
        purpose="验证管理员对 is_only_self 内容的可见性",
        preconditions=("admin 账号",),
        identity_and_data_requirements=("role=admin",),
        steps=("调用 GET /contents 作为 admin",),
        expected_result=None,          # 无业务规则证据 → 不编造
        ...
    ),
    feature="内容可见性",
    dimensions=("角色权限", "数据隔离"),
    risk_level="HIGH",
    execution_class="REQUIRES_USER_AUTHORIZATION",
    requirement_evidence=(),          # 需求文档未明确 admin 对"仅自己"的可见性 → 空
    implementation_evidence=(
        EvidenceReference(relative_path="server/routes/admin.js", line=24, ...),  # admin 列表 WHERE is_secret=0
    ),
    expected_result_confidence=ConclusionType.UNKNOWN,
    oracle_status="NO_ORACLE",        # 需求未定义 → 无判定标准
)
```

---

## 7. 执行适配器与授权契约（回应问题 6 + 裁定 3）

### 7.1 适配器接口（问题 6 落实：plan 纯计划、execute 留桩）

```python
class TestExecutorAdapter(Protocol):
    adapter_kind: str   # android | http_api | database | web_ui | desktop | generic

    def supported(self, case: TestCase) -> bool: ...
    def plan(self, case: TestCase) -> ExecutorPlan: ...   # 纯计划生成，禁止任何外部调用
    def execute(self, case: TestCase, plan: ExecutorPlan) -> ExecutionRecord: ...
        # 未来实现；本阶段仅接口声明，不实现、不 import subprocess/curl/sql/ADB
```

**硬约束（问题 6 落实）**：
- `plan()` 只产出结构化的 `ExecutorPlan`（命令/请求/SQL 的结构化描述），**不得** import subprocess、不得调用 curl/SQL 客户端/ADB/浏览器/任何外部执行程序。
- `execute()` 的存在**不构成隐含授权**。它只是接口声明；任何真实执行都必须经过 §7.3 的逐用例授权。

### 7.2 ExecutorPlan 数据结构

```python
@dataclass(frozen=True)
class ExecutorPlan:
    plan_id: str
    case_id: str
    adapter_kind: str
    steps: tuple[dict, ...]   # 结构化步骤（如 {kind:"http_request", method:"GET", path:"/contents"}）
    required_authorization: str   # 执行该计划需要的授权项
    side_effects: str             # 副作用说明
    execution_status: str = "NOT_EXECUTED"   # 固定，永不在此层变 EXECUTED
```

### 7.3 执行授权契约（裁定 3 落实：逐用例授权）

```python
@dataclass(frozen=True)
class ExecutionAuthorization:
    case_id: str             # 绑定具体用例
    adapter_kind: str        # 绑定执行器
    project_fingerprint: str # 绑定目标项目指纹（哈希）
    operation_scope: str     # 操作范围（只读/写库/联网/装设备/改数据）
    valid_from: str          # 有效期起
    valid_until: str         # 有效期止
    # 高风险操作（真实账号、联网、数据修改、设备安装）必须单独授权，不得捆绑
```

**硬规则（裁定 3 落实）**：
- 授权**按单个用例**，不允许"一次授权整个 adapter 后自动执行所有用例"。
- 执行前**再次校验**：case_id、adapter、项目指纹、操作范围、有效期逐一核对，任一不符即拒绝执行。
- 真实账号、联网、数据修改、设备安装 → 仍需**单独授权**，且不属于本阶段范围。

---

## 8. 可追溯闭环与失效判定（架构要求 2 落实）

### 8.1 追溯链

```
TestCase → relation_ids → BusinessRelation → EvidenceReference → 原始文件
```

每个用例通过 `relation_ids` 反向追溯到它依赖的关系，每条关系通过 `evidence` 追溯到原始文件 + 行号 + 内容哈希。

### 8.2 失效判定（源文件哈希变化）

- 每条关系/用例记录其依赖的**源文件内容哈希**（`digest` / `source_digests`）。
- 当源文件哈希变化时，相关关系/用例标记为 `STALE` 或 `NEEDS_REVALIDATION`，**不得继续当作当前有效结论**。
- 判定机制复用 `snapshot.py` 的 `ContentVerifyResult`（MATCH / CHANGED / NOT_VERIFIED），不新建哈希机制。

```python
# 失效状态
RELATION_VALIDITY = {"CURRENT", "STALE", "NEEDS_REVALIDATION"}
```

---

## 9. CLI 契约补齐（回应问题 5）

DESIGN-01 的 CLI 示例 `design-test --out --authorization-file` 缺参数值，不能作为可执行接口规范。DESIGN-02 补齐：

```bash
python -m usit design-test \
    <project> \                          # 必填：项目根（绝对路径）
    --out <output_dir> \                 # 必填：输出目录（须在项目外，绝对路径）
    --authorization-file <json_path> \   # 必填：外部授权文件（绝对路径，≤16KiB）
    [--force]                            # 可选：输出目录已存在时覆盖（默认冲突则新建时间戳子目录）
```

**接口契约明细（问题 5 落实）**：

| 项 | 规范 |
|----|------|
| 输入来源 | `business_semantics` 的 `analyze()` 产出 + `scanner` 的 parser-contract + `graph` 边 |
| 输出目录 | 须在项目外（`canonical_output` 强制）；输出 `business_relations.json` + `test_design.json` |
| 授权文件格式 | 复用 `safety.ScanAuthorization`（`allowed_actions` 必须含 `static_read_only_scan`，`forbidden_actions` 必须含执行/构建/联网/写项目等）|
| 路径规范 | `canonical_project` / `canonical_output` 强制，拒绝 reparse point、拒绝输出与项目重叠 |
| 预算限制 | 复用 `MAX_FILES`/`MAX_FILE_BYTES`/`MAX_TOTAL_READ_BYTES`，超限降级 |
| 失败行为 | 任一阶段失败 → 不产出半成品，输出目录保持原子（先写临时文件再 rename）|
| 原子发布 | 两个 JSON 或同时发布、或都不发布（事务式，避免只出一半）|
| 确定性 | 同一输入两次运行产出相同 relation_id/case_id（内容派生 ID），无随机 |

---

## 10. 通用性验收（保留并强化，回应问题 8）

两个合成项目必须**从 IMPLEMENT-02 开始纳入验收**（审查明确要求，不能留到阶段结束）：

1. **博客系统**（Python/Flask + SQLite）：`is_published`/`author_id`/`role` → 权限矩阵用例；验收 `requires_role`/`grants_access`/`reads`/`writes` 关系能产出。
2. **任务管理 CLI**（无 DB、无 HTTP）：todo→doing→done 状态机 → 验收 `transitions_to`/`contains`/`command` 节点类型能产出，且**不产出权限关系**（证明不硬编码 role）。

**验收断言（强化）**：
1. 两个项目产出的 relation_type 集合是 `RELATION_TYPES` 的**子集**，且互不相同（证明非硬编码）。
2. 任务管理 CLI **不生成** `requires_role`/`grants_access`（无权限字段），博客系统生成（有 role）。
3. 每条关系/用例带证据 + 置信度；UNKNOWN 明确标记，`oracle_status=NO_ORACLE` 时 expected_result 为 None。
4. 输出确定性：同一输入两次运行 case_id/relation_id 一致。
5. **证据对应性**：不得出现"有 file:line 但语义不对应却标 STATIC_INFERENCE"的用例。

---

## 11. 分阶段实施顺序（回应审查建议四）

审查建议拆四步，DESIGN-02 明确采用，并标注每步验收：

| 阶段 | 工作内容 | 验收重点 |
|------|---------|---------|
| B-IMPLEMENT-01 | 实现 `business_relations.py` | 关系生成、证据对应性校验（§2.3）、冲突处理、置信度分级（§3）|
| B-IMPLEMENT-02 | 实现 `test_design.py` | 八类维度（§4 四要素）、用例生成（§5）、缺陷回归闭环、**合成项目验收开始** |
| B-IMPLEMENT-03 | 实现 `executor_adapter.py` + CLI | 只生成计划不执行、逐用例授权契约（§7.3）、CLI 契约（§9）|

**合成项目通用性测试从 B-IMPLEMENT-02 开始纳入验收**，不延后。

---

## 12. 冻结资产与门禁（不变）

| 资产 | 状态 |
|------|------|
| `understanding_model.py`（含 VERIFIED_FACT/PASS 硬拒绝）| 不改 |
| `file_understanding.py` 冻结分支规则 | 不改 |
| `safety.py` 授权门禁 / `REQUIRED_FORBIDDEN_ACTIONS` | 不改 |
| `snapshot.py` 内容哈希 | 不改 |
| `scanner.py` S1-C 冻结基线（`58ba5f25…`）| 不改 |
| business_semantics.py 本身 | 不改（只读消费；其 VERIFIED_FACT 误用另开工单）|
| F1 / P0.8 / F2 / 历史授权连续性 / 安全信任链门禁 | 全部保持原状态 |

**DESIGN-02 不解除任何门禁、不更新任何冻结哈希、不绕过 `VERIFIED_FACT=0` 与 `ExecutionRecord=NOT_RUN`。**

---

## 附：DESIGN-01 → DESIGN-02 差异清单

| # | DESIGN-01 内容 | 审查问题 | DESIGN-02 修订 |
|---|---------------|---------|---------------|
| 1 | 关系类型混合节点+关系语义 | 问题 1 不够通用 | 节点类型/关系类型分离 + 可扩展注册（§2.1）|
| 2 | 有 file:line 就 STATIC_INFERENCE | 问题 2 机械赋值 | 证据↔关系对应性校验，不对应→UNKNOWN（§2.3）|
| 3 | 八类维度仅凭信号识别 | 问题 3 重复误判 | 每维度四要素（触发/证据/排除/置信度）（§4）|
| 4 | 家庭相册默认确定性预期 | 问题 4 未经证明 | requirement/implementation 证据二分 + oracle_status（§5.2/§6）|
| 5 | CLI 示例缺参数 | 问题 5 不完整 | 完整 CLI 契约（§9）|
| 6 | execute() 接口可能暗示可执行 | 问题 6 边界冲突 | plan 纯计划 + execute 留桩 + 逐用例授权（§7）|
| 7 | 无业务规则/代码现状区分 | 架构要求 1 | requirement_evidence / implementation_evidence（§5.2）|
| 8 | 无追溯闭环 | 架构要求 2 | relation_ids 追溯 + 源哈希失效判定（§8）|
| 9 | 关系类型围绕 Web/API/DB | 问题 1 | 命令/组件/状态/事件/存储节点（§2.1）|
| 10 | 三个待决策未定 | 裁定 | 全部落地（§1）|
| 11 | input_data 字段 | 字段重叠 | 删除，统一用 identity_and_data_requirements（§5.1）|
| 12 | business_semantics VERIFIED_FACT 复用 | 与禁止冲突 | 降级映射，不改源文件（§3.4）|

---

## 最终结论

DESIGN-02 已逐条落实独立审查的 12 项修订 + 3 个裁定 + 2 项架构要求。设计基线（含阶段 A business_semantics）已同步至正式工作区，冻结资产与门禁全部保持。

**STOP — 等待独立审查与下一阶段授权。未授权 IMPLEMENT-01。**
