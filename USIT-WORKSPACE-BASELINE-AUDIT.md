# USIT-WORKSPACE-BASELINE-AUDIT｜双工作区基线审计

> 生成时间：2026-10-30（GMT+8）
> 审计模式：READ-ONLY AUDIT（只读对比，未修改/合并/删除/覆盖/回滚任何文件）
> 审计对象：Desktop 副本（A）vs 正式工作区（B）

---

## 一、两个工作区定义

| 标识 | 路径 | 性质 |
|------|------|------|
| A（Desktop 副本）| `C:\Users\Administrator\Desktop\_整理20261026\杂项\usit_p0` | 阶段 A 实际工作地 |
| B（正式工作区）| `C:\Users\Administrator\WorkBuddy\2026-10-15-22-09-47\usit_p0` | 主线（含 `reports/` 历史 + `AGENTS.md`）|

---

## 二、双工作区逐目录差异

### 2.1 `usit/` 目录（27 个文件）

**分叉（内容不同）3 个文件**：

| 文件 | A（Desktop）| B（正式）| 差异性质 |
|------|------------|---------|---------|
| `cli.py` | `65c1366a…`（189 行）| `879a3735…`（150 行）| A 多 `understand` 命令 + `business_semantics` import |
| `python_worker.py` | `299358a1…`（89 行）| `c23e922f…`（85 行）| A 改 `from usit.scanner` → `from usit._analysis` |
| `scanner.py` | `981de7fc…`（998 行）| `58ba5f25…`（1262 行）| A 拆分出 `_analysis.py`，B 内联逻辑 |

**一致 24 个文件**（含 `_analysis.py`、`business_semantics.py`，因 DESIGN-02 开工前已同步）。

### 2.2 `tests/` 目录（41 个文件）

**100% 一致，零差异。**（含同步进来的 `test_business_semantics.py`）

### 2.3 根目录文档

| 差异 | 说明 |
|------|------|
| `AGENTS.md` | 仅 B 有（正式工作区规范）|
| `USIT-STAGE-B-DESIGN-02.md` + `-EXECUTION-REPORT.md` | 仅 B 有（DESIGN-02 产出）|
| `_workorder_full.md` | 仅 A 有（临时合并工单，非正式交付物）|

---

## 三、3 个分叉文件双向差异分析

### 3.1 `cli.py`（A vs B）

- **A 独有**：`from .business_semantics import analyze_business` + `_understand()` 函数 + `understand` 子命令注册（阶段 A 新增 CLI 入口）
- **B 独有**：无（B 是阶段 A 之前的版本，无 understand 命令）
- **历史状态**：B 是 S1-C 冻结版；A 在 B 基础上加了阶段 A 的 understand 命令
- **判定**：A 是 B 的**增量超集**（A = B + understand 命令）

### 3.2 `python_worker.py`（A vs B）

- **A 独有**：`_analyze` 函数内 `from usit._analysis import analyze_file`（性能优化，避免加载 scanner 重依赖链）
- **B 独有**：`from usit.scanner import analyze_file`（旧版）
- **历史状态**：A 做了性能优化；B 是优化前版本
- **风险**：A 的优化依赖 `_analysis.py` 的存在；B 环境下两者并存（见 §五）

### 3.3 `scanner.py`（A vs B，最大分叉 298 行 diff）

- **A 独有**：`from ._analysis import (EXT_LANG, CATEGORY_BY_EXT, ... analyze_file)` —— 把纯分析逻辑（语言识别/分类/正则/analyze_file）拆到 `_analysis.py`
- **B 独有**：`EXT_LANG`/`CATEGORY_BY_EXT`/`PATTERNS`/`analyze_file` 等**内联定义**在 scanner.py 内（1262 行 vs A 998 行）
- **历史状态**：B 是 S1-C 审计确认的冻结基线 `58ba5f25…`；A 是性能优化重构版
- **判定**：A 是 B 的**重构版**（逻辑等价拆分，未验证行为完全一致）

---

## 四、已同步 6 文件审计（DESIGN-02 开工前同步）

| 文件 | 来源路径（A）| 大小 | SHA256 | 同步时间 |
|------|-------------|------|--------|---------|
| `usit/_analysis.py` | `Desktop\...\usit\_analysis.py` | 18099 B | `a5b4c9e2…f461` | 2026-10-30 22:42 |
| `usit/business_semantics.py` | `Desktop\...\usit\business_semantics.py` | 31025 B | `0c5dcb34…c889` | 2026-10-30 22:42 |
| `tests/test_business_semantics.py` | `Desktop\...\tests\test_business_semantics.py` | 10016 B | `522ceb25…0678` | 2026-10-30 22:42 |
| `USIT-STAGE-B-DESIGN-01.md` | Desktop | — | `8ac207d8…4061` | 2026-10-30 22:42 |
| `USIT-STAGE-B-DESIGN-01-EXECUTION-REPORT.md` | Desktop | — | `386e8f39…dc2f` | 2026-10-30 22:42 |
| `USIT_项目进展报告.md` | Desktop | — | `a04d3c36…fc53` | 2026-10-30 22:42 |

**注**：DESIGN-03 工单明确「不同步、合并、删除、回滚或覆盖任何代码文件」，故本工单**不再做任何同步动作**。上表的同步是 DESIGN-02 开工前（上一工单）已发生的历史事实，本工单仅如实记录。

---

## 五、兼容性审计（工单要求第 4 点）

### 5.1 已同步 3 个代码文件与 B 当前代码的兼容性

| 文件 | import 验证 | 兼容性结论 |
|------|-----------|-----------|
| `business_semantics.py` | ✅ import 成功 | 兼容（依赖的 safety/safeio 在 B 里一致）|
| `_analysis.py` | ✅ import 成功（`analyze_file` 存在）| **有条件兼容**（见 5.2）|
| `test_business_semantics.py` | ✅ 可加载 | 兼容 |

### 5.2 关键兼容性风险：`analyze_file` 重复定义

**实测事实**：
- `scanner.analyze_file is _analysis.analyze_file` → **False**（两个不同对象）
- B 的 `scanner.py:458` 内联定义 `analyze_file`
- A 同步进来的 `_analysis.py` 也定义 `analyze_file`
- B 的 `python_worker.py` 引用 `scanner.analyze_file`（内联版）

**风险判定（NOT_VERIFIED，不擅自判定正确版本）**：
- 两个 `analyze_file` 并存，`scanner.analyze_file`（B 内联）与 `_analysis.analyze_file`（A 拆分）**行为是否一致未验证**。
- 若后续在 B 里混用两者，可能产生不一致结果。
- **本工单不判定哪个正确**，仅如实记录此风险，留待独立审查/后续 IMPLEMENT 阶段处理。

### 5.3 未验证资产声明

同步进来的 `_analysis.py`、`business_semantics.py`、`test_business_semantics.py` **均未经过本工单的验证，不标记为"已验证资产"**。它们的存在状态是"已同步、可 import"，但**功能正确性、与 B 代码的深度兼容性、测试通过性均未验证**（工单禁止运行测试）。

---

## 六、正式工作区 SHA256 基线

（完整清单见交付物 5 的独立文件 `USIT-WORKSPACE-SHA256-BASELINE.txt`，本处记录关键分叉文件与冻结基线）

### 6.1 冻结基线（S1-C 审计确认，不得改动）

| 文件 | SHA256 | 状态 |
|------|--------|------|
| `usit/scanner.py` | `58ba5f25483429ce7482ae1d852deabce6ea0490f2e106c6955c6ff5323b45e3` | 冻结（S1-C）|
| `usit/cli.py` | `879a3735838e183f41dd08054095b514ffc0e81e2b43776b75a77e25d138b8d9` | 冻结 |
| `usit/file_understanding.py` | `71995a09da04427c028282cfeba17c6b12ebdbcae97796adc2998af7c455ce5b` | 冻结 |

### 6.2 分叉文件（待审查，未判定正确版本）

| 文件 | A SHA256 | B SHA256 |
|------|---------|---------|
| `cli.py` | `65c1366a…` | `879a3735…` |
| `python_worker.py` | `299358a1…` | `c23e922f…` |
| `scanner.py` | `981de7fc…` | `58ba5f25…` |

---

## 七、审计结论

1. 双工作区分叉**范围明确**：仅 `usit/cli.py`、`usit/python_worker.py`、`usit/scanner.py` 3 个文件；`tests/` 41 文件全一致；其余 `usit/` 24 文件全一致。
2. 分叉**根因**：A 做了"性能优化拆分"（抽出 `_analysis.py`）+ 阶段 A 新增（business_semantics + understand 命令），B 停在 S1-C 冻结版。
3. **不擅自判定正确版本**：3 个分叉文件均标记为"待审查"，B 的 scanner.py 是冻结基线（`58ba5f25…`），但 A 的拆分版是否等价**未验证**。
4. **兼容性风险已记录**：`analyze_file` 双重定义，未验证行为一致。
5. 同步的 3 个代码文件**不标记为已验证资产**。
6. 本工单**零代码变更**（无同步/合并/删除/覆盖/回滚）。

---

## 附：来源不明文件标记

| 文件 | 来源 | 标记 |
|------|------|------|
| `_workorder_full.md`（仅 A）| 临时合并工单 | 非正式交付物，来源为 DESIGN-01 时临时生成 |
| 其余所有文件 | 均可追溯到 S1-C 或阶段 A | 来源明确 |

**无 UNKNOWN 来源文件**（所有文件均有明确来源）。
