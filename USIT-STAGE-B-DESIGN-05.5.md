# USIT-STAGE-B-DESIGN-05.5｜执行交接状态冲突与事务锁恢复修订

> 阶段 B · DESIGN-05.5 · READ-ONLY ANALYSIS + DESIGN REVISION
> 作者：USIT 主开发 · 生成：2026-10-31（GMT+8）
> 上游：DESIGN-05.4
> 工作区：`C:\Users\Administrator\WorkBuddy\2026-10-15-22-09-47\usit_p0`
> 状态：**STOP — 等待用户独立审查**（未授权 IMPLEMENT-01）

---

## 0. 范围与结论预览

本轮针对 DESIGN-05.4 遗留的四项缺陷定向修订，**不扩展架构**：

| 编号 | 问题 | 严重级 |
|------|------|--------|
| P0-1 | EXHAUSTED 状态与已有 RESERVED 执行冲突 | P0 |
| P0-2 | handoff_execution 与 atomic_commit 重复获取锁 | P0 |
| P1-1 | PREPARED 事务缺少可恢复载荷及幂等规则 | P1 |
| P1-2 | 授权锁文件路径不统一（`locks/` vs `authorizations/<id>/`）| P1 |

> 文档设计通过 ≠ 代码实现通过。所有伪代码为设计级契约，**不得被解读为已实现或已验证**。

---

## 1. P0-1｜EXHAUSTED 状态与已有 RESERVED 执行冲突

### 1.1 问题定位

DESIGN-05.4 的 `handoff_execution()` 步骤 3 要求 `authorization_status == "ACTIVE"` 才允许交接。但当 `max_use_count=1` 时，额度一旦预留，`slots_committed == max_use_count`，授权状态由 ACTIVE 转入 **EXHAUSTED**，此时已合法预留的 Reservation 因主记录不再是 ACTIVE 而无法交接——**合法预留无法执行，自相矛盾**。

### 1.2 修订：区分"新增预留"与"完成已有预留"两种权限

核心原则：**"禁止新增 Reservation" 与 "允许完成已有 Reservation" 是两件独立的事，不得用单一状态字段混为一谈。**

| 授权状态 | 新增 Reservation（占用新额度）| 完成已有 Reservation（交接执行）|
|----------|------------------------------|--------------------------------|
| ACTIVE | 允许（额度未满且满足全部约束）| 允许（已有有效 RESERVED 可交接）|
| EXHAUSTED | **禁止** | **允许**（此前合法创建且仍有效的 RESERVED 可交接）|
| EXPIRED | 禁止 | 禁止 |
| REVOKED | 禁止 | 禁止 |
| INVALIDATED | 禁止 | 禁止 |
| CORRUPTED | 禁止 | 禁止 |
| （INDETERMINATE 为 Reservation 级状态，非授权级；授权级无 INDETERMINATE）| — | 见 1.4 |

### 1.3 交接门槛修订（handoff_execution 步骤 3 替换）

原步骤 3 从"必须 ACTIVE"改为"允许交接状态集合"：

```python
# 交接允许状态：ACTIVE 或 EXHAUSTED
HANDOFF_ALLOWED_STATUSES = ("ACTIVE", "EXHAUSTED")
# 新增预留允许状态：仅 ACTIVE
RESERVE_ALLOWED_STATUSES = ("ACTIVE",)
```

- `reserve_slot()` 只允许 `authorization_status in RESERVE_ALLOWED_STATUSES`。
- `handoff_execution()` 只允许 `authorization_status in HANDOFF_ALLOWED_STATUSES`。

**交接时其余二次验证（05.4 步骤 4–10）不变**：仍须重新校验 `expires_at`、`revoked_at`、`invalidated_at`、`project_fingerprint`、`plan_digest`、`reservation` 状态、`attempt` 是否已存在。

### 1.4 状态转换与拒绝原因

- ACTIVE → EXHAUSTED：`slots_committed == max_use_count`（额度占满即 EXHAUSTED，与执行完成无关，延续 05.4 §4 语义）。
- EXHAUSTED 不得回退 ACTIVE（额度不退还）。
- EXHAUSTED 内的 RESERVED 交接成功 → Reservation/Attempt 进入各自终态，**不影响**授权仍为 EXHAUSTED。
- 拒绝原因枚举：`DENY_STATE`（授权状态非交接允许集合）/ `DENY_EXPIRED` / `DENY_REVOKED` / `DENY_INVALIDATED` / `DENY_FINGERPRINT` / `DENY_PLAN_DIGEST` / `DENY_RESERVATION` / `DENY_ALREADY_ATTEMPTED`。

### 1.5 边界案例（设计场景，不运行测试）

| # | 场景 | 预期 |
|---|------|------|
| 1 | `max_use_count=1`，预留后 ACTIVE→EXHAUSTED，随后交接该 RESERVED | 允许交接，Attempt 进入 EXECUTION_STARTED |
| 2 | `max_use_count=1`，EXHAUSTED 后尝试再次 reserve_slot | `DENY_EXHAUSTED`（非 ACTIVE，禁止新增预留）|
| 3 | `max_use_count=1`，EXHAUSTED 且该 RESERVED 已 INDETERMINATE，再请求交接 | `DENY_RESERVATION`（该 Reservation 已非 RESERVED）|
| 4 | EXHAUSTED 但已过期（expires_at 已过），请求交接 | `DENY_EXPIRED`（交接前二次验证仍拦截）|
| 5 | EXHAUSTED 且 project_fingerprint 已变化，请求交接 | `DENY_FINGERPRINT` |
| 6 | ACTIVE（额度未满）且有多个 RESERVED 待交接 | 逐个交接，每个独立判断，互不影响 |

---

## 2. P0-2｜handoff_execution 与 atomic_commit 重复获取锁

### 2.1 问题定位

DESIGN-05.4 的 `handoff_execution()` 在步骤 1 已持有授权级锁，步骤 13 又调用 `_atomic_commit()`，而 `_atomic_commit()` 内部再次 `_acquire_lock()`。未定义可重入锁时，同一执行流第二次获取同一把非重入锁 → **死锁**。

### 2.2 修订：拆分为两层（外部锁 vs 锁内事务）

```python
# 外层：负责锁的获取与释放 + 事务入口
def atomic_commit(auth_id, operation_type, payload_builder) -> result:
    lock = _acquire_lock(_lock_path(auth_id), protocol_version=LOCK_PROTO_V1)
    if lock is None: return DENY_LOCK_UNAVAILABLE
    try:
        return atomic_commit_locked(auth_id, operation_type, payload_builder)
    finally:
        _release_lock(lock)

# 内层：假定调用者已持有正确授权锁，只做锁内事务提交，不再获取锁
def atomic_commit_locked(auth_id, operation_type, payload_builder) -> result:
    # 前置：调用者必须已持有 auth_id 对应的授权锁
    _assert_lock_held(auth_id)          # 校验锁所有权（见 2.4）
    doc = _read_verified(f"authorizations/{auth_id}/authorization.json")
    ...   # PREPARED → 写候选 → fsync → 原子替换 → 校验 → COMMITTED
```

### 2.3 锁调用关系

| 函数 | 是否获取锁 | 是否要求已持锁 | 说明 |
|------|-----------|---------------|------|
| `reserve_slot()` | ✅ 获取 | — | 完整操作入口 |
| `handoff_execution()` | ✅ 获取 | — | 完整操作入口 |
| `atomic_commit()` | ✅ 获取 | — | 事务提交入口（独立使用）|
| `atomic_commit_locked()` | ❌ 不获取 | ✅ 必须已持锁 | 锁内事务，仅被上层在持锁状态调用 |
| `revoke()` / `invalidate()` | ✅ 获取 | — | 完整操作入口 |

**规则**：`handoff_execution()` 在持锁状态下直接调用 `atomic_commit_locked()`，**不再**调用会二次加锁的 `atomic_commit()`。任何操作入口函数获取锁后，内部一律走 `_locked` 后缀的内层函数。**不存在隐含递归加锁**。

### 2.4 锁所有权与 token 校验

- 每次 `_acquire_lock()` 成功返回一个 `lock_token`（进程内唯一句柄 + 锁文件对象身份）。
- `atomic_commit_locked()` 入口调用 `_assert_lock_held(auth_id)`：校验当前线程/进程持有 `auth_id` 对应锁的 `lock_token`，不匹配 → `DENY_LOCK_NOT_HELD`（防御性编程，防止误用）。
- 锁文件身份：持锁期间锁文件 inode/handle 不得变化，检测到变化 → 锁失效 → `DENY_LOCK_IDENTITY`。

### 2.5 异常 / 提交失败 / 崩溃时的锁释放与恢复责任

- **正常路径**：`atomic_commit()` 的 `finally` 释放锁。
- **异常路径**：`atomic_commit()` 的 `finally` 保证释放锁（即使 `atomic_commit_locked()` 抛异常）。
- **进程崩溃**：OS 自动释放锁（Windows handle 关闭释放 / POSIX flock 随 fd 关闭释放），无残留死锁。
- **事务恢复责任**：锁释放不改变事务状态；崩溃后事务状态由 journal（见 §3）决定，恢复进程需重新获取锁再判定，不能因"锁已释放"就假定事务成功或失败。

---

## 3. P1-1｜PREPARED 事务缺少可恢复载荷及幂等规则

### 3.1 问题定位

DESIGN-05.4 事务日志仅存 `operation_payload_digest`，没有完整操作载荷或可验证的恢复来源。崩溃矩阵中的"回滚 / 重放 / ABORT"缺可执行依据。

### 3.2 修订：选择方案 B（持久化完整新文档候选 + 路径/摘要/关联）

在 `authorizations/<auth_id>/` 目录内，PREPARED 阶段持久化以下恢复材料：

```
authorizations/<auth_id>/
  authorization.json            # 当前已提交文档
  journal/<transaction_id>.txn  # 事务日志（含恢复材料引用）
  pending/<transaction_id>.json # 完整新文档候选文件（含全部记录变更）
```

**journal 条目（扩展 05.4 的 8 字段）**：

```python
@dataclass(frozen=True)
class TransactionJournal:
    transaction_id: str
    authorization_id: str
    operation_type: str
    expected_revision: int
    new_revision: int
    operation_payload_digest: str     # 规范化载荷摘要（校验用）
    pending_path: str                 # 候选文件完整路径
    pending_sha256: str               # 候选文件内容摘要
    transaction_state: str            # PREPARED/COMMITTED/ABORTED/INDETERMINATE
    created_at: str
    committed_at: str | None
```

### 3.3 恢复时如何校验与判定

恢复进程（重新获取授权锁后）：

1. 读 `journal/<transaction_id>.txn`。
2. 校验 `pending_path` 指向的候选文件是否存在；校验 `pending_sha256` 是否匹配（检测候选文件是否被篡改/截断）。
3. 判断目标 `authorization.json` 是否已提交：
   - 读当前 `authorization.json`，取其 `record_revision`。
   - 若 `record_revision == journal.new_revision` 且当前文档摘要 == `pending_sha256` → **已提交**，补写 `COMMITTED`。
   - 若 `record_revision == journal.expected_revision`（未变）→ **未提交**，可按幂等规则重放或 ABORT。
   - 若两者都不匹配（revision 跳变 / 摘要不一致）→ **INDETERMINATE → DENY**，人工审计。
4. 无法证明事务状态 → **DENY**，不得猜测成功，不得自动重复执行。

### 3.4 transaction_id 幂等规则

- `transaction_id` 全局唯一（UUID v4 / ULID）。
- 提交前，检查 `journal/<transaction_id>.txn` 是否已存在同 `transaction_id` 的 `COMMITTED`：若存在 → **幂等返回 COMMITTED，不重复消费额度、不重复创建 Attempt**。
- `attempt_id`、`reservation_id` 在候选文档中唯一；重复提交被 `expected_revision` + 唯一键双重拦截。
- 同一 `transaction_id` 只允许一次 COMMITTED 落盘；后续同 ID 请求一律返回已提交状态或 DENY 重放。

### 3.5 PREPARED/COMMITTED/ABORTED/INDETERMINATE 转换

```
PREPARED → COMMITTED      （原子替换成功 + revision 匹配）
PREPARED → ABORTED        （明确回滚：候选文件删除或标记废弃，无副作用）
PREPARED → INDETERMINATE  （崩溃，revision/摘要无法确认）
COMMITTED / ABORTED / INDETERMINATE → 终态
```

### 3.6 逐阶段崩溃恢复矩阵（覆盖五类位置）

| 崩溃位置 | 恢复依据 | 幂等/拒绝行为 |
|----------|----------|--------------|
| 临时文件写入中 | journal 可能尚无记录或记录 pending_sha256 与候选不匹配 | 无副作用，可重试；候选损坏则丢弃重写 |
| fsync（候选文件）前 | 候选未落盘，pending_sha256 校验失败 | 重试；不得认为已提交 |
| 原子替换（os.replace）期间 | replace 原子，要么旧要么新 | 见 3.3 步骤 3 判定 |
| 目录持久化（父目录 fsync）后 | 新文档可见但 journal 仍 PREPARED | 核对 revision → 补 COMMITTED 或 INDETERMINATE |
| COMMITTED 日志写入前 | 新文档已提交但 journal PREPARED | 核对 revision/摘要一致 → 补 COMMITTED（幂等）|
| COMMITTED 日志写入后 | journal COMMITTED | 幂等返回已提交，不重复消费 |

**核心规则**：任一崩溃位置均有明确恢复依据、幂等行为与拒绝策略；**不确定状态不得自动放行**。

---

## 4. P1-2｜授权锁文件路径不统一

### 4.1 问题定位

DESIGN-05.4 目录结构写 `authorizations/<authorization_id>/<authorization_id>.lock`，但伪代码多处用 `locks/{auth_id}.lock`，两套路径混用。

### 4.2 修订：统一为授权目录内锁文件

**唯一锁路径（全文统一）**：

```
authorizations/<authorization_id>/<authorization_id>.lock
```

即锁文件属于授权目录，与 `authorization.json`、`journal/`、`pending/` 同处 `authorizations/<authorization_id>/` 下。

### 4.3 锁文件生命周期规则

| 项 | 规则 |
|----|------|
| 创建 | 首次使用授权时创建；`open(..., O_CREAT)`，不覆盖已存在锁文件 |
| 打开 | 所有进程以相同路径打开同一锁文件对象 |
| 关闭 | 操作完成后 `close()`；进程退出 OS 自动释放 |
| 清理 | 授权终态（REVOKED/EXPIRED 且无活跃事务）后可删，但**删除不发生在持锁期间** |
| 禁止 | 持锁期间删除或替换锁文件（inode/handle 变化 → 锁失效 → DENY）|

### 4.4 辅助函数

```python
def _lock_path(auth_id: str) -> str:
    return f"authorizations/{auth_id}/{auth_id}.lock"
```

**全文所有伪代码、目录结构、事务恢复流程、示例统一使用 `_lock_path(auth_id)`，删除 `locks/{auth_id}.lock` 写法。** 不采用独立 `locks/` 目录，故无迁移兼容问题。

---

## 5. 文档一致性检查

| # | 检查项 | 结论 |
|---|--------|------|
| 1 | 三类记录职责与状态定义一致 | 一致（05.5 未改记录结构）|
| 2 | max_use_count / Reservation / Attempt / EXHAUSTED 派生规则 | 一致（EXHAUSTED=额度占满，与执行完成无关）|
| 3 | ACTIVE 与 EXHAUSTED 新增/已有预留权限 | 一致（HANDOFF_ALLOWED_STATUSES / RESERVE_ALLOWED_STATUSES）|
| 4 | handoff/atomic_commit/atomic_commit_locked 锁调用 | 一致（外层取锁，内层 `_locked` 不再取锁）|
| 5 | transaction_id/attempt_id/reservation_id 唯一性与幂等 | 一致（journal 幂等 + expected_revision + 唯一键）|
| 6 | PREPARED 恢复流程与崩溃矩阵 | 一致（方案 B 完整候选 + 五类崩溃位置）|
| 7 | 锁路径引用统一 | 一致（唯一 `authorizations/<id>/<id>.lock`）|
| 8 | 不将伪代码等同于已实现/已验证 | 一致（全文标注设计级契约）|

---

## 6. IMPLEMENT-01 准入结论

**PASS-WITH-ISSUES（维持阻断）。**

四项缺陷全部定向封闭：P0-1（EXHAUSTED 交接冲突）、P0-2（重复锁死锁）均已给出明确修订而非"已解决"空话；P1-1（可恢复载荷 + 幂等）、P1-2（锁路径统一）均给出完整伪代码/状态表/路径规则。

**但以下能力仍为设计级契约，未真实实现，NOT_VERIFIED / NOT_IMPLEMENTED / NOT_TESTED**：

1. **存储事务能力**（05.4 延续）：单一 JSON 原子替换 + 锁 + fsync + journal 尚未真实实现与崩溃注入验证。
2. **锁机制**（P0-2 修订的可重入拆分 + token 校验）尚未实现。
3. **事务恢复 + 幂等**（P1-1 方案 B）尚未实现。

**未解决问题（05.4 遗留，不得静默删除）**：
- P0-BLOCK-1：存储事务能力未实现（NOT_VERIFIED）。
- P0-BLOCK-2：`analyze_file` 双重定义（`scanner.py` vs `_analysis.py`）。
- P1-BLOCK-3：A/B 三文件分叉（`cli.py` / `python_worker.py` / `scanner.py`）。

**IMPLEMENT-01 继续保持 STOP。本阶段不具备 IMPLEMENT-01 准入资格。F1/P0.8/F2 门禁保持原状态。STOP — 等待用户独立审查。此工单不包含代码实现授权。**

---

## 7. 交付物索引

| 文件 | 内容 |
|------|------|
| `USIT-STAGE-B-DESIGN-05.5.md` | 本文（四项缺陷修订）|
| `USIT-STAGE-B-DESIGN-05.5-ISSUES.md` | 问题清单 |
| `USIT-STAGE-B-DESIGN-05.5-DIFF.md` | DESIGN-05.4 → 05.5 差异 |
| `USIT-STAGE-B-DESIGN-05.5-EXECUTION-REPORT.md` | 执行报告 |
| `USIT-STAGE-B-DESIGN-05.5-HASH-MANIFEST.md` | 前四份哈希清单 |
| `USIT-STAGE-B-DESIGN-05.5-HASH-RECEIPT.txt` | MANIFEST 自身摘要收据 |
