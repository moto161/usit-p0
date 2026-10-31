# USIT-STAGE-B-DESIGN-05.5-DIFF｜DESIGN-05.4 → DESIGN-05.5 差异清单

> 生成：2026-10-31（GMT+8）· 用途：DESIGN-05.5 交付物

---

## 一、差异总览

DESIGN-05.5 针对 05.4 四项缺陷定向修订：EXHAUSTED 交接冲突、重复取锁死锁、PREPARED 缺恢复载荷、锁路径不统一。

---

## 二、逐项差异清单

| # | DESIGN-05.4 内容 | 缺陷 | DESIGN-05.5 修订 |
|---|-----------------|------|-----------------|
| 1 | handoff 步骤 3 要求 authorization_status == ACTIVE | max_use_count=1 时预留即 EXHAUSTED，合法预留无法交接 | 交接允许状态集合 {ACTIVE, EXHAUSTED}；预留仅 ACTIVE |
| 2 | handoff 步骤 13 调 `_atomic_commit()`（内部再取锁）| 同流二次取非重入锁 → 死锁 | 拆 `atomic_commit()`（外层取锁）+ `atomic_commit_locked()`（内层不取锁）|
| 3 | 事务日志仅 operation_payload_digest | 崩溃恢复无可执行依据 | 方案 B：持久化完整候选 + pending_path/pending_sha256 + journal 幂等 |
| 4 | 目录结构写 `authorizations/<id>/<id>.lock`，伪代码用 `locks/{auth_id}.lock` | 两套路径混用 | 统一 `authorizations/<id>/<id>.lock`，删除 locks/ 写法 |

---

## 三、字段/结构变更对照

| 类别 | DESIGN-05.4 | DESIGN-05.5 |
|------|-------------|-------------|
| 交接状态门槛 | 仅 ACTIVE | ACTIVE 或 EXHAUSTED（HANDOFF_ALLOWED_STATUSES）|
| 新增预留门槛 | 未显式区分 | 仅 ACTIVE（RESERVE_ALLOWED_STATUSES）|
| 锁函数层次 | atomic_commit 单层 | atomic_commit（取锁）+ atomic_commit_locked（不取锁）|
| 事务恢复材料 | 仅 digest | journal 9 字段 + pending 完整候选 + pending_sha256 |
| 锁路径 | locks/ 与 authorizations/<id>/ 混用 | 唯一 `authorizations/<id>/<id>.lock` |

---

## 四、未改变的部分（DESIGN-05.4 已定，05.5 保持）

- 方案 A 目录 + 每授权单一 JSON 文档存储布局
- 三类记录模型与三类状态机
- `(authorization_id, slot_index)` 唯一键、slot 不退还
- at-most-once（每 slot 最多一次，不自动重放）
- FAILED vs UNKNOWN 严格区分
- EXHAUSTED = 额度占满（与执行完成无关）
- 计数派生视图（attempt/completed/success/failure/indeterminate）
- 单向哈希清单 + HASH-RECEIPT
- 双模式发布威胁模型、四维文件身份、Oracle 三态
- plan() 纯计划、execute() 留桩
- 冻结资产与门禁（F1/P0.8/F2）
- 三个阻断项（存储事务 / analyze_file / A/B 分叉）

---

## 五、结论

DESIGN-05.5 是 05.4 的**定向修订**，闭合执行交接状态冲突（P0-1）、锁重入死锁（P0-2）、事务可恢复载荷与幂等（P1-1）、锁路径统一（P1-2）。存储事务、锁机制、事务恢复仍为设计级契约，维持 IMPLEMENT-01 阻断。
