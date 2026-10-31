# USIT-STAGE-B-DESIGN-05.4｜存储并发事务与授权撤销交接安全封闭

> 阶段 B · DESIGN-05.4 · READ-ONLY AUDIT + DESIGN DOCUMENT REVISION
> 作者：USIT 主开发 · 生成：2026-10-30（GMT+8）
> 上游：DESIGN-01 至 DESIGN-05.3
> 工作区：`C:\Users\Administrator\WorkBuddy\2026-10-15-22-09-47\usit_p0`
> 状态：**STOP — 等待用户独立审查**（未授权 IMPLEMENT-01）

---

## 0. 问题定位与范围

DESIGN-05.3 已用分层记录模型（AuthorizationRecord / ReservationRecord / AttemptRecord）解决"单一 attempt + 终态 vs 多次额度"的结构矛盾，并把**存储事务能力**列为 IMPLEMENT-01 前置阻断项。05.3 留下四个问题，本轮只封闭这四个，**不再扩展架构**：

- D054-01（P0）：存储布局 + 跨授权并发写入安全 + 多文件事务提交
- D054-02（P0）：执行交接前授权二次验证 + 撤销/交接竞争 + 线性化点
- D054-03（P1）：`execution_attempt_count` 统一定义
- D054-04（P1）：`EXHAUSTED` 状态语义修正

> 文档设计通过 ≠ 代码实现通过。所有伪代码为设计级契约，**不得被解读为已实现或已验证**。

---

## 1. D054-01｜存储布局与跨授权并发写入安全（P0）

### 1.1 方案 A vs 方案 B 比较

| 维度 | 方案 A：每授权独立目录 | 方案 B：全部授权单一 JSON |
|------|------------------------|---------------------------|
| 并发写入冲突 | 不同授权之间天然隔离，互不触碰同一文件 | 所有授权共享一个文件，任何写入串行化 |
| 锁粒度 | 授权级锁（`locks/<auth_id>.lock`）| 全局单锁，所有授权串行 |
| 原子替换 | 每个授权目录内单文档替换 | 整个库单文档替换，写放大严重 |
| 崩溃恢复 | 损坏影响单一授权，可隔离 | 单点损坏影响全库 |
| 文件损坏影响范围 | 局部（一个授权）| 全局 |
| 记录完整性 | 授权内主记录+子记录需跨文件一致性 | 天然单文档一致，但体积增长 |
| Windows/POSIX 支持 | 相同，目录+文件锁 | 相同，单文件锁 |
| 迁移 SQLite | 每授权一行/多行，映射直接 | 需拆库，成本高 |

**裁决：推荐方案 A 的目录结构 + 每授权单一 JSON 文档**（混合式）：

```
authorizations/
  <authorization_id>/
    authorization.json                 # 单一文档，内含：主记录 + reservations[] + attempts[] + 事务日志头
    <authorization_id>.lock            # 授权级锁文件
```

即：**不同授权分属不同目录（并发隔离），同一授权内的三条记录塞进同一个 `authorization.json`（用 `os.replace()` 单文件原子替换实现同一授权内的多记录一致性）**。这样既避免了方案 B 的全局串行与写放大，又避免了"多文件连续写入非原子"的缺陷。

> 关键：**不能仅因为文件分开就宣称事务安全**。同一授权内的主记录/预留/尝试三者的更新，必须落在**同一个原子替换单元**里，否则退回到 05.3 已否定的"多文件非原子"。

### 1.2 事务日志头（嵌入 authorization.json）

在单一文档内维护一个事务日志头字段，用于崩溃恢复的幂等判定：

```python
@dataclass(frozen=True)
class TransactionLog:
    transaction_id: str        # 全局唯一，单调 UUID v4 / ULID
    authorization_id: str      # 归属授权
    operation_type: str        # RESERVE / START_EXECUTION / COMPLETE / REVOKE / INVALIDATE / EXPIRE
    expected_revision: int     # 乐观并发：预期主记录 record_revision
    new_revision: int          # 提交后的 record_revision
    operation_payload_digest: str  # 本次操作的规范化载荷 SHA256
    transaction_state: str     # PREPARED / COMMITTED / ABORTED / INDETERMINATE
    created_at: str
    committed_at: str | None
```

事务状态转换（合法路径）：

```
PREPARED → COMMITTED     （提交成功且落盘）
PREPARED → ABORTED       （主动回滚）
PREPARED → INDETERMINATE （崩溃，无法确认提交）
COMMITTED / ABORTED / INDETERMINATE → 终态（不再改变）
```

**语义**：
- `PREPARED`：意图已持久化，但提交结果未确认。
- `COMMITTED`：记录内容与计数已原子落盘。
- `ABORTED`：明确回滚，无副作用。
- `INDETERMINATE`：崩溃，**默认 DENY**，需人工审计决定恢复路径。

### 1.3 并发锁协议

- 锁粒度：**授权级锁**，每个 `authorization_id` 一个锁文件 `<auth_id>.lock`。
- 不同 authorization_id 的写操作**可以并发**（各自锁独立）。
- 同一 authorization_id 的所有写操作**串行**（同一把锁）。
- 共享文件索引/全局发布元数据（如目录枚举、CURRENT 指针）需要**独立于授权级锁的全局元数据锁**。
- 所有进程使用**相同锁文件路径 + 相同协议版本**（锁协议版本字段写入锁文件头）。
- 锁文件在锁持有期间**不得被删除或替换**；检测到锁文件 inode/handle 变化 → 锁失效 → DENY。
- 锁失败、锁身份异常、协议版本不一致 → **DENY**。
- **禁止用内存锁替代跨进程锁**；**禁止用随机文件名替代互斥锁**。

**为什么不同授权并发写入不会互相覆盖**：每个授权的所有状态都封装在 `<auth_id>/authorization.json` 单一文档内，不同授权落在**不同物理路径**，互不共享写目标；唯一共享的是目录枚举（只读，靠全局元数据锁保护 + 版本化 manifest），故两个授权的并发 `os.replace()` 作用于不同文件，天然互不覆盖。

### 1.4 同一授权内多记录事务提交（原子替换方案）

```python
def atomic_commit(auth_id, operation_type, payload_builder) -> result:
    # 1. 获取授权级排他锁
    lock = _acquire_lock(f"locks/{auth_id}.lock", protocol_version=LOCK_PROTO_V1)
    if lock is None: return DENY_LOCK_UNAVAILABLE
    try:
        # 2. 读取并校验当前 authorization.json（含完整性摘要）
        doc = _read_verified(f"authorizations/{auth_id}/authorization.json")
        if doc is None: return DENY_CORRUPTED
        auth = doc["authorization"]
        # 3. 验证当前 revision（乐观并发）
        expected = auth["record_revision"]
        # 4. 写事务日志 PREPARED（写入内存构造的新 doc，尚未落盘）
        txn = TransactionLog(transaction_id=_new_txn_id(),
                             expected_revision=expected,
                             new_revision=expected + 1,
                             operation_payload_digest=_digest(payload),
                             transaction_state="PREPARED", ...)
        new_doc = payload_builder(doc, txn)      # 产生新状态
        # 5. 持久化 PREPARED（独立 pre-commit 记录，用于崩溃识别）
        _persist_txn_prepared(auth_id, txn)      # 单独小文件或 journal 区
        # 6. 写入新文档到临时文件（同目录），fsync 文件
        _write_tmp_and_fsync(f"authorizations/{auth_id}/.authorization.json.tmp", new_doc)
        # 7. 原子替换 authorization.json，fsync 目录
        _atomic_replace_and_fsync(".authorization.json.tmp", "authorization.json")
        # 8. 校验新文档完整性与关联一致性（计数==子记录集合大小等）
        _verify_consistency(new_doc)             # 失败 → 进入 INDETERMINATE
        # 9. 标记事务 COMMITTED（更新 journal 状态）
        _persist_txn_committed(auth_id, txn)
        return COMMITTED
    except CrashDuringReplace:
        _persist_txn_indeterminate(auth_id, txn)  # 无法确认 → INDETERMINATE → DENY
        return INDETERMINATE
    finally:
        _release_lock(lock)
```

**崩溃矩阵（§2.4 逐项）**：

| 崩溃点 | 恢复 |
|--------|------|
| 步骤 5（PREPARED 落盘）前 | 无副作用，额度/记录均未提交，可安全重试 |
| 步骤 5 后、步骤 7（原子替换）前 | 发现 PREPARED 但无 COMMITTED → 回滚，重放或 ABORT |
| 步骤 7 原子替换期间 | 替换要么成功（新文档可见）要么失败（旧文档保留），`os.replace` 原子性保证不出现半写 |
| 步骤 7 后、步骤 9（COMMITTED 标记）前 | 新文档已可见但 journal 仍是 PREPARED → 核对新文档 revision 与 txn.new_revision，一致则补标记 COMMITTED；不一致 → INDETERMINATE → DENY |
| 步骤 8 校验失败 | 新文档已替换但一致性校验不通过 → INDETERMINATE → DENY（不可退回，防止静默覆盖）|

**关键声明**：本方案依赖 `os.replace()` 的**单文件原子替换** + **fsync 文件与目录** + **journal 日志**。在 NTFS（本地）与 POSIX 本地文件系统上，`os.replace()` 对同一目录内重命名是原子的；但**目录 fsync 在 Windows 与 POSIX 的持久化语义存在差异**，且 **SMB/UNC、NFS 等网络文件系统不保证 rename 原子性**。

> **诚实结论：在"单一 JSON 文档 + 原子替换 + 锁 + fsync + journal"这一具体组合尚未在目标平台（Windows 本地 NTFS / POSIX 本地）被真实实现与故障注入验证之前，多记录原子提交的持久化保证仍属设计级契约，NOT_VERIFIED。因此 D054-01 继续维持 IMPLEMENT-01 前置阻断项。**

---

## 2. D054-02｜执行交接前授权二次验证（P0）

### 2.1 完整执行交接流程（必须全过才允许交接）

```python
def handoff_execution(auth_id, reservation_id) -> result:
    # 1. 获取授权级排他锁
    lock = _acquire_lock(f"locks/{auth_id}.lock", protocol_version=LOCK_PROTO_V1)
    if lock is None: return DENY_LOCK_UNAVAILABLE
    try:
        # 2. 重新读取并校验 AuthorizationRecord（不是缓存，不是内存）
        doc = _read_verified(f"authorizations/{auth_id}/authorization.json")
        auth = doc["authorization"]
        # 3. 检查 authorization_status == ACTIVE（二次验证，不信任预留时的状态）
        if auth["authorization_status"] != "ACTIVE": return DENY_STATE
        # 4. 检查有效期
        if _now() >= auth["expires_at"]: return DENY_EXPIRED
        # 5. 检查撤销
        if auth.get("revoked_at") is not None: return DENY_REVOKED
        # 6. 检查失效
        if auth.get("invalidated_at") is not None: return DENY_INVALIDATED
        # 7. 检查项目身份与指纹（重新计算并比对，不信任历史值）
        if _project_fingerprint_now() != auth["project_fingerprint"]: return DENY_FINGERPRINT
        # 8. 检查计划摘要（重新计算比对）
        if _plan_digest_now() != auth["plan_digest"]: return DENY_PLAN_DIGEST
        # 9. 检查 ReservationRecord 是否仍有效（存在且状态未进入终态）
        resv = _find_reservation(doc, reservation_id)
        if resv is None or resv["reservation_status"] not in ("RESERVED",):
            return DENY_RESERVATION
        # 10. 检查 AttemptRecord 是否不存在重复交接
        if resv.get("attempt_id") is not None:
            return DENY_ALREADY_ATTEMPTED
        # 11. 持久化 EXECUTION_STARTED（在锁内、原子替换文档）
        new_doc = _set_reservation_started(doc, reservation_id)   # reservation_status → EXECUTION_STARTED
        new_doc = _create_attempt(new_doc, reservation_id)        # 新建 AttemptRecord，status=EXECUTION_STARTED
        # 12. 持久化 attempt_id 与 reservation_id 绑定
        #     （attempt.reservation_id = reservation_id 写入同一文档）
        # 13. 提交事务（os.replace 原子替换）
        _atomic_commit(auth_id, "START_EXECUTION", lambda d: new_doc)
        # 14. 仅在提交成功后才允许执行交接
        return HANDOFF_GRANTED
    finally:
        _release_lock(lock)
```

**要点**：步骤 2–8 是**执行交接前的二次验证**，全部在授权级锁内**重新从持久化读取**并重新计算指纹/摘要，**不信任预留时刻的任何状态或缓存**。

### 2.2 撤销与交接竞争矩阵（工单 §3.2 逐项）

| 并发事件 | 保证 |
|----------|------|
| 预留后、执行前撤销 | 交接时步骤 5 检测 revoked_at → 拒绝交接 |
| 预留后、执行前过期 | 步骤 4 检测 expires_at → 拒绝交接 |
| 预留后、项目指纹变化 | 步骤 7 重新计算比对 → 拒绝交接 |
| 预留后、计划摘要变化 | 步骤 8 重新计算比对 → 拒绝交接 |
| 撤销与执行交接同时发生 | 两者都要拿同一把授权级锁 → 锁确定唯一先后顺序 |
| 执行交接已提交后撤销 | 撤销只能阻止**后续尚未交接的额度**，**不得声称追回已交接执行** |
| 执行结果未知时撤销 | 保留 INDETERMINATE，禁止重放；撤销不改变已发生的执行 |

### 2.3 撤销线性化点

- 撤销操作必须使用**相同的 authorization_id 授权级锁**（与交接、预留共用同一把锁）。
- **`EXECUTION_STARTED` 提交（步骤 13 的原子替换落盘）是执行交接的明确线性化点**。
- 若撤销先提交：交接的步骤 5 读到 revoked_at → 交接失败。
- 若交接先提交：撤销在锁内只能看到 `EXECUTION_STARTED` 已落盘，只能把授权状态置 REVOKED，阻止**后续未交接额度**，**不得回滚已交接执行**。
- **不得承诺已交接操作可被撤销回滚。**

> 线性化点的成立前提是"撤销"与"交接"都通过同一把授权级锁串行化、且都落在同一 `authorization.json` 原子替换单元内。在存储事务能力被真实实现与验证前，此线性化保证**仍属设计级契约，NOT_VERIFIED**——这正是 D054-01 阻断项的直接下游。

---

## 3. D054-03｜execution_attempt_count 统一定义（P1）

**选择方式 B：从 AttemptRecord 集合派生统计（不持久化计数器字段）。**

理由：单一 JSON 文档内 `attempts[]` 是唯一事实来源；持久化一个冗余计数器（方式 A）会引入"计数器 vs 集合大小"的一致性风险，与本轮"用单文档原子替换消除多文件不一致"的目标相悖。

规则：

- `execution_attempt_count = len(attempts[])`，统计**全部状态的 AttemptRecord**（CREATED / EXECUTION_STARTED / SUCCEEDED / FAILED / INDETERMINATE 均计入）。
- **INDETERMINATE 计入 attempt_count**（它确实是一次已登记的执行尝试）。
- `execution_completed_count = 状态 ∈ {SUCCEEDED, FAILED} 的 attempt 数`。
- `execution_success_count = 状态 == SUCCEEDED 的数`。
- `execution_failure_count = 状态 == FAILED 的数`。
- `indeterminate_count = 状态 == INDETERMINATE 的数`。

不变量：

```
execution_attempt_count = len(attempts[])                # 派生，无独立持久化字段
execution_completed_count = success_count + failure_count
execution_attempt_count >= execution_completed_count
authorization_slots_committed <= max_use_count
```

- 派生值**不得伪装成已持久化字段**：文档中 `execution_attempt_count` 等四计数**一律作为查询派生值**出现，不写入 `authorization.json` 的持久化字段（主记录仅持久化 `authorization_slots_committed`，其余四个为派生视图）。
- 一致性边界：派生统计在**同一授权级锁内**对**同一份已验证文档**计算，与记录更新原子一致，无需缓存。
- 禁止缓存派生计数（缓存失效会重新引入不一致）；每次需要时在同一锁内现算。
- **废除** 05.2/05.3 中"计数更新必须与记录状态在同一事务"的持久化计数表述，改为"计数是派生视图，天然与记录一致"。

**字段口径唯一性**：全文档统一采用上述派生口径，DIFF 记录 05.3 主记录 dataclass 中 `execution_completed_count`/`execution_success_count`/`execution_failure_count`/`indeterminate_count` 四个持久化字段的**移除映射**（它们从持久化字段降级为派生视图字段）。

---

## 4. D054-04｜EXHAUSTED 状态语义修正（P1）

### 4.1 授权状态（额度维度，与执行完成维度分离）

| 状态 | 语义 |
|------|------|
| ACTIVE | 有效且**存在未分配额度**（slots_committed < max_use_count 且未过期/撤销/失效）|
| EXHAUSTED | **有效额度已全部分配**（slots_committed == max_use_count），无论是否已执行完毕 |
| EXPIRED | 超过有效期 |
| REVOKED | 已主动撤销 |
| INVALIDATED | 项目指纹 / 计划摘要 / 绑定条件失效 |
| CORRUPTED | 记录损坏或一致性无法确认 |

### 4.2 关键语义（工单 §五 逐条）

- **EXHAUSTED 不代表所有执行已经完成**：额度全部分配即 EXHAUSTED，此时可能仍有 RESERVED / EXECUTION_STARTED / INDETERMINATE 的额度在执行中。
- **RESERVED 额度尚未执行时，授权仍可处于 EXHAUSTED**（额度已占满，只是还没执行完）。
- **INDETERMINATE 不会退还额度**：额度一旦提交（slots_committed +1）永不回退，故 INDETERMINATE 的额度仍占用槽位。
- **EXHAUSTED 不得重新变回 ACTIVE**：额度不退还，slots_committed 只增不减，故 EXHAUSTED 是单向前进。
- 优先级：`CORRUPTED > REVOKED > INVALIDATED > EXPIRED > EXHAUSTED > ACTIVE`（更严重/更晚的状态覆盖更早状态，但终态一旦进入不可逆）。
- 所有状态转换必须有持久化证据（同一 `authorization.json` 内记录 `record_revision` 递增 + 事务日志）。

### 4.3 状态转换表

| 当前 | 目标 | 触发条件 | 允许 |
|------|------|----------|------|
| ACTIVE | EXHAUSTED | slots_committed == max_use_count | ✅ |
| ACTIVE | EXPIRED | now >= expires_at | ✅ |
| ACTIVE | REVOKED | 主动撤销 | ✅ |
| ACTIVE | INVALIDATED | 指纹/摘要/绑定失效 | ✅ |
| EXHAUSTED | ACTIVE | — | ❌（额度不退还）|
| EXHAUSTED | EXPIRED/REVOKED/INVALIDATED | 相应条件 | ✅ |
| EXPIRED/REVOKED/INVALIDATED | ACTIVE/EXHAUSTED | — | ❌（终态不可逆）|
| 任意 | CORRUPTED | 一致性校验失败 | ✅（终态）|
| CORRUPTED | 其他 | — | ❌（需人工重建）|

---

## 5. 全局契约一致性复核

| # | 审查项 | 结论 |
|---|--------|------|
| 1 | AuthorizationRecord / ReservationRecord / AttemptRecord 字段关系 | 一致（05.4 增加存储布局 + 派生计数口径）|
| 2 | 存储布局与锁粒度 | 一致（方案 A 目录 + 单文档 + 授权级锁）|
| 3 | 事务日志及恢复规则 | 一致（PREPARED/COMMITTED/ABORTED/INDETERMINATE）|
| 4 | 不同授权并发写入安全 | 一致（不同物理路径互不覆盖）|
| 5 | 撤销与执行交接线性化 | 一致（同一锁 + EXECUTION_STARTED 提交为线性化点）|
| 6 | project_fingerprint / plan_digest 二次验证 | 一致（交接前重新计算比对）|
| 7 | execution_attempt_count 口径 | 一致（派生视图，全文档唯一）|
| 8 | EXHAUSTED 状态语义 | 一致（额度维度，与执行完成分离）|
| 9 | 四计数不变量 | 一致（派生口径）|
| 10 | at-most-once | 一致（每 slot 最多一次，不自动重放）|
| 11 | Oracle 三态与统计隔离 | 保持 |
| 12 | plan() 纯计划 / execute() 留桩 | 保持 |
| 13 | 文件身份四维模型 | 保持 |
| 14 | 版本发布与 HASH-RECEIPT | 保持 |
| 15 | A/B 三文件分叉未合并 | 保持 |
| 16 | F1/P0.8/F2 门禁 | 保持 |

---

## 6. IMPLEMENT-01 准入结论

**PASS-WITH-ISSUES（附前置阻断项，维持阻断）。**

| 编号 | 验收条件 | 结论 |
|------|----------|------|
| A01 | 存储布局有明确选择和理由 | ✅ 方案 A 目录 + 单文档 |
| A02 | 不同授权并发写入不覆盖 | ✅ 不同物理路径 |
| A03 | 同一授权多记录可恢复事务方案 | ⚠️ 方案已设计，持久化保证 NOT_VERIFIED |
| A04 | 事务日志状态转换完整 | ✅ PREPARED/COMMITTED/ABORTED/INDETERMINATE |
| A05 | 崩溃恢复不重复执行 | ⚠️ 依赖原子替换 + journal，NOT_VERIFIED |
| A06 | 执行交接前二次验证 | ✅ 完整流程已列 |
| A07 | 撤销与交接线性化点 | ✅ 同一锁 + EXECUTION_STARTED 提交 |
| A08 | 指纹/计划摘要重新验证 | ✅ 交接前重算比对 |
| A09 | execution_attempt_count 语义唯一 | ✅ 派生视图，口径唯一 |
| A10 | EXHAUSTED 与执行完成分离 | ✅ |
| A11 | 计数不变量一致 | ✅ |
| A12 | Windows/POSIX 能力边界 | ✅ 本地可靠，网络 FS 不保证 |
| A13 | 源码/测试零修改 | ✅ |
| A14 | 冻结哈希不变 | ✅ |
| A15 | 未验证项明确标记 | ✅ |
| A16 | IMPLEMENT-01 准入结论 | ⚠️ **维持阻断** |

**前置阻断项（IMPLEMENT-01 前必须解决，否则阻断）**：

1. **存储事务能力（D054-01，最高优先级）**：必须选定并真实实现"单一 JSON 文档 + 原子替换 + 锁 + fsync + journal"（或 SQLite/WAL），并通过目标平台（Windows 本地 NTFS / POSIX 本地）的崩溃注入验证，确认多记录原子提交的持久化保证。**在真实实现与故障注入验证之前，D054-01、D054-02 的持久化与线性化保证均为设计级契约，NOT_VERIFIED。**
2. `analyze_file` 行为一致性 NOT_VERIFIED（`scanner.py` vs `_analysis.py` 双重定义）。
3. A/B 三文件分叉（`cli.py` / `python_worker.py` / `scanner.py`）未合并。

**STOP — 等待用户独立审查。不得开始 IMPLEMENT-01，不得修改源码，不得运行测试，不得合并 A/B，不得解除 F1/P0.8/F2 门禁。此工单不包含代码实现授权。**

---

## 7. 交付物索引

| 文件 | 内容 |
|------|------|
| `USIT-STAGE-B-DESIGN-05.4.md` | 本文（存储布局 + 事务 + 撤销交接 + 计数 + EXHAUSTED）|
| `USIT-STAGE-B-DESIGN-05.4-ISSUES.md` | 问题清单 |
| `USIT-STAGE-B-DESIGN-05.4-DIFF.md` | DESIGN-05.3 → 05.4 差异 |
| `USIT-STAGE-B-DESIGN-05.4-EXECUTION-REPORT.md` | 执行报告 |
| `USIT-STAGE-B-DESIGN-05.4-HASH-MANIFEST.md` | 前四份哈希清单 |
| `USIT-STAGE-B-DESIGN-05.4-HASH-RECEIPT.txt` | MANIFEST 自身摘要收据 |
