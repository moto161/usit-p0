# USIT-STAGE-B-DESIGN-05.1-DIFF｜DESIGN-05 → DESIGN-05.1 差异清单

> 生成：2026-10-30（GMT+8）· 用途：DESIGN-05.1 交付物

---

## 一、差异总览

DESIGN-05.1 是 DESIGN-05 的**定向修订**，封闭独立审查发现的 5 项遗留问题。不改 DESIGN-05 已确定的架构方向，只收口授权消费状态机、文件身份分级、发布威胁模型、跨平台发布、哈希清单 5 个层面。

---

## 二、逐项差异清单

| # | DESIGN-05 内容 | 缺陷 | DESIGN-05.1 修订 |
|---|---------------|------|-----------------|
| 1 | 消费状态机四态（NOT_CONSUMED/RESERVED/CONSUMED/CORRUPTED），崩溃自动回可重试 | RESERVED 崩溃漏洞、无 INDETERMINATE、三概念混淆 | 八态（ISSUED/RESERVED/CONSUMED/REJECTED/EXPIRED/REVOKED/INVALIDATED/INDETERMINATE）+ 三概念独立 + at-most-once |
| 2 | 文件身份四层，但"内容哈希匹配≈身份可信" | 内容验证与对象身份混淆 | 四维身份独立 + 七态验证 + CONTENT_MATCH≠身份 + 平台降级矩阵 |
| 3 | 发布只讲 SHA256 校验 | 威胁模型不完整、SHA256 当防篡改 | 双模式（本地完整性/可信发布）+ 能力边界 + 回滚检测 |
| 4 | Windows/POSIX 故障处理不完整 | 缺 10 条硬规则 | 10 条硬规则 + 崩溃矩阵 + read_current 强制 7 步 |
| 5 | 哈希清单循环引用 + 空值 | 循环依赖 | 单向哈希清单 + 外部审计记录 MANIFEST 自身哈希 |

---

## 三、字段/状态变更对照

| 类别 | DESIGN-05 | DESIGN-05.1 |
|------|-----------|-------------|
| 授权状态 | 四态 + `authorization_state` | 八态，新增 `INDETERMINATE`/`REJECTED`，`attempt_id` 唯一 |
| 消费概念 | `use_count` 单概念 | 三概念：`authorization_use_count`/`execution_attempt_count`/`execution_result` |
| 文件验证 | 四层身份（含"内容哈希匹配=身份"）| 四维身份 + 七态验证（CONTENT_MATCH 独立于身份）|
| 发布模式 | 单模式 SHA256 | 双模式：本地完整性 / 可信发布 |
| 哈希清单 | 循环引用 | 单向清单，MANIFEST 自身哈希由外部审计保存 |

---

## 四、未改变的部分（DESIGN-05 已定，05.1 保持）

- 五要素关系基数矩阵
- canonical-json-v1 + 摘要/签名分离
- 四层文件身份框架（细化但未推翻）
- 不可变版本目录 + CURRENT 指针思路
- Oracle 三态 + 统计隔离
- 授权五态 + 消费态（补充 INDETERMINATE）
- plan() 纯计划、execute() 留桩
- 冻结资产与门禁

---

## 五、结论

DESIGN-05.1 是 DESIGN-05 的**安全状态机与交付完整性最终修订**，封闭 5 项遗留问题，重点强化授权防重放闭环（INDETERMINATE 门禁）、文件身份分级（CONTENT_MATCH≠身份）、发布威胁模型（双模式）。
