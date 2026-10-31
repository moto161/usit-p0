# USIT-STAGE-B-DESIGN-05.2-DIFF｜DESIGN-05.1 → DESIGN-05.2 差异清单

> 生成：2026-10-30（GMT+8）· 用途：DESIGN-05.2 交付物

---

## 一、差异总览

DESIGN-05.2 是 DESIGN-05.1 的**定向封闭**，修复三个问题：授权消费计数语义矛盾、版本发布临时目录所有权与并发互斥、哈希外部校验收据缺失。

---

## 二、逐项差异清单

| # | DESIGN-05.1 内容 | 缺陷 | DESIGN-05.2 修订 |
|---|-----------------|------|-----------------|
| 1 | use_count 单字段，RESERVED 已递增又描述为成功后次数 | 计数语义矛盾 | 拆分 slots_committed / consumed_count / attempt_count 三字段 + 15 字段完整状态记录 |
| 2 | 状态机八态（无 EXECUTION_STARTED）| 缺执行交接态 | 九态，新增 EXECUTION_STARTED |
| 3 | 固定 `tmp = vdir + ".tmp"` + `rmtree(tmp)` | 可删他人文件、无并发互斥 | 唯一临时目录（排他 + 所有权）+ 发布锁 + QUARANTINED |
| 4 | 无 CURRENT 并发切换规则 | 缺互斥保护 | 发布锁保护 + 临时指针原子替换 + 版本序号单调防回退 |
| 5 | 哈希清单无外部校验收据 | 缺 MANIFEST 摘要收据 | HASH-RECEIPT.txt + 外部锚定 NOT_VERIFIED |

---

## 三、字段/状态变更对照

| 类别 | DESIGN-05.1 | DESIGN-05.2 |
|------|-------------|-------------|
| 授权计数 | `use_count`（单字段）| `authorization_slots_committed` + `authorization_consumed_count` + `execution_attempt_count` |
| 授权状态 | 八态 | 九态（新增 EXECUTION_STARTED）|
| 状态记录 | 伪代码维护 `data[auth_id]` 整数 | 15 字段完整 `AuthorizationStateRecord` |
| 临时目录 | 固定 `.tmp` 名 | 唯一随机名 + `.owner` 所有权记录 |
| 发布互斥 | 无 | 跨进程发布锁（flock/locking）|
| 哈希收据 | 无 | HASH-RECEIPT.txt（记录 MANIFEST 自身摘要）|

---

## 四、未改变的部分（DESIGN-05.1 已定，05.2 保持）

- 四维文件身份 + CONTENT_MATCH≠身份
- 双模式发布威胁模型
- 不可变版本目录 + CURRENT 指针思路
- 单向哈希清单设计（05.2 补 RECEIPT）
- Oracle 三态 + 统计隔离
- plan() 纯计划、execute() 留桩
- 冻结资产与门禁

---

## 五、结论

DESIGN-05.2 是 DESIGN-05.1 的**授权消费事务与并发发布安全封闭**，修复计数语义矛盾、发布并发互斥、哈希校验收据三个问题，为 IMPLEMENT-01 建立更严密的契约。
