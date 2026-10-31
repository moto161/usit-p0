# USIT-STAGE-B-DESIGN-04-ISSUES｜问题清单

> 生成：2026-10-30（GMT+8）· 上游：DESIGN-01/02/03 审查
> 分级：P0=阻断实现 / P1=重要但可延后 / P2=轻微

---

## 一、P0（阻断，本工单已封闭）

| 编号 | 位置 | 问题 | 封闭方案 | 验收标准 |
|------|------|------|---------|---------|
| D04-01-P0 | DESIGN-03 §6 | `signature` 字段把 SHA256/HMAC 混写为"签名"，无算法/密钥/公钥分发；单纯 SHA256 被误当数字签名 | 分层完整性：sha256 可验证哈希 + 授权文件外部不可写 + issuer 显式记录；不发明密钥；未来升级需 bump AUTH_SCHEMA_VERSION | 完整性校验可验证；篡改→拒绝；如实声明"不防恶意篡改" |
| D04-01-P0-重放 | DESIGN-03 §6 | `use_count += 1` 非原子，并发可绕过 max_use_count=1 | 文件锁 + 临界区原子消费（_consume_authorization）| 并发消费只成功一次；崩溃后状态一致 |
| D04-03-P0 | DESIGN-03 §7.5 | "两个 rename = 事务"错误，存在新旧混合可见窗口 | 不可变版本目录 + 原子 CURRENT 指针 + manifest | 读者永不读到混合版本；崩溃可恢复 |

---

## 二、P1（重要，本工单已封闭）

| 编号 | 位置 | 问题 | 封闭方案 | 验收标准 |
|------|------|------|---------|---------|
| D04-02-P1 | DESIGN-03 §8 | `propagate_invalidation(changed_digest)` 用内容哈希反查，混淆身份与内容 | 身份优先（st_dev:st_ino）+ 五态验证 + 显式六元组 | 失效按身份定位；删除/移动/重命名正确传播 |
| D04-04-P1 | DESIGN-03 §5.5 | `_resolve_multi_evidence` 未区分共存/互斥/基数，可能误判冲突 | 基数约束注册表 + 冲突分类表 + 禁多数表决 | feature→多 entity 不误判冲突；真矛盾保留双方 |
| D04-05-P1 | DESIGN-03 §5.1/5.2 | `relation_id` 声明 3 参、注释 4 参不一致；node_id 缺项目作用域 | 统一 3 参（方向由 relation_type 编码）；node_id 加 project_scope；补版本兼容矩阵 | ID 确定、唯一、稳定；跨文档签名一致 |
| D04-06-P1 | DESIGN-03 §3 | INFERRED_ORACLE 探索结果可能混入业务通过率 | 四计数器统计隔离 + Oracle 状态机 + 禁非法升级 | 探索结果不计业务/回归通过率 |
| D04-07-P1 | DESIGN-03 §7 | 仅 resolve()+字符串前缀，无授权时刻↔访问时刻身份绑定 | 身份绑定(st_dev:st_ino) + open_no_follow 描述符访问 | 路径替换/重定向→拒绝；TOCTOU→拒绝 |
| D04-08-P1 | DESIGN-03 §6/§8 | 授权状态散落（revocation_status 三态 + INVALIDATED/CONSUMED 概念）| 统一五态状态机 + 合法转换表 + 审计日志 | 非法转换拒绝；INVALIDATED/REVOKED/CONSUMED 终态 |

---

## 三、P2（轻微，非阻断，建议 IMPLEMENT 阶段消除）

| 编号 | 位置 | 问题 | 建议 |
|------|------|------|------|
| ISSUE-G1 | DESIGN-02 §2.2 vs DESIGN-03 §5 | `BusinessRelation.digest`（DESIGN-02）与 `EvidenceIntegrity.expected_digest`（DESIGN-04）字段命名层级不一致 | IMPLEMENT 时统一为证据层 `EvidenceIntegrity` 承载哈希，BusinessRelation 不冗余存 digest |
| ISSUE-G2 | DESIGN-02 §7.3 | `ExecutionAuthorization` 在 DESIGN-02 无 `authorization_id`（DESIGN-03 补），引用链 ExecutorPlan→Authorization 通过 case_id 而非 authorization_id | IMPLEMENT 时明确 Authorization 有独立 authorization_id，ExecutorPlan 通过 case_id 关联，二者不可混 |
| ISSUE-G3 | DESIGN-02 §8.2 | `RELATION_VALIDITY` 有 `STALE` 和 `NEEDS_REVALIDATION` 两个名字，语义未区分 | 统一为 `CURRENT`/`STALE` 两态；`NEEDS_REVALIDATION` 是动作提示而非状态 |

---

## 四、NOT_VERIFIED（受只读限制，无法验证，如实标记）

| 编号 | 事项 | 原因 | 处理 |
|------|------|------|------|
| NV-1 | `scanner.analyze_file` vs `_analysis.analyze_file` 行为一致性 | 工单禁止运行测试 | 留待 IMPLEMENT 阶段验证（见 RECONCILIATION-PLAN）|
| NV-2 | 两工作区 3 分叉文件的功能差异 | 只读，未运行任何命令验证 | 留待合并工单 |
| NV-3 | DESIGN-04 各伪代码（原子发布/消费/失效传播）真实可运行性 | 设计阶段，未实现 | 留待 IMPLEMENT-01/02/03 |

---

## 五、已封闭问题的证据索引

| 编号 | DESIGN-04 章节 |
|------|---------------|
| D04-01 密码学 | §2 |
| D04-02 证据哈希 | §3 |
| D04-03 原子发布 | §4 |
| D04-04 关系基数 | §5 |
| D04-05 ID/注册表 | §6 |
| D04-06 Oracle 统计 | §7 |
| D04-07 TOCTOU | §8 |
| D04-08 授权状态 | §9 |
