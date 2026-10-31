# USIT-DESIGN-03-04-DIFF｜DESIGN-03 → DESIGN-04 差异清单

> 生成：2026-10-30（GMT+8）· 用途：DESIGN-04 交付物 5

---

## 一、差异总览

DESIGN-04 是 DESIGN-03 的**第二轮独立审查**，封闭 8 项工程契约缺陷。DESIGN-03 的"方向正确 + 证据规则收紧"保持不变，DESIGN-04 补齐了**授权密码学、多文件原子发布、证据失效传播、关系基数、ID 契约、Oracle 统计、TOCTOU、授权状态机** 8 个层面。

---

## 二、逐项差异清单

| # | DESIGN-03 内容 | 审查缺陷 | DESIGN-04 修订 |
|---|---------------|---------|---------------|
| 1 | §6 `signature` 把 SHA256/HMAC 混写为签名 | 单纯 SHA256 被误当数字签名 | 分层完整性（sha256 可验证哈希 + issuer 显式 + 不发明密钥），bump AUTH_SCHEMA_VERSION |
| 2 | §6 `use_count += 1` 非原子 | 并发可绕过防重放 | 文件锁 + 临界区原子消费 |
| 3 | §8 `propagate_invalidation(changed_digest)` 按内容哈希反查 | 混淆身份与内容 | 身份优先（st_dev:st_ino）+ 五态验证 + 显式六元组 |
| 4 | §7.5 "两个 rename = 事务" | 新旧混合可见窗口 | 不可变版本目录 + 原子 CURRENT 指针 + manifest |
| 5 | §5.5 `_resolve_multi_evidence` 未区分共存/互斥/基数 | 可能误判冲突 | 基数约束注册表 + 冲突分类表 + 禁多数表决 |
| 6 | §5.1 `relation_id` 3参 vs §5.2 注释 4参不一致 | 声明与调用不一致 | 统一 3 参；node_id 加 project_scope；补版本兼容矩阵 |
| 7 | §3 Oracle 三态但无统计隔离 | 探索结果可能混入业务通过率 | 四计数器统计隔离 + Oracle 状态机 |
| 8 | §7 仅 resolve()+字符串前缀 | 无身份绑定，TOCTOU | 身份绑定(st_dev:st_ino) + open_no_follow 描述符访问 |
| 9 | §6/§8 授权状态散落（revocation_status 三态 + INVALIDATED/CONSUMED 概念）| 无统一状态机 | 统一五态状态机 + 合法转换表 + 审计日志 |

---

## 三、字段/接口变更对照

| 类别 | DESIGN-03 | DESIGN-04 |
|------|-----------|-----------|
| 授权完整性 | `signature: str`（HMAC/哈希）| `integrity_algorithm: str` + `integrity_digest: str`（可验证哈希，非签名）|
| 授权状态 | `revocation_status: ACTIVE/REVOKED/EXPIRED` | `authorization_state: ACTIVE/EXPIRED/REVOKED/INVALIDATED/CONSUMED`（五态）|
| 防重放 | `use_count: int`（非原子）| 原子消费 `_consume_authorization`（文件锁）|
| 证据哈希 | `digest`（BusinessRelation 上）| `EvidenceIntegrity`（source_id/expected_digest/actual_digest/digest_algorithm/verification_status/verified_at）|
| 失效传播 | `propagate_invalidation(changed_digest)` | `propagate_invalidation(changed_source_ids)`（身份优先）|
| 关系 ID | `relation_id(source_id, relation_type, target_id)`（注释里多 direction）| `relation_id(source_id, relation_type, target_id)`（3 参，方向由 relation_type 编码）|
| 节点 ID | `node_id(kind, identifier)` | `node_id(kind, identifier, project_scope)` |
| 关系基数 | 无 | `RELATION_CARDINALITY`（ONE_TO_ONE/ONE_TO_MANY/MANY_TO_ONE/MANY_TO_MANY）|
| 发布 | 两文件各自 rename | 不可变版本目录 + `CURRENT` 指针 + `manifest.json` |
| 统计 | 无 | `STATISTICS`（business_verified/business_total/exploratory/no_oracle）|
| 路径安全 | canonical_project + 拒绝 reparse | 增加 `canonical_project_bound`（身份绑定）+ `verify_path_identity` |

---

## 四、未改变的部分（DESIGN-03 已确定，DESIGN-04 保持）

- 证据规则收紧（逐证据重验证、删除映射表、`_evidence_merely_hints`）
- Oracle 三态语义（VERIFIED/INFERRED/NO）
- 关系模型总体结构（节点/关系类型分离、可扩展注册）
- TestCase 包装 TestSpecification
- 八类测试维度框架
- 家庭相册三字段独立建模
- plan() 纯计划、execute() 留桩
- 追溯闭环方向
- 冻结资产与门禁（F1/P0.8/F2 全保持）
- 分阶段实施顺序（IMPLEMENT-01→02→03）

---

## 五、结论

DESIGN-04 是 DESIGN-03 的**工程契约封闭修订**，不改 DESIGN-03 已确定的架构方向，只补齐 8 个契约层面的可验证规则与拒绝条件。
