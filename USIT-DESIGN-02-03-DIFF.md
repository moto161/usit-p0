# USIT-DESIGN-02-03-DIFF｜DESIGN-02 → DESIGN-03 差异清单

> 生成时间：2026-10-30（GMT+8）
> 用途：DESIGN-03 交付物 4 —— DESIGN-01 与 DESIGN-02 差异清单（本文为 DESIGN-02 → DESIGN-03 差异）

---

## 一、差异总览

DESIGN-03 是在 DESIGN-02 基础上，根据独立审查发现的 **7 项设计缺陷** 进行的修订。修订不改变 DESIGN-02 已确定的架构方向（关系模型、TestCase 包装 TestSpecification、八类维度、执行适配器接口、追溯闭环），而是**收紧证据规则、补齐缺失字段、明确判定规则**。

---

## 二、逐项差异清单

| # | DESIGN-02 内容 | 审查缺陷 | DESIGN-03 修订 |
|---|---------------|---------|---------------|
| 1 | §3.4 置信度映射表（VERIFIED_FACT→STATIC_INFERENCE）| 直接降级 = 未重新验证证据↔关系对应性 | 删除映射表，改为逐证据重验证（`_revalidate_upstream_evidence`）；新增 `_evidence_merely_hints`（仅名称相似→AI_HYPOTHESIS）|
| 2 | INFERRED_ORACLE 可产生业务预期 | 实现现状冒充业务正确标准 | INFERRED_ORACLE 仅限探索性检查，禁止业务正确性 PASS/FAIL；新增 `oracle_basis` 字段（requirement/implementation/none）|
| 3 | 三类维度（正常流程/跨模块/回归）证据条件宽松 | 信号触发直接升级为确定结论 | 补齐充分条件 + 降级路径（§4.1/§4.2/§4.3 各自证据判定表）|
| 4 | 关系模型缺节点ID/关系ID/方向/端点约束/注册表版本/多证据冲突 | 不完整 | 补齐 5 项（§5.1–§5.5）|
| 5 | 执行授权缺签发者/完整性/有效期/撤销/防重放 | 不完整 | 补齐 5 项（§6，`ExecutionAuthorization` 扩展为 10+ 字段）|
| 6 | CLI 缺项目根/授权结构/参数示例/覆盖策略/失败清理 | 不完整 | 补齐 5 项（§7.1–§7.5）|
| 7 | 追溯失效无传播规则 | 不完整 | 补齐五步失效传播链（§8，`propagate_invalidation`）|

---

## 三、新增字段/接口/规则对照

| 类别 | DESIGN-02 | DESIGN-03 |
|------|-----------|-----------|
| 置信度映射 | 存在映射表 | 删除，逐证据重验证 |
| 证据暗示 vs 支撑 | 未区分 | `_evidence_supports` / `_evidence_merely_hints` 二分 |
| oracle 字段 | `oracle_status`（三态）| 增加 `oracle_basis`，收紧语义 |
| 关系 ID | 无 | `node_id()` / `relation_id()`（内容派生，确定性）|
| 关系方向 | 有向但未声明语义 | 明确 15 种关系方向 + 逆关系不推断 |
| 端点约束 | 无 | `RELATION_ENDPOINT_CONSTRAINTS`（16 种关系合法端点组合）|
| 注册表版本 | 无 | `NODE_REGISTRY_VERSION` / `RELATION_REGISTRY_VERSION` |
| 多证据冲突 | 仅"CONFLICT 保留双方" | `_resolve_multi_evidence`（6 条规则）|
| 授权字段 | case_id/adapter/指纹/范围 | 增加 issuer/signature/valid_until/revocation/nonce/max_use_count/use_count |
| CLI | 缺参数值 | 补齐项目根规范、授权 JSON 结构、完整参数示例、覆盖策略、原子发布 |
| 失效传播 | 仅"标记 STALE" | `propagate_invalidation` 五步传播链（源→关系→用例→计划→授权）|

---

## 四、未改变的部分（DESIGN-02 已确定，DESIGN-03 保持）

- 关系模型总体结构（节点类型与关系类型分离、可扩展注册）
- `TestCase` 包装冻结 `TestSpecification`（不复制字段）
- 八类测试维度的框架（正常/异常/边界/角色权限/数据隔离/跨模块/状态转换/回归）
- 家庭相册三字段（`is_only_self` / `is_secret` / `family_ids`）独立建模
- `plan()` 纯计划、`execute()` 留桩
- 逐用例授权 + 项目指纹绑定
- 追溯闭环方向（TestCase→BusinessRelation→Feature/Entity/Endpoint→EvidenceReference→文件）
- 冻结资产与门禁（understanding_model/file_understanding/safety/snapshot/scanner S1-C 基线、F1/P0.8/F2）
- 分阶段实施顺序（B-IMPLEMENT-01→02→03，合成项目验收从 IMPLEMENT-02 起）

---

## 五、结论

DESIGN-03 是 DESIGN-02 的**收紧与补齐修订**，不推翻 DESIGN-02 的架构方向，仅落实审查意见中 7 项缺陷的证据规则、字段定义与判定规则。
