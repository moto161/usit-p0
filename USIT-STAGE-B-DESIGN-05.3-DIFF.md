# USIT-STAGE-B-DESIGN-05.3-DIFF｜DESIGN-05.2 → DESIGN-05.3 差异清单

> 生成：2026-10-30（GMT+8）· 用途：DESIGN-05.3 交付物

---

## 一、差异总览

DESIGN-05.3 解决 DESIGN-05.2 的结构性矛盾：主记录单一 attempt + 终态 与 max_use_count 多次使用无法兼容。核心是**分层记录模型**，把授权主记录、额度预留、执行尝试拆成三类独立记录。

---

## 二、逐项差异清单

| # | DESIGN-05.2 内容 | 缺陷 | DESIGN-05.3 修订 |
|---|-----------------|------|-----------------|
| 1 | 单一 `AuthorizationStateRecord` 塞入 reservation_id/attempt_id + 终态 | 单一 attempt + 终态 与多次额度矛盾 | 三类记录：AuthorizationRecord / ReservationRecord / AttemptRecord |
| 2 | `consumed_count` 单字段 | 易误解为成功次数 | 四计数：completed/success/failure/indeterminate + 不变量 |
| 3 | 无完整事务提交方案 | 多文件连续写入非原子 | 存储事务列为 IMPLEMENT-01 前置阻断项 |
| 4 | 单一 state 字段混合授权/执行生命周期 | 语义混乱 | 三类状态机分离（授权/预留/执行）|
| 5 | FAILED 与 UNKNOWN 未严格区分 | 语义模糊 | 三态严格区分 + result_evidence 关联 |

---

## 三、字段/记录变更对照

| 类别 | DESIGN-05.2 | DESIGN-05.3 |
|------|-------------|-------------|
| 记录模型 | 单一 `AuthorizationStateRecord`（15 字段）| 三类记录（AuthorizationRecord / ReservationRecord / AttemptRecord）|
| 额度计数 | `authorization_slots_committed` | 保留 + 新增 `execution_completed_count`/`success_count`/`failure_count`/`indeterminate_count` |
| 消费计数 | `authorization_consumed_count` | **废除**，改为 `execution_completed_count` |
| 状态字段 | 单一 `state`（九态）| 三类 `authorization_status` / `reservation_status` / `attempt_status` |
| 事务 | 无明确方案 | 列为前置阻断项，候选：单文档原子替换/SQLite/WAL |

---

## 四、未改变的部分（DESIGN-05.2 已定，05.3 保持）

- 唯一临时目录 + `.owner` 所有权
- 跨进程发布锁
- 单向哈希清单 + HASH-RECEIPT
- 双模式发布威胁模型
- 四维文件身份
- Oracle 三态 + 统计隔离
- plan() 纯计划、execute() 留桩
- 冻结资产与门禁

---

## 五、结论

DESIGN-05.3 是 DESIGN-05.2 的**多次授权记录模型修订**，用分层记录模型解决"单一 attempt + 终态 vs 多次额度"的结构性矛盾，并把存储事务能力列为 IMPLEMENT-01 前置阻断项。
