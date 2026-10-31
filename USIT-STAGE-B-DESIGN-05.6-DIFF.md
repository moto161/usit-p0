# USIT-STAGE-B-DESIGN-05.6-DIFF｜DESIGN-05.5 → DESIGN-05.6 差异清单

> 生成：2026-10-31（GMT+8）· 用途：DESIGN-05.6 交付物

---

## 一、差异总览

DESIGN-05.6 在 05.5 基础上补齐 NOTE-1（锁 token）、NOTE-2（事务清理），并为三个阻断项制定验收方案。**无架构性变更**。

---

## 二、逐项差异清单

| # | DESIGN-05.5 内容 | 缺陷 | DESIGN-05.6 修订 |
|---|-----------------|------|-----------------|
| 1 | `_assert_lock_held(auth_id)` 仅一句，无 token 定义 | 锁所有权无法真实校验 | NOTE-1：9 字段 LockToken + 所有权校验规则 + 函数契约 + 跨平台边界 |
| 2 | journal/pending 无清理策略 | 恢复文件无限累积或误删风险 | NOTE-2：7 类文件 + 清理准入 + 幂等审计 + 清理状态机 |
| 3 | 三个阻断项仅列名，无验收方案 | 无进入 IMPLEMENT 的可执行门槛 | 三阻断项各制定方案 + 验收标准 + 故障点矩阵 |
| 4 | analyze_file 双重定义仅标注 NOT_VERIFIED | 未区分"实现相同/行为等价/对象身份" | P0-BLOCK-2 四概念严格区分 + 验证维度 |

---

## 三、关键事实确认（本轮新增证据）

| 事实 | 证据 |
|------|------|
| `scanner.py` 不 import `_analysis` | grep 无 `_analysis` 引用 |
| 两个 `analyze_file` 实现逐字节相同 | 只读比对 scanner.py:458–498 vs _analysis.py:258–296 |
| `scanner.analyze_file is _analysis.analyze_file` = False | 两独立对象（RECONCILIATION-PLAN 已记）|

---

## 四、未改变的部分（DESIGN-05.5 已定，05.6 保持）

- 交接/预留权限分离（ACTIVE/EXHAUSTED）
- atomic_commit / atomic_commit_locked 锁拆分
- 事务方案 B（完整候选 + journal 幂等）
- 锁路径统一 `authorizations/<id>/<id>.lock`
- 三类记录模型、三类状态机、计数派生视图
- at-most-once、FAILED vs UNKNOWN、EXHAUSTED 语义
- 单向哈希清单 + HASH-RECEIPT
- 冻结资产与门禁（F1/P0.8/F2）
- 三个阻断项均保持未解决

---

## 五、结论

DESIGN-05.6 是 05.5 的**补全 + 验收方案**：补齐锁所有权契约与事务清理策略，并为三个阻断项建立可执行、可审计的验收方案。三个阻断项**均未解决**，IMPLEMENT-01 继续 STOP。
