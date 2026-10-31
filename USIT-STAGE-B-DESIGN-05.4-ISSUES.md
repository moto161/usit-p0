# USIT-STAGE-B-DESIGN-05.4-ISSUES｜问题清单

> 生成：2026-10-30（GMT+8）· 用途：DESIGN-05.4 交付物

---

## 一、本轮封闭的问题

| 编号 | 问题 | 严重级 | 状态 |
|------|------|--------|------|
| D054-01 | 存储布局与跨授权并发写入安全 | P0 | 已封闭（方案已设计，持久化保证 NOT_VERIFIED）|
| D054-02 | 执行交接前授权二次验证 | P0 | 已封闭（流程完整，线性化点明确）|
| D054-03 | execution_attempt_count 统一定义 | P1 | 已封闭（派生视图口径）|
| D054-04 | EXHAUSTED 状态语义修正 | P1 | 已封闭（额度维度分离）|

---

## 二、遗留问题（P0/P1/P2）

| 编号 | 问题 | 严重级 | 说明 |
|------|------|--------|------|
| P0-BLOCK-1 | 存储事务能力未实现 | P0 | 单一 JSON 原子替换 + 锁 + fsync + journal 仅设计级契约，未真实实现，未做崩溃注入验证 |
| P0-BLOCK-2 | analyze_file 双重定义 | P0 | scanner.py 与 _analysis.py 行为一致性 NOT_VERIFIED |
| P1-BLOCK-3 | A/B 三文件分叉 | P1 | cli.py / python_worker.py / scanner.py 未合并 |
| P2-NOTE-1 | 目录 fsync 跨平台语义差异 | P2 | Windows 与 POSIX 目录持久化语义不同，需实现时单独验证 |
| P2-NOTE-2 | 网络文件系统锁/rename 不可靠 | P2 | SMB/UNC/NFS 上锁与原子替换不保证，需 DENY 或降级 |

---

## 三、阻断项汇总（IMPLEMENT-01 前必须解决）

1. P0-BLOCK-1：存储事务能力（D054-01 直接下游）。
2. P0-BLOCK-2：analyze_file 行为一致性。
3. P1-BLOCK-3：A/B 三文件分叉。

三项未闭合前，IMPLEMENT-01 维持阻断。
