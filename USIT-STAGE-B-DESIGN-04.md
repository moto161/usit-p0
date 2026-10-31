# USIT-STAGE-B-DESIGN-04｜独立设计审查与缺陷封闭工单

> 阶段 B · DESIGN-04 · READ-ONLY INDEPENDENT AUDIT + DESIGN REVISION
> 作者：USIT 主开发 · 生成：2026-10-30（GMT+8）
> 上游：DESIGN-01 / DESIGN-02 / DESIGN-03 / USIT-WORKSPACE-BASELINE-AUDIT
> 工作区：`C:\Users\Administrator\WorkBuddy\2026-10-15-22-09-47\usit_p0`
> 状态：**STOP — 等待独立审查结果确认及下一阶段授权**（未授权 IMPLEMENT-01）

---

## 0. 核心问题回答

> **DESIGN-03 是否已经形成可以安全指导 IMPLEMENT-01 的完整、无重大歧义、可测试、可追溯的工程契约？**

**回答：尚未。DESIGN-03 是合格的"方向正确 + 证据规则收紧"修订，但在 8 个工程契约层面仍存在可阻断 IMPLEMENT-01 的缺陷。** 其中 2 项为 **P0 阻断**（授权密码学把 SHA256 当签名、多文件发布把两个 rename 当事务），6 项为 P1（证据失效反查、关系基数、ID 契约、Oracle 统计、TOCTOU、授权状态机）。本工单逐一封闭，形成 DESIGN-04 契约。

> 文档设计通过 ≠ 代码实现通过。所有字段/接口/伪代码均不代表对应机制已成立，本工单不把"写下了字段"当成"已实现"。

---

## 1. 审查方法声明

本工单读取了以下真实源码以核对设计契约与现有接口的一致性（NOT_VERIFIED 之处如实标注）：

| 读取对象 | 用途 |
|---------|------|
| `usit/understanding_model.py` | 核对 `EvidenceReference`（17 字段，含 `digest_expected`/`digest_match`）、`ConclusionType`、`ExecutionRecord`、`TestSpecification` 硬约束 |
| `usit/safety.py` | 核对 `canonical_project`/`canonical_output`/`overlaps`/`ScanAuthorization`/`load_authorization_file` 真实契约 |
| `usit/snapshot.py` | 核对 `ContentVerifyResult`（MATCH/CHANGED/UNAVAILABLE 三态）、`DescriptorIdentity`、`capture()` 信任边界 |
| `usit/safeio.py` | 核对 `open_no_follow`、`is_reparse`、`identity_of` TOCTOU 原语 |
| DESIGN-01/02/03 + 各执行报告 + 基线审计 | 核对跨文档字段/状态/ID 一致性 |

**关键上游事实（用于封闭 8 项）**：
1. `snapshot.py` 明确声明"**No cryptography is used here on purpose**。Signing a snapshot would need key management and a verification protocol that this project does not have"——这构成 DESIGN-04 授权密码学的**硬约束边界**：不得发明密钥基础设施。
2. `safety.canonical_project` 已存在（`resolve(strict=True)` + `reject_reparse_components`），但**无授权时刻 vs 访问时刻的身份绑定**（缺 `st_dev`/`st_ino` 绑定）。
3. `ContentVerifyResult.status` 实际为三态 `MATCH/CHANGED/UNAVAILABLE`，与工单要求五态 `MATCH/CHANGED/MISSING/UNREADABLE/NOT_CHECKED` **不一致**，需在 B 层做状态映射，不得改冻结模型。
4. `EvidenceReference` 已有 `digest_expected`/`digest_match` 字段，DESIGN-04 失效判定必须复用，不得新建哈希机制。

---

## 2. D04-01｜执行授权密码学完整性（P0）

### 2.1 缺陷认定

DESIGN-03 §6 的 `ExecutionAuthorization` 定义 `signature: str # 完整性签名（HMAC/哈希，防篡改）`，把三种不同强度的机制混为一谈：

| 机制 | 提供什么 | 需要什么 | DESIGN-03 误用 |
|------|---------|---------|---------------|
| 无密钥哈希（SHA256）| 完整性（防意外损坏）| 无密钥 | 被当成"防篡改"——攻击者可同时改内容+重算哈希 |
| HMAC（带共享密钥）| 完整性 + 对称身份（防篡改，双方共享密钥）| 共享密钥 | 与"哈希"混写，未说明密钥从哪来 |
| 数字签名（Ed25519/RSA）| 完整性 + 非对称身份（可验证签发者）| 公私钥对 + 公钥分发 | 从未提及，但"签发者"语义隐含需要 |

**特别审查结论**：单纯 SHA256 **不能被当成数字签名**。DESIGN-03 的 `signature` 字段既没有算法字段、没有密钥来源、没有公钥分发，等于在契约里写了"有个签名"，但没有定义它如何成立。这是 P0。

### 2.2 封闭方案：分层完整性，不发明密钥基础设施

**关键裁决**：本项目（阶段 B）**没有密钥管理基础设施**（`snapshot.py` 已明确声明）。因此 DESIGN-04 明确：

> **阶段 B 的 `ExecutionAuthorization` 不采用密码学签名，而是"可验证哈希 + 授权文件外部不可写性 + 签发者显式记录"三层。** 只有当未来引入真正的密钥基础设施（独立工单 + 单独授权）后，才可升级 `integrity` 字段为密码学签名，且必须 bump `AUTH_SCHEMA_VERSION`。

```python
@dataclass(frozen=True)
class ExecutionAuthorization:
    authorization_id: str
    # ---- 签发者（显式身份，非密码学认证）----
    issuer: str                     # 签发者标识（who 签发，审计用，非密码学身份）
    # ---- 完整性（可验证哈希，非签名）----
    integrity_algorithm: str        # 固定 "sha256"（唯一允许值，拒绝其它）
    integrity_digest: str           # 对规范化签名载荷的 SHA-256
    # ---- 绑定（原有）----
    case_id: str
    adapter_kind: str
    project_fingerprint: str
    operation_scope: str
    # ---- 有效期与撤销（原有，状态机见 D04-08）----
    valid_from: str
    valid_until: str
    authorization_state: str        # 见 D04-08 状态机（替代原 revocation_status）
    revocation_reason: str = ""
    # ---- 防重放（原有）----
    nonce: str
    max_use_count: int = 1
    use_count: int = 0
```

### 2.3 规范化序列化（签名载荷的确定性）

```python
def _auth_canonical_payload(auth: "ExecutionAuthorization") -> bytes:
    """签名/哈希的规范化载荷。字段顺序固定，排除完整性字段自身与可变计数字段。"""
    # 关键：signature/integrity_digest/use_count 不参与哈希（否则自指/不可判定）
    canonical = {
        "authorization_id": auth.authorization_id,
        "issuer": auth.issuer,
        "case_id": auth.case_id,
        "adapter_kind": auth.adapter_kind,
        "project_fingerprint": auth.project_fingerprint,
        "operation_scope": auth.operation_scope,
        "valid_from": auth.valid_from,
        "valid_until": auth.valid_until,
        "authorization_state": auth.authorization_state,
        "revocation_reason": auth.revocation_reason,
        "nonce": auth.nonce,
        "max_use_count": auth.max_use_count,
    }
    # 复用 understanding_model._canon_json 的确定性排序 + 无空格分隔符
    import json, hashlib
    serialized = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
```

**规则**：
- `integrity_digest` **覆盖所有绑定字段**（指纹、用例、执行器、范围、有效期、nonce、max_use_count、签发者、状态、撤销原因）。
- `use_count`（可变消费计数）**不参与**完整性哈希，其防重放由原子消费机制保证（见 §2.5）。
- 任何字段被修改/复制/替换后，`integrity_digest` 失配 → 拒绝。

### 2.4 授权文件被修改/复制/替换/篡改的拒绝

| 攻击 | 拒绝机制 |
|------|---------|
| 内容被篡改 | `integrity_digest` 失配 → 拒绝 |
| 复制授权到另一个用例 | `case_id` + `project_fingerprint` + `adapter_kind` 不匹配 → 拒绝 |
| 替换为过期授权 | `valid_until` / `authorization_state` 校验 → 拒绝 |
| 授权文件本身在项目内 | `safety.load_authorization_file` 已拒绝（`AUTHORIZATION_FILE_IN_PROJECT`）|
| 授权文件超限 | `safety` 已有 `MAX_AUTHORIZATION_BYTES`（16 KiB）|

**完整性信任链边界（如实声明）**：`integrity_digest` 只证明"载荷未被意外/无意的修改破坏"，**不防恶意篡改**（攻击者可同时改载荷+重算哈希）。这是阶段 B 的**已知且如实声明**的边界（与 `snapshot.py` 一致），不冒充密码学保证。

### 2.5 use_count 原子消费（防重放，P0 子项）

DESIGN-03 的 `use_count += 1` 是**非原子**的，存在并发竞争（两个执行器同时读到 `use_count=0`，都消费一次，绕过 `max_use_count=1`）。

```python
# 原子消费：单一文件锁 + 读改写在同一临界区内完成
import os, tempfile, fcntl  # POSIX; Windows 用 msvcrt.locking

def _consume_authorization(auth_path: str, auth: "ExecutionAuthorization") -> bool:
    """
    原子消费：打开授权状态文件并加排他锁，读→判断→写→释放，全程持锁。
    返回 True 表示本次消费成功；False 表示已达 max_use_count 或状态非法。
    """
    # 状态文件独立于授权内容文件，记录 {authorization_id: use_count}
    state_path = auth_path + ".state"
    fd = os.open(state_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        # 加锁：POSIX fcntl 排他锁 / Windows msvcrt.locking(LK_LOCK)
        _lock_exclusive(fd)
        data = _read_json_fd(fd)
        used = data.get(auth.authorization_id, 0)
        if auth.authorization_state != "ACTIVE":
            return False
        if used >= auth.max_use_count:
            return False
        data[auth.authorization_id] = used + 1
        _write_json_fd(fd, data)
        return True
    finally:
        _unlock_and_close(fd)
```

**崩溃/中断/重复请求处理**：
- 消费写入是**单次原子写**（写满后 fsync + 关闭），崩溃发生在写前→未消费（安全，下次可消费）；崩溃发生在写后→已消费（安全，不会重复）。
- 重复请求：第二次消费时 `used >= max_use_count` → 拒绝。
- 进程中断持锁：OS 自动释放文件锁，不会死锁。

### 2.6 验证顺序（执行前统一判定）

```python
def verify_execution_authorization(auth: "ExecutionAuthorization", *,
                                   case_id, adapter_kind, project_fingerprint,
                                   now_utc) -> tuple[bool, str]:
    """按固定顺序校验，任一失败即拒绝，返回 (是否允许, 拒绝原因)。"""
    # 1. 完整性：integrity_algorithm == "sha256" 且 integrity_digest 匹配
    if auth.integrity_algorithm != "sha256":
        return False, "INTEGRITY_ALGORITHM_UNSUPPORTED"
    if auth.integrity_digest != _auth_canonical_payload(auth):
        return False, "INTEGRITY_MISMATCH"
    # 2. 状态：authorization_state 必须 ACTIVE（状态机见 D04-08）
    if auth.authorization_state != "ACTIVE":
        return False, "AUTHORIZATION_NOT_ACTIVE"
    # 3. 绑定：用例/执行器/项目指纹逐一匹配
    if auth.case_id != case_id: return False, "CASE_ID_MISMATCH"
    if auth.adapter_kind != adapter_kind: return False, "ADAPTER_MISMATCH"
    if auth.project_fingerprint != project_fingerprint: return False, "PROJECT_FINGERPRINT_MISMATCH"
    # 4. 有效期
    if not (auth.valid_from <= now_utc < auth.valid_until):
        return False, "AUTHORIZATION_TIME_INVALID"
    # 5. 防重放（原子消费）
    return _consume_authorization(...) or (False, "USE_COUNT_EXCEEDED")
```

---

## 3. D04-02｜证据哈希与失效传播（P1）

### 3.1 缺陷认定

DESIGN-03 §8 的 `propagate_invalidation(changed_digest)` 用"**changed_digest 反查旧 digest**"来定位失效证据，这正是工单禁止的模糊逻辑——它把"内容哈希值"当成了"源文件身份"，混淆了：

- `source_id`（源文件身份标识）
- `expected_digest`（捕获时的内容哈希）
- `actual_digest`（当前重新计算的内容哈希）
- `digest_algorithm`（哈希算法）
- `verification_status`（校验结果）
- `verified_at`（校验时间）

### 3.2 封闭方案：显式六元组 + 身份优先

```python
@dataclass(frozen=True)
class EvidenceIntegrity:
    """证据哈希校验的显式六元组。身份优先于内容哈希。"""
    source_id: str                 # 源文件身份（st_dev:st_ino 或规范相对路径，见下）
    expected_digest: str           # 捕获时的 SHA-256（64 hex）
    digest_algorithm: str          # 固定 "sha256"
    actual_digest: str | None      # 本次重算的 SHA-256；未算则 None
    verification_status: str       # 见状态机（五态映射）
    verified_at: str | None        # ISO-8601 UTC，未校验则 None
```

### 3.3 验证状态机（五态，映射到冻结三态）

工单要求五态，但冻结的 `ContentVerifyResult.status` 只有三态。DESIGN-04 在 **B 层新增五态**，并**映射**到冻结三态（不改冻结模型）：

| B 层状态 | 含义 | 映射到 ContentVerifyResult |
|---------|------|--------------------------|
| `MATCH` | 当前内容哈希 == expected_digest | `MATCH` |
| `CHANGED` | 读成功但哈希不同 | `CHANGED` |
| `MISSING` | 文件不存在 | `UNAVAILABLE`（reason="missing"）|
| `UNREADABLE` | 文件存在但无法读（权限/损坏）| `UNAVAILABLE`（reason="unreadable"）|
| `NOT_CHECKED` | 尚未执行校验 | 不调用 verify（初始态）|

**规则**：`NOT_CHECKED` 是**默认态**，不等于 `MATCH`；任何未经校验的证据，其依赖的关系/用例**不得作为当前有效结论**（保守失效）。

### 3.4 源文件身份如何确定

**身份 = `(st_dev, st_ino)`，不是路径字符串，也不是内容哈希。**

- `source_id` 记录 `st_dev:st_ino`（复用 `snapshot.DescriptorIdentity` 的 `(dev, ino)` 语义），**辅以**规范化相对路径作为可读别名。
- 身份用于回答"这**是**不是同一个文件"，内容哈希用于回答"内容**变没变**"。
- 反查失效时，**按 `source_id`（身份）定位**，而非按内容哈希反查。

### 3.5 失效传播算法（身份优先，禁止 changed_digest 反查）

```python
def propagate_invalidation(changed_source_ids: frozenset[str]) -> None:
    """
    按【源文件身份】定位失效，禁止按内容哈希反查。
    传播链（单向）：EvidenceIntegrity → EvidenceReference → BusinessRelation
                  → TestCase → ExecutorPlan → ExecutionAuthorization
    """
    # 1. 找到 source_id 在 changed_source_ids 中的 EvidenceIntegrity → verification_status=CHANGED/MISSING/UNREADABLE
    # 2. 反向找引用这些 EvidenceReference 的 BusinessRelation → RELATION_VALIDITY=STALE
    # 3. 反向找 relation_ids 含这些 relation 的 TestCase → STALE
    # 4. 反向找 case_id 匹配的 ExecutorPlan → STALE
    # 5. 反向找 case_id 匹配的 ExecutionAuthorization → authorization_state=INVALIDATED
```

### 3.6 多证据/多关系传播规则

| 场景 | 规则 |
|------|------|
| 一个证据引用多个文件 | 任一文件身份失效 → 该证据整体失效 |
| 一个关系依赖多条证据 | 按 `_resolve_multi_evidence` 重新计算；若支撑证据全部失效 → 关系 STALE；若有剩余有效支撑证据 → 关系仍 CURRENT 但置信度可能降级 |
| 多个关系共同支持一个用例 | 任一 `relation_ids` 中的关系 STALE → 用例 STALE（保守，因为用例结论依赖全部关系）|
| 源文件被删除 | `source_id` 对应身份消失 → MISSING → 传播 STALE |
| 源文件被移动/重命名 | 身份可能不变（同 inode rename）→ 但路径别名变化需重新解析；身份变化（复制）→ 视为新文件，旧引用 STALE |

### 3.7 恢复后为何不自动恢复 + revalidate 流程

- **恢复原文件（内容哈希回到原值）不自动清除 STALE**：因为"哈希回到原值"只能由**一次新的校验**发现，而该校验本身必须被显式触发；且无法区分"有意回滚"与"巧合相同"。
- `revalidate` 流程：显式重跑 `verify_current_content` → 若 MATCH，则从证据→关系→用例→计划→授权逐层**重新评估**，而非简单把 STALE 翻回 CURRENT；每层重新走各自的置信度/有效性判定。授权被 INVALIDATED 后**不得自动恢复 ACTIVE**，必须重新签发（见 D04-08）。

---

## 4. D04-03｜多文件原子发布（P0）

### 4.1 缺陷认定

DESIGN-03 §7.5 声称"先写两个 `.tmp`，两阶段成功后 `rename` 到最终路径"即可实现原子性。**这是错的**：两个独立的 `rename` 系统调用之间有一个**可见窗口**——第一个 rename 成功后、第二个 rename 前，读者能看到**新旧混合版本**（business_relations.json 是新的，test_design.json 还是旧的）。"两个 rename"不等于"事务"。

### 4.2 封闭方案：不可变版本目录 + 原子 CURRENT 指针

```python
# 发布协议：
#   输出根目录结构（不可变版本目录 + 单一指针）
#   <out>/
#     manifest.json                 # 版本 manifest（含两文件 SHA256）
#     versions/
#       v<timestamp>-<digest8>/     # 不可变版本目录
#         business_relations.json
#         test_design.json
#     CURRENT                       # 原子指针（内容为版本目录名，单行）

import os, json, hashlib, shutil, tempfile

def publish_atomic(out_dir: str, business_relations: bytes, test_design: bytes,
                   version: str) -> None:
    """多文件原子发布。读者只通过 CURRENT 指针看到一致版本。"""
    # 1. 计算两个文件的 SHA256
    br_digest = hashlib.sha256(business_relations).hexdigest()
    td_digest = hashlib.sha256(test_design).hexdigest()
    # 2. 写入不可变版本目录（.tmp 前缀，写完后原子 rename 为正式目录名）
    version_dir = os.path.join(out_dir, "versions", version)
    tmp_dir = version_dir + ".tmp"
    if os.path.exists(tmp_dir):
        shutil.rmtree(tmp_dir)          # 清理上次失败残留
    os.makedirs(tmp_dir, exist_ok=True)
    with open(os.path.join(tmp_dir, "business_relations.json"), "wb") as f:
        f.write(business_relations)
    with open(os.path.join(tmp_dir, "test_design.json"), "wb") as f:
        f.write(test_design)
    # 3. 写 manifest（在同一版本目录内，保证一致性）
    manifest = {
        "schema_version": "usit-design-publish-v1",
        "version": version,
        "files": {
            "business_relations.json": {"sha256": br_digest, "bytes": len(business_relations)},
            "test_design.json": {"sha256": td_digest, "bytes": len(test_design)},
        },
    }
    with open(os.path.join(tmp_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, sort_keys=True, indent=2)
    # 4. 原子发布：tmp 目录整体 rename 为正式版本目录（单个原子操作）
    os.replace(tmp_dir, version_dir)    # os.replace = 原子 rename
    # 5. 原子切换 CURRENT 指针：写临时指针 + 原子 rename
    tmp_pointer = os.path.join(out_dir, ".CURRENT.tmp")
    with open(tmp_pointer, "w", encoding="utf-8") as f:
        f.write(version + "\n")
    os.replace(tmp_pointer, os.path.join(out_dir, "CURRENT"))
```

### 4.3 读取协议（读者永不读到混合版本）

```python
def read_current(out_dir: str) -> tuple[bytes, bytes]:
    """读者：先读 CURRENT 指针确定版本，再读该版本目录内的两个文件。
    版本目录不可变，因此一旦指针确定，两个文件必然一致。"""
    version = open(os.path.join(out_dir, "CURRENT"), encoding="utf-8").read().strip()
    vdir = os.path.join(out_dir, "versions", version)
    br = open(os.path.join(vdir, "business_relations.json"), "rb").read()
    td = open(os.path.join(vdir, "test_design.json"), "rb").read()
    # 可选：校验 manifest 中的 SHA256
    return br, td
```

### 4.4 崩溃恢复规则

| 崩溃点 | 状态 | 恢复 |
|--------|------|------|
| 写 tmp 目录中途崩溃 | tmp 目录半成品 | 下次发布前 `rmtree(tmp_dir)` 清理（或启动时清理 `.tmp` 残留）|
| tmp → 正式目录 rename 前崩溃 | 仅 tmp 残留，正式目录无此版本 | 清理 tmp，CURRENT 指向旧版本（一致）|
| rename 成功后、CURRENT 切换前崩溃 | 新版本目录已存在，但 CURRENT 仍指旧版本 | 旧版本完整可读（一致）；新版本目录成为孤儿，下次可回收 |
| CURRENT 切换后崩溃 | 一切完成 | 新版本一致可读 |

**关键不变式**：**CURRENT 指针是唯一的"当前版本"真相来源**。读者永远通过指针取一致版本；任何时刻指针指向的版本目录都是完整不可变的。

### 4.5 磁盘空间不足 / rename 失败

- 写 tmp 时空间不足 → 写失败抛异常 → tmp 目录残留，正式目录未创建，CURRENT 不变 → 读者仍见旧版本。
- `os.replace` 失败（跨设备/权限）→ 同目录内 rename 通常不跨设备；若失败，正式目录未创建，CURRENT 不变。
- 所有失败路径**不产生半成品对读者可见**。

### 4.6 Windows / POSIX 差异

| 差异 | 处理 |
|------|------|
| Windows `os.replace` 目标存在时 | `os.replace` 在 Windows 上原子覆盖（同 POSIX）|
| Windows 文件名大小写不敏感 | 版本目录名用小写 hex（`digest8`），避免大小写歧义 |
| Windows 目录 rename 需目标不存在 | `os.replace` 目录到目录：目标不存在才成功；需先确保 tmp 目标不存在 |
| 文件锁差异 | 发布不使用锁（不可变目录 + 原子指针天然无锁竞争）|

---

## 5. D04-04｜关系基数与真实冲突判定（P1）

### 5.1 缺陷认定

DESIGN-03 §5.5 的 `_resolve_multi_evidence` 只处理"同一关系的多条证据"，**没有区分**：

- 多个关系同时成立（合法的 MANY）
- 同一关系的多条支持证据（多证据）
- 同一关系的反向证据（真冲突）
- 违反业务约束的互斥关系（约束冲突）
- 证据不足导致的 UNKNOWN（非冲突）

并且**缺少关系基数约束**，导致"一个 feature 指向两个 entity"可能被误判为冲突。

### 5.2 封闭方案：基数约束注册表 + 冲突分类

```python
# 关系基数约束（由关系类型注册表声明）
CARDINALITY_KINDS = {"ONE_TO_ONE", "ONE_TO_MANY", "MANY_TO_ONE", "MANY_TO_MANY"}

RELATION_CARDINALITY = {
    "derives_from":    ("feature", "MANY_TO_ONE"),   # 多 feature 可派生自同一 requirement
    "contains":        ("module", "ONE_TO_MANY"),    # 一 module 含多 symbol
    "implements":      ("symbol", "MANY_TO_ONE"),    # 多 symbol 实现一 feature
    "uses":            ("feature", "MANY_TO_MANY"),  # feature 使用多 entity/store
    "reads":           ("endpoint", "MANY_TO_MANY"),
    "writes":          ("endpoint", "MANY_TO_MANY"),
    "invokes":         ("feature", "MANY_TO_MANY"),
    "requires_role":   ("endpoint", "MANY_TO_MANY"),
    "grants_access":   ("role", "MANY_TO_MANY"),
    "restricted_by":   ("entity", "MANY_TO_MANY"),
    "transitions_to":  ("state", "MANY_TO_MANY"),
    "emits":           ("feature", "MANY_TO_MANY"),
    "handles":         ("symbol", "MANY_TO_MANY"),
    "covers":          ("test_scenario", "MANY_TO_MANY"),
}
```

**核心规则（工单要求逐条落实）**：
1. 基数约束**必须由关系类型注册表声明**（上表），不得在判定时临时猜测。
2. **一个 feature 指向两个 entity 不是冲突**：`uses` 基数是 MANY_TO_MANY，多个 target 是合法共存，不是互斥。
3. **区分关系共存与互斥**：默认所有关系共存；只有**业务约束显式声明互斥**（如"一个 field 只能有一个 `restricted_by` 主权限"）时才判定互斥。
4. **冲突必须保留双方证据及来源**（复用 `understanding_model.make_conflict`）。
5. **不得通过证据数量多数表决覆盖明确矛盾**：2 条证据说 A、1 条说 B，不等于 A 赢，而是 CONFLICT。
6. **缺少业务约束时不得自行假定互斥**：无显式互斥声明 → 不判冲突，按多证据合并或共存处理。

### 5.3 冲突判定表

| 情形 | 判定 | 处理 |
|------|------|------|
| 同 relation_id，多条证据全部支撑 | 非冲突 | STATIC_INFERENCE（多证据）|
| 同 relation_id，部分支撑、部分无关 | 非冲突 | STATIC_INFERENCE（以支撑者为准）|
| 同 relation_id，证据 A 说 X→Y，证据 B 说 X→Z（同 source 同 relation_type 不同 target）| **真冲突** | CONFLICT，保留双方 |
| 同 relation_id，证据 A 说存在 X→Y，证据 B 说 X→Y 不存在 | **反向冲突** | CONFLICT |
| 违反显式业务约束（如 ONE_TO_ONE 出现第二个 target）| **约束冲突** | CONFLICT，记录约束来源 |
| 无证据 | 非冲突 | UNKNOWN |
| 多个不同 relation_id 同时指向同 source | **非冲突**（共存）| 各自独立关系 |

---

## 6. D04-05｜确定性 ID 与注册表契约（P1）

### 6.1 缺陷认定

DESIGN-03 §5.1 定义 `relation_id(source_id, relation_type, target_id)`（3 参），但 §5.2 的方向语义注释里写 `relation_id(source_id, relation_type, target_id, direction)`（4 参）——**声明与调用参数不一致**。此外未解决 node_id 的项目作用域、方向是否入 ID、大小写/Unicode/路径规范化、内容变化是否导致 ID 变化、删除重建身份连续性、版本兼容矩阵。

### 6.2 封闭方案

#### 6.2.1 确定性 ID 算法（统一签名）

```python
def _stable_id(prefix: str, *parts: Any) -> str:
    """复用 understanding_model._stable_id。内容派生、确定性、无随机。
    前缀 + SHA-256(规范化 JSON) 前 32 hex。"""
    # 已存在，复用，不重写

def node_id(kind: str, identifier: str, project_scope: str) -> str:
    """节点 ID：绑定项目作用域 + 类型 + 标识符。
    项目作用域入 ID，避免同名节点跨项目碰撞。"""
    return _stable_id("node", project_scope, kind, _norm_identifier(identifier))

def relation_id(source_id: str, relation_type: str, target_id: str) -> str:
    """关系 ID：3 参（source_id, relation_type, target_id）。
    方向由 relation_type 本身编码（见下），不单独入 ID。"""
    return _stable_id("rel", source_id, relation_type, target_id)
```

**裁决**：
- `relation_id` 为 **3 参**，`direction` **不单独作为参数**——因为 DESIGN-03 §5.2 已明确"逆关系需显式声明"，方向语义**由 relation_type 编码**（`contains` 天然是 container→contained，不需要额外 direction 字段）。§5.2 注释里的 4 参是**笔误**，DESIGN-04 修正为 3 参。
- `node_id` 增加 `project_scope`（项目作用域），解决同名节点跨项目碰撞。

#### 6.2.2 规范化规则

```python
def _norm_identifier(value: str) -> str:
    """标识符规范化：NFKC Unicode 归一 + 大小写保留（标识符大小写敏感）。
    路径类标识符额外做 POSIX 分隔符归一 + 拒绝 '..'。"""
    import unicodedata
    v = unicodedata.normalize("NFKC", value)
    # 路径规范化：反斜杠→正斜杠，拒绝绝对路径/..（复用 snapshot._normalise_rel 精神）
    return v
```

| 项 | 规则 |
|----|------|
| 大小写 | **保留**（标识符大小写敏感，`User` ≠ `user`）|
| Unicode | NFKC 归一（视觉等价字符归一为同一）|
| 路径 | POSIX 分隔符、拒绝 `..` 与绝对路径、拒绝 backslash（复用 `snapshot._normalise_rel` 规则）|
| 内容变化 | **内容变化 → ID 变化**（ID 是内容派生的，这是特性不是缺陷）|
| 删除重建 | 内容相同 → ID 相同（内容派生，不依赖时间/随机）；内容不同 → ID 不同，视为新节点 |

#### 6.2.3 版本兼容矩阵

```python
SCHEMA_VERSION = "usit-business-relations-v1"        # 数据结构字段变化时 bump
NODE_REGISTRY_VERSION = "node-registry-v1"           # 节点类型集合变化时 bump
RELATION_REGISTRY_VERSION = "relation-registry-v1"   # 关系类型集合变化时 bump
AUTH_SCHEMA_VERSION = "usit-auth-v1"                 # 授权 schema 变化时 bump（D04-01）
PUBLISH_SCHEMA_VERSION = "usit-design-publish-v1"    # 发布协议变化时 bump（D04-03）
```

| 变化 | bump 哪个版本 | 旧数据处理 |
|------|-------------|-----------|
| 数据字段增删 | SCHEMA_VERSION | 旧 schema 拒绝读取（`validate_document` 已按 schema_version 拒绝）|
| 新增节点类型 | NODE_REGISTRY_VERSION | 旧数据不产出新节点类型，可读（向后兼容）|
| 新增关系类型 | RELATION_REGISTRY_VERSION | 旧数据不含新关系，可读 |
| 授权字段变化 | AUTH_SCHEMA_VERSION | 旧授权拒绝（状态机要求重新签发）|

**规则**：`schema_version` 与 `registry_version` **分离**（DESIGN-03 §5.4 已定，DESIGN-04 补兼容矩阵）。schema 不兼容 → 拒绝；registry 新增 → 向后兼容可读，但旧数据不能声称支持新类型。

---

## 7. D04-06｜Oracle 与统计隔离（P1）

### 7.1 Oracle 三态（DESIGN-03 已定，DESIGN-04 确认并补统计）

| Oracle | 含义 | 能否产生 expected_result | 能否 PASS/FAIL | 统计归属 |
|--------|------|------------------------|---------------|---------|
| `VERIFIED_ORACLE` | 有明确业务规则/需求证据 | 能 | **能**（业务正确性）| 计入**业务通过率** |
| `INFERRED_ORACLE` | 预期从实现现状推断 | 能（标 AI_HYPOTHESIS）| **不能**（仅探索）| 计入**探索性**，不计业务通过率 |
| `NO_ORACLE` | 无任何可靠依据 | 否（None）| 不能 | 不计任何通过率 |

### 7.2 统计隔离（工单要求逐条落实）

```python
# 统计口径：四个独立计数器，互不混算
STATISTICS = {
    "business_verified": 0,     # VERIFIED_ORACLE 用例的 PASS 数
    "business_total": 0,        # VERIFIED_ORACLE 用例总数
    "exploratory": 0,           # INFERRED_ORACLE 用例数（不计通过率）
    "no_oracle": 0,             # NO_ORACLE 用例数（不产生判定）
}

def business_pass_rate() -> float:
    """业务通过率 = business_verified / business_total（仅 VERIFIED_ORACLE）。"""
    return STATISTICS["business_verified"] / STATISTICS["business_total"] if STATISTICS["business_total"] else 0.0
```

| 规则 | 落实 |
|------|------|
| 探索性结果不计入业务通过率 | INFERRED_ORACLE 用例的 PASS 不进 `business_verified` |
| 不计入正式回归通过率 | 回归通过率只统计 VERIFIED_ORACLE 用例 |
| 不计入 VERIFIED_ORACLE 统计 | INFERRED_ORACLE 完全隔离 |
| 不作为自动修复成功依据 | 修复成功判定只认 VERIFIED_ORACLE 的 PASS |
| NO_ORACLE 不产生业务判定 | expected_result=None，不生成 PASS/FAIL |
| VERIFIED_ORACLE 可追溯 | 必须能追溯到明确需求/业务规则证据（`oracle_basis="requirement"` + requirement_evidence 非空）|

### 7.3 Oracle 状态机

```
NO_ORACLE --(补充业务规则证据)--> VERIFIED_ORACLE
NO_ORACLE --(从实现推断)--> INFERRED_ORACLE
INFERRED_ORACLE --(补充业务规则证据)--> VERIFIED_ORACLE
INFERRED_ORACLE --(证据失效)--> NO_ORACLE
VERIFIED_ORACLE --(证据失效/需求撤回)--> NO_ORACLE
```

**非法转换**：`INFERRED_ORACLE` 不得自行升级为 `VERIFIED_ORACLE` 而不补充 `requirement_evidence`（否则把"实现现状"冒充"业务正确"）。

---

## 8. D04-07｜路径安全与 TOCTOU（P1）

### 8.1 缺陷认定

DESIGN-03 §7 只要求"canonical_project 规范化 + 拒绝 reparse point"，但**没有把授权时刻的路径身份绑定到访问时刻**。攻击面：

1. 授权检查通过后，路径被替换（symlink/junction 重定向）。
2. 扫描期间源文件被替换（TOCTOU：check-time vs open-time）。
3. 输出目录被重定向。

DESIGN-03 依赖 `resolve()` + 字符串前缀判断，这正是工单禁止的"仅依赖 resolve() 或字符串前缀"。

### 8.2 封闭方案：身份绑定 + 描述符级访问

```python
# canonical_project 真实契约（safety.py 已实现，DESIGN-04 核对确认）：
#   resolve(strict=True) + reject_reparse_components + is_dir
# 缺口：无 st_dev/st_ino 身份绑定。DESIGN-04 在 B 层补充：
def canonical_project_bound(path: str) -> tuple[Path, tuple[int, int]]:
    """授权时刻：规范化 + 记录 (st_dev, st_ino) 身份。"""
    root = safety.canonical_project(path)
    st = os.stat(root)
    return root, (int(st.st_dev), int(st.st_ino))

def verify_path_identity(root: Path, bound_identity: tuple[int, int]) -> bool:
    """访问时刻：重新 stat，比较 (st_dev, st_ino) 是否仍一致。"""
    try:
        st = os.stat(root, follow_symlinks=False)
    except OSError:
        return False
    return (int(st.st_dev), int(st.st_ino)) == bound_identity
```

**核心机制（工单要求逐条）**：

| 攻击面 | 拒绝机制 |
|--------|---------|
| Windows 绝对路径规范化 | `canonical_project` 已有（resolve + 拒绝 reparse）|
| UNC 路径 / 盘符 | resolve 后归一；拒绝 reparse point 覆盖 junction |
| 大小写不敏感路径 | `safety._norm` 用 `os.path.normcase` 归一比较 |
| reparse/junction/symlink | `reject_reparse_components`（逐组件）+ `open_no_follow`（最终组件）|
| 项目/输出目录真实路径重叠 | `safety.overlaps` 已有（commonpath 判断）|
| 授权检查后路径被替换 | **身份绑定**：授权时刻记录 (st_dev,st_ino)，访问时刻重新 stat 比对，不一致 → 拒绝 |
| 扫描期间源文件被替换 | **描述符级访问**：`safeio.open_no_follow` 返回绑定描述符，读前 fstat、读后 fstat 比对身份，不一致 → 拒绝（复用 snapshot.verify_current_content 的模式）|
| 输出目录被重定向 | 输出目录身份绑定 + 拒绝 reparse point |
| 检查与打开文件之间的竞态 | 描述符级：一次 open 绑定身份，后续操作只读该描述符，不二次 resolve 路径 |

**关键裁决**：
- **不得仅依赖 `resolve()` 或字符串前缀**判断安全。`resolve()` 只用于规范化展示，安全判定靠**身份绑定（st_dev/st_ino）+ 描述符级访问（open_no_follow）**。
- 身份绑定机制**复用 `snapshot.DescriptorIdentity` + `safeio.open_no_follow` + `safety.reject_reparse_components`**，不新建原语（这些原语已存在且经冻结审计）。

### 8.3 检测到路径身份变化后的拒绝行为

```
身份变化检测到 → 立即 ScanBlocked("PATH_IDENTITY_CHANGED", ...)
               → 不继续读任何文件、不产出任何关系/用例
               → 输出目录保持原状（无半成品）
               → 审计日志记录：授权时刻身份 vs 访问时刻身份
```

---

## 9. D04-08｜授权失效状态区分（P1）

### 9.1 缺陷认定

DESIGN-03 §6 用 `revocation_status: ACTIVE/REVOKED/EXPIRED`，§8 又出现 `INVALIDATED`、`CONSUMED` 概念，状态**散落多处、无统一状态机、无合法转换表**。

### 9.2 封闭方案：统一授权状态机

```python
AUTHORIZATION_STATES = {"ACTIVE", "EXPIRED", "REVOKED", "INVALIDATED", "CONSUMED"}
```

| 状态 | 含义 | 进入条件 |
|------|------|---------|
| `ACTIVE` | 有效 | 签发时初始态 |
| `EXPIRED` | 超过有效期 | `now >= valid_until` |
| `REVOKED` | 被主动撤销 | 撤销操作显式设置 |
| `INVALIDATED` | 依赖证据/项目指纹变化 | 失效传播（D04-02）触发 |
| `CONSUMED` | 使用次数耗尽 | `use_count >= max_use_count`（原子消费）|

### 9.3 合法转换表

| 从 → 到 | 合法性 | 说明 |
|---------|--------|------|
| ACTIVE → EXPIRED | 合法 | 时间流逝，自动 |
| ACTIVE → REVOKED | 合法 | 主动撤销 |
| ACTIVE → INVALIDATED | 合法 | 证据/指纹变化，自动 |
| ACTIVE → CONSUMED | 合法 | 使用耗尽，自动 |
| EXPIRED → ACTIVE | **非法** | 过期不可复活 |
| REVOKED → ACTIVE | **非法** | 撤销不可恢复（需重新签发新授权）|
| REVOKED → 其它 | **非法** | 撤销是终态 |
| INVALIDATED → ACTIVE | **非法** | 证据变化后必须重新签发，不得自动恢复 |
| CONSUMED → ACTIVE | **非法** | 耗尽不可重置（需重新签发）|
| CONSUMED → 其它 | **非法** | 耗尽是终态 |
| 任意 → ACTIVE | 仅初始签发 | 唯一进入 ACTIVE 的方式是**重新签发新授权** |

**规则**：
- `INVALIDATED` 必须**重新签发**授权（旧授权作废，不能恢复）。
- `REVOKED` **不允许恢复**（若需恢复，签发新授权）。
- `CONSUMED` **不允许重置**（`use_count` 不可清零，需新授权）。
- 每个状态转换**进入审计日志**（时间、原因、操作者）。
- 执行前统一判定：`authorization_state == ACTIVE` 且通过完整性/绑定/有效期/防重放校验，才允许执行。

### 9.4 与 D04-01 状态字段的统一

DESIGN-03 §6 的 `revocation_status` 字段在 DESIGN-04 中**重命名为 `authorization_state`**（承载五态），`revocation_reason` 仅在 `REVOKED` 时必填。这修正了"状态散落"问题。

---

## 10. 全局契约一致性审查（工单第五部分）

逐项核对 DESIGN-01/02/03/04 的字段/状态/ID/门禁：

| # | 审查项 | 结论 | 证据 |
|---|--------|------|------|
| 1 | DESIGN-01/02/03 字段定义是否冲突 | 发现 1 处（见下 ISSUE-G1）| 详见 ISSUES 文档 |
| 2 | BusinessRelation/EvidenceReference/TestCase/ExecutorPlan/ExecutionAuthorization 引用链 | **不完整**：Authorization 缺 `authorization_id` 在 DESIGN-02 里没有（DESIGN-03 补），但 ExecutorPlan→TestCase→Relation→Evidence→文件 链完整 | ISSUE-G2 |
| 3 | 每个 ID 唯一且稳定 | `relation_id` 3参/4参不一致（已修，D04-05）；node_id 缺 project_scope（已修）| D04-05 |
| 4 | 每个状态有合法转换规则 | 授权状态无（已补 D04-08）；Oracle 状态已补 D04-06 | D04-08/06 |
| 5 | 每个结论有对应证据 | 是（evidence 字段贯穿）| DESIGN-02 §2.3 |
| 6 | UNKNOWN/CONFLICT/STALE/NOT_CHECKED 区分 | 未完全区分（STALE vs NEEDS_REVALIDATION 在 DESIGN-02 §8 有两个名，DESIGN-03 未统一）| ISSUE-G3 |
| 7 | VERIFIED_FACT=0 门禁 | **仍成立**（understanding_model 硬拒绝，DESIGN-04 未改）| 源码行 278/316 |
| 8 | ExecutionRecord=NOT_RUN 门禁 | **仍成立**（DESIGN-04 未改）| 源码行 329-337 |
| 9 | plan() 纯计划 | **仍成立**（DESIGN-04 未改，execute 仍留桩）| DESIGN-02 §7.1 |
| 10 | execute() 留桩 | **仍成立** | DESIGN-02 §7.1 |
| 11 | 八类测试维度完整 | **仍成立** | DESIGN-02 §4 |
| 12 | is_only_self/is_secret/family_ids 独立建模 | **仍成立** | DESIGN-02 §6.1 |
| 13 | 未经业务证据的权限预期 | **无**（DESIGN-03 已降级，DESIGN-04 确认）| DESIGN-03 §3 |
| 14 | 静态推断冒充运行验证 | **无**（NOT_RUN 门禁 + oracle 隔离）| 多处 |

---

## 11. 双工作区基线（工单第四部分）

详见独立交付物 `USIT-WORKSPACE-RECONCILIATION-PLAN.md`。本处摘要：

| 分叉文件 | 处理建议 |
|---------|---------|
| `cli.py`（A=增量超集）| 采用 A 的 `understand` 命令，但需独立审查 A 新增代码后再合并 |
| `python_worker.py`（A=优化版）| 采用 A 的 `from _analysis import`，但需先解决 `analyze_file` 双重定义 |
| `scanner.py`（A=拆分重构版）| 保留 B（S1-C 冻结基线），A 的拆分需行为等价验证后才能合并 |
| `analyze_file` 双重定义 | 行为一致性 NOT_VERIFIED，需 IMPLEMENT 阶段单独验证 |

**本工单只提出建议，不选择性覆盖文件。**

---

## 12. 最终结论：**PASS-WITH-ISSUES**

- **8 项重点任务全部逐条审查**：2 项 P0（D04-01 授权密码学、D04-03 原子发布）+ 6 项 P1 均形成封闭方案。
- **P0 问题均有明确封闭方案及可验证标准**（D04-01：分层完整性不发明密钥 + 原子消费；D04-03：不可变版本目录 + 原子 CURRENT 指针）。
- 授权密码学与防重放契约完整（D04-01）、证据失效传播链完整（D04-02）、多文件发布一致性恢复机制完整（D04-03）、关系基数无逻辑混淆（D04-04）、ID 与注册表契约一致（D04-05）、Oracle 统计隔离（D04-06）、路径 TOCTOU 契约完整（D04-07）、授权状态机完整（D04-08）。
- 双工作区三个分叉文件均有处理方案（A11）。
- 冻结资产和门禁零修改（A12）。
- DESIGN-01/02/03/04 之间无未解释的 P0 契约冲突；存在 3 个非阻断 ISSUE（见 ISSUES 文档，P1/P2）。

**为何是 PASS-WITH-ISSUES 而非 PASS**：DESIGN-04 封闭了全部 P0/P1 设计缺陷，但仍存在 3 个非阻断问题（G1 字段命名、G2 引用链在 DESIGN-02 未显式声明 authorization_id、G3 STALE/NEEDS_REVALIDATION 双名），且 `analyze_file` 行为一致性 NOT_VERIFIED（受只读限制无法验证）。这些问题不阻断 IMPLEMENT-01 的**设计正确性**，但应在 IMPLEMENT 阶段消除。

**STOP — 等待用户和独立审查确认。未授权 IMPLEMENT-01。未将设计审查 PASS 描述为代码功能 PASS。未将 NOT_RUN 描述为测试通过。**

---

## 13. 交付物索引

| 文件 | 内容 |
|------|------|
| `USIT-STAGE-B-DESIGN-04.md` | 本文（完整审查与修订设计契约）|
| `USIT-STAGE-B-DESIGN-04-EXECUTION-REPORT.md` | 真实执行报告 |
| `USIT-STAGE-B-DESIGN-04-ISSUES.md` | 问题清单（P0/P1/P2）|
| `USIT-WORKSPACE-RECONCILIATION-PLAN.md` | 双工作区处理方案 |
| `USIT-DESIGN-03-04-DIFF.md` | DESIGN-03 → DESIGN-04 差异 |
