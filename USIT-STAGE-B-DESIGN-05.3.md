# USIT-STAGE-B-DESIGN-05.3｜多次授权记录模型与执行状态机修订

> 阶段 B · DESIGN-05.3 · READ-ONLY AUDIT + DESIGN DOCUMENT REVISION
> 作者：USIT 主开发 · 生成：2026-10-30（GMT+8）
> 上游：DESIGN-01 至 DESIGN-05.2
> 工作区：`C:\Users\Administrator\WorkBuddy\2026-10-15-22-09-47\usit_p0`
> 状态：**STOP — 等待用户独立审查**（未授权 IMPLEMENT-01）

---

## 0. 问题定位

DESIGN-05.2 的结构性矛盾：主记录 `AuthorizationStateRecord` 只有一个 `reservation_id` + 一个 `attempt_id`，且 `CONSUMED` 是终态，却允许 `max_use_count > 1` 多次使用。**单一 attempt + 终态 与 多次额度 在结构上无法兼容**。本轮采用**分层记录模型**正面解决，不再只改字段名。

> 文档设计通过 ≠ 代码实现通过。所有伪代码为设计级契约。

---

## 1. D053-01｜授权主记录与执行记录拆分（P0）

### 1.1 分层记录模型（模型 B）

采用三类独立记录，**不再将所有信息塞入单个状态记录**：

```python
# A. 授权主记录（描述授权生命周期，不保存单个执行记录）
@dataclass(frozen=True)
class AuthorizationRecord:
    authorization_id: str          # 唯一，不可变
    nonce: str                     # 唯一，不可变
    max_use_count: int             # >=1，不可变
    authorization_slots_committed: int   # 已分配额度，只增不减
    execution_completed_count: int       # 可靠确认完成的次数（success+failure）
    execution_success_count: int         # 可靠确认成功次数
    execution_failure_count: int         # 可靠确认失败次数
    indeterminate_count: int             # 结果无法确认次数（单独统计）
    authorization_status: str      # ACTIVE/EXHAUSTED/EXPIRED/REVOKED/INVALIDATED/CORRUPTED
    project_fingerprint: str       # 不可变
    plan_digest: str               # 不可变
    issued_at: str                 # 不可变
    expires_at: str
    revoked_at: str | None
    invalidated_at: str | None
    schema_version: str            # "usit-auth-v3"
    record_revision: int           # 每次变更 +1，乐观并发控制
```

**主记录不保存单个 reservation_id 或 attempt_id 作为唯一当前执行记录。**

```python
# B. 额度预留记录（每次预留单独一条）
@dataclass(frozen=True)
class ReservationRecord:
    reservation_id: str            # 全局唯一
    authorization_id: str          # 外键
    slot_index: int                # 1..max_use_count，唯一
    reservation_status: str        # RESERVED/EXECUTION_STARTED/COMPLETED/INDETERMINATE/REJECTED
    reserved_at: str
    reserved_by: str
    owner_token: str
    attempt_id: str | None         # 最多绑定一个 attempt
    record_revision: int
```

```python
# C. 执行尝试记录（每次尝试单独一条）
@dataclass(frozen=True)
class AttemptRecord:
    attempt_id: str                # 全局唯一
    authorization_id: str          # 外键
    reservation_id: str            # 外键，1:0..1
    attempt_sequence: int          # 递增
    attempt_status: str            # CREATED/EXECUTION_STARTED/SUCCEEDED/FAILED/INDETERMINATE
    execution_result: str          # SUCCESS/FAILURE/UNKNOWN
    started_at: str | None
    completed_at: str | None
    result_evidence: str | None    # 关联 attempt_id/project_fingerprint/plan_digest
    executor_identity: str | None
    project_fingerprint: str
    plan_digest: str
    record_revision: int
```

### 1.2 约束（工单 §2.1 逐条）

**ReservationRecord 约束**：
- `reservation_id` 全局唯一。
- `(authorization_id, slot_index)` 唯一。
- `slot_index` 从 1 开始，不得超过 `max_use_count`。
- 已提交的 slot 不得重新分配。
- 一个 reservation 最多绑定一个 attempt_id。
- 不允许通过删除 ReservationRecord 退还额度。

**AttemptRecord 约束**：
- `attempt_id` 全局唯一。
- 一个 reservation 最多对应一个 attempt。
- 同一 attempt_id 不允许重复执行。
- 已进入执行阶段的 attempt 不允许自动重试。

### 1.3 记录关联关系与基数

```
AuthorizationRecord 1 : N ReservationRecord     (一授权多预留)
ReservationRecord   1 : 0..1 AttemptRecord       (一预留最多一尝试)
AuthorizationRecord 1 : N AttemptRecord          (一授权多尝试)
```

| 关系 | 基数 | 唯一键 | 引用完整性 | 删除限制 |
|------|------|--------|-----------|---------|
| Auth → Reservation | 1:N | `(authorization_id, slot_index)` | 子记录 authorization_id 必须存在于主记录 | 主记录不可删（终态保留）|
| Reservation → Attempt | 1:0..1 | `reservation_id` | attempt.reservation_id 必须存在 | reservation 有 attempt 时不可删 |
| Auth → Attempt | 1:N | `attempt_id` | attempt.authorization_id 必须存在 | attempt 不可删（防重放）|

**规则**：任何记录缺失、引用不一致、重复主键或孤立记录，**均不得被解释为执行未发生**；默认 DENY，并进入审计异常处理。

### 1.4 三类状态机分离

**授权状态机**（描述授权本身，不描述某次执行）：

```
ACTIVE → EXHAUSTED    （slots_committed == max_use_count 且全部完成/终态）
ACTIVE → EXPIRED      （now >= expires_at）
ACTIVE → REVOKED      （主动撤销）
ACTIVE → INVALIDATED  （证据/指纹变化）
任意 → CORRUPTED      （记录不一致/损坏）
```

**预留状态机**（描述某个额度槽位）：

```
RESERVED → EXECUTION_STARTED
RESERVED → REJECTED
EXECUTION_STARTED → COMPLETED
EXECUTION_STARTED → INDETERMINATE
RESERVED → INDETERMINATE        （崩溃/超时）
COMPLETED / INDETERMINATE / REJECTED → 终态
```

**执行状态机**（描述某次尝试）：

```
CREATED → EXECUTION_STARTED
EXECUTION_STARTED → SUCCEEDED
EXECUTION_STARTED → FAILED
EXECUTION_STARTED → INDETERMINATE
SUCCEEDED / FAILED / INDETERMINATE → 终态
```

**关键说明（工单 §2.3 逐条）**：
- 某次 Attempt SUCCEEDED，**不代表**整个 Authorization 进入终态。
- 某次 Attempt FAILED，**也不代表**整个 Authorization 进入终态。
- Authorization 只有"额度全部分配完毕 / 过期 / 撤销 / 失效"等条件满足时才改变生命周期状态。
- 某个 Reservation 进入 INDETERMINATE，**不影响**其他已独立预留额度的状态判断，但必须遵守授权整体 DENY 策略和额度限制。

---

## 2. D053-02｜计数定义与多次额度竞争（P0）

### 2.1 统一计数语义

| 字段 | 含义 | 更新时机 |
|------|------|---------|
| `authorization_slots_committed` | 已不可撤销分配的额度数 | 额度预留时 +1 |
| `execution_attempt_count` | 已持久化创建的 AttemptRecord 总数 | 创建 AttemptRecord 时 +1 |
| `execution_completed_count` | 可靠确认完成次数（success+failure）| 结果可靠确认时 +1 |
| `execution_success_count` | 可靠确认成功次数 | SUCCESS 时 +1 |
| `execution_failure_count` | 可靠确认失败次数 | FAILURE 时 +1 |
| `indeterminate_count` | 结果无法确认次数 | UNKNOWN 时 +1 |

**不变量**：

```
execution_completed_count = execution_success_count + execution_failure_count
execution_attempt_count >= execution_completed_count
authorization_slots_committed <= max_use_count
```

**INDETERMINATE 不计入 completed_count**，单独统计 `indeterminate_count`。

**规则**：
- 额度预留时 `slots_committed +1`。
- 创建 AttemptRecord 时 `execution_attempt_count +1`。
- 只有可靠确认执行结果后 `completed_count` 才 +1。
- 任何计数更新必须与对应记录状态**在同一原子事务（或可恢复事务）中保持一致**。

### 2.2 多进程并发额度预留（事务伪代码）

假设 `max_use_count=3`，两个进程同时申请最后一个额度：

```python
def reserve_slot(auth_id: str) -> str | None:
    # 1. 获取授权级排他锁
    lock = _acquire_auth_lock(auth_id)          # 授权级锁，非全局锁
    if lock is None: return DENY_LOCK_UNAVAILABLE
    try:
        # 2. 重新读取并验证 AuthorizationRecord
        auth = _read_auth_record(auth_id)
        _verify(auth)                            # 校验完整性/摘要
        # 3. 检查授权状态和有效期
        if auth.authorization_status != "ACTIVE": return DENY_STATE
        if _now() >= auth.expires_at: return DENY_EXPIRED
        # 4. 从持久化 ReservationRecord 确认已分配 slot（非内存计数）
        committed = _list_reservation_slots(auth_id)   # 读持久化子记录
        if len(committed) >= auth.max_use_count: return DENY_EXHAUSTED
        # 5. 分配唯一 slot_index（取已分配集合的补集最小者）
        slot = _next_free_slot(committed, auth.max_use_count)
        # 6. 写 ReservationRecord（唯一键 (auth_id, slot) 保证不超发）
        reservation = ReservationRecord(reservation_id=_stable_id("resv", auth_id, slot),
                                        slot_index=slot, reservation_status="RESERVED", ...)
        _insert_reservation(reservation)         # 违反唯一键则失败
        # 7. 更新主记录 slots_committed 和 revision（乐观并发）
        _update_auth_committed(auth_id, auth.record_revision)
        # 8. 持久化并验证关联一致性（主记录计数 == 子记录集合大小）
        _verify_consistency(auth_id)
        # 9. 成功后才返回 reservation_id
        return reservation.reservation_id
    finally:
        _release_auth_lock(lock)
    # 10. 任一步失败均不得允许执行交接
```

**保证**：
- 同一个 `slot_index` 只能被一个 ReservationRecord 占用（唯一键约束）。
- 两个进程不能同时成功预留同一额度（锁 + 唯一键双重保证）。
- 主记录计数与 ReservationRecord 集合一致（第 8 步校验）。
- 不能仅凭内存计数判断额度是否可用（第 4 步读持久化）。
- 不能依靠随机 reservation_id 代替并发互斥（锁 + 唯一键）。
- 锁失败、状态文件损坏、事务提交不完整一律 DENY。

### 2.3 at-most-once 边界（工单 §3.3）

- 每个 slot 最多执行一次。
- 每个 reservation 最多产生一个 attempt。
- 每个 attempt 最多交接一次执行。
- 结果未知时不自动重放。
- **不得声称 exactly-once**。
- **不得因为 FAILED 就自动重新使用原 reservation**。
- 需要再次尝试时，必须使用**新的、尚未分配的额度**，并重新经过完整授权检查。
- INDETERMINATE 不得自动重试，也不得自动释放额度。

**核心解释**：多次授权 = 多个独立额度各自最多执行一次，**不是同一个 attempt 可以重复执行**。

---

## 3. D053-03｜崩溃恢复与记录一致性（P0）

### 3.1 故障矩阵（工单 §四逐条）

| 崩溃点 | 预期处理 |
|--------|---------|
| Reservation 写入前 | 不得执行，额度未提交 |
| Reservation 已持久化，主记录更新前 | 恢复时核对事务日志；无法确认则 DENY |
| 主记录已更新，事务提交前 | 不得推断提交成功，按事务恢复规则处理 |
| RESERVED 后、Attempt 创建前 | 保留额度，不自动退还 |
| Attempt 创建后、EXECUTION_STARTED 前 | 不得执行交接，按恢复规则处理 |
| EXECUTION_STARTED 持久化后 | 结果未知时 INDETERMINATE |
| 执行成功、结果记录前 | INDETERMINATE，不自动重放 |
| 执行失败、结果记录前 | INDETERMINATE，不自动重放 |
| SUCCESS 记录提交后再次请求 | DENY 重放 |
| 多进程同时恢复 | 授权级排他锁串行化 |
| 主记录与子记录计数不一致 | DENY，进入 CORRUPTED/审计隔离 |
| 子记录丢失或被替换 | DENY |
| 存储回滚到旧版本 | DENY，不能仅靠旧文件自证未回滚 |

### 3.2 事务存储机制（A08 核心）

**关键结论：当前文件存储架构（多个普通文件连续写入）无法可靠实现多记录原子提交。这必须列为 IMPLEMENT-01 的前置阻断项。**

**诚实声明**：

> `os.replace()` + 文件锁的组合只能保证**单文件**的原子替换，**不能**保证主记录 + ReservationRecord + AttemptRecord 三者的**跨文件原子提交**。多个普通文件的连续写入**不是**原子事务。

**可选实现方案（IMPLEMENT-01 前必须选定其一，否则阻断）**：

| 方案 | 原子性保证 | 复杂度 | 阶段 B 现状 |
|------|-----------|--------|------------|
| SQLite（单文件事务）| ✅ 多行原子提交（BEGIN/COMMIT）| 低 | 未引入，需 IMPLEMENT 决策 |
| 单一 JSON 文档（内含 records 数组，原子替换整个文档）| ✅ 单文件原子替换 | 中 | 可行，但需放弃"多文件"架构 |
| 事务日志（WAL）+ 恢复 | ✅ 可恢复事务 | 高 | 需实现 WAL |
| 多文件 + 锁（当前 05.2 隐含）| ❌ 非原子 | — | **不可接受** |

**裁决**：DESIGN-05.3 明确——

1. **存储事务能力是 IMPLEMENT-01 的前置阻断项**。
2. 推荐方案：**单一 JSON 文档存储所有记录**（`authorization_id → {主记录, reservations[], attempts[]}`），用 `os.replace()` 原子替换整个文档 + 文件锁 + fsync，实现单文件内多记录的一致性。
3. 若采用 SQLite，需在 IMPLEMENT-01 引入 SQLite 依赖（单独决策）。
4. **不得用伪代码掩盖"多文件非原子"的问题。**

---

## 4. D053-04｜执行结果与审计语义（P1）

### 4.1 SUCCESS / FAILURE / UNKNOWN 判定

1. **SUCCESS**：必须有可核验的执行完成证据（`result_evidence` 非空，关联 attempt_id/project_fingerprint/plan_digest）。
2. **FAILURE**：必须区分"执行已发生但失败"（有执行证据但结果失败）与"执行是否发生无法确认"（后者归 UNKNOWN）。
3. 无法确认执行是否发生时只能是 **UNKNOWN/INDETERMINATE**。
4. **不存在执行记录不能证明执行未发生**。
5. `result_evidence` 必须关联 `attempt_id`、`project_fingerprint`、`plan_digest`。
6. 结果记录必须防止重复提交（attempt_id 主键）和事后覆盖（终态不可改）。
7. **修正 DESIGN-05.2 中 `consumed_count` 容易被理解为成功次数的问题**——改用 `execution_completed_count`（success+failure 之和）。

### 4.2 字段命名统一

- 废除 `authorization_consumed_count`（歧义）。
- 统一为 `execution_completed_count` / `execution_success_count` / `execution_failure_count` / `indeterminate_count`。
- 全套文档禁止同一字段不同含义（差异映射见 DIFF）。

---

## 5. D053-05｜Windows/POSIX 锁与存储边界补充（P1）

### 5.1 Windows

| 项 | 说明 |
|----|------|
| 锁文件创建/锁定范围 | `msvcrt.locking(LK_LOCK)` 需先 seek 到字节偏移，锁定**指定字节范围** |
| 进程异常退出后锁释放 | OS 自动释放（handle 关闭即释放）|
| 锁文件被删除/替换风险 | 锁文件 inode/handle 变化 → 锁失效；需检测 |
| NTFS vs SMB/UNC | NTFS 本地锁可靠；**SMB/UNC 锁不可靠** |
| 不支持可靠互斥的文件系统 | **DENY** |

### 5.2 POSIX

| 项 | 说明 |
|----|------|
| `flock` 文件描述符生命周期 | 锁绑定 fd，fd 关闭即释放；进程退出自动释放 |
| 多进程/多线程竞争 | flock 对同一文件跨进程互斥；同进程多 fd 需注意 |
| 锁文件删除/替换/inode 变化 | 删除后新文件 inode 不同 → 锁失效，需检测 |
| 本地 vs NFS | 本地 flock 可靠；**NFS 锁不可靠** |
| 目录/文件 fsync 边界 | 文件 fsync 保证内容落盘；目录 fsync 保证目录项 |

### 5.3 共同规则

- 锁粒度：**授权级锁**（每个 authorization_id 独立锁文件），不得仅用全局锁或内存锁。
- 所有参与进程必须使用**相同锁文件及锁协议**。
- 锁文件身份异常、锁丢失、锁协议版本不一致 → **DENY**。
- 锁机制无法提供保证的平台 → 标记 `IDENTITY_UNAVAILABLE` 或 `LOCK_UNVERIFIED`。
- **不得宣称未经测试的平台兼容性为 VERIFIED**。

---

## 6. D053-06｜全局契约一致性检查

| # | 审查项 | 结论 |
|---|--------|------|
| 1 | Authorization/Reservation/Attempt 三类记录定义 | 一致（本工单新增）|
| 2 | 字段唯一性/可空性/不可变性/更新时机 | 一致 |
| 3 | max_use_count 与 slots_committed | 一致（max 只与 slots_committed 比较）|
| 4 | completed/success/failure/indeterminate 计数 | 一致（满足不变量）|
| 5 | 三类状态机及合法转换 | 一致 |
| 6 | 多次额度并发预留 | 一致（锁 + 唯一键 + 持久化读取）|
| 7 | at-most-once 约束 | 一致 |
| 8 | 崩溃恢复及事务完整性 | 一致（存储事务列为前置阻断项）|
| 9 | 五类记录引用链 | 完整 |
| 10 | Oracle 三态及统计隔离 | 保持 |
| 11 | plan() 纯计划 / execute() 留桩 | 保持 |
| 12 | 文件身份四维模型 | 保持 |
| 13 | 05.2 版本发布与 HASH-RECEIPT | 保持 |
| 14 | A/B 三文件分叉未合并 | 保持 |
| 15 | F1/P0.8/F2 门禁 | 保持 |

---

## 7. IMPLEMENT-01 准入结论

**PASS-WITH-ISSUES（附前置阻断项）。**

| 关键项 | 状态 |
|--------|------|
| A01 主记录不再保存单一 attempt | ✅ 分层模型 |
| A02 三类记录独立持久化 | ✅ |
| A03 关系基数明确 | ✅ 1:N / 1:0..1 / 1:N |
| A04 三类状态机独立 | ✅ |
| A05 slot_index 唯一不重复 | ✅ 唯一键 |
| A06 计数无歧义 | ✅ 不变量 |
| A07 并发不超发 | ✅ 锁 + 唯一键 |
| **A08 多文件原子提交** | ⚠️ **前置阻断项**：当前文件架构无法可靠多记录原子提交，IMPLEMENT-01 前必须选定单一文档原子替换或 SQLite |
| A09 UNKNOWN 不重试不退额度 | ✅ |
| A10 FAILED vs UNKNOWN 区分 | ✅ |
| A11 锁边界明确 | ✅ |
| A12 字段冲突有差异映射 | ✅ |

**前置阻断项（IMPLEMENT-01 开始前必须解决）**：
1. **存储事务能力**：必须在单一 JSON 文档原子替换 / SQLite / WAL 三者中选定其一，否则无法可靠保证主记录 + 额度记录 + 执行记录的一致性。
2. `analyze_file` 行为一致性 NOT_VERIFIED。
3. A/B 三文件分叉未合并。

**STOP — 等待用户独立审查。不得开始 IMPLEMENT-01，不得修改源码，不得运行测试，不得合并 A/B，不得解除 F1/P0.8/F2 门禁。此工单不包含代码实现授权。**

---

## 8. 交付物索引

| 文件 | 内容 |
|------|------|
| `USIT-STAGE-B-DESIGN-05.3.md` | 本文（分层记录 + 状态机 + 事务阻断项）|
| `USIT-STAGE-B-DESIGN-05.3-ISSUES.md` | 问题清单 |
| `USIT-STAGE-B-DESIGN-05.3-DIFF.md` | DESIGN-05.2 → 05.3 差异 |
| `USIT-STAGE-B-DESIGN-05.3-EXECUTION-REPORT.md` | 执行报告 |
| `USIT-STAGE-B-DESIGN-05.3-HASH-MANIFEST.md` | 前四份哈希清单 |
| `USIT-STAGE-B-DESIGN-05.3-HASH-RECEIPT.txt` | MANIFEST 自身摘要收据 |
