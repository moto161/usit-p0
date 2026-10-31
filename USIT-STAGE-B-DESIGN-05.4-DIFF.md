# USIT-STAGE-B-DESIGN-05.4-DIFF｜DESIGN-05.3 → DESIGN-05.4 差异清单

> 生成：2026-10-30（GMT+8）· 用途：DESIGN-05.4 交付物

---

## 一、差异总览

DESIGN-05.4 在 05.3 分层记录模型基础上，封闭四个遗留问题：存储布局/跨授权并发、执行交接前二次验证、attempt_count 口径、EXHAUSTED 语义。

---

## 二、逐项差异清单

| # | DESIGN-05.3 内容 | 缺陷 | DESIGN-05.4 修订 |
|---|-----------------|------|-----------------|
| 1 | 存储布局未定（仅列"单文档/SQLite/WAL"三候选）| 未落到具体目录结构 | 方案 A 目录 + 每授权单 JSON 文档 + 授权级锁 |
| 2 | 多文件原子提交仅列为阻断项，无事务日志 | 无恢复协议 | 事务日志头（PREPARED/COMMITTED/ABORTED/INDETERMINATE）+ 崩溃矩阵 |
| 3 | 无执行交接前二次验证流程 | 撤销可能在交接后才发现 | 交接前 14 步全量二次验证（指纹/摘要/状态/有效期）|
| 4 | 撤销与交接线性化点未明确 | 竞争顺序不确定 | EXECUTION_STARTED 提交为线性化点，同一授权级锁串行 |
| 5 | 主记录 dataclass 持久化 4 个计数字段 | 计数 vs 集合大小易不一致 | 改为派生视图，仅持久化 slots_committed |
| 6 | EXHAUSTED 未与执行完成分离 | 语义模糊 | 明确"额度全部分配即 EXHAUSTED"，与执行完成无关 |

---

## 三、字段/结构变更对照

| 类别 | DESIGN-05.3 | DESIGN-05.4 |
|------|-------------|-------------|
| 存储 | 未定（三候选）| 方案 A：`authorizations/<id>/authorization.json` + `<id>.lock` |
| 事务 | 无日志结构 | 事务日志头 8 字段 + 4 态 |
| attempt_count | 持久化字段 `execution_attempt_count` | 派生视图 `len(attempts[])` |
| completed/success/failure/indeterminate | 持久化字段 | 派生视图（4 计数）|
| EXHAUSTED | 未明确是否含执行完成 | 额度全部分配即 EXHAUSTED，与执行无关 |
| 交接前验证 | 未单列 | 14 步全量二次验证 |

---

## 四、未改变的部分（DESIGN-05.3 已定，05.4 保持）

- 三类记录模型（AuthorizationRecord / ReservationRecord / AttemptRecord）
- 三类状态机分离
- `(authorization_id, slot_index)` 唯一键、slot 不退还
- at-most-once（每 slot 最多一次，不自动重放）
- FAILED vs UNKNOWN 严格区分
- 单向哈希清单 + HASH-RECEIPT
- 双模式发布威胁模型
- 四维文件身份
- Oracle 三态 + 统计隔离
- plan() 纯计划、execute() 留桩
- 冻结资产与门禁（F1/P0.8/F2）

---

## 五、结论

DESIGN-05.4 是 05.3 的**存储事务 + 撤销交接安全封闭**，把存储事务从"候选清单"落实到"目录 + 单文档 + 锁 + 事务日志 + 崩溃矩阵"，新增执行交接前二次验证与撤销线性化点，并把计数改为派生视图、EXHAUSTED 与执行完成解耦。持久化保证与线性化保证在真实实现前均标记 NOT_VERIFIED，维持 IMPLEMENT-01 阻断。
