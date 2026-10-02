# 语料治理进化提案（v3 之后）

> 状态：2026-10-02。语料 v3（25,273 条 / 432 部）已合入后，语料线的目标从「把该有的法律补齐（覆盖）」转向「把覆盖与质量变成可复现、可审计、可交专业复核（治理）」。本文分「已执行」与「未执行（含原因与下一步）」两栏，避免把提案写成既成事实。

## 0. 四个阶段（粗排）

```
覆盖 ──▶ 质量 ──▶ 版本口径 ──▶ 金标代表性
(补齐 law)  (逐行可信)  (谁生效)     (测得准)
   v3 已完成   本轮流标/报告   本轮流清单   本轮 38→75
```

一句话：**v3 解决了「有没有」，本轮把「可信不可信、算不算得清、测不测得准」变成可复算产物。**

## 1. 已执行（本轮产物，均可复算）

| 项 | 产物 | 复算命令 | 关键数字（v3 实跑） |
|---|---|---|---|
| 覆盖率可复算 | `scripts/coverage_report.py`、`corpus/zhuma_catalog_titles.json`、[coverage-report.md](coverage-report.md) | `python scripts/coverage_report.py --corpus data/corpus_v3.jsonl --catalog corpus/zhuma_catalog_titles.json --md docs/coverage-report.md` | 219/242 = **90.5%**，未覆盖 23 |
| 逐行质检旗标 | `scripts/corpus_audit.py`、[corpus-audit.md](corpus-audit.md) | `python scripts/corpus_audit.py --corpus data/corpus_v3.jsonl --md docs/corpus-audit.md --json <json>` | 疑似交错 5010、映射条号 506、重复条号 802、整篇一条 11、超长 4、空/超短 0 |
| 缺口可追踪 | `corpus/missing_laws.json` | 由 `flk_missed.json` + 目录科目交叉生成（见文件 `_derivation`） | 23 部，均无竹马正文且 flk 检索 0 命中 |
| 版本口径清点 | `corpus/same_name_laws.json` | 由语料 × `flk_manifest_a.json`/`flk_matched.json` 实算 | 跨来源同名 1 部；flk 非现行有效 4 条；v1 内部重复 401 组 |
| 真实金标扩容 | `gold/gold_real_v3_batch1.jsonl`、`gold/candidates_unverified.jsonl`、[gold-review-worksheet-v3-batch1.md](gold-review-worksheet-v3-batch1.md) | 见 [real-question-eval.md](real-question-eval.md) §12.5 | 38 → **75** 题；候选 33 条（未核验） |
| 覆盖率/质检测试 | `tests/test_coverage_report.py`、`tests/test_corpus_audit.py`、`tests/test_build_real_gold.py` | `python -m unittest discover -s tests` | 62 → 103 通过 |

**纪律**：本轮**不改语料文件、不产 v4**；既有行字节冻结、id 不变（真实金标绑定这些 id）。所有语料内容层面的变更只写提案（下文 §3）。

## 2. 未执行（含原因与下一步）

### 2.1 语料内容变更：一轮只写提案，不动手

| 提案 | 为什么没做 | 下一步 |
|---|---|---|
| **公报源污染行替换**（治安管理处罚法、民诉法、公司法等，疑似交错 5010 行） | `polluted-interleave` 是启发式风险带，**无法区分「窄栏但顺序正确」与「窄栏且串行交错」**；在没人工确证前替换会引入新错误 | 人工取 `corpus-audit.md` 的疑似清单，对照官方文本逐法确证；确证后用 flk 官方版本按 append-only 追加干净行，老行标 `superseded`（不删，保住金标 id） |
| **v1 内部重复 (law,num) 401 组**（企业所得税法实施条例 264 行等） | 未确证是同法两版本、附件还是解析重复 | 先人工判定重复成因，再决定保留/折叠；牵扯金标 id 的须谨慎 |
| **flk 非现行有效版本补齐**（sxx=2 的 2 条） | 需回 flk 核实是否有更新版本未入库 | 逐条回查；若有，按 append-only 追加新版本并在 `meta` 标 `effective`/`gbrq` |
| **缺口 23 部补入** | 需发布机关官网来源，外抓要浏览器会话/WAF，subagent 拿不到 | 维护者用浏览器会话按 `missing_laws.json` 的 `suggested_source` 逐部取源 |

### 2.2 映射条号口径确认（原 §5 第 2 条）

31 部走回退通道（`一、`/`N.`→`第X条`/整篇一条）的文书，`meta.num_origin`/`unit` 已留痕。**未做**法律专业确认是否能作为正式条号引用。下一步：把 `corpus-audit.md` 的 num-mapped 表（506 行）交专业复核。

### 2.3 检索层联动：只给建议，实现归检索线

以下涉及检索实现，**归平行检索线**，本线只给口径建议：

- **同名多来源折叠**：跨来源同名 1 部（环境污染刑事案件解释，v1 公报 24 行 vs flk 20 行）。建议以 flk 官方版本为权威，v1 行标 `superseded`，检索层按 `meta` 折叠或对 superseded 降权——**不改语料行本身**。
- **版本元数据**：建议 Citation 输出带 `meta.effective`/`gbrq`，由上层按时间有效性过滤（原 §5 第 6 条）。

### 2.4 金标到 200 题

本轮到 75 题（+37）。差的 125 题卡在「问句可核验 × 语料有条文可答」的交集：候选池 33 条里，多条因**对应法律不在语料**（道路交通安全法、个人所得税法、义务教育法、社会保险法、居民身份证法、老年人权益保障法、妇女权益保障法、消费者权益保护法本身——语料只有其实施条例）而无法入金。下一步两条腿：① 补这些法入语料（§2.1）；② 继续从更多可达来源核验候选池。

## 3. 交专业复核的两张表（明确责任人缺口）

| 表 | 位置 | 内容 | 为什么必须人工 |
|---|---|---|---|
| 重复条号 dup-law-num | [corpus-audit.md](corpus-audit.md) §交专业复核一 | 802 行（法名/条号/来源/行 id） | 是否同法两版本/附件/解析重复，机器判不了 |
| 回退映射 num-mapped | [corpus-audit.md](corpus-audit.md) §交专业复核二 | 506 行（法名/映射后条号/原文标记/行 id） | 「一、」「N.」能否当正式条号引用，需法律判断 |
| 疑似交错 polluted-interleave | [corpus-audit.md](corpus-audit.md) §疑似双栏交错 | 按法 top 20（共 5010 行） | 启发式不能区分「窄栏顺序正确」与「交错」 |

**现状缺口**：以上三表目前**尚无指定复核人**。建议在项目内明确一名法律专业复核人；金标复核状态持久化在 `gold/*_review_status.json`，重复生成工作表不丢结果。

## 4. 与主分支的边界

- 本线 worktree：`D:/Claudeworkspace/statute-rag-wt-corpus`（分支 `feat/corpus-governance`）。
- 语料与官方原件不入仓库（`data/` 被 gitignore）；仓库只带代码 + 公开事实清单（法名/条号/URL）+ 金标 + 测试。
- 检索实现（`statute_rag/retrieval.py` 等）归检索线，本线不碰。
