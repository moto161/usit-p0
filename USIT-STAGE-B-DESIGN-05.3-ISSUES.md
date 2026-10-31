# USIT-STAGE-B-DESIGN-05.3-ISSUES｜问题清单

> 生成：2026-10-30（GMT+8）· 上游：DESIGN-05.2 结构性矛盾

---

## 一、本工单封闭的问题

| 编号 | 位置 | DESIGN-05.2 缺陷 | DESIGN-05.3 封闭 | 结果 |
|------|------|-----------------|-----------------|------|
| D053-01 | 主记录 | 单一 reservation_id/attempt_id + CONSUMED 终态，与 max_use_count 多次使用矛盾 | 分层记录模型（Authorization/Reservation/Attempt 三类）+ 三状态机分离 | 已封闭 |
| D053-02 | 计数 | consumed_count 歧义（易被当成功次数）| execution_completed_count 等四计数 + 不变量 | 已封闭 |
| D053-03 | 崩溃恢复 | 无完整事务提交方案 | 存储事务能力列为 IMPLEMENT-01 前置阻断项 | 已封闭 |
| D053-04 | 执行结果 | FAILED 与 UNKNOWN 混 | 三态严格区分 + result_evidence 关联 | 已封闭 |
| D053-05 | 锁边界 | 未细分平台 | Windows/POSIX 锁边界 + 不支持平台 DENY | 已封闭 |

---

## 二、前置阻断项（IMPLEMENT-01 前必须解决）

| 编号 | 问题 | 风险 | 处理 |
|------|------|------|------|
| BLOCK-1 | **存储事务能力**：当前多文件架构无法可靠多记录原子提交 | **高（P0）** | IMPLEMENT-01 前选定单一 JSON 原子替换 / SQLite / WAL 之一 |
| BLOCK-2 | `analyze_file` 行为一致性 | 中 | IMPLEMENT-01 前单独验证 |
| BLOCK-3 | A/B 三文件分叉未合并 | 中 | 合并工单 + 单独授权 |

---

## 三、遗留问题（非阻断）

| 编号 | 问题 | 风险 | 处理 |
|------|------|------|------|
| NV-1 | 密钥基础设施未引入 | 低 | 未来独立工单 |
| NV-2 | 外部审计锚定 NOT_VERIFIED | 低 | 引入受保护存储后验证 |
| NV-3 | at-most-once（非 exactly-once）| 低（已声明）| 如实记录边界 |
| NV-4 | Windows/POSIX 锁未经实测 | 低 | IMPLEMENT 阶段实测 |

---

## 四、NOT_VERIFIED（受只读限制）

| 编号 | 事项 | 原因 |
|------|------|------|
| NV-5 | 分层记录模型伪代码可运行性 | 设计阶段未实现 |
| NV-6 | 存储事务方案（SQLite/单文档/WAL）实际性能与可靠性 | 未实现未测试 |
| NV-7 | Windows/POSIX 锁语义实测 | 禁止平台实验 |
