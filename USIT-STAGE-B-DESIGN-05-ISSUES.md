# USIT-STAGE-B-DESIGN-05-ISSUES｜问题清单

> 生成：2026-10-30（GMT+8）· 上游：DESIGN-04 六项遗留缺陷
> 分级：P0=阻断 / P1=重要 / P2=轻微

---

## 一、本工单封闭的六项问题（D05-01 ~ D05-06）

| 编号 | 位置 | DESIGN-04 缺陷 | DESIGN-05 封闭 | 结果 |
|------|------|---------------|---------------|------|
| D05-01 | §5 关系基数 | `RELATION_CARDINALITY` 用 `(source_kind, CARDINALITY)` 元组，无法表达方向性基数，与冲突判定表矛盾 | 五要素 `RelationCardinalitySpec`（source/target cardinality + direction + mutual_exclusion_group + conflict_policy）+ 消除矛盾的冲突判定表 | 已封闭 |
| D05-02 | §2 授权完整性 | 未区分摘要与签名，未定义规范化版本，未明确"未签名 DENY" | `canonical-json-v1` + `signature_algorithm`/`signature` 字段 + 默认 DENY | 已封闭 |
| D05-03 | §2.5 原子消费 | 只写"加锁 use_count+1"，缺 nonce 作用域/损坏/锁失效/崩溃/消费时机/跨平台锁 | 完整消费状态机（NOT_CONSUMED→RESERVED→CONSUMED）+ 故障矩阵 + 执行前预留 | 已封闭 |
| D05-04 | §8 文件身份 | 把 `(st_dev,st_ino)` 当通用永久身份 | 四层身份模型（path/object/content/project）+ 平台矩阵 + 安全读取状态机 + 降级规则 | 已封闭 |
| D05-05 | §4 版本发布 | `read_current()` 校验可选，manifest 无自校验，CURRENT 损坏无处理 | 强制校验 + manifest_digest 自校验 + CURRENT 损坏报错不猜测 + 清理策略 | 已封闭 |
| D05-06 | 执行报告 | 交付物哈希缺失 | 独立 HASH-MANIFEST + 算法/时间/字节数记录 + 冻结/新增哈希区分 | 已封闭 |

---

## 二、遗留问题（非阻断，转入 IMPLEMENT 阶段）

| 编号 | 问题 | 风险 | 处理 |
|------|------|------|------|
| NV-1 | 密钥基础设施未引入，签名默认 DENY | 低（签名未启用，不影响 IMPLEMENT-01 其它部分）| IMPLEMENT 阶段不实现签名，未来独立工单 |
| NV-2 | `scanner.analyze_file` vs `_analysis.analyze_file` 行为一致性 | 中 | IMPLEMENT-01 前单独验证 |
| NV-3 | 两工作区 3 分叉文件未合并 | 中 | 合并工单 + 单独授权 |
| P2-G1 | BusinessRelation.digest 与 EvidenceIntegrity 字段层级 | 低 | IMPLEMENT 统一到证据层 |
| P2-G2 | Authorization 引用链（authorization_id vs case_id）| 低 | IMPLEMENT 明确 |
| P2-G3 | STALE/NEEDS_REVALIDATION 双名 | 低 | IMPLEMENT 统一为 CURRENT/STALE |

---

## 三、用户重点复核的三项安全门槛

| 门槛 | 状态 | 关键证据 |
|------|------|---------|
| 授权防重放闭环 | ✅ 闭环 | D05-03 状态机（NOT_CONSUMED→RESERVED→CONSUMED→CORRUPTED）+ 故障矩阵 + nonce 唯一性 + 执行前预留 |
| 文件身份跨平台可靠 | ✅ 可靠 | D05-04 四层身份 + 平台矩阵（NTFS FileIndex / POSIX inode / UNC 降级）+ inode 重用防御 |
| 发布读取强制校验 | ✅ 强制 | D05-05 manifest 自校验 + read_current 强制 + CURRENT 损坏报错 + 不自动选最新 |

---

## 四、NOT_VERIFIED（受只读限制，无法验证）

| 编号 | 事项 | 原因 |
|------|------|------|
| NV-4 | D05-01~06 伪代码真实可运行性 | 设计阶段，未实现 |
| NV-5 | `analyze_file` 行为一致性 | 禁止运行测试 |
| NV-6 | 3 分叉文件功能差异 | 只读，未运行验证 |
