# USIT 项目进展报告

> 通用软件理解与智能测试系统 · 阶段 A（深化只读语义理解）
> 目标项目：家庭相册（`E:\家庭相册`，Android + Node.js/Express + MySQL）
> 生成时间：2026-10-30

---

## 一、这一阶段做了什么

USIT 的总纲是「让 AI 真正理解、检查、验证、维护一个软件项目」，而不是「生成更多 JSON / 让扫描器通过更多测试」。S1-C 文件理解层只是基础设施。

本阶段（A）把理解能力从**符号级**（symbols/calls/imports）推进到了**业务语义级**：从需求文档、数据库设计文档、API 文档和源码里，提取出软件的**功能、数据、权限、接口、状态**，并对「文档声称 vs 代码实现」做交叉印证。

核心交付物是一个新的只读适配器 `usit/business_semantics.py`（`understand` 命令），产出**五层结构**：

| 层 | 内容 | 本次提取结果 |
|----|------|-------------|
| `entities` | 数据库表和字段 | 6 张主表 |
| `features` | 功能 + 状态(✅/⏳) + 优先级(P0-P3) + 所属模块 | 129 个功能 |
| `endpoints` | API 接口 + 鉴权方式 | 84 个接口 |
| `discrepancies` | 文档 vs 代码的差异（三种类型） | 2 条差异 |
| `field_gaps` | 差异的「该改哪里」定位锚点 | 1 个字段，18 个锚点 |

关键原则：**证据优先**。每条结论都带 `file:line` 证据和置信度分级（`VERIFIED_FACT` / `STATIC_INFERENCE` / `UNKNOWN` / `CONFLICT`），AI 的推断和已证实的事实严格区分。

---

## 二、最重要的一个发现：三重证据链

系统自动交叉印证后，发现了一个「**文档声称已完成、但代码从未实现**」的完整证据链：

**`contents.is_only_self`（「仅自己可见」）字段缺失**

**证据 1 —— 数据库文档定义了字段**
`数据库设计文档.md:140`：`| is_only_self | TINYINT(1) DEFAULT 0 |`

**证据 2 —— 需求文档把该功能标记为已完成**
`家庭APP需求文档.md:183`：`| 仅自己 | 发布者可见，自动取消家庭勾选 | P0 | ✅ |`

**证据 3 —— 代码里这个字段零引用**
全项目（android + server）grep，`is_only_self` 及所有变体（`onlySelf` / `only_self` / `isOnlySelf`）**出现 0 次**。
对比：同表的兄弟字段 `is_secret` 出现 18 次，且全部正常工作。

> 结论：需求文档和数据库文档都承诺了「仅自己可见」这个 P0 功能，但代码里没有任何地方实现它。「仅自己」和「秘密」本应是两个独立语义，代码里却只实现了「秘密」，用 `is_secret` 一并顶替了「仅自己」。

---

## 三、差异清单（discrepancies）

### 差异 1（置信度：CONFLICT）
```
PRD 标记「仅自己」为 DONE(P0)，但代码全项目零引用其对应字段 'is_only_self'。
证据：家庭APP需求文档.md:183（模块「2 可见范围」）
```

### 差异 2（置信度：STATIC_INFERENCE）
```
权限字段 'contents.is_only_self' 在文档中有定义，但从未被任何源码引用。
证据：数据库设计文档.md（表 contents）
```

---

## 四、缺口定位（field_gaps）：该改哪里

对 `is_only_self`，系统自动反向定位了 18 处「本应引用它」的代码位置（`is_secret` 的兄弟引用点）。最关键的几处：

**发布写入链路（缺字段写入）**
```
server/routes/contents.js:13    const { ..., is_secret, family_ids } = req.body;   ← 没解构 is_only_self
server/routes/contents.js:24    (is_secret === 1) ? [] : family_ids              ← 缺「仅自己↔指定家庭」互斥
server/routes/contents.js:28    INSERT INTO contents (..., is_secret)            ← INSERT 列列表缺 is_only_self
server/routes/contents.js:30    [..., is_secret || 0]                            ← 参数列表缺 is_only_self
```

**读取过滤链路（缺可见性过滤）**
```
server/routes/contents.js:71    WHERE c.is_secret = 0        ← 普通用户 Feed 过滤，缺 is_only_self=0
server/routes/contents.js:78    c.is_secret = 0              ← 家庭可见分支，缺 is_only_self=0
server/routes/admin.js:32       LEFT JOIN ... AND c.is_secret = 0   ← 管理员 Feed，缺 is_only_self=0
server/routes/admin.js:24       WHERE is_secret = 0
server/middleware/mediaAuth.js:17   WHERE ... AND c.is_secret = 1   ← 静态资源鉴权
```

**Android 端（DTO 序列化缺字段）**
```
android/.../ContentDtos.kt:38   @SerializedName("is_secret") val isSecret: Int = 0   ← 缺 is_only_self
android/.../Models.kt:67        @SerializedName("is_secret") val isSecret: Int
```

> 即：要真正实现「仅自己可见」，需要在发布（写入 `is_only_self`）、Feed 读取（过滤 `is_only_self=0`）、管理员列表、静态资源鉴权、Android DTO 这几处同步补上字段。

---

## 五、软件理解全景（entities / features）

### 5.1 数据模型（6 张主表）

| 表 | 字段数 | 关键字段 |
|----|--------|---------|
| `users` | 15 | role(admin/member)、is_core、app_lock_* |
| `families` | 6 | core_member_id |
| `family_members` | 5 | is_core |
| `member_relations` | 6 | a_to_b / b_to_a（关系矩阵）|
| `contents` | 12 | type(photo/video/diary)、is_secret、**is_only_self**、view_count |
| `content_families` | 3 | content_id / family_id（可见家庭关系）|

### 5.2 功能全景（129 个功能，节选）

**已完成模块（✅ DONE）**：登录/注册、首页 Feed、发布内容、相册、聊天(单聊/群聊)、家庭/成员、家庭树、家庭账本、个人中心、应用锁、管理员(家庭管理/秘密文件/日志/邀请码/用户详情)、内容详情+点赞评论、用户端秘密文件……

**用户账号模块**：用户注册(P0)、用户角色(P0)、admin 特殊性(P0)、登录方式(P0)、密码修改(P1)、应用锁(P2)、**多账号切换(P0 ⏳ 未完成)**

**家庭模块**：家庭创建(P0)、核心成员(P0)、核心变更(P0)、家庭删除(P0)

**成功指标**：7 个账号全部正常登录(✅)、核心功能无 Crash(⏳)、启动时间(✅)、列表滑动帧率(✅)、权限边界正确(⏳)

### 5.3 接口鉴权（84 个接口）

系统自动识别出每个接口的鉴权方式：`admin`（需管理员）、`auth`（需登录）、无鉴权（仅限 login/register 等公开路径）。当前未发现「本应鉴权却无鉴权」的漏洞接口。

---

## 六、当前能力 vs 项目总纲

对照你最初写的总纲，本阶段落在哪：

| 总纲目标 | 现状 |
|---------|------|
| 自动识别语言/框架/模块 | ✅ 已有（scanner 层）|
| 建立软件知识体系（功能→数据→权限→调用） | 🟡 本阶段完成「功能→数据→权限→状态→证据」第一版 |
| 自动发现问题（带证据定位） | ✅ 本阶段完成（三重证据链 + 18 锚点）|
| 自动生成测试方案 | ⬜ 尚未开始 |
| 授权后执行测试 | ⬜ 尚未开始 |
| AI 协助修复 | ⬜ 只差「给改法」最后一步 |
| 回归测试 | ⬜ 尚未开始 |

---

## 七、下一步

当前闭环已走到「发现问题 → 定位 → 给证据」，还差最后一环：**把 18 个锚点收敛成可执行的修复建议**（对 `is_only_self` 产出具体 diff 草案，仍是只读输出、不落地改代码）。

之后才是「用户授权 → AI 改代码 → 编译 → 测试 → 回归」的执行阶段。
