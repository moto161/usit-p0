# USIT-STAGE-B-DESIGN-03｜正式工作区基线审计与设计契约最终修订

> 阶段 B 设计最终修订 · READ-ONLY AUDIT + DESIGN REVISION
> 作者：USIT 主开发 · 生成：2026-10-30
> 上游：DESIGN-02（7 项遗留缺陷）+ 基线审计（`USIT-WORKSPACE-BASELINE-AUDIT.md`）
> 状态：**STOP — 等待独立审查与下一阶段授权**（未授权 IMPLEMENT-01）

---

## 0. 执行模式声明

本工单为**只读审计 + 设计修订**。动作与禁止事项如实记录：

| 动作 | 状态 |
|------|------|
| 双工作区基线审计 | ✅ 完成（见 `USIT-WORKSPACE-BASELINE-AUDIT.md`）|
| 修订 DESIGN-02 的 7 项缺陷 | ✅ 本文件 |
| 修改 `usit/*.py` / `tests/*.py` | ⬜ 未执行（工单禁止）|
| 同步/合并/删除/回滚/覆盖代码文件 | ⬜ 未执行 |
| 运行测试/构建/安装/登录/真实扫描 | ⬜ 未执行 |
| 访问家庭相册 / 联网 / 调外部 AI | ⬜ 未执行 |
| 更新冻结哈希 / 解除门禁 | ⬜ 未执行 |
| 创建 business_relations/test_design/executor_adapter | ⬜ 未执行 |

---

## 1. 七项设计缺陷修订总览

| # | DESIGN-02 缺陷 | DESIGN-03 修订 | 章节 |
|---|---------------|---------------|------|
| 1 | 置信度映射（VERIFIED_FACT→STATIC_INFERENCE 直接转换）| 改为**逐证据重新验证**，禁止映射表转换 | §2 |
| 2 | INFERRED_ORACLE 可产生业务预期 | INFERRED_ORACLE 仅限探索，禁止 PASS/FAIL 判定 | §3 |
| 3 | 三类维度证据条件不完整 | 补齐正常流程/跨模块/回归的证据充分条件 | §4 |
| 4 | 关系模型缺 ID/方向/端点/版本/冲突 | 补齐节点ID/关系ID/方向/端点约束/注册表版本/多证据冲突 | §5 |
| 5 | 执行授权缺签发/完整性/撤销/防重放 | 补齐签发者/完整性/有效期/撤销/指纹/防重放 | §6 |
| 6 | CLI 缺路径/结构/示例/覆盖/清理 | 补齐项目根/授权结构/参数示例/覆盖策略/失败清理 | §7 |
| 7 | 追溯失效缺传播规则 | 明确失效传播：关系→用例→执行计划→授权 | §8 |

---

## 2. 修订 1：置信度映射（禁止直接降级）

### 2.1 DESIGN-02 的缺陷

DESIGN-02 §3.4 写了一张**映射表**：「business_semantics 的 VERIFIED_FACT → 本阶段 STATIC_INFERENCE」。这是**把"降级"当成了"重新验证"**——它默认"上游说是 VERIFIED_FACT，我们降一档就是 STATIC_INFERENCE"，仍然没有回答核心问题：**这条证据是否真的支持该关系断言？**

### 2.2 DESIGN-03 修订：删除映射表，改为逐证据重验证

**删除** DESIGN-02 §3.4 的映射表。改为：

```python
def _revalidate_upstream_evidence(upstream_confidence: str,
                                  evidence: EvidenceReference,
                                  relation: BusinessRelation) -> ConclusionType:
    """
    上游置信度（无论 VERIFIED_FACT 还是其他）只作为"提示"，不是"判定"。
    本层必须重新验证每条证据是否真正支持 relation 断言。
    """
    # 1. 上游声称 VERIFIED_FACT 但无授权验证协议 → 本层一律视为"未验证"，重走校验
    #    （不信任上游标签，只信任本层对证据内容 vs 关系断言的实际核对）
    # 2. 逐条核对 evidence 内容是否支撑 relation 断言（见 §2.3 对应表）
    if not evidence or not (evidence.relative_path or evidence.source_id):
        return ConclusionType.UNKNOWN
    if _evidence_supports(evidence, relation.relation_type):
        return ConclusionType.STATIC_INFERENCE
    if _evidence_merely_hints(evidence, relation.relation_type):
        return ConclusionType.AI_HYPOTHESIS   # 仅命名/注释相似，非事实
    return ConclusionType.UNKNOWN
```

**核心规则（修订 1 落实）**：
- **不存在任何"上游置信度 → 本层置信度"的映射表**。
- 上游的 `VERIFIED_FACT` / `STATIC_INFERENCE` 标签**一律忽略**，本层只看证据内容本身。
- 每条证据必须重新经过 §2.3（DESIGN-02 已定义）的"证据↔关系对应性校验"。
- `_evidence_merely_hints`（证据仅暗示、如字段名相似）→ `AI_HYPOTHESIS`，不是 STATIC_INFERENCE。

### 2.3 证据支撑判定（在 DESIGN-02 §2.3 基础上补充）

新增"暗示"vs"支撑"的区分：

| 证据形态 | 判定 |
|---------|------|
| 证据行含关系双方的**实际语义**（如 SQL 含 entity 名 + 字段名的过滤逻辑）| `_evidence_supports` → STATIC_INFERENCE |
| 证据行仅含关系一方的**名称相似**（如注释提及、字段名含 secret）| `_evidence_merely_hints` → AI_HYPOTHESIS |
| 证据行与关系断言无关 | UNKNOWN |

---

## 3. 修订 2：Oracle 语义（禁止实现现状冒充业务正确）

### 3.1 DESIGN-02 的缺陷

DESIGN-02 §5.3 定义 `INFERRED_ORACLE = "预期结果从实现推断"`，并允许其产生 `expected_result`。这**把"代码现状"当成了"业务正确标准"**——审查明确指出这是错的。

### 3.2 DESIGN-03 修订：INFERRED_ORACLE 仅限探索，禁止 PASS/FAIL

```python
# oracle_status 三态（重新定义语义）：
#   VERIFIED_ORACLE  — 有明确业务规则/需求证据 → 可产生确定性预期，可用于 PASS/FAIL 判定
#   INFERRED_ORACLE  — 预期仅从实现现状推断 → 只允许"探索性检查"，禁止用于业务正确性 PASS/FAIL
#   NO_ORACLE        — 无任何可靠依据 → expected_result 必须 None，不产生任何判定

# expected_result 与 oracle_status 的严格约束：
#   VERIFIED_ORACLE  → expected_result 可非空；expected_result_confidence ∈ {STATIC_INFERENCE}
#   INFERRED_ORACLE  → expected_result 可非空，但 expected_result_confidence ∈ {AI_HYPOTHESIS}
#                      且用例必须标注 execution_class = 探索性（见下）
#   NO_ORACLE        → expected_result 必须 None；expected_result_confidence == UNKNOWN
```

**硬规则（修订 2 落实）**：
- `INFERRED_ORACLE` 产生的 `expected_result` **只能标记为 `AI_HYPOTHESIS`**，永远不是 `STATIC_INFERENCE`。
- `INFERRED_ORACLE` 的用例**只能用于探索性检查**（发现"代码现状是否符合预期"），**不得**用于"业务是否正确"的 PASS/FAIL 判定。
- 业务正确性判定**只允许** `VERIFIED_ORACLE`（有需求/规则证据）时进行。
- 新增字段 `oracle_basis`：记录该 oracle 的依据类型（`requirement` / `implementation` / `none`），与 `oracle_status` 对应。

```python
# TestCase 新增字段
oracle_basis: str = "none"   # requirement | implementation | none
                             # requirement → VERIFIED_ORACLE
                             # implementation → INFERRED_ORACLE
                             # none → NO_ORACLE
```

---

## 4. 修订 3：三类测试维度证据条件补齐

DESIGN-02 §4 的表里，正常流程、跨模块关联、回归影响三类的"必要证据"仍不够严格。DESIGN-03 补齐：

### 4.1 正常流程（原：feature 有 endpoint/command 即确认）

**DESIGN-03 补充证据充分条件**：
- 不仅要有 endpoint/command/symbol 存在，还必须能证明该 symbol **属于该 feature**（通过 `implements`/`contains` 关系，或模块归属证据）。
- 若 feature 名与 symbol 名仅"相似"而无归属证据 → 降为 `AI_HYPOTHESIS`，不是 STATIC_INFERENCE。

| 证据 | 判定 |
|------|------|
| feature → implements → symbol（有归属关系证据）| STATIC_INFERENCE |
| feature 名与 symbol 名相似（无归属证据）| AI_HYPOTHESIS |
| 无 symbol | UNKNOWN |

### 4.2 跨模块关联（原：≥2 条跨节点关系即确认）

**DESIGN-03 补充**：跨模块关联必须有**明确的数据流方向**（A 产出 → B 消费），不能仅凭"两个节点都出现"就认定跨模块。

| 证据 | 判定 |
|------|------|
| 存在 `writes`(A→X) + `reads`(B→X) 两条**同目标**关系（数据流贯通）| STATIC_INFERENCE |
| 仅两个 feature 各自引用不同 entity（无共享数据流）| AI_HYPOTHESIS |
| 单节点 | UNKNOWN |

### 4.3 回归影响（原：discrepancy 有 file:line 即确认）

**DESIGN-03 补充**：回归影响必须证明 discrepancy 的**影响范围**（波及哪些功能），不能仅凭"有 discrepancy"就认定"该功能需要回归"。

| 证据 | 判定 |
|------|------|
| discrepancy 的 field_gap 锚点**明确指向**某功能的代码位置 | STATIC_INFERENCE |
| discrepancy 存在，但影响范围需推断 | AI_HYPOTHESIS |
| 无 discrepancy | 不触发回归维度 |

---

## 5. 修订 4：关系模型补齐（ID/方向/端点/版本/冲突）

DESIGN-02 的关系模型缺以下要素，DESIGN-03 补齐：

### 5.1 节点与关系的确定性 ID（补充）

```python
# 节点 ID：内容派生，全局唯一
def node_id(kind: str, identifier: str) -> str:
    return _stable_id("node", kind, identifier)   # node-<sha256 前 32>

# 关系 ID：由 (source_id, relation_type, target_id, direction) 内容派生
def relation_id(source_id, relation_type, target_id) -> str:
    return _stable_id("rel", source_id, relation_type, target_id)
```

### 5.2 关系方向（补充）

DESIGN-02 的关系是**有向**的（source → target），但未显式声明方向语义。DESIGN-03 明确：

```python
# 关系是有向的，方向语义如下（不可逆）：
#   derives_from:      feature -> requirement        （feature 派生自 requirement）
#   contains:          container -> contained         （module 包含 symbol）
#   implements:        implementer -> feature          （symbol 实现 feature）
#   uses:              user -> used                   （feature 使用 entity）
#   reads / writes:    reader/writer -> data           （endpoint 读/写 entity）
#   requires_role:     subject -> role                 （endpoint 需要 role）
#   grants_access:     role -> resource                （role 授予访问 resource）
#   restricted_by:     resource -> permission          （resource 受限于 permission）
#   transitions_to:    from_state -> to_state          （状态转换方向）
#   emits / handles:   emitter -> event / handler -> event
#   covers:            test_scenario -> feature
# 逆关系需显式声明，不得由方向推断（例如有 uses 不代表有 used_by）
```

### 5.3 端点约束（补充）

```python
# 每种关系类型规定合法的 (source_kind, target_kind) 端点组合。
# 非法组合在构造时拒绝，而不是静默接受。
RELATION_ENDPOINT_CONSTRAINTS = {
    "derives_from":    {("feature", "requirement")},
    "contains":        {("module", "symbol"), ("module", "module"), ("entity", "field")},
    "implements":      {("symbol", "feature"), ("component", "feature")},
    "uses":            {("feature", "entity"), ("feature", "store"), ("feature", "symbol")},
    "reads":           {("endpoint", "entity"), ("endpoint", "field"), ("command", "store")},
    "writes":          {("endpoint", "entity"), ("endpoint", "field"), ("command", "store")},
    "invokes":         {("feature", "endpoint"), ("command", "symbol")},
    "requires_role":   {("endpoint", "role"), ("feature", "role")},
    "grants_access":   {("role", "permission"), ("role", "entity")},
    "restricted_by":   {("entity", "permission"), ("field", "permission")},
    "transitions_to":  {("state", "state")},
    "emits":           {("feature", "event"), ("command", "event")},
    "handles":         {("symbol", "event"), ("command", "event")},
    "covers":          {("test_scenario", "feature")},
}
```

### 5.4 注册表版本（补充）

```python
# 节点类型注册表版本 + 关系类型注册表版本，独立于 schema 版本
NODE_REGISTRY_VERSION = "node-registry-v1"
RELATION_REGISTRY_VERSION = "relation-registry-v1"
# 任何新增节点/关系类型都必须 bump 对应注册表版本，并记录变更日志。
# schema 版本（usit-business-relations-v1）与注册表版本分离：
#   - schema 版本：数据结构字段变化时 bump
#   - 注册表版本：节点/关系类型集合变化时 bump
```

### 5.5 多证据冲突处理（补充）

DESIGN-02 只说了"CONFLICT 保留双方"，但没定义**同一关系的多条证据互相矛盾**时怎么处理。DESIGN-03 补齐：

```python
def _resolve_multi_evidence(evidences: tuple[EvidenceReference, ...]) -> ConclusionType:
    """
    多条证据指向同一关系时的处理：
    1. 全部支撑 → STATIC_INFERENCE
    2. 部分支撑、部分无关 → STATIC_INFERENCE（以支撑者为准，无关者不影响）
    3. 部分支撑、部分暗示 → STATIC_INFERENCE（支撑者优先）
    4. 出现矛盾（A 证据说 X→Y，B 证据说 X→Z）→ CONFLICT（保留双方，不选边）
    5. 全部仅暗示 → AI_HYPOTHESIS
    6. 全部无关 → UNKNOWN
    """
```

---

## 6. 修订 5：执行授权补齐（签发/完整性/撤销/防重放）

DESIGN-02 §7.3 的 `ExecutionAuthorization` 缺 5 项，DESIGN-03 补齐：

```python
@dataclass(frozen=True)
class ExecutionAuthorization:
    authorization_id: str          # 确定性 ID
    # ---- 签发者与完整性（补充）----
    issuer: str                    # 签发者标识（谁签发的授权）
    signature: str                 # 完整性签名（HMAC/哈希，防篡改）
    # ---- 绑定（原有）----
    case_id: str                   # 绑定具体用例
    adapter_kind: str              # 绑定执行器
    project_fingerprint: str       # 绑定项目指纹
    operation_scope: str           # 操作范围
    # ---- 有效期与撤销（补充）----
    valid_from: str                # 生效时间
    valid_until: str               # 失效时间
    revocation_status: str         # ACTIVE / REVOKED / EXPIRED
    revocation_reason: str = ""    # 撤销原因（REVOKED 时必填）
    # ---- 防重放（补充）----
    nonce: str                     # 一次性随机数（防重放）
    max_use_count: int = 1         # 最大使用次数（默认 1，用完即失效）
    use_count: int = 0             # 已使用次数
```

**硬规则（修订 5 落实）**：
- **签发者**：每份授权必须记录签发者，匿名授权无效。
- **完整性**：授权记录必须带签名/哈希，执行前校验完整性，篡改即拒绝。
- **有效期**：`valid_until` 过期即拒绝；`revocation_status != ACTIVE` 即拒绝。
- **撤销**：任何授权可被撤销（REVOKED），撤销后立即失效，需记录撤销原因。
- **防重放**：`nonce` 唯一，`max_use_count` 默认 1，`use_count >= max_use_count` 即拒绝重放。
- 执行前校验**全部**字段（签发者、签名、指纹、用例、执行器、范围、有效期、撤销态、重放计数），任一不符即拒绝。

---

## 7. 修订 6：CLI 补齐（路径/结构/示例/覆盖/清理）

DESIGN-02 §9 的 CLI 契约仍缺 5 项，DESIGN-03 补齐：

### 7.1 项目根路径规范（补充）

```bash
# <project> 必须是绝对路径，且经过 canonical_project 规范化。
# 拒绝：相对路径、含 reparse point、不存在的路径。
# 示例（合法）：
#   C:\Users\...\家庭相册        （Windows 绝对路径）
#   /home/user/project           （POSIX 绝对路径）
# 示例（非法，被拒）：
#   ./project                    （相对路径）
#   E:\家庭相册\..\其他           （含 ..）
```

### 7.2 授权文件结构（补充）

```json
{
  "project_root": "<绝对路径，须与 CLI <project> 一致>",
  "output_dir": "<绝对路径，须在项目外>",
  "allowed_actions": ["static_read_only_scan", "overwrite_existing_output"],
  "forbidden_actions": ["execute", "build", "test", "install", "network", "write_project", "read_credentials"],
  "authorized_at": "2026-10-30T14:00:00+00:00",
  "expires_at": "2026-10-30T15:00:00+00:00"
}
```

**约束**：`allowed_actions` 必须含 `static_read_only_scan`；`forbidden_actions` 必须含执行/构建/测试/安装/联网/写项目/读凭据等全部禁止项（复用 `safety.REQUIRED_FORBIDDEN_ACTIONS`）。

### 7.3 参数示例（补充，完整可执行）

```bash
# 完整示例（Windows）：
python -m usit design-test \
    "C:\Users\Administrator\WorkBuddy\2026-10-15-22-09-47\家庭相册" \
    --out "C:\Users\Administrator\WorkBuddy\usit_output\design-test-01" \
    --authorization-file "C:\Users\Administrator\WorkBuddy\usit_auth\design-test-auth.json"

# 完整示例（POSIX）：
python -m usit design-test \
    /home/user/家庭相册 \
    --out /home/user/usit_output/design-test-01 \
    --authorization-file /home/user/usit_auth/design-test-auth.json
```

### 7.4 覆盖策略（补充）

| 场景 | 行为 |
|------|------|
| 输出目录不存在 | 创建 |
| 输出目录存在且为空 | 直接使用 |
| 输出目录存在且非空，无 `--force` | **拒绝**，报错并提示换路径或加 `--force` |
| 输出目录存在且非空，有 `--force` | 覆盖（但仅覆盖 design-test 自己的产出文件，不清空无关文件）|

### 7.5 失败清理规则（补充）

| 失败点 | 清理行为 |
|--------|---------|
| 授权校验失败 | 不创建任何文件，不写任何内容 |
| 输入读取失败（超预算/文件缺失）| 不产出半成品，输出目录保持原状 |
| 关系生成失败 | 不写 business_relations.json |
| 用例生成失败 | 不写 test_design.json |
| 任一阶段失败 | 两个 JSON 都不发布（原子性），已写的临时文件删除 |

**原子发布实现**：先写 `<out>/.tmp/business_relations.json.tmp` + `<out>/.tmp/test_design.json.tmp`，两阶段都成功后 `rename` 到最终路径；任一失败则删除 `.tmp`，最终路径不出现半成品。

---

## 8. 修订 7：追溯失效传播规则

DESIGN-02 §8 只说了"源文件哈希变化 → 关系/用例标记 STALE"，但没定义**失效如何传播**。DESIGN-03 补齐传播链：

```python
# 失效传播链（自上而下）：
# 源文件哈希变化
#   → 依赖该文件的 EvidenceReference.digest 失配（snapshot ContentVerifyResult = CHANGED）
#   → 依赖该 EvidenceReference 的 BusinessRelation 标记 STALE
#   → 依赖该 BusinessRelation 的 TestCase（通过 relation_ids）标记 STALE
#   → 依赖该 TestCase 的 ExecutorPlan 标记 STALE
#   → 依赖该 TestCase 的 ExecutionAuthorization 标记 EXPIRED（作废，需重新签发）

# 传播规则：
def propagate_invalidation(changed_digest: str) -> None:
    """
    1. 找到所有 digest == changed_digest 的 EvidenceReference → 标记 digest_match=False
    2. 反向找引用这些 EvidenceReference 的 BusinessRelation → RELATION_VALIDITY = STALE
    3. 反向找 relation_ids 含这些 relation 的 TestCase → 标记 STALE
    4. 反向找 case_id 匹配的 ExecutorPlan → 标记 STALE
    5. 反向找 case_id 匹配的 ExecutionAuthorization → revocation_status = EXPIRED
    """
```

**硬规则（修订 7 落实）**：
- 失效**单向传播**（源 → 关系 → 用例 → 计划 → 授权），不反向。
- 标记 STALE 的**任何**关系/用例/计划**不得**继续作为当前有效结论，也不得用于新用例生成。
- 标记 EXPIRED 的授权**立即作废**，执行前校验必拒。
- 源文件恢复（哈希回到原值）**不自动**清除 STALE，需显式 `revalidate` 流程重新验证。

---

## 9. 冻结资产与门禁（不变）

（同 DESIGN-02 §12，本工单不重复列举。核心：`understanding_model.py` 硬约束、`file_understanding.py` 冻结规则、`safety.py` 门禁、`snapshot.py`、`scanner.py` S1-C 基线 `58ba5f25…`、F1/P0.8/F2/历史授权连续性/安全信任链——**全部保持原状态**。）

**DESIGN-03 不解除任何门禁、不更新冻结哈希、不绕过 `VERIFIED_FACT=0` 与 `ExecutionRecord=NOT_RUN`。**

---

## 10. 分阶段实施（不变）

（同 DESIGN-02 §11：B-IMPLEMENT-01 → 02 → 03，合成项目验收从 IMPLEMENT-02 起。本工单不重复，且**未授权开始 IMPLEMENT-01**。）

---

## 附：DESIGN-02 → DESIGN-03 差异清单

| # | DESIGN-02 内容 | 审查缺陷 | DESIGN-03 修订 |
|---|---------------|---------|---------------|
| 1 | §3.4 置信度映射表（VERIFIED_FACT→STATIC_INFERENCE）| 直接降级=未重新验证 | 删除映射表，逐证据重验证（§2）|
| 2 | INFERRED_ORACLE 可产生业务预期 | 实现现状冒充业务正确 | INFERRED_ORACLE 仅探索、禁 PASS/FAIL、新增 oracle_basis（§3）|
| 3 | 三类维度证据条件宽松 | 信号直接升级确定结论 | 补齐充分条件 + 降级路径（§4）|
| 4 | 关系模型缺 ID/方向/端点/版本/冲突 | 不完整 | 补齐 5 项（§5）|
| 5 | 执行授权缺签发/完整性/撤销/防重放 | 不完整 | 补齐 5 项（§6）|
| 6 | CLI 缺路径/结构/示例/覆盖/清理 | 不完整 | 补齐 5 项（§7）|
| 7 | 追溯失效无传播规则 | 不完整 | 补齐传播链（§8）|

---

## 最终结论

DESIGN-03 已落实基线审计（`USIT-WORKSPACE-BASELINE-AUDIT.md`）+ 7 项设计缺陷修订。所有缺陷均有明确接口约束、字段定义或判定规则。

**STOP — 等待独立审查与下一阶段授权。未授权 IMPLEMENT-01。**
