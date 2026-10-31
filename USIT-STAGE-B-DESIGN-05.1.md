# USIT-STAGE-B-DESIGN-05.1｜安全状态机与交付完整性最终修订

> 阶段 B · DESIGN-05.1 · READ-ONLY AUDIT + DESIGN DOCUMENT REVISION
> 作者：USIT 主开发 · 生成：2026-10-30（GMT+8）
> 上游：DESIGN-01 / DESIGN-02 / DESIGN-03 / DESIGN-04 / DESIGN-05
> 工作区：`C:\Users\Administrator\WorkBuddy\2026-10-15-22-09-47\usit_p0`
> 状态：**STOP — 等待用户独立审查**（未授权 IMPLEMENT-01）

---

## 0. 任务定位

针对 DESIGN-05 独立审查发现的 5 项遗留问题做最后一轮定向修订。本工单**只修订设计文档**，不写代码、不运行测试、不创建 IMPLEMENT 模块、不实际创建密钥/签发授权/执行授权操作。

> 文档设计通过 ≠ 代码实现通过。所有伪代码为设计级契约。

---

## 1. D051-01｜授权消费状态机：防重放与崩溃恢复（最高优先级）

### 1.1 三个独立概念（不得互相替代）

```python
authorization_use_count    # 已消耗的授权次数（与 max_use_count 比较）
execution_attempt_count    # 已发起的执行尝试次数（每次尝试 +1，与 use_count 无关）
execution_result           # SUCCESS / FAILURE / UNKNOWN（本次执行结果）
```

**三者语义独立**：一次授权可产生多次执行尝试（重试场景），每次尝试有独立结果；`use_count` 只在授权被真正"消耗"时递增。

### 1.2 修订状态机

```
正常路径：ISSUED → RESERVED → CONSUMED
```

异常状态：`REJECTED` / `EXPIRED` / `REVOKED` / `INVALIDATED` / `INDETERMINATE`

```python
AUTHORIZATION_STATES = {
    "ISSUED",        # 已签发，未消费
    "RESERVED",      # 已预留执行权（use_count 已原子递增，但执行结果未确认）
    "CONSUMED",      # 已消费（执行成功且已记录）
    "REJECTED",      # 校验失败拒绝
    "EXPIRED",       # 超过有效期
    "REVOKED",       # 主动撤销
    "INVALIDATED",   # 依赖证据/指纹变化
    "INDETERMINATE", # 无法确认实际执行是否发生
}
```

### 1.3 关键规则（工单 §3.2 逐条落实）

1. **RESERVED 不能因进程崩溃自动回到可重试状态**——崩溃后状态残留 RESERVED，下次发现 RESERVED 且无对应执行记录，进入 INDETERMINATE，而非自动回 ISSUED。
2. **无法确认实际执行是否发生时，必须进入 INDETERMINATE**（不是回到 ISSUED，也不是直接 CONSUMED）。
3. **INDETERMINATE 默认禁止再次执行**。
4. **只有存在可靠幂等执行机制 + 可验证执行记录时，才允许定义安全恢复路径**（从 INDETERMINATE 恢复到可执行）。
5. **`max_use_count=1` 时不得通过释放 reservation 无限重复执行**——release 只允许在"明确未执行"时发生，且受 INDETERMINATE 门禁约束。
6. **每个授权消费操作必须有唯一 attempt_id**（`attempt_id = _stable_id("attempt", authorization_id, nonce, attempt_seq)`）。
7. **nonce 必须具备明确唯一性作用域 + 持久化防重放记录**（见 §1.5）。
8. **授权消费状态必须具备原子更新 + 持久化规则**（写 + fsync，见 §1.6）。
9. **状态存储损坏、回滚、替换、锁失败均 DENY**。
10. **未能确认状态一致性时不得继续执行**。

### 1.4 状态转换合法性矩阵

| 从 → 到 | 合法性 | 条件 |
|---------|--------|------|
| ISSUED → RESERVED | 合法 | use_count < max_use_count，原子递增成功 |
| ISSUED → REJECTED | 合法 | 校验失败 |
| ISSUED → EXPIRED | 合法 | now >= valid_until |
| ISSUED → REVOKED | 合法 | 主动撤销 |
| ISSUED → INVALIDATED | 合法 | 证据/指纹变化 |
| RESERVED → CONSUMED | 合法 | 执行成功 + 已持久化记录 |
| RESERVED → INDETERMINATE | 合法 | 无法确认执行结果（崩溃/超时）|
| RESERVED → ISSUED | **非法**（默认）| 仅当"明确未执行"且存在可验证记录时，经安全恢复路径才允许 |
| CONSUMED → 任意 | **非法** | 终态 |
| INDETERMINATE → CONSUMED | 合法（仅安全恢复）| 可靠幂等 + 可验证执行记录证明已执行 |
| INDETERMINATE → ISSUED | 合法（仅安全恢复）| 可靠幂等 + 可验证记录证明未执行 |
| INDETERMINATE → 其它 | **非法** | 默认禁止再次执行 |
| REJECTED/EXPIRED/REVOKED/INVALIDATED → 任意 | **非法** | 终态，需重新签发 |

### 1.5 nonce 唯一性与持久化防重放

- nonce = `secrets.token_hex(16)`（128 位熵），作用域为**整个授权生命周期**（全局唯一）。
- 持久化防重放记录：单独维护 `nonce_registry`（已使用 nonce 的持久化集合）。
- 校验顺序：先查 nonce 是否已使用 → 已使用则 DENY（重放）→ 未使用则登记并继续。

### 1.6 原子更新与持久化

```python
def consume(state_path, auth_id, nonce, max_use) -> str:
    """原子消费：排他锁 + 读改写 + fsync。返回状态。"""
    fd = _open_state(state_path)
    if fd is None: return "DENY_LOCK_UNAVAILABLE"
    try:
        if not _lock_exclusive(fd): return "DENY_LOCK_UNAVAILABLE"
        data = _read_json_fd(fd)
        if _is_corrupted(data): return "DENY_CORRUPTED"
        if nonce in data.get("nonces", set()): return "DENY_REPLAY"
        used = data.get(auth_id, 0)
        if used >= max_use: return "DENY_USE_EXCEEDED"
        data[auth_id] = used + 1
        data["nonces"].add(nonce)
        _write_json_fd_fsync(fd, data)   # 写 + fsync 后才算持久化
        return "RESERVED"
    finally:
        _unlock_close(fd)
```

### 1.7 崩溃恢复矩阵（工单 §3.3 逐条）

| 崩溃位置 | 恢复要求 |
|---------|---------|
| RESERVED 写入前 | 不得产生执行（未持久化 = 未预留）|
| RESERVED 写入后、执行前 | 默认不允许直接重放；发现 RESERVED 无执行记录 → INDETERMINATE |
| 执行期间 | 结果未知 → INDETERMINATE |
| 执行成功后、记录前 | 不得假定未执行 → INDETERMINATE（保守）|
| CONSUMED 持久化后 | 不得再次消费 |
| 状态文件损坏 | DENY |
| 状态存储回滚 | DENY |

### 1.8 exactly-once 边界声明

> **如果执行系统与授权状态存储无法形成同一个事务，就不能声称已实现 exactly-once。**

**DESIGN-05.1 明确采用 `at-most-once` 语义**：默认保证"授权最多被消费一次"，不保证"执行恰好一次"。

| 策略 | 采用 | 说明 |
|------|------|------|
| at-most-once | ✅ 默认 | 防重放（授权不重复消费）；执行可能 0 或 1 次 |
| 幂等重试 | 可选（安全恢复路径）| 仅当执行机制幂等 + 有可验证执行记录 |
| 人工恢复 | 兜底 | INDETERMINATE 状态由人工判定 |

**无法保证的边界**：当执行系统与授权存储非同一事务时，`RESERVED` 后崩溃存在"授权已预留但执行未发生"的窗口，此窗口内的执行次数为 0，但授权 use_count 已 +1——这是 at-most-once 的固有边界，如实声明。

---

## 2. D051-02｜文件身份与内容验证分级

### 2.1 缺陷认定

DESIGN-05 §4 仍存在"内容哈希匹配 → 身份可信"的模糊表述。**必须废除"内容哈希匹配直接等同于文件身份可信"。**

### 2.2 四个独立身份维度

```python
PATH_IDENTITY      # 规范化路径身份（可读定位，非安全身份）
OBJECT_IDENTITY    # 文件系统对象身份（NTFS FileIndex / POSIX st_dev:st_ino）
CONTENT_IDENTITY   # 文件内容摘要身份（SHA256，最可靠但昂贵）
PROJECT_IDENTITY   # 项目根目录 + 项目指纹身份
```

### 2.3 独立验证状态（不得压缩成一个 VERIFIED 布尔值）

```python
VERIFICATION_STATES = {
    "PATH_VERIFIED",            # 路径规范化 + 无 reparse
    "OBJECT_IDENTITY_VERIFIED", # 对象身份验证通过
    "CONTENT_MATCH",            # 内容摘要匹配
    "CONTENT_CHANGED",          # 内容摘要不匹配
    "PROJECT_IDENTITY_VERIFIED",# 项目根身份验证通过
    "IDENTITY_UNAVAILABLE",     # 身份信息缺失/不可靠
    "NOT_VERIFIED",             # 未执行验证
}
```

**核心裁决**：

> **`CONTENT_MATCH` 只能证明"实际读取的字节与预期摘要匹配"，不能单独证明 OBJECT_IDENTITY 或 PROJECT_IDENTITY。**

内容哈希匹配 ≠ 对象身份可信。两者必须独立判定、独立记录。

### 2.4 平台能力矩阵

| 平台 | OBJECT_IDENTITY 可靠性 | 处理 |
|------|----------------------|------|
| Windows 本地 NTFS | `FileIndex`+`VolumeSerialNumber` 稳定 | 可靠 |
| Windows UNC 网络路径 | 不稳定（SMB 可能不暴露）| **降级 IDENTITY_UNAVAILABLE**，仅 content 验证 |
| POSIX 本地文件系统 | `st_dev:st_ino` 可靠，但 inode 可重用 | 可靠 + inode 重用防御 |
| 网络文件系统（NFS 等）| 不稳定 | 降级 IDENTITY_UNAVAILABLE |
| symlink/junction/reparse | 拒绝（open_no_follow + reject_reparse_components）| 拒绝 |
| 文件复制 | 新对象身份 | 视为新文件，旧引用失效 |
| 文件移动/重命名（同卷）| 对象身份不变，path 变 | 需重新解析 path 别名 |
| 文件移动（跨卷）| 新对象身份 | 视为新文件 |
| inode 重用 | 旧 inode 命中新内容 | 必须 content/mtime 双验防重用 |
| 身份信息缺失/不可靠 | — | **明确降级为 IDENTITY_UNAVAILABLE，不假装完成身份验证** |

### 2.5 安全读取协议（顺序固定）

```
1. 验证项目根目录身份（PROJECT_IDENTITY）→ 失败则终止
2. 规范化并检查目标路径（PATH_VERIFIED）→ 拒绝 reparse/../
3. 打开目标文件（open_no_follow）
4. 验证打开后的对象身份（OBJECT_IDENTITY_VERIFIED，或 IDENTITY_UNAVAILABLE）
5. 从已验证文件句柄读取（不二次 resolve 路径）
6. 计算实际内容摘要（CONTENT_IDENTITY）
7. 检查读取过程中的异常变化（fstat 复验 + path 复验）
8. 生成分级验证结果（多项状态并列，非单一布尔）
9. 证据等级不足时禁止产生 VERIFIED_FACT
```

**规则**：若文件系统不支持可靠对象身份验证 → 明确降级为 `IDENTITY_UNAVAILABLE`，**不得假装完成身份验证**，且其结论受此限制约束。

### 2.6 证据等级与允许结论对应表

| 验证状态组合 | 允许结论 |
|-------------|---------|
| PATH_VERIFIED + OBJECT_IDENTITY_VERIFIED + CONTENT_MATCH + PROJECT_IDENTITY_VERIFIED | 可产生 STATIC_INFERENCE（内容+身份双可信）|
| PATH_VERIFIED + CONTENT_MATCH（缺对象身份）| 仅 STATIC_INFERENCE，且标注"对象身份 IDENTITY_UNAVAILABLE" |
| CONTENT_MATCH 但 PROJECT_IDENTITY 未验证 | 降级，标注项目身份未验证 |
| CONTENT_CHANGED | CHANGED，不得产生有效结论 |
| IDENTITY_UNAVAILABLE + CONTENT_MATCH | 有限可信（content-only），明确标注限制 |
| 任一未验证（NOT_VERIFIED）| 不得产生 VERIFIED_FACT |

**VERIFIED_FACT 在任何情况下都禁止由本阶段产生**（沿用 `understanding_model` 硬约束）。

---

## 3. D051-03｜版本发布完整性与威胁模型

### 3.1 威胁模型（区分意外损坏 vs 恶意篡改）

**A. 意外损坏**：
- 文件截断、磁盘错误、写入中断、文件丢失、摘要不匹配。

**B. 恶意篡改**：
- JSON 被修改、manifest 被修改、CURRENT 被替换、JSON 和 manifest 同时被修改并重算摘要、旧版本回滚、未授权用户替换发布目录。

### 3.2 安全能力边界（不得把 SHA256 当防篡改/身份认证）

| 机制 | 提供 | 不提供 |
|------|------|--------|
| SHA256 | 检测内容变化 | 防恶意篡改、发布者身份 |
| manifest SHA256 | 检测 manifest 内容变化 | 证明来源身份 |
| 数字签名 | 验证签发身份 + 内容完整性 | 需可信密钥体系 |
| 受保护存储 | 限制未授权文件替换 | — |
| 版本单调计数 / 可信记录 | 识别回滚 | — |

### 3.3 两个运行配置

**模式 A：本地完整性模式**
- 目标：防意外损坏。
- 手段：SHA256 校验。
- 明确**不抵御**具有发布目录写权限的恶意篡改者。

**模式 B：可信发布模式**
- 必须具备可信签发者。
- manifest 必须有真实签名验证。
- 签名密钥**不得**与待保护文件放在同一可被任意修改的位置。
- 签名验证失败必须拒绝读取。
- **不具备可信密钥基础设施时不得启用。**

**当前阶段（B）仅启用模式 A**；模式 B 需未来独立工单引入密钥基础设施。

### 3.4 恶意篡改检测与拒绝规则

| 篡改 | 检测 | 拒绝 |
|------|------|------|
| JSON 被修改 | JSON SHA256 不匹配 manifest | 拒绝 |
| manifest 被修改 | manifest_digest 不匹配（模式 A）或签名失败（模式 B）| 拒绝 |
| CURRENT 被替换 | CURRENT 指向的版本验证失败 | 拒绝 |
| JSON+manifest 同时改并重算摘要 | 模式 A 无法检测（如实声明）；模式 B 签名检测 | 模式 A 不抵御，模式 B 拒绝 |
| 旧版本回滚 | 版本单调计数/可信记录（模式 B）| 模式 B 检测，模式 A 不抵御 |
| 未授权替换发布目录 | 受保护存储（模式 B）| 模式 B 检测 |

**回滚检测规则**：版本号采用单调递增（时间戳 + digest8），模式 B 额外维护可信的"当前版本单调计数"记录，检测到版本号倒退 → 拒绝。

---

## 4. D051-04｜跨平台版本发布与故障恢复

### 4.1 发布协议硬规则（工单 §六 逐条）

1. 临时目录必须与目标版本目录位于**同一文件系统**，否则拒绝原子发布。
2. 版本目录名称必须**唯一且不可覆盖**。
3. 目标版本已存在时**必须拒绝覆盖**。
4. 两个 JSON 文件必须先完整写入并验证。
5. manifest 必须在两个 JSON 验证后生成。
6. 发布前必须重新验证所有文件摘要。
7. CURRENT 只能指向已验证的完整版本。
8. CURRENT 损坏时拒绝自动猜测最新版本。
9. 发布失败时旧 CURRENT 必须保持有效。
10. **不得通过先删除旧 CURRENT 再写新 CURRENT 的方式发布**。

### 4.2 发布协议（跨平台统一逻辑）

```python
def publish(out_dir, br: bytes, td: bytes, version: str):
    # 1. 校验版本目录唯一（存在则拒绝覆盖）
    vdir = f"{out_dir}/versions/{version}"
    if os.path.exists(vdir): raise PublishError("VERSION_EXISTS")
    # 2. 临时目录必须与目标同文件系统（同一父目录）
    tmp = vdir + ".tmp"
    if os.path.exists(tmp): shutil.rmtree(tmp)   # 清理上次残留
    os.makedirs(tmp)
    # 3. 写两个 JSON（完整写入后验证摘要）
    _write(tmp, "business_relations.json", br)
    _write(tmp, "test_design.json", td)
    _verify_digests(tmp, br, td)                  # 发布前重新验证
    # 4. 生成 manifest（两个 JSON 验证后）
    manifest = _build_manifest(br, td, version)   # 含 manifest_digest
    _write_manifest(tmp, manifest)
    # 5. 原子发布：tmp 目录整体 rename 为正式版本目录
    os.replace(tmp, vdir)
    # 6. 原子切换 CURRENT（写临时指针 + replace，不先删旧指针）
    _atomic_update_pointer(out_dir, "CURRENT", version)
```

### 4.3 Windows vs POSIX 差异矩阵

| 操作 | POSIX | Windows |
|------|-------|---------|
| 临时文件写入 | 普通写 + fsync | 普通写 + FlushFileBuffers |
| 文件内容持久化 | `fsync(fd)` | `_commit(fd)` / `FlushFileBuffers` |
| 目录元数据持久化 | `fsync` 目录 fd | 无直接等价，靠文件 fsync |
| 原子替换 | `os.replace`（rename 原子）| `os.replace`（MoveFileEx REPLACE_EXISTING）|
| 目录 replace（tmp→正式）| 目标不存在才成功 | 目标不存在才成功 |
| 文件锁 | `fcntl.flock` | `msvcrt.locking` |
| 并发发布 | 锁 + 版本目录唯一性 | 同左 |

### 4.4 崩溃恢复矩阵

| 故障 | 处理 |
|------|------|
| 磁盘空间不足 | 写失败 → 清理 tmp，CURRENT 不变（旧版本有效）|
| 文件写入失败 | 同上 |
| manifest 写入失败 | 清理 tmp，CURRENT 不变 |
| rename/replace 失败 | 目标不存在才 rename；失败则清理 tmp，CURRENT 不变 |
| CURRENT 更新失败 | 旧 CURRENT 保持有效（临时指针未替换）|
| 进程崩溃（任意点）| tmp 残留（下次清理），正式版本目录未创建或已创建但 CURRENT 未切，旧版本可读 |
| 版本目录损坏 | 该版本拒绝读取，CURRENT 若指向它则拒绝（不猜）|
| 多进程并发发布 | 锁 + 版本目录唯一性防冲突 |

### 4.5 旧版本清理规则

- 不删除 CURRENT 指向版本。
- 不删除正在被读取的版本。
- 不删除审计保留版本。
- 清理失败不影响当前版本读取（非关键路径）。
- 清理时机：仅在发布成功后，保留最近 N 个（默认 10）。

### 4.6 read_current() 强制规则（工单 §读取端）

```python
def read_current(out_dir):
    # 1. 读取 CURRENT
    # 2. 校验指针格式（非空、无路径穿越、合法版本名）
    # 3. 定位指定版本目录
    # 4. 校验 manifest（manifest_digest 匹配）
    # 5. 校验两个 JSON 的摘要（逐一比对 manifest 记录）
    # 6. 校验版本标识一致（CURRENT 指向的版本号 == manifest 里的 version）
    # 7. 所有检查通过后才返回数据
    # 任一步失败 → 整个版本拒绝读取（raise PublishError）
```

**任一步失败，整个版本拒绝读取**，不返回半成品。

---

## 5. D051-05｜五份文档哈希闭环（单向清单）

### 5.1 禁止循环引用

- 不得让 EXECUTION-REPORT 依赖 HASH-MANIFEST 的最终哈希。
- 不得让 HASH-MANIFEST 依赖 EXECUTION-REPORT 的最终哈希。

### 5.2 单向哈希清单设计

```python
# 单向依赖（无环）：
#   前四份文件 → HASH-MANIFEST 记录其 SHA256
#   HASH-MANIFEST 自身 SHA256 → 仅由外部审计记录，不回写
#   EXECUTION-REPORT → 只记录 HASH-MANIFEST 的文件名 + 生成时间，不记录其摘要
```

- **HASH-MANIFEST 是前四份文件的唯一哈希索引**。
- **执行报告记录 HASH-MANIFEST 的文件名和生成时间，不记录其自身摘要**。
- **HASH-MANIFEST 自身摘要由外部审计记录单独保存**。
- 所有空值必须消除，无法计算时明确标记 NOT_VERIFIED，不得伪造。

### 5.3 最终生成顺序

1. 完成 DESIGN-05.1（本文）。
2. 完成 ISSUES。
3. 完成 DIFF。
4. 完成 EXECUTION-REPORT（不含最终清单哈希）。
5. 对前四份文件计算 SHA256。
6. 生成 HASH-MANIFEST（记录前四份的哈希）。
7. 计算 HASH-MANIFEST 自身 SHA256（仅外部审计用，不回写）。
8. **不再互相回写**（消除循环）。

### 5.4 循环依赖消除证明

- EXECUTION-REPORT 正文不写任何文件的 SHA256（哈希全部集中在 HASH-MANIFEST）。
- HASH-MANIFEST 只写前四份文件的哈希，不写自身哈希。
- 前四份文件均不写 HASH-MANIFEST 的哈希。
- 结论：**无环，单向引用成立。**

---

## 6. 全局一致性复核（工单 §八）

| # | 审查项 | 结论 |
|---|--------|------|
| 1 | DESIGN-01→05.1 关键字段一致性 | 一致 |
| 2 | 五类记录引用链完整 | 完整 |
| 3 | Oracle 三态及统计隔离 | 一致 |
| 4 | 证据可信度状态 | 一致（D051-02 细化为多维度）|
| 5 | STALE/INVALIDATED/EXPIRED/REVOKED/CONSUMED/INDETERMINATE 边界 | 一致（D051-01 补 INDETERMINATE）|
| 6 | plan() 纯计划 | 保持 |
| 7 | execute() 留桩 | 保持 |
| 8 | 八类测试维度完整 | 保持 |
| 9 | is_only_self/is_secret/family_ids 独立建模 | 保持 |
| 10 | A/B 三文件分叉未擅自合并 | 保持 |
| 11 | F1/P0.8/F2 门禁不变 | 保持 |
| 12 | 无设计推断冒充运行验证 | 确认无 |

---

## 7. IMPLEMENT-01 准入结论

**PASS-WITH-ISSUES —— 具备 IMPLEMENT-01 设计准入条件。**

| 关键项 | 状态 |
|--------|------|
| 授权消费无自动重放漏洞（D051-01）| ✅ at-most-once + INDETERMINATE 门禁 |
| 文件身份四维独立（D051-02）| ✅ CONTENT_MATCH ≠ 身份可信 |
| 发布威胁模型完整（D051-03）| ✅ 双模式 + 能力边界 |
| 跨平台发布恢复完整（D051-04）| ✅ 崩溃矩阵 + read_current 强制校验 |
| 哈希单向清单（D051-05）| ✅ 无循环引用 |

**剩余非阻断项**：① 密钥基础设施未引入（模式 B 未启用）；② `analyze_file` 行为一致性 NOT_VERIFIED；③ A/B 分叉未合并。

**STOP — 等待用户独立审查。不得开始 IMPLEMENT-01，不得修改源码，不得解除任何历史门禁。本工单不包含任何代码实现授权。**

---

## 8. 交付物索引

| 文件 | 内容 |
|------|------|
| `USIT-STAGE-B-DESIGN-05.1.md` | 本文（五项封闭 + 全局复核）|
| `USIT-STAGE-B-DESIGN-05.1-ISSUES.md` | 问题清单 |
| `USIT-STAGE-B-DESIGN-05.1-DIFF.md` | DESIGN-05 → 05.1 差异 |
| `USIT-STAGE-B-DESIGN-05.1-EXECUTION-REPORT.md` | 执行报告 |
| `USIT-STAGE-B-DESIGN-05.1-HASH-MANIFEST.md` | 哈希清单（单向）|
