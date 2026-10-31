# USIT-STAGE-B-DESIGN-05-HASH-MANIFEST｜交付物哈希清单

> 生成时间：2026-10-30（GMT+8）
> 哈希算法：SHA-256（hex 小写 64 字符）
> 工作区：`C:\Users\Administrator\WorkBuddy\2026-10-15-22-09-47\usit_p0`
> 用途：DESIGN-05 交付物 4 —— 记录本次新增文档的完整性哈希

---

## 一、本次新增交付物（5 份）

| 文件 | 字节数 | SHA256 |
|------|--------|--------|
| `USIT-STAGE-B-DESIGN-05.md` | 27092 | `a416af6e9c9482d2e2de9cbe19c73bef6463c7c3770ce3910cc0dcaa6a506d2c` |
| `USIT-STAGE-B-DESIGN-05-ISSUES.md` | 3362 | `0b84c7c358073539531ec92fdebe4cf91879fd2b6454b052a056a54478e1d8dd` |
| `USIT-DESIGN-04-05-DIFF.md` | 3447 | `3170c1d074d65e2a0a876dcda3854279264c858a008d5302b401bee1e6202a34` |
| `USIT-STAGE-B-DESIGN-05-EXECUTION-REPORT.md` | （本表生成后回填）| （本表生成后回填）|
| `USIT-STAGE-B-DESIGN-05-HASH-MANIFEST.md` | （本文件，自指）| （本表生成后回填）|

> 注：EXECUTION-REPORT 与 HASH-MANIFEST 的字节数/哈希在本文件**写完并定稿后**回填（见"二、哈希语义边界"的自指说明）。最终值以执行报告 §6 的汇总表为准。

---

## 二、哈希语义边界（如实声明）

1. **哈希只证明内容一致性**（同一内容 → 同一哈希），**不能证明报告作者身份**。作者身份需数字签名（本阶段未引入）。
2. **冻结源码文件哈希与新增报告文件哈希严格区分**，不混列（见 §三）。
3. 原始 DESIGN-01/02/03/04 **未修改**（本工单只新增，不覆盖）。
4. 哈希算法固定 SHA-256，hex 小写 64 字符；生成时间 2026-10-30（GMT+8）。
5. 若某文件无法实际计算哈希 → 标记 `NOT_VERIFIED`，**不得编造**。

---

## 三、冻结源码文件哈希（未改动，仅列对照，不属新增）

| 文件 | SHA256 | 状态 |
|------|--------|------|
| `usit/scanner.py` | `58ba5f25483429ce7482ae1d852deabce6ea0490f2e106c6955c6ff5323b45e3` | 冻结（S1-C）|
| `usit/cli.py` | `879a3735838e183f41dd08054095b514ffc0e81e2b43776b75a77e25d138b8d9` | 冻结 |
| `usit/file_understanding.py` | `71995a09da04427c028282cfeba17c6b12ebdbcae97796adc2998af7c455ce5b` | 冻结 |

**结论**：本次新增 5 份文档；冻结源码 3 文件哈希保持不变（执行前后一致，见执行报告 §7）。

---

## 四、原始 DESIGN-01/02/03/04 未被修改确认

本工单只通过 Write 创建 5 个新文件，未 Edit/覆盖任何 `USIT-STAGE-B-DESIGN-01/02/03/04*.md`、`USIT-DESIGN-*.md`、`USIT-WORKSPACE-*.md`。原始文档完整性由既有哈希清单（`USIT-WORKSPACE-SHA256-BASELINE.txt` 及各阶段执行报告）继续保证。
