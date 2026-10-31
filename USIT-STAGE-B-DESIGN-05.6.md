# USIT-STAGE-B-DESIGN-05.6｜锁所有权契约、事务清理与阶段阻断项验收方案

> 阶段 B · DESIGN-05.6 · READ-ONLY ANALYSIS + DESIGN REVISION
> 作者：USIT 主开发 · 生成：2026-10-31（GMT+8）
> 上游：DESIGN-05.5
> 工作区：`C:\Users\Administrator\WorkBuddy\2026-10-15-22-09-47\usit_p0`
> 状态：**STOP — 等待用户独立审查**（未授权 IMPLEMENT-01）

---

## 0. 范围与结论预览

本轮补齐 DESIGN-05.5 遗留的 NOTE-1、NOTE-2，并为三个阻断项（P0-BLOCK-1 / P0-BLOCK-2 / P1-BLOCK-3）制定**可执行、可审计的解决与验收方案**。**只制定方案，不执行**。

> 文档设计通过 ≠ 代码实现通过。所有伪代码为设计级契约，不得被解读为已实现或已验证。

---

## 1. NOTE-1｜锁所有权与 Lock Token 契约

### 1.1 LockToken 数据结构（拒绝布尔标志）

```python
@dataclass(frozen=True)
class LockToken:
    authorization_id: str        # 归属授权
    process_id: int              # 获取进程 PID
    thread_id: int               # 获取线程标识
    lock_path: str               # 锁文件绝对路径（必须与 authorization_id 对应）
    lock_file_identity: str      # 锁文件对象身份（inode/dev 或 Windows 等价）
    acquisition_id: str          # 每次获取唯一（UUID/ULID）
    acquired_at: str             # 获取时间戳
    platform: str                # "windows-ntfs" / "posix-local"
    lock_proto_version: str      # 锁协议版本
    released: bool               # 生命周期状态（仅进程内可变，token 其余字段不可变）
```

**禁止**：token 只含 `locked=True` 布尔标志。必须能据此独立校验"谁、在哪个进程/线程、锁了哪个文件对象、何时、是否仍有效"。

### 1.2 所有权校验规则

| 规则 | 约束 |
|------|------|
| 跨线程使用 | **默认禁止**（token 绑定 thread_id，跨线程校验失败）|
| 跨进程传递 | **默认禁止**（token 绑定 process_id + lock_file_identity）|
| 锁文件路径 | 必须与 authorization_id 对应（`_lock_path(auth_id)` 唯一映射）|
| 锁文件身份 | 必须与实际持有锁的文件对象一致（inode/dev 或等价）|
| 已释放 token | 拒绝（`released == True` → 校验失败）|
| 过期/失效/伪造 token | 拒绝（acquisition_id 不可伪造，锁文件身份不匹配即拒）|
| 跨授权复用 | 不同 authorization_id **不得复用**同一 token |
| 重复释放 | 幂等拒绝第二次（`released` 已 True，二次 release 报错或忽略并记录）|
| 错误释放 | 非当前 token 持有者释放 → 拒绝并记录异常 |
| 持锁期间替换锁文件 | 锁文件身份变化 → 锁失效 → 后续 `assert_lock_owned` 全部 DENY |

### 1.3 函数调用契约（前置/后置/所有权变化/异常）

| 函数 | 前置条件 | 后置条件 | 锁所有权变化 | 异常处理 |
|------|----------|----------|--------------|----------|
| `acquire_authorization_lock(auth_id)` | 锁文件可打开 | 返回 LockToken（released=False）| 获得所有权 | 失败返回 None → 上层 DENY |
| `release_authorization_lock(token)` | token 有效且未释放 | token.released=True | 释放所有权 | 重复释放/错误释放 → 记录并拒绝 |
| `assert_lock_owned(auth_id, token)` | token 由调用者持有 | 通过则返回，不改变状态 | 不变 | 不匹配 → DENY_LOCK_NOT_OWNED |
| `atomic_commit(...)` | 无（自行取锁）| 事务提交结果 | 获取→释放（finally）| 异常仍释放锁 |
| `atomic_commit_locked(...)` | 已持 auth_id 锁（token 传入）| 锁内事务结果 | 不变（不取锁）| 异常上抛，锁由外层释放 |
| `handoff_execution(...)` | 无（自行取锁）| 交接结果 | 获取→释放 | finally 释放 |
| `revoke_authorization(auth_id)` | 无（自行取锁）| 撤销结果 | 获取→释放 | finally 释放 |

**核心**：`atomic_commit_locked` 与 `handoff_execution` 之间通过 `assert_lock_owned` 校验真实所有权；任何 `_locked` 后缀函数都不自行取锁。

### 1.4 跨平台规则（不声称等价）

| 项 | Windows/NTFS | POSIX 本地 |
|----|--------------|-----------|
| 锁原语 | `msvcrt.locking`（字节范围锁）| `flock`（fd 级）|
| 句柄生命周期 | handle 关闭即释放 | fd 关闭即释放 |
| 进程异常退出 | OS 释放 handle → 锁释放 | OS 关闭 fd → 锁释放 |
| 线程内重复获取 | 需谨慎（同进程多 handle）| 需谨慎（同进程多 fd）|
| 文件替换/删除/重创建 | 锁绑定原 handle，替换后新 handle 无锁 → 失效 | 删除后 inode 变 → 锁失效 |
| 网络文件系统（SMB/UNC/NFS）| **不保证** | **不保证** |

**结论**：Windows 与 POSIX 锁行为**不完全等价**，本设计不宣称等价。无法证明的平台能力标记 `NOT_VERIFIED`。网络文件系统一律 DENY 或降级 `LOCK_UNVERIFIED`。

### 1.5 验收

- 任意持锁函数都能通过 `assert_lock_owned` 验证**真实锁所有权**。
- 伪造 token、跨线程 token、跨授权 token、已释放 token 均**不能通过**校验。
- 全链路**无隐含重复加锁路径**。

---

## 2. NOTE-2｜事务 Journal/Pending 清理与保留策略

### 2.1 事务文件分类

| 类型 | 路径模式 | 内容 |
|------|----------|------|
| PREPARED journal | `journal/<txn_id>.txn`（state=PREPARED）| 事务意图，未确认提交 |
| COMMITTED journal | 同上（state=COMMITTED）| 已确认提交 |
| ABORTED journal | 同上（state=ABORTED）| 已回滚 |
| INDETERMINATE journal | 同上（state=INDETERMINATE）| 崩溃，状态未知 |
| candidate document | `pending/<txn_id>.json` | 完整新文档候选 |
| temporary document | `pending/<txn_id>.json.tmp` | 写入中的临时文件 |
| recovery metadata | 随 journal 字段（pending_path / pending_sha256 / expected_revision / new_revision）| 恢复判定依据 |

### 2.2 清理准入规则

| 状态 | 允许清理？ | 说明 |
|------|-----------|------|
| COMMITTED | ✅（满足保留期）| 终态，可清理（审计保留后）|
| ABORTED | ✅（无副作用）| 终态，可清理 |
| PREPARED | ❌ | 未终结，禁止自动清理 |
| INDETERMINATE | ❌ | 状态未知，禁止自动清理 |
| 正在恢复中 | ❌ | 恢复流程持有引用，禁止清理 |

**规则**：
- 无法证明事务已安全终结时**不得清理**；无法判断时 **DENY**。
- 清理操作**必须持有授权级锁**（避免与恢复/新事务竞争）。
- 清理失败**不影响授权继续执行**（清理是旁路，非主路径）。
- 清理过程再崩溃 → 下次恢复时按 journal 状态重判，幂等。
- journal 与候选文档保留期限：审计保留期内不删；终态且过保留期才可清理。

### 2.3 幂等与审计字段

```python
@dataclass(frozen=True)
class CleanupRecord:
    cleanup_id: str              # 全局唯一
    transaction_id: str          # 被清理事务
    candidate_digest: str        # 候选文件摘要（校验用）
    cleanup_result: str          # CLEANED / FAILED / SKIPPED
    cleanup_timestamp: str
    cleanup_actor: str           # 执行者标识
    cleanup_failure_reason: str | None
```

- 清理幂等：重复 cleanup 同一 transaction_id，结果一致，不重复删除。
- **不得删除仍被恢复流程引用的候选文件**（清理前检查 journal 状态与引用）。

### 2.4 清理状态机

```
PENDING → CLAIMED → CLEANED
   │          │
   └──────────┴→ FAILED（可重试，仍 PENDING）
                 → INDETERMINATE（崩溃，下次恢复重判）
```

| 转换 | 触发 | 说明 |
|------|------|------|
| PENDING → CLAIMED | 取得锁 + 判定可清理 | 标记本进程负责清理 |
| CLAIMED → CLEANED | 删除 journal+候选成功 | 记录 cleanup_id/result |
| CLAIMED → FAILED | 删除失败 | 保留 journal，记录失败原因，可重试 |
| CLAIMED → INDETERMINATE | 清理中崩溃 | 下次恢复按 journal 状态重判 |

### 2.5 验收

- 每种事务状态都有明确保留/清理策略。
- 未终结（PREPARED/INDETERMINATE）或恢复中的事务**不会被误删**。
- 清理失败**不会被误报成功**。

---

## 3. P0-BLOCK-1｜存储事务真实实现与故障注入验收方案（只定方案）

### 3.1 未来实现最小范围

1. 授权级锁（NOTE-1 契约）。
2. 单授权 JSON 原子提交（`authorization.json`）。
3. PREPARED/COMMITTED/ABORTED/INDETERMINATE journal。
4. candidate document 持久化 + 摘要校验。
5. transaction_id 幂等。
6. 崩溃恢复 + DENY 策略。
7. 事务清理与审计（NOTE-2 契约）。

### 3.2 故障注入点矩阵

| 故障点 | 预期磁盘状态 | 恢复动作 | 授权是否继续 | 人工介入 |
|--------|--------------|----------|--------------|----------|
| 锁获取前 | 无变化 | 重试 | 否（未执行）| 否 |
| 锁获取后、PREPARED 前 | 无变化 | 重试 | 否 | 否 |
| PREPARED 写入中 | journal 半写/无 | 丢弃重写 | 否 | 否 |
| PREPARED 写入后、candidate 前 | journal=PREPARED | 重放或 ABORT | 否 | 否 |
| candidate 写入中断 | candidate 半写 | 摘要校验失败 → 重写 | 否 | 否 |
| candidate fsync 前 | candidate 未落盘 | 重写 | 否 | 否 |
| candidate fsync 后、替换前 | candidate 完整 | 按 revision 判定 | 否 | 否 |
| 原子替换前 | 旧文档 | 重放 | 否 | 否 |
| 原子替换后 | 新文档可见 | 核对 revision/摘要 → 补 COMMITTED | 否 | 否 |
| 目录 fsync 前 | 新文档可见但目录项未持久化 | 核对 → 补 COMMITTED 或 INDETERMINATE | 否 | 可能 |
| 目录 fsync 后、COMMITTED 前 | 新文档持久化 | 补 COMMITTED（幂等）| 否 | 否 |
| COMMITTED 写入中 | journal 半写 | 重写 COMMITTED | 否 | 否 |
| COMMITTED 写入后 | journal=COMMITTED | 幂等返回已提交 | 否 | 否 |
| 清理过程中 | 部分删除 | 按 NOTE-2 状态机重判 | 是（旁路）| 否 |
| 恢复过程中 | 未定 | 重新获取锁后重判 | 否 | 可能 |

### 3.3 验收标准

- 不发生重复消费额度。
- 不发生重复创建 Attempt。
- 不发生未提交事务被当作已提交。
- 不发生未验证候选文件被应用。
- 不确定状态一律 DENY。
- Windows/NTFS 与 POSIX 本地分别验证。
- SMB/UNC/NFS **不自动纳入支持范围**。

> 本轮**不执行**任何故障注入实验。全部为方案，状态 NOT_VERIFIED。

---

## 4. P0-BLOCK-2｜analyze_file 行为一致性验收方案（只定方案）

### 4.1 目标文件与已知事实

| 事实 | 证据 |
|------|------|
| `scanner.analyze_file` 定义于 `scanner.py:458` | 已读（458–498 行）|
| `_analysis.analyze_file` 定义于 `_analysis.py:258` | 已读（258–296 行）|
| 两实现**逐字节相同** | 本工单只读比对确认 |
| `scanner.py` **不 import `_analysis`** | 已 grep（无 `from . import _analysis` / 无 `_analysis` 引用）|
| `scanner.analyze_file is _analysis.analyze_file` | **False**（两独立对象）|

### 4.2 关键区分（不得混淆）

| 概念 | 含义 | 现状 |
|------|------|------|
| 对象身份相同 | 同一函数对象（`is`）| **False**（两个独立对象）|
| 函数实现相同 | 源码逐字节一致 | **True**（本工单比对）|
| 行为等价 | 相同输入产生相同输出 | **NOT_TESTED**（需运行验证）|
| 结果数据相同 | 输出规范化后一致 | **NOT_TESTED** |

**结论**：实现相同 ≠ 行为等价（依赖链、全局状态、monkeypatch 可能引入差异）；本工单只确认"实现相同"，**行为等价需运行验证，状态 NOT_TESTED**。

### 4.3 验证维度（未来验证必须覆盖）

1. 参数、默认值、返回类型、异常行为比较。
2. 文件读取、编码处理、语言识别、解析、错误恢复比较。
3. 返回字段、嵌套结构、排序、空值、边界比较。
4. 相同输入下输出规范化比较方法（定义 canonical 序列化）。
5. 测试样本分类：正常 / 空文件 / 损坏 / 未知扩展名 / 多语言 / 超大 / 编码异常 / 路径异常。

### 4.4 允许差异 vs 行为不一致

- **允许差异**：函数对象身份不同（`is` False）、模块内私有辅助函数命名空间不同、`__module__`/`__qualname__` 不同。
- **行为不一致**：相同输入下输出 row 的字段值、符号/导入/调用列表、诊断信息、异常抛出不一致。

### 4.5 验证时必须记录

- 输入文件哈希、函数版本（模块路径 + 行号 + 文件哈希）、输出摘要、差异证据（diff）。

### 4.6 验收

- 形成可复现的行为等价性测试方案 + PASS/FAIL 判定规则。
- 本轮状态：**NOT_TESTED**（工单禁止运行测试）。

---

## 5. P1-BLOCK-3｜A/B 工作区三文件分叉解决方案（只定方案）

### 5.1 既有结论（依据 RECONCILIATION-PLAN）

| 文件 | A SHA256 | B SHA256 | 差异 |
|------|----------|----------|------|
| `cli.py` | `65c1366a…c42fe` | `879a3735…b8d9`（冻结）| A=增量超集（understand 命令）|
| `python_worker.py` | `299358a1…90cbd` | `c23e922f…1b04` | A 改 import scanner→_analysis |
| `scanner.py` | `981de7fc…8913` | `58ba5f25…45e3`（冻结）| A 拆分出 _analysis.py |

### 5.2 逐文件解决方案

| 文件 | 方案 | 理由 | 依赖顺序 | 最小验证 | 回滚 |
|------|------|------|----------|----------|------|
| `cli.py` | 移植 A 的 understand 命令（增量）| A 是 B 超集，无破坏 | 先解决 analyze_file 等价 | understand 命令可运行 | 恢复 B 冻结版 |
| `python_worker.py` | import 切到 _analysis | A 优化方向正确 | **依赖 P0-BLOCK-2 行为等价验证** | 切后 worker 输出一致 | 恢复 scanner import |
| `scanner.py` | **保留 B 冻结版**，不合并 A 拆分 | B 是 S1-C 冻结基线，A 拆分未验证 | 最末，需单独授权 | 拆分重构等价验证 | 保持 B |

### 5.3 禁止事项

- **禁止**直接 A 覆盖 B 或 B 覆盖 A。
- **禁止**仅凭文件行数/代码量判定哪个更正确。
- **禁止**本轮实际合并。

### 5.4 合并后的冻结基线生成方法

合并完成并通过验证后，**需单独授权**才可：
1. 重新计算三文件 SHA256。
2. 更新冻结基线清单。
3. 记录新旧哈希对照 + 合并工单编号。

### 5.5 验收

- 形成逐文件合并决策表、依赖关系图、执行顺序。
- **未实际合并前，P1-BLOCK-3 保持未解决。**

---

## 6. 阶段门禁与准入条件（严格区分）

| 里程碑 | 状态 | 与其它项关系 |
|--------|------|--------------|
| DESIGN-05.6 文档修订完成 | ✅ 本轮完成 | 仅设计，非实现 |
| P0-BLOCK-1 实现 + 故障注入验证 | ❌ 未开始 | 需 IMPLEMENT 授权 |
| P0-BLOCK-2 行为等价性验证 | ❌ 未开始（NOT_TESTED）| 需运行测试授权 |
| P1-BLOCK-3 A/B 合并 + 验证 | ❌ 未开始 | 需单独合并授权 |
| F1 / P0.8 / F2 门禁解除 | ❌ 保持原状态 | 独立授权 |
| IMPLEMENT-01 正式准入 | ❌ STOP | 上述全部完成 + 独立审查 |

**任何一项不得自动等同于另一项。本轮完成后，三个阻断项保持原状态，IMPLEMENT-01 继续 STOP。**

---

## 7. 全局契约一致性复核

| # | 检查项 | 结论 |
|---|--------|------|
| 1 | 三类记录职责与状态定义 | 一致（05.6 未改）|
| 2 | 锁 token 契约与 05.5 锁拆分 | 一致（token 补全 assert_lock_owned 依据）|
| 3 | NOTE-2 清理与 05.5 journal 结构 | 一致（清理只作用于终态）|
| 4 | 事务幂等规则 | 一致（cleanup_id/transaction_id 唯一）|
| 5 | EXHAUSTED / ACTIVE 权限 | 一致（05.5 已定，未改）|
| 6 | 锁路径统一 | 一致（`authorizations/<id>/<id>.lock`）|
| 7 | 伪代码 ≠ 已验证 | 一致（全文标注）|
| 8 | 三个阻断项未擅自解决 | 一致（均保持未解决）|

---

## 8. 交付物索引

| 文件 | 内容 |
|------|------|
| `USIT-STAGE-B-DESIGN-05.6.md` | 本文 |
| `USIT-STAGE-B-DESIGN-05.6-ISSUES.md` | 问题清单 |
| `USIT-STAGE-B-DESIGN-05.6-DIFF.md` | DESIGN-05.5 → 05.6 差异 |
| `USIT-STAGE-B-DESIGN-05.6-EXECUTION-REPORT.md` | 执行报告 |
| `USIT-STAGE-B-DESIGN-05.6-HASH-MANIFEST.md` | 哈希清单 |
| `USIT-STAGE-B-DESIGN-05.6-HASH-RECEIPT.txt` | 哈希收据 |
