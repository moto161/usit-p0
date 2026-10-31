# USIT-STAGE-B-DESIGN-05.5-ISSUES｜问题清单

> 生成：2026-10-31（GMT+8）· 用途：DESIGN-05.5 交付物

---

## 一、本轮修订的问题

| 编号 | 问题 | 严重级 | 状态 |
|------|------|--------|------|
| P0-1 | EXHAUSTED 状态与已有 RESERVED 执行冲突 | P0 | 已修订（交接/预留权限分离）|
| P0-2 | handoff_execution 与 atomic_commit 重复获取锁 | P0 | 已修订（外层取锁 / 内层 _locked）|
| P1-1 | PREPARED 事务缺可恢复载荷及幂等规则 | P1 | 已修订（方案 B 完整候选 + journal 幂等）|
| P1-2 | 授权锁文件路径不统一 | P1 | 已修订（统一 `authorizations/<id>/<id>.lock`）|

---

## 二、遗留问题（05.4 未解决，不得静默删除）

| 编号 | 问题 | 严重级 | 说明 |
|------|------|--------|------|
| P0-BLOCK-1 | 存储事务能力未实现 | P0 | 单文档原子替换 + 锁 + fsync + journal 仅设计级契约，未真实实现，未崩溃注入验证 |
| P0-BLOCK-2 | analyze_file 双重定义 | P0 | scanner.py vs _analysis.py，行为一致性 NOT_VERIFIED |
| P1-BLOCK-3 | A/B 三文件分叉 | P1 | cli.py / python_worker.py / scanner.py 未合并 |

---

## 三、新发现 / 待办（本轮记录，不擅自扩展）

| 编号 | 项 | 说明 |
|------|----|------|
| NOTE-1 | 锁 token 校验机制 | P0-2 引入 `_assert_lock_held()`，需在 IMPLEMENT-01 定义进程内 token 实现 |
| NOTE-2 | journal 目录清理策略 | journal/pending 目录的清理规则尚未定义（终态后可清理，需后续工单）|

---

## 四、未验证项汇总

- 存储事务、锁机制、事务恢复 + 幂等：均 **NOT_IMPLEMENTED / NOT_TESTED / NOT_VERIFIED**。
- 本阶段为设计修订，非代码实现。
