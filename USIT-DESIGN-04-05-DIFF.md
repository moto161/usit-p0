# USIT-DESIGN-04-05-DIFF｜DESIGN-04 → DESIGN-05 差异清单

> 生成：2026-10-30（GMT+8）· 用途：DESIGN-05 交付物 5

---

## 一、差异总览

DESIGN-05 是 DESIGN-04 的**最后一轮缺陷封闭**，针对 6 项工程契约遗留问题。DESIGN-04 已确定的架构方向（节点/关系分离、TestCase 包装、Oracle 隔离、授权状态机框架、不可变版本目录思路）保持不变，DESIGN-05 补齐了**关系基数方向性、摘要/签名分离、消费状态机、四层文件身份、强制读取校验、交付物哈希** 6 个层面。

---

## 二、逐项差异清单

| # | DESIGN-04 内容 | 缺陷 | DESIGN-05 修订 |
|---|---------------|------|---------------|
| 1 | `RELATION_CARDINALITY` 用 `(source_kind, "CARDINALITY")` 元组 | 无法表达方向性，与冲突表矛盾 | 五要素 `RelationCardinalitySpec`（source/target cardinality + direction + mutual_exclusion_group + conflict_policy）+ 消除矛盾的冲突判定表 |
| 2 | `_auth_canonical_payload` 只算 SHA256，未区分摘要/签名 | 未定义规范化版本、未定义未签名 DENY | `canonical-json-v1` + `signature_algorithm`/`signature` 字段 + 默认 DENY |
| 3 | `_consume_authorization` 只"加锁 use_count+1" | 缺状态机/故障/时机/跨平台锁 | 完整消费状态机 + 故障矩阵 + 执行前预留 + 跨平台锁抽象 |
| 4 | 把 `(st_dev, st_ino)` 当通用身份 | inode 重用/UNC 不可靠/复制移动规则不清 | 四层身份模型 + 平台矩阵 + 安全读取状态机 + inode 重用防御 |
| 5 | `read_current()` manifest 校验"可选" | 无自校验、CURRENT 损坏无处理 | 强制校验 + manifest_digest 自校验 + CURRENT 损坏报错 + 清理策略 |
| 6 | 执行报告无交付物哈希 | 证据缺口 | 独立 HASH-MANIFEST + 冻结/新增哈希区分 |

---

## 三、字段/接口变更对照

| 类别 | DESIGN-04 | DESIGN-05 |
|------|-----------|-----------|
| 关系基数 | `RELATION_CARDINALITY: dict[str, tuple]` | `RELATION_CARDINALITY_MATRIX: dict[str, RelationCardinalitySpec]`（五要素）|
| 授权摘要 | `integrity_algorithm` + `integrity_digest` | 增加 `canonical_version`、`signature_algorithm`、`signature`（默认空 = 未签名 DENY）|
| 授权消费 | `_consume_authorization`（加锁递增）| 四态状态机 + `consume_authorization`（RESERVED/CONSUMED/DENY）|
| 文件身份 | `(st_dev, st_ino)` 二元组 | `FileIdentity` 四层（path/object/content/project）|
| 发布 schema | `usit-design-publish-v1` | `usit-design-publish-v2`（manifest 增加 manifest_digest）|
| 读取校验 | manifest 校验可选 | `read_current()` 强制校验 + `PublishError` |
| 交付物哈希 | 无 | `USIT-STAGE-B-DESIGN-05-HASH-MANIFEST.md` |

---

## 四、未改变的部分（DESIGN-04 已定，DESIGN-05 保持）

- 节点/关系类型分离、可扩展注册
- TestCase 包装 TestSpecification
- 八类测试维度
- 家庭相册三字段独立建模
- Oracle 三态 + 统计隔离
- 授权五态（ACTIVE/EXPIRED/REVOKED/INVALIDATED/CONSUMED）
- plan() 纯计划、execute() 留桩
- 冻结资产与门禁（F1/P0.8/F2 全保持）
- 双工作区处理方案

---

## 五、结论

DESIGN-05 是 DESIGN-04 的**缺陷封闭修订**，不改架构方向，只补齐 6 个契约层面的可验证规则、故障矩阵与拒绝条件。三项安全门槛（防重放闭环、文件身份跨平台、发布读取强制校验）全部收口。
