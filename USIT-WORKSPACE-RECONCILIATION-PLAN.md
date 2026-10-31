# USIT-WORKSPACE-RECONCILIATION-PLAN｜双工作区处理方案

> 生成：2026-10-30（GMT+8）· 上游：`USIT-WORKSPACE-BASELINE-AUDIT.md`
> 性质：**只提建议，不选择性覆盖文件**。本工单不执行任何合并/同步/覆盖。

---

## 一、背景

基线审计（DESIGN-03）已查清：双工作区（A=Desktop 副本，B=正式工作区）在 `usit/` 目录有 3 个分叉文件，`tests/` 41 文件 100% 一致。本方案为这 3 个文件提供独立处理建议，供后续合并工单使用。

---

## 二、三个分叉文件处理建议

### 2.1 `cli.py`（A=增量超集，B=S1-C 冻结）

| 属性 | A（Desktop）| B（正式）|
|------|------------|---------|
| SHA256 | `65c1366a…c42fe` | `879a3735…b8d9` |
| 差异 | A 多 `understand` 命令 + `business_semantics` import | B 无 understand |

**建议：采用 A 的部分改动（`understand` 命令），但需独立审查。**

- A 是 B 的**增量超集**（A = B + understand 命令），无破坏性改动。
- 合并动作：**只移植 A 新增的 `understand` 子命令注册 + `_understand()` 函数 + `business_semantics` import**，不动 B 的其它部分。
- 前置条件：`business_semantics.py` 已在 B（DESIGN-02 已同步），import 可满足。
- 风险：A 的 `_understand()` 可能依赖 A 版 `scanner`/`_analysis`（见 2.3），需一并核对。

### 2.2 `python_worker.py`（A=优化版，B=旧版）

| 属性 | A | B |
|------|-----|-----|
| SHA256 | `299358a1…90cbd` | `c23e922f…1b04` |
| 差异 | A 改 `from usit.scanner import analyze_file` → `from usit._analysis import analyze_file` | B 从 scanner import |

**建议：采用 A 的部分改动（import 优化），但必须先解决 `analyze_file` 双重定义。**

- A 的改动是性能优化（避免加载 scanner 重依赖链），方向正确。
- **阻断点**：B 的 `scanner.py:458` 内联定义 `analyze_file`，A 的 `_analysis.py` 也定义 `analyze_file`，二者 `is` 不同（已实测 False）。若直接改 import，需确认 `_analysis.analyze_file` 与 `scanner.analyze_file` **行为等价**（NOT_VERIFIED）。
- 合并动作：在 `analyze_file` 行为等价验证通过后，才把 `python_worker.py` 的 import 切换到 `_analysis`。

### 2.3 `scanner.py`（A=拆分重构版，B=S1-C 冻结基线）

| 属性 | A | B |
|------|-----|-----|
| SHA256 | `981de7fc…8913` | `58ba5f25…45e3` |
| 差异 | A 拆出 `_analysis.py`（998 行）| B 内联逻辑（1262 行）|

**建议：保留 B（S1-C 冻结基线），A 的拆分需行为等价验证后才能合并。**

- B 是 S1-C 审计确认的冻结基线 `58ba5f25…`，**不得覆盖**。
- A 的拆分是重构（逻辑等价拆分，未验证行为完全一致），存在 `analyze_file` 双重定义风险。
- 合并动作：**不直接采用 A 的 scanner.py**。若确实要采用拆分优化，需：
  1. 行为等价验证（对比 A/B 的 analyze_file 在相同输入下的输出）。
  2. 通过后，作为**新的重构工单**合并，且必须 bump 相关版本、更新冻结哈希（需单独授权）。
- 在此之前，B 的 scanner.py 保持冻结不变。

---

## 三、`analyze_file` 双重定义处理（核心风险）

| 事实 | 结论 |
|------|------|
| `scanner.analyze_file is _analysis.analyze_file` | **False**（两个独立对象）|
| B 的 scanner.py:458 内联定义 | 冻结基线的一部分 |
| A 的 _analysis.py 定义 | 同步来的阶段 A 文件 |

**处理原则**：
- **不擅自判定哪个正确**（工单要求）。
- IMPLEMENT 阶段必须先做**行为等价验证**（同一输入，两函数输出一致）。
- 验证通过 → 才能安全切换 import；验证失败 → 保留 B 内联版，A 的 `_analysis.py` 拆分不合并。

---

## 四、已同步文件验证状态（如实）

| 文件 | 状态 | 说明 |
|------|------|------|
| `_analysis.py` | 已同步、可 import，**未验证** | 功能正确性、与 B 深度兼容性 NOT_VERIFIED |
| `business_semantics.py` | 已同步、可 import，**未验证** | VERIFIED_FACT 误用未改（另开工单）|
| `test_business_semantics.py` | 已同步、可加载，**未验证** | 未运行（工单禁止测试）|

**均不标记为"已验证资产"。**

---

## 五、冻结哈希保护边界

| 文件 | SHA256 | 保护状态 |
|------|--------|---------|
| `scanner.py` | `58ba5f25…45e3` | 冻结（S1-C），保护边界明确 |
| `cli.py` | `879a3735…b8d9` | 冻结，保护边界明确 |
| `file_understanding.py` | `71995a09…ce5b` | 冻结，保护边界明确 |

**保护边界仍然有效**：DESIGN-04 及本方案不更新任何冻结哈希、不解除任何门禁。

---

## 六、建议的合并顺序（后续工单，非本工单执行）

1. **IMPLEMENT 前**：验证 `analyze_file` 行为等价（需要运行测试 → 需单独授权）。
2. **合并 `cli.py`**：移植 understand 命令（低风险，增量）。
3. **合并 `python_worker.py`**：import 切换（依赖第 1 步结果）。
4. **`scanner.py`**：暂不合并，保留 B 冻结版，直到拆分重构有充分验证 + 单独授权。

---

## 七、结论

- 3 个分叉文件均有明确处理建议，**不选择性覆盖任何文件**。
- 核心阻断是 `analyze_file` 行为等价性 NOT_VERIFIED，需 IMPLEMENT 阶段单独验证。
- 冻结哈希保护边界保持有效，本方案不越权。
