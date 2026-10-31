# USIT-STAGE-B-DESIGN-05.2｜授权消费事务与并发发布安全封闭

> 阶段 B · DESIGN-05.2 · READ-ONLY AUDIT + DESIGN DOCUMENT REVISION
> 作者：USIT 主开发 · 生成：2026-10-30（GMT+8）
> 上游：DESIGN-01 至 DESIGN-05.1
> 工作区：`C:\Users\Administrator\WorkBuddy\2026-10-15-22-09-47\usit_p0`
> 状态：**STOP — 等待用户独立审查**（未授权 IMPLEMENT-01）

---

## 0. 任务定位

针对 DESIGN-05.1 遗留的三个问题做定向封闭：① 授权消费计数与状态机统一；② 版本发布临时目录所有权与并发互斥；③ HASH-MANIFEST 外部审计校验收据。

> 文档设计通过 ≠ 代码实现通过。所有伪代码为设计级契约，本工单不创建真实密钥、不签发真实授权、不执行授权。

---

## 1. D052-01｜授权消费计数与状态机彻底统一（P0）

### 1.1 修正计数语义（消除 DESIGN-05.1 矛盾）

DESIGN-05.1 的矛盾：`RESERVED` 时递增 `use_count`，又把 `use_count` 描述为"执行成功后的已消费次数"。本次彻底拆分：

```python
@dataclass(frozen=True)
class AuthorizationStateRecord:
    authorization_id: str            # 唯一，不可变
    nonce: str                       # 唯一，128 位熵，不可变
    max_use_count: int               # 必填，>=1，不可变
    authorization_slots_committed: int  # 已不可撤销分配的额度，RESERVED 持久化时 +1，永不自动回退
    authorization_consumed_count: int   # 已确认执行完成的次数，仅执行结果可靠确认后 +1
    execution_attempt_count: int        # 已持久化登记的执行尝试次数，每次尝试 +1
    reservation_id: str | None          # 每次额度预留唯一，持久化
    attempt_id: str | None              # 每次尝试唯一，持久化
    state: str                          # 见 §1.3 九态
    execution_result: str               # SUCCESS / FAILURE / UNKNOWN
    created_at: str                     # ISO-8601，不可变
    updated_at: str                     # ISO-8601，每次变更更新
    project_fingerprint: str            # 绑定项目指纹，不可变
    plan_digest: str                    # 绑定执行计划摘要，不可变
    schema_version: str                 # "usit-auth-v2"
```

### 1.2 字段属性表（工单 §2.2 逐条）

| 字段 | 类型 | 必填 | 唯一 | 更新时机 | 不可变 |
|------|------|------|------|---------|--------|
| authorization_id | str | 是 | 是 | 签发时 | 是 |
| nonce | str | 是 | 是 | 签发时 | 是 |
| max_use_count | int | 是 | 否 | 签发时 | 是 |
| authorization_slots_committed | int | 是 | 否 | RESERVED 持久化 | 只增不减 |
| authorization_consumed_count | int | 是 | 否 | 执行结果可靠确认 | 只增不减 |
| execution_attempt_count | int | 是 | 否 | 每次尝试登记 | 只增不减 |
| reservation_id | str | 条件 | 是 | RESERVED 时 | 生成后不变 |
| attempt_id | str | 条件 | 是 | 执行交接前 | 生成后不变 |
| state | str | 是 | 否 | 状态转换 | 否 |
| execution_result | str | 是 | 否 | 执行后 | 否 |
| created_at | str | 是 | 否 | 签发时 | 是 |
| updated_at | str | 是 | 否 | 每次变更 | 否 |
| project_fingerprint | str | 是 | 否 | 签发时 | 是 |
| plan_digest | str | 是 | 否 | 签发时 | 是 |
| schema_version | str | 是 | 否 | 签发时 | 是 |

**核心规则**：
- `max_use_count` 必须与 `authorization_slots_committed` 比较，**不能与成功次数（consumed_count）混用**。
- `authorization_slots_committed` 一旦增加，**不得自动减少**（额度预留不可退还，保证 at-most-once）。
- `authorization_consumed_count` **不得因 RESERVED 而增加**（RESERVED 只影响 slots_committed）。

### 1.3 九态状态机

```python
AUTHORIZATION_STATES = {
    "ISSUED",              # 已签发
    "RESERVED",            # 已预留额度（slots_committed 已 +1）
    "EXECUTION_STARTED",   # 已交接执行（attempt 已持久化）
    "CONSUMED",            # 已确认执行完成（consumed_count 已 +1）
    "INDETERMINATE",       # 执行结果无法确认
    "REJECTED",            # 校验失败
    "EXPIRED",             # 超期
    "REVOKED",             # 主动撤销
    "INVALIDATED",         # 证据/指纹变化
}
```

### 1.4 关键规则（工单 §2.3 逐条）

1. 先完成授权验证，再在排他锁下原子预留额度。
2. `reservation_id`、`attempt_id`、计数器、`RESERVED` 状态必须在任何执行调用前持久化。
3. 只有持久化成功后才允许进入执行交接。
4. 执行交接前必须持久化 `EXECUTION_STARTED`。
5. 执行结果只有具有可靠证据才允许记录 SUCCESS / FAILURE。
6. 无法确认执行结果必须进入 INDETERMINATE。
7. INDETERMINATE 默认拒绝再次执行。
8. RESERVED 或 EXECUTION_STARTED 崩溃恢复时，**不得自动释放额度**。
9. 不允许通过删除记录、重置计数器、重新生成 nonce 绕过额度限制。
10. `authorization_consumed_count` 不得因 RESERVED 而增加。
11. `authorization_slots_committed` 一旦增加，不得自动减少。
12. 状态文件损坏、回滚、替换、锁失败、写入失败，全部 DENY。

### 1.5 状态转换合法性矩阵

| 从 → 到 | 合法性 | 条件 |
|---------|--------|------|
| ISSUED → RESERVED | 合法 | 验证通过 + slots_committed < max_use_count，锁下原子预留 |
| RESERVED → EXECUTION_STARTED | 合法 | attempt_id 持久化成功 |
| EXECUTION_STARTED → CONSUMED | 合法 | 执行结果可靠确认（SUCCESS/FAILURE）|
| EXECUTION_STARTED → INDETERMINATE | 合法 | 无法确认执行结果 |
| RESERVED → INDETERMINATE | 合法 | 崩溃/超时，无法确认 |
| INDETERMINATE → CONSUMED | 合法（仅安全恢复）| 可靠幂等 + 可验证执行记录 |
| INDETERMINATE → 其它 | 非法（默认）| 需人工判定 |
| CONSUMED → 任意 | 非法 | 终态 |
| REJECTED/EXPIRED/REVOKED/INVALIDATED → 任意 | 非法 | 终态，需重新签发 |

### 1.6 attempt_id 与执行记录（工单 §2.4）

- `attempt_id = _stable_id("attempt", authorization_id, reservation_id, execution_attempt_count)`，内容派生，唯一。
- `reservation_id = _stable_id("resv", authorization_id, nonce, slots_committed)`，每次预留唯一。
- `attempt_id` / `reservation_id` 在**执行前**持久化（写入状态记录）。
- 执行记录防重复写入：以 `attempt_id` 为主键，已存在则拒绝重写。
- 执行结果 UNKNOWN 时：保留证据（记录 "result_evidence=UNKNOWN"，附观测信息），**不得用"没有执行记录"推断"执行一定未发生"**。
- **避免误判**：无执行记录 ≠ 未执行；无记录只说明"无法确认"，应进入 INDETERMINATE 而非 ISSUED。

### 1.7 并发及崩溃矩阵（工单 §2.5）

| 场景 | 要求 |
|------|------|
| 两个进程同时预留同一授权 | 排他锁下原子 CAS，最多一个成功分配最后额度 |
| RESERVED 写入前崩溃 | 不得执行（未持久化 = 未预留）|
| RESERVED 持久化后崩溃 | 保留额度（slots_committed 已 +1 不退还），进入恢复检查 |
| EXECUTION_STARTED 写入后崩溃 | INDETERMINATE |
| 执行成功但结果记录前崩溃 | INDETERMINATE（不假定未执行）|
| CONSUMED 持久化后重放 | DENY |
| 状态文件损坏 | DENY |
| nonce 重复 | DENY |
| 锁不可用 | DENY |
| 磁盘写入/持久化失败 | DENY |

**语义声明**：采用 **at-most-once**，**不得声称 exactly-once**。

---

## 2. D052-02｜版本发布临时目录所有权与并发互斥（P0）

### 2.1 删除固定临时目录设计（禁止项）

**禁止**：`tmp = vdir + ".tmp"`（固定名，多进程冲突）。

**禁止**：`if os.path.exists(tmp): shutil.rmtree(tmp)`（可能删除其他进程正在写的文件）。

### 2.2 唯一临时目录

```python
import secrets, os, json, time

def _new_tmp_dir(version_id: str, publisher_id: str) -> str:
    """每次发布独立的临时目录，排他创建。"""
    unique = secrets.token_hex(16)                      # 128 位熵随机唯一标识
    tmp_name = f".tmp-{version_id}-{unique}"
    # 排他创建：已存在则报错（不覆盖）
    os.mkdir(tmp_name)                                   # O_EXCL 语义，存在则 FileExistsError
    # 写入所有权记录
    owner = {
        "publisher_id": publisher_id,
        "process_id": os.getpid(),
        "created_at": _iso_now(),
        "version_id": version_id,
        "owner_token": secrets.token_hex(16),
    }
    _write_json(tmp_name + "/.owner", owner)
    return tmp_name
```

**规则**：
- 临时目录与目标版本目录位于同一文件系统（同一父目录）。
- 记录 `publisher_id`/`process_id`/`created_at`/`version_id`/`owner_token`。
- 记录文件只能由当前发布进程创建和管理。
- **任何进程不得清理无法证明归自己所有的临时目录。**

### 2.3 发布锁（跨进程互斥）

| 项 | 说明 |
|----|------|
| 锁作用域 | 同一个输出仓库（out_dir）|
| POSIX 锁机制 | `fcntl.flock(LOCK_EX)` 于锁文件 |
| Windows 锁机制 | `msvcrt.locking(LK_LOCK)` 于锁文件 |
| 锁获取超时 | 默认 30s，超时失败 |
| 锁不可用 | DENY（不发布）|
| 进程异常退出 | OS 自动释放文件锁（flock/locking 随进程结束释放）|
| CURRENT 更新 | 必须在发布互斥保护下执行 |
| 禁止项 | 不允许仅依赖随机目录名替代发布锁 |

### 2.4 残留临时目录清理（工单 §3.4）

1. 只能在取得发布锁后进行。
2. 只能检查明确的临时目录命名空间（`.tmp-*`）。
3. 必须验证目录所有权信息（读 `.owner`）。
4. 无法确认创建进程已结束时**不得删除**。
5. 所有权信息缺失或损坏时不得删除，标记 **QUARANTINED**。
6. 不得删除正式版本目录。
7. 不得删除 CURRENT 指向的版本。
8. 不得删除正在被读取的版本。
9. 清理失败不得影响当前版本读取。

**优先采用隔离标记（QUARANTINED）+ 人工清理**，不得为清理方便引入数据丢失风险。

### 2.5 CURRENT 并发切换（工单 §3.5）

- 发布锁保护 CURRENT 更新（读改写均在锁内）。
- 读取端避免半写入指针：写临时指针文件（唯一名）→ `os.replace` 原子替换。
- 临时指针文件唯一创建（`secrets.token_hex` 后缀 + 排他）。
- 指针替换失败时旧 CURRENT 保持有效（未替换）。
- 两个发布进程先后完成：发布锁串行化，后完成者基于最新 CURRENT 更新；**版本号 + 发布序号单调递增**。
- **不允许较早开始但较晚完成的发布意外覆盖更新版本**：发布前读取当前版本序号，若自己的版本序号 <= 当前序号，则拒绝覆盖（检测回退）。

### 2.6 跨平台持久化边界（工单 §3.6）

| 操作 | Windows | POSIX |
|------|---------|-------|
| 文件写入 + 内容持久化 | `FlushFileBuffers`/`_commit(fd)` | `fsync(fd)` |
| manifest 持久化 | 同上 | 同上 |
| 临时目录持久化 | 目录项由文件系统保证，关键文件 fsync | `fsync` 目录 fd |
| 版本目录发布 | `os.replace`（MoveFileEx）| `os.replace`（rename）|
| CURRENT 原子替换 | `os.replace` | `os.replace` |
| 父目录元数据持久化 | 无直接等价，靠文件 fsync | `fsync` 父目录 fd |
| 锁释放及崩溃恢复 | 进程结束自动释放 locking | 进程结束自动释放 flock |

**硬规则**：
- **不得将 `os.replace()` 直接描述为完整的跨平台持久化事务**（它只保证原子替换，不保证元数据已落盘）。
- 若某平台/文件系统无法提供设计所需保证 → **明确降级或拒绝发布**。

---

## 3. D052-03｜HASH-MANIFEST 外部审计校验收据（P1）

### 3.1 保持单向哈希关系

- HASH-MANIFEST 记录前四份文档的 SHA256。
- HASH-MANIFEST 不记录自身 SHA256。
- 不允许执行报告与 HASH-MANIFEST 相互回写。
- 不允许伪造摘要或使用占位哈希。

### 3.2 新增独立校验收据

新增 `USIT-STAGE-B-DESIGN-05.2-HASH-RECEIPT.txt`，记录 HASH-MANIFEST 自身摘要，**不再反向写入 HASH-MANIFEST**：

```
receipt_version: 1
manifest_filename: USIT-STAGE-B-DESIGN-05.2-HASH-MANIFEST.md
manifest_size_bytes: <实际值>
manifest_sha256: <实际值>
hash_algorithm: SHA-256
generated_at: <ISO-8601>
source_workspace: C:\Users\Administrator\WorkBuddy\2026-10-15-22-09-47\usit_p0
verification_status: NOT_VERIFIED
verification_method: external-audit-anchor-pending
```

### 3.3 外部锚定要求（工单 §4.3 区分四层）

1. 收据文件**记录了**摘要（本工单做到）。
2. 收据与清单内容**一致**（本工单做到，实际计算）。
3. 收据被复制到独立审计位置（本工单**未做**，无独立审计存储）。
4. 独立审计位置是否具备防篡改能力（本工单**无**，未引入受保护存储/签名）。

**关键声明**：

> **不得因为生成了 receipt，就声称其具有不可篡改性。**

**外部锚定状态 = NOT_VERIFIED**（若无真正独立的只读/签名/受保护审计存储）。本阶段禁止创建真实密钥或引入新凭据。

---

## 4. 全局一致性复核（工单 §五）

| # | 审查项 | 结论 |
|---|--------|------|
| 1 | DESIGN-01→05.2 字段语义一致 | 一致（授权字段已拆分为 slots_committed/consumed_count/attempt_count）|
| 2 | authorization_slots_committed 与 max_use_count | 一致（max 只与 slots_committed 比较）|
| 3 | authorization_consumed_count 与 execution_attempt_count | 一致（独立）|
| 4 | reservation_id/attempt_id/nonce 唯一性及关联 | 一致（内容派生唯一 + 绑定链）|
| 5 | RESERVED/EXECUTION_STARTED/CONSUMED/INDETERMINATE 边界 | 一致（九态）|
| 6 | 五类记录引用链 | 完整 |
| 7 | plan() 纯计划 / execute() 留桩 | 保持 |
| 8 | Oracle 三态与统计隔离 | 保持 |
| 9 | 文件身份四维模型 | 保持 |
| 10 | 版本发布与读取完整性规则 | 保持（补充并发互斥）|
| 11 | A/B 三文件分叉未擅自处理 | 保持 |
| 12 | F1/P0.8/F2 门禁不变 | 保持 |

---

## 5. IMPLEMENT-01 准入结论

**PASS-WITH-ISSUES —— 具备 IMPLEMENT-01 设计准入条件。**

| 关键项 | 状态 |
|--------|------|
| 额度预留与消费语义分离（D052-01）| ✅ slots_committed vs consumed_count 独立 |
| 并发预留不超发（D052-01）| ✅ 排他锁 + CAS + 不可退还额度 |
| 临时目录所有权与互斥（D052-02）| ✅ 唯一临时目录 + 发布锁 + QUARANTINED |
| 哈希校验收据（D052-03）| ✅ 单向 + RECEIPT，外部锚定 NOT_VERIFIED |

**剩余非阻断项**：① 密钥基础设施未引入；② `analyze_file` NOT_VERIFIED；③ A/B 分叉未合并；④ 外部审计锚定 NOT_VERIFIED（无受保护存储）。

**STOP — 等待用户独立审查。不得开始 IMPLEMENT-01，不得修改源码，不得解除历史门禁。本工单不包含任何代码实现授权。**

---

## 6. 交付物索引

| 文件 | 内容 |
|------|------|
| `USIT-STAGE-B-DESIGN-05.2.md` | 本文（三项封闭 + 全局复核）|
| `USIT-STAGE-B-DESIGN-05.2-ISSUES.md` | 问题清单 |
| `USIT-STAGE-B-DESIGN-05.2-DIFF.md` | DESIGN-05.1 → 05.2 差异 |
| `USIT-STAGE-B-DESIGN-05.2-EXECUTION-REPORT.md` | 执行报告 |
| `USIT-STAGE-B-DESIGN-05.2-HASH-MANIFEST.md` | 前四份哈希清单 |
| `USIT-STAGE-B-DESIGN-05.2-HASH-RECEIPT.txt` | MANIFEST 自身摘要收据 |
