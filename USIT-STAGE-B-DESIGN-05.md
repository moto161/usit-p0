# USIT-STAGE-B-DESIGN-05｜剩余设计缺陷封闭与实施准入审查

> 阶段 B · DESIGN-05 · READ-ONLY DESIGN AUDIT + DOCUMENT REVISION
> 作者：USIT 主开发 · 生成：2026-10-30（GMT+8）
> 上游：DESIGN-01 / DESIGN-02 / DESIGN-03 / DESIGN-04
> 工作区：`C:\Users\Administrator\WorkBuddy\2026-10-15-22-09-47\usit_p0`
> 状态：**STOP — 等待用户审查及 IMPLEMENT-01 单独授权**

---

## 0. 核心问题回答

> **当前设计是否已经具备安全实施 IMPLEMENT-01 的条件？**

**回答：本工单封闭后，具备（PASS-WITH-ISSUES）。** DESIGN-04 遗留的 6 项工程契约缺陷，尤其用户重点复核的三项安全门槛——**授权防重放闭环、文件身份跨平台可靠、发布读取强制校验**——在 DESIGN-05 全部收口。剩余非阻断项（密钥基础设施尚未引入、`analyze_file` 行为一致性 NOT_VERIFIED）不影响 IMPLEMENT-01 的**设计正确性**，但须在实现阶段如实标记。

> 文档设计通过 ≠ 代码实现通过。本工单所有伪代码均为**设计级契约**，不标记为已实现。

---

## 1. D05-01｜关系基数与冲突规则统一

### 1.1 缺陷认定

DESIGN-04 §5 的 `RELATION_CARDINALITY` 用一个 `("source_kind", "CARDINALITY")` 元组表达，**无法表达方向性基数**（source 与 target 的基数可能不同），且与冲突判定表存在矛盾：`uses` 标 MANY_TO_MANY，但冲突判定表里"同 relation_id 不同 target"仍判 CONFLICT——两者打架。

### 1.2 封闭方案：五要素基数声明（唯一权威来源）

**关系类型注册表是基数规则的唯一权威来源。** 每种关系类型必须声明五个要素：

```python
@dataclass(frozen=True)
class RelationCardinalitySpec:
    source_cardinality: str      # "ONE" | "MANY"
    target_cardinality: str      # "ONE" | "MANY"
    direction: str               # 有向关系的方向语义（如 "feature->entity"）
    mutual_exclusion_group: str | None   # 互斥组名；None 表示无互斥约束
    conflict_policy: str         # "NONE" | "MUTUAL_EXCLUSION" | "CARDINALITY_VIOLATION"

# 完整基数矩阵（唯一权威）
RELATION_CARDINALITY_MATRIX: dict[str, RelationCardinalitySpec] = {
    "derives_from":   RelationCardinalitySpec("MANY", "ONE", "feature->requirement", None, "NONE"),
    "contains":       RelationCardinalitySpec("ONE",  "MANY", "module->symbol", None, "NONE"),
    "implements":     RelationCardinalitySpec("MANY", "ONE", "symbol->feature", None, "NONE"),
    "uses":           RelationCardinalitySpec("MANY", "MANY", "feature->entity", None, "NONE"),
    "reads":          RelationCardinalitySpec("MANY", "MANY", "endpoint->entity", None, "NONE"),
    "writes":         RelationCardinalitySpec("MANY", "MANY", "endpoint->entity", None, "NONE"),
    "invokes":        RelationCardinalitySpec("MANY", "MANY", "feature->endpoint", None, "NONE"),
    "requires_role":  RelationCardinalitySpec("MANY", "MANY", "endpoint->role", None, "NONE"),
    "grants_access":  RelationCardinalitySpec("MANY", "MANY", "role->permission", None, "NONE"),
    "restricted_by":  RelationCardinalitySpec("ONE",  "ONE",  "entity->permission", "permission_owner", "MUTUAL_EXCLUSION"),
    "transitions_to": RelationCardinalitySpec("MANY", "MANY", "state->state", None, "NONE"),
    "emits":          RelationCardinalitySpec("MANY", "MANY", "feature->event", None, "NONE"),
    "handles":        RelationCardinalitySpec("MANY", "MANY", "symbol->event", None, "NONE"),
    "covers":         RelationCardinalitySpec("MANY", "MANY", "test_scenario->feature", None, "NONE"),
}
```

### 1.3 冲突判定表（消除矛盾）

| 情形 | 判定 | 依据 |
|------|------|------|
| 同一 source 指向多个 target，且 target_cardinality == "MANY" | **非冲突**（合法共存）| 基数允许 |
| 同一 source 指向多个 target，且 target_cardinality == "ONE" | **冲突**（基数违反）| CARDINALITY_VIOLATION |
| 同一 target 被多个 source 指向，且 source_cardinality == "ONE" | **冲突**（基数违反）| CARDINALITY_VIOLATION |
| 两条关系进入同一 `mutual_exclusion_group` 且指向不同 target | **冲突**（业务互斥）| MUTUAL_EXCLUSION |
| 证据 A 说 X→Y 存在，证据 B 说 X→Y 不存在（反向证据）| **冲突** | 反向矛盾 |
| 证据 A 说 X→Y，证据 B 说 X→Z（同 relation_id 不同 target），但 target_cardinality=="MANY" | **非冲突**（多证据合并）| 基数允许，非矛盾 |
| 无证据 | **UNKNOWN**（非冲突）| 证据不足 |
| 注册表未声明约束 | **非冲突**（不得假定互斥）| 缺省 NONE |

### 1.4 核心规则（工单要求逐条落实）

1. 关系类型注册表（上矩阵）是基数规则的**唯一权威**。
2. 每种关系声明 source_cardinality / target_cardinality / direction / mutual_exclusion_group / conflict_policy。
3. **ONE_TO_MANY 不得因同 source 多 target 自动判冲突**（target_cardinality=="MANY" 即合法）。
4. MANY_TO_MANY 必须允许多个有效目标。
5. **只有违反已声明基数或明确互斥业务约束才判 CONFLICT**。
6. 注册表未声明约束 → 不假定互斥（conflict_policy 缺省 NONE）。
7. **UNKNOWN 与 CONFLICT 严格区分**：无证据=UNKNOWN，有矛盾证据=CONFLICT。
8. **冲突保留所有正反证据，不得多数投票覆盖**（复用 `understanding_model.make_conflict`）。

### 1.5 边界案例

| 边界 | 处理 |
|------|------|
| `restricted_by` 一个 entity 出现两个 permission | target_cardinality="ONE" → 基数违反 CONFLICT |
| `uses` 一个 feature 指向 3 个 entity | target_cardinality="MANY" → 合法，3 条独立关系 |
| `contains` 一个 module 含 5 个 symbol | target_cardinality="MANY" → 合法 |
| `derives_from` 3 个 feature 派生自 1 个 requirement | source_cardinality="MANY" → 合法 |

---

## 2. D05-02｜授权完整性摘要与签名契约

### 2.1 缺陷认定

DESIGN-04 §2 的 `_auth_canonical_payload` 只定义了 SHA256，未区分"摘要"与"签名"，未明确规范化算法版本，未说明"未配置签名时 DENY"。

### 2.2 封闭方案：摘要与签名严格分离

**核心裁决**：
- **SHA256 integrity digest 只用于检测数据变化，不等于身份认证。**
- **签名**（未来引入）提供身份认证，**摘要**（当前）只提供完整性。
- **当前阶段（B）未配置可信签发者和真实签名验证机制 → 执行授权必须 DENY（默认拒绝）。**

```python
@dataclass(frozen=True)
class ExecutionAuthorization:
    authorization_id: str
    # ---- 完整性（摘要，非签名）----
    integrity_algorithm: str        # 固定 "sha256"（唯一允许值）
    integrity_digest: str           # SHA256(canonical_payload) 的 hex
    canonical_version: str          # 固定 "canonical-json-v1"（规范化算法版本）
    # ---- 身份（未来签名，当前留空）----
    issuer: str                     # 签发者标识；仅审计，非可信身份
    signature_algorithm: str = ""   # 空字符串 = 未签名；未来 "ed25519"
    signature: str = ""             # 空字符串 = 未签名
    # ---- 绑定（原有）----
    case_id: str
    adapter_kind: str
    project_fingerprint: str
    operation_scope: str
    # ---- 有效期与状态（原有）----
    valid_from: str
    valid_until: str
    authorization_state: str
    revocation_reason: str = ""
    # ---- 防重放（原有）----
    nonce: str
    max_use_count: int = 1
    use_count: int = 0
```

### 2.3 规范化 JSON 算法（明确版本）

```python
def canonical_payload(auth: "ExecutionAuthorization") -> bytes:
    """
    规范化算法 canonical-json-v1：
    - 字段顺序：按字段名排序（sort_keys=True）
    - 分隔符：无空格（separators=(",", ":")）
    - 编码：UTF-8，ensure_ascii=False
    - 排除字段：integrity_digest 自身、signature、use_count（自指/可变，不入摘要）
    - 排除字段之外的【全部安全相关字段】都必须纳入
    """
    canonical = {
        "authorization_id": auth.authorization_id,
        "issuer": auth.issuer,
        "signature_algorithm": auth.signature_algorithm,
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
    import json
    return json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")

def compute_integrity_digest(auth: "ExecutionAuthorization") -> str:
    import hashlib
    return hashlib.sha256(canonical_payload(auth)).hexdigest()  # 64 hex 小写
```

**摘要计算三步**：`canonical_payload`（规范化 JSON）→ `SHA256(canonical_payload)` → hex 小写 64 字符编码。

### 2.4 签名与摘要的关系

| 机制 | 提供 | 阶段 B 状态 |
|------|------|------------|
| integrity_digest | 完整性（检测篡改，可被恶意重算）| **启用** |
| signature | 身份认证（防恶意篡改）| **未配置 → 默认 DENY** |

**规则**：`signature_algorithm == ""`（未签名）时，执行授权**一律 DENY**，除非未来通过独立工单引入密钥基础设施 + 单独授权。

### 2.5 拒绝条件（工单要求逐条）

| 条件 | 拒绝原因 |
|------|---------|
| 字段缺失 | `FIELD_MISSING` |
| 重复字段（JSON 同名键）| `DUPLICATE_FIELD` |
| 类型错误 | `FIELD_TYPE_INVALID` |
| 未知关键字段 | `UNKNOWN_FIELD` |
| integrity_digest 不匹配 | `INTEGRITY_MISMATCH` |
| signature 无效（未来）| `SIGNATURE_INVALID` |
| signature_algorithm 为空 | `SIGNATURE_REQUIRED_DENY`（默认拒绝）|

### 2.6 密钥轮换 / 撤销 / 签发者信任链（未来规则，当前未启用）

- 引入签名时，签发者公钥进入**信任锚定存储**（独立文件，非项目内）。
- 密钥轮换：新公钥版本化（`key_version`），旧公钥保留一个过渡期验证旧签名。
- 密钥撤销：撤销记录进入信任存储，撤销后该公钥签发的授权立即 DENY。
- 签发者信任链：单一根信任（无中间 CA），签发者公钥由用户显式配置。
- **以上均为未来设计，阶段 B 不实现、不标记为可用。**

### 2.7 设计级伪代码 vs 可执行代码

本工单所有代码块是**设计契约**（字段/流程/拒绝条件的声明），**不是已实现代码**。不得将伪代码标记为已实现、已测试或已通过。

---

## 3. D05-03｜授权原子消费与防重放

### 3.1 缺陷认定

DESIGN-04 §2.5 只写"加锁后 use_count += 1"，未定义 nonce 作用域、状态损坏处理、锁失效处理、崩溃一致性、消费时机（执行前/后）、跨平台锁差异。

### 3.2 封闭方案：完整消费状态机

```python
# 授权消费状态机
CONSUMPTION_STATES = {"NOT_CONSUMED", "RESERVED", "CONSUMED", "CORRUPTED"}

# 状态转换（合法）
# NOT_CONSUMED --(reserve: 原子 CAS use_count 0->1)--> RESERVED
# RESERVED      --(commit: 执行成功)--> CONSUMED
# RESERVED      --(release: 执行失败/回滚)--> NOT_CONSUMED（仅当允许重试）
# 任意态        --(检测到状态文件损坏/被替换)--> CORRUPTED --> DENY
# CORRUPTED     --(管理员显式重置+重新签发)--> 新授权（旧授权作废）
```

### 3.3 nonce 唯一性与作用域

- `nonce` 是**全局唯一**的随机值（`secrets.token_hex(16)`，128 位熵），作用域为**整个授权生命周期**。
- nonce 用于：① 授权 ID 的熵源之一；② 审计日志关联；③ 防重放（同一 nonce 的授权只允许消费一次）。
- **nonce 不得跨授权复用**；复用即视为重放，DENY。

### 3.4 max_use_count / use_count 一致性约束

- `0 <= use_count <= max_use_count`。
- `max_use_count >= 1`（0 无意义，拒绝）。
- `use_count > max_use_count` → 状态损坏，DENY。
- 消费成功判定：`use_count` 从 `< max_use_count` 原子递增到 `<= max_use_count`。

### 3.5 原子消费实现（含故障处理）

```python
def consume_authorization(state_path: str, auth_id: str, max_use: int) -> str:
    """
    原子消费。返回 "RESERVED" | "ALREADY_CONSUMED" | "DENY_CORRUPTED"。
    消费时机：在【实际执行之前】预留（RESERVED），执行成功后 commit（CONSUMED）。
    """
    fd = _open_state_file(state_path)          # 打开状态文件
    if fd is None:
        return "DENY_LOCK_UNAVAILABLE"          # 无法打开/加锁 → DENY
    try:
        if not _lock_exclusive(fd):             # 加排他锁
            return "DENY_LOCK_UNAVAILABLE"      # 锁失效/不可用 → DENY
        data = _read_json_fd(fd)                # 读状态
        if _is_corrupted(data, auth_id, max_use):
            return "DENY_CORRUPTED"             # 损坏 → DENY
        used = data.get(auth_id, 0)
        if used >= max_use:
            return "ALREADY_CONSUMED"           # 已达上限 → 拒绝
        data[auth_id] = used + 1                # 原子递增
        _write_json_fd_fsync(fd, data)          # 写 + fsync（崩溃一致性）
        return "RESERVED"
    finally:
        _unlock_close(fd)
```

### 3.6 故障处理矩阵（工单要求逐条）

| 故障 | 处理 |
|------|------|
| 状态文件损坏（JSON 解析失败）| DENY_CORRUPTED |
| 状态文件被删除 | 重新创建为空状态（视为全新，use_count=0），但**授权本身若曾消费过需靠审计日志判定**——保守：删除后 DENY（因无法证明未消费）|
| 状态文件被替换/回滚 | 检测到内容哈希变化 → DENY_CORRUPTED |
| 消费记录重复使用 | nonce + use_count 双重防线；use_count 达上限即拒 |
| 进程崩溃（RESERVED 后、commit 前）| 状态残留 RESERVED，下次执行发现 RESERVED 且无对应执行记录 → 视为未消费，可重试（或超时释放）|
| 进程崩溃（commit 后）| 状态 CONSUMED 已落盘（fsync），不会重复消费 |
| 文件锁失效/不可用 | DENY_LOCK_UNAVAILABLE |
| Windows vs POSIX 锁差异 | Windows 用 `msvcrt.locking(LK_LOCK)`（字节范围锁），POSIX 用 `fcntl.flock(LOCK_EX)`；两者语义不同但都提供排他性；跨平台抽象层 `_lock_exclusive` 分别实现 |

### 3.7 消费时机（执行前 vs 后）

**消费发生在执行前（RESERVED），执行成功后 commit（CONSUMED）。**

- **执行前预留**：保证并发下只有一个执行器拿到执行权。
- **失败时是否重试**：`RESERVED` 状态下执行失败 → `release` 回滚到 NOT_CONSUMED，允许重试；但**是否允许重试由 `max_use_count` 约束**（max_use_count=1 时，release 后仍可再消费一次，但需重新预留）。
- **已 CONSUMED 不可重试**：执行成功即终结。

---

## 4. D05-04｜跨平台文件身份与 TOCTOU

### 4.1 缺陷认定

DESIGN-04 §8 把 `(st_dev, st_ino)` 当成通用身份，但工单指出：inode 会重用、Windows UNC 无稳定 ino、复制/移动后身份变化规则复杂。**不得把 (st_dev, st_ino) 视为所有平台通用且永久稳定的唯一身份。**

### 4.2 封闭方案：四层身份模型

```python
@dataclass(frozen=True)
class FileIdentity:
    path_identity: str        # 规范化绝对路径（可读别名，非安全身份）
    object_identity: str | None   # 文件系统对象身份（Windows: FileIndex/VolumeSerial; POSIX: st_dev:st_ino）
    content_identity: str | None  # 内容摘要 SHA256（最可靠，但昂贵）
    project_identity: str | None  # 项目根对象身份（授权时绑定）
```

| 身份层 | 提供什么 | 稳定性 | 平台差异 |
|--------|---------|--------|---------|
| path_identity | 定位（可读）| 弱（移动/重命名即变）| 无 |
| object_identity | 区分"是不是同一对象"| 中（inode 会重用）| **Windows NTFS**：`FileIndex`+`VolumeSerialNumber`（稳定）| **Windows UNC**：不可靠（SMB 可能不暴露稳定身份）| **POSIX**：`st_dev:st_ino`（inode 可能重用）|
| content_identity | 内容是否一致（最强）| 高（内容变即变）| 无（跨平台通用）|
| project_identity | 项目根是否被替换 | 中 | 同 object_identity |

### 4.3 平台差异矩阵（工单要求逐条）

| 场景 | object_identity 可靠性 | 处理 |
|------|----------------------|------|
| Windows 本地 NTFS | `FileIndex`+`VolumeSerialNumber`，稳定 | 可靠 |
| Windows UNC 网络路径 | 不稳定（SMB 可能不暴露）| **不信任 object_identity，强制 content_identity** |
| POSIX 本地文件系统 | `st_dev:st_ino`，但 inode 可重用 | 可靠，但需防 inode 重用 |
| symlink/junction/reparse point | 拒绝（`open_no_follow` + `reject_reparse_components`）| 拒绝 |
| 文件复制后 | 新 inode/FileIndex → 身份变化 | 视为新文件，旧引用 STALE |
| 文件移动/重命名（同卷）| inode/FileIndex 不变 → 身份不变，但 path 变 | 需重新解析 path 别名 |
| 文件移动（跨卷）| 新 inode → 身份变化 | 视为新文件 |
| inode 重用 | 旧 inode 被新文件占用 | 必须结合 content_identity 或 mtime 防重用 |
| 扫描期间被替换 | 身份变化（inode 变）| 描述符级检测 |
| 检查后、读取前被替换 | 打开时绑定身份，读后 fstat 复验 | 复验不一致 → 拒绝 |

### 4.4 安全读取协议（状态机）

```
OPEN_FILE
  → open_no_follow(path)                    # 不跟随 symlink/junction
  → fstat(fd) → 记录 object_identity        # 绑定打开后的对象身份
  → 验证 object_identity 合法（若平台不可靠，跳过，仅用 content）
  → READ（从已验证的 fd 读，不二次 resolve 路径）
  → 读后 fstat(fd) 复验 object_identity     # 检测读取期间替换
  → 读后 os.stat(path, nofollow) 复验路径仍指向同一对象  # 检测路径替换
  → 计算 content_identity（SHA256 实际内容）
  → 比对 expected_digest
      ├─ 全部一致 → 标记 MATCH，可产生 VERIFIED 结论
      ├─ object_identity 变化 → CHANGED（对象被替换）
      ├─ 内容摘要不匹配 → CHANGED
      ├─ 读失败/权限/损坏 → UNREADABLE
      └─ 平台不支持可靠身份 → 降级为 content-only 验证，NOT_VERIFIED 或 CHANGED
```

**硬规则**：
- **不满足条件时标记 UNREADABLE 或 CHANGED，禁止继续产生 VERIFIED 结论。**
- **不支持可靠身份验证的文件系统**（如某些 UNC）：**降级为 content-only 验证**，但明确标记"身份不可靠"，其 VERIFIED 结论受此限制约束。
- inode 重用防御：object_identity 相同时，**必须额外比对 content_identity 或 mtime**，否则不判 MATCH（防止 inode 重用导致旧身份命中新内容）。

---

## 5. D05-05｜版本发布与读取完整性强制校验

### 5.1 缺陷认定

DESIGN-04 §4 的 `read_current()` 把 manifest 校验写成"可选"，且未定义 manifest 自身校验、CURRENT 损坏处理、旧版本清理策略。

### 5.2 封闭方案：强制校验

```python
@dataclass(frozen=True)
class PublishManifest:
    schema_version: str       # "usit-design-publish-v2"（本工单 bump）
    version: str              # 版本号（时间戳 + digest8）
    files: dict[str, dict]    # {文件名: {sha256, bytes}}
    manifest_digest: str      # 对 manifest 内容的 SHA256（自校验，见下）
```

### 5.3 发布协议（强制完整性）

```python
def publish(out_dir, business_relations: bytes, test_design: bytes, version: str):
    """不可变版本目录 + 原子 CURRENT 指针。manifest 自身带校验摘要。"""
    br_digest = hashlib.sha256(business_relations).hexdigest()
    td_digest = hashlib.sha256(test_design).hexdigest()
    # 1. 写不可变版本目录（.tmp 前缀，写完 rename）
    tmp_dir = f"{out_dir}/versions/{version}.tmp"
    _write_files(tmp_dir, business_relations, test_design)
    # 2. 写 manifest（含两文件 SHA256 + manifest 自校验摘要）
    manifest_body = {"schema_version": "usit-design-publish-v2", "version": version,
                     "files": {"business_relations.json": {"sha256": br_digest, "bytes": len(business_relations)},
                               "test_design.json": {"sha256": td_digest, "bytes": len(test_design)}}}
    manifest_digest = hashlib.sha256(json.dumps(manifest_body, sort_keys=True).encode()).hexdigest()
    manifest_body["manifest_digest"] = manifest_digest
    _write_manifest(tmp_dir, manifest_body)
    # 3. 原子发布：tmp 目录整体 rename 为正式版本目录
    os.replace(tmp_dir, f"{out_dir}/versions/{version}")
    # 4. 原子切换 CURRENT 指针（先写临时指针再 rename）
    _atomic_update_pointer(out_dir, "CURRENT", version)
```

### 5.4 读取协议（强制校验，不允许 optional）

```python
def read_current(out_dir) -> tuple[bytes, bytes]:
    """
    强制完整性校验。任一失败即抛错，不返回半成品。
    """
    version = _read_pointer(out_dir, "CURRENT")        # 读 CURRENT
    if version is None:
        raise PublishError("CURRENT_MISSING_OR_CORRUPTED")   # 不猜测版本
    vdir = f"{out_dir}/versions/{version}"
    if not os.path.isdir(vdir):
        raise PublishError("VERSION_DIR_MISSING")
    # 1. 读 manifest 并校验 manifest_digest
    manifest = _read_manifest(vdir)
    _verify_manifest(manifest)                          # manifest 自身校验
    # 2. 逐个文件校验 SHA256
    br = _read_file(vdir, "business_relations.json")
    td = _read_file(vdir, "test_design.json")
    if hashlib.sha256(br).hexdigest() != manifest["files"]["business_relations.json"]["sha256"]:
        raise PublishError("BR_DIGEST_MISMATCH")
    if hashlib.sha256(td).hexdigest() != manifest["files"]["test_design.json"]["sha256"]:
        raise PublishError("TD_DIGEST_MISMATCH")
    return br, td
```

### 5.5 强制校验规则（工单要求逐条）

1. 每个版本目录**不可变**（发布后禁止修改）。
2. manifest 包含两个 JSON 的 SHA256（强制）。
3. manifest 自身有 `manifest_digest` 自校验（强制）。
4. CURRENT 指针必须指向**完整且已验证**的版本。
5. `read_current()` **强制校验，不允许 optional**。
6. 任一文件缺失 / 摘要不匹配 / 版本不一致 → **整个版本拒绝读取**。
7. 发布崩溃不得暴露半成品（.tmp 目录不参与读取）。
8. Windows / POSIX 原子替换差异（见 §5.6）。
9. 旧版本保留 + 清理策略（见 §5.7）。
10. **不允许自动选择"最新目录"代替 CURRENT**。
11. **CURRENT 损坏时不得自动猜测版本**（报错）。
12. 所有失败返回明确错误状态（`PublishError` 带原因码）。

### 5.6 Windows / POSIX 原子替换差异

| 操作 | POSIX | Windows |
|------|-------|---------|
| 文件 `os.replace` | 原子 | 原子（`MoveFileEx` with REPLACE_EXISTING）|
| 目录 `os.replace`（tmp→正式）| 原子（rename）| **需目标不存在**，否则失败 |
| CURRENT 指针替换 | 原子 | 原子 |

**规则**：目录 rename 前确保目标不存在（先删残留）；CURRENT 指针用"写临时 + replace"实现原子切换，跨平台一致。

### 5.7 旧版本保留及清理策略

- 旧版本目录**默认保留**（可追溯、可回滚）。
- 清理策略：保留最近 N 个版本（默认 10），超出自动删除最旧（仅在发布成功后执行清理，且不删 CURRENT 指向的版本）。
- 清理失败不影响发布（非关键路径，记日志即可）。

---

## 6. D05-06｜执行报告及交付物哈希完整性

### 6.1 五份新增文档 SHA256

（实际计算值见交付物 `USIT-STAGE-B-DESIGN-05-HASH-MANIFEST.md`，本处声明算法与规则）

| 项 | 说明 |
|----|------|
| 哈希算法 | SHA-256（hex 小写 64 字符）|
| 生成时间 | 2026-10-30（GMT+8）|
| 覆盖文件 | 5 份 DESIGN-05 新增文档 |
| 字节数 | 逐文件记录（见 MANIFEST）|

### 6.2 哈希语义边界

- 哈希**只证明内容一致性**（同一内容 → 同一哈希），**不能证明报告作者身份**。
- 冻结源码文件哈希（`scanner.py=58ba5f25…` 等）与新增报告文件哈希**严格区分**，不混列。
- 原始 DESIGN-01/02/03/04 **未修改**（本工单只新增，不覆盖）。

### 6.3 无法计算的哈希

若某文件无法实际计算（不应发生，但规则先行），标记 `NOT_VERIFIED`，**不得编造**。

---

## 7. 全局一致性审查（工单第四部分）

| # | 审查项 | 结论 |
|---|--------|------|
| 1 | DESIGN-01→05 字段一致性 | 一致（跨文档字段签名统一）|
| 2 | node_id/relation_id/evidence_id/test_case_id/authorization_id 唯一性 | 一致（均内容派生确定性 ID）|
| 3 | VERIFIED_ORACLE/INFERRED_ORACLE/NO_ORACLE 判定 | 一致（D04-06 已定，D05 未改）|
| 4 | VERIFIED_FACT/STATIC_INFERENCE/AI_HYPOTHESIS/UNKNOWN/CONFLICT 证据要求 | 一致（VERIFIED_FACT 仍禁止设计阶段产生）|
| 5 | STALE/INVALIDATED/EXPIRED/REVOKED/CONSUMED 状态边界 | 一致（D04-08 五态 + D05-03 消费态补充）|
| 6 | BusinessRelation→EvidenceReference→TestCase→ExecutorPlan→ExecutionAuthorization 引用链 | 完整 |
| 7 | plan() 纯计划 / execute() 留桩 | 保持 |
| 8 | 八类测试维度完整 | 保持 |
| 9 | is_only_self/is_secret/family_ids 独立建模 | 保持 |
| 10 | 双工作区三文件分叉处理方案 | 仍有效（未擅自处理）|
| 11 | F1/P0.8/F2 门禁 | 仍保持原状态 |
| 12 | 无"设计推断冒充运行事实" | 确认无 |

---

## 8. 实施准入结论

**PASS-WITH-ISSUES —— 具备 IMPLEMENT-01 设计准入条件。**

| 门槛 | 状态 |
|------|------|
| 授权防重放闭环（D05-03）| ✅ 完整状态机 + 故障矩阵 |
| 文件身份跨平台可靠（D05-04）| ✅ 四层身份 + 平台矩阵 + 降级规则 |
| 发布读取强制校验（D05-05）| ✅ 强制校验 + manifest 自校验 |
| 摘要/签名严格分离（D05-02）| ✅ 默认 DENY 未签名 |
| 关系基数无矛盾（D05-01）| ✅ 五要素矩阵 |

**剩余非阻断项**：① 密钥基础设施尚未引入（签名默认 DENY，IMPLEMENT 阶段不实现签名）；② `analyze_file` 行为一致性 NOT_VERIFIED（需 IMPLEMENT 前单独验证）。

**STOP — 等待用户独立审查并明确授权 IMPLEMENT-01。本工单不包含代码实现授权，不解除任何历史门禁。**

---

## 9. 交付物索引

| 文件 | 内容 |
|------|------|
| `USIT-STAGE-B-DESIGN-05.md` | 本文（六项封闭 + 全局审查）|
| `USIT-STAGE-B-DESIGN-05-EXECUTION-REPORT.md` | 执行报告 |
| `USIT-STAGE-B-DESIGN-05-ISSUES.md` | 问题清单 |
| `USIT-STAGE-B-DESIGN-05-HASH-MANIFEST.md` | 交付物哈希清单 |
| `USIT-DESIGN-04-05-DIFF.md` | DESIGN-04 → 05 差异 |
