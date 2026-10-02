[English](./README.md) · 简体中文

[![CI](https://github.com/1438388098-glitch/statute-rag/actions/workflows/ci.yml/badge.svg)](https://github.com/1438388098-glitch/statute-rag/actions/workflows/ci.yml)

# statute-rag · 法条混合检索底座

把「条」当检索单元、把「可验证的出处」当硬约束的法条检索 pipeline，配套**可复现的离线评测**。结构化分条 → 字符二元组 BM25（原查询 + 同义扩展查询）/ LIKE 多路融合（RRF）→ 强制条文级引用。

**版本：v0.1.1 tag；主分支在其上新增了度量底座（通道深度与 k 解耦、Recall@k 曲线）、冻结的 Reranker 接口与可安装打包——见 [CHANGELOG.md](CHANGELOG.md)。语义向量通道在路线图中，不在任何版本的宣称范围内。**

## 快速跑通（无需语料，约 30 秒）

纯 Python 标准库，零第三方运行时依赖。Python ≥ 3.8（CI 跑 3.9 与 3.13）。

```bash
# 1) 生成合成演示语料：约 100 条程序生成的假条文 + 合成金标（固定种子，可重复）
python scripts/make_demo_corpus.py

# 2) 在演示语料上评测检索三件套
python scripts/run_eval.py --corpus demo_corpus/corpus.jsonl --gold demo_corpus/gold.jsonl --out-dir demo_corpus

# 3) 检索演示
python scripts/search_cli.py --corpus demo_corpus/corpus.jsonl "台账公示" --k 2
```

> 演示语料上的分数**只验证管线能跑通**（模板生成的假条文彼此极易区分），不代表真实法条检索质量。真实评测数字见下文「验证」。

可选：作为包安装 `pip install .`（同义词典随包分发为 package-data；`import statute_rag; statute_rag.__version__`）。

## 数字（当前口径 v3 语料 25,273 条 / 432 部；两套金标并列）

> **口径变更声明**：2026-10 语料按法考汇编对账补全（14,212 → 25,273 条），**当前口径以 v3 语料为准**。语料扩容会让同一批老问句的 R@5 变化（hybrid 真实 R@5 一度从 v1 的 44.7% 降到 31.6%），经检索侧重标定后回到 44.7%。下表是 v3 当前口径；**v1 / v2 是历史口径**（见文末并列表），请勿与当前数字混读。诊断、逐题迁移与全网格见 [docs/retrieval-v3-diagnosis.md](docs/retrieval-v3-diagnosis.md)。

| 检索器 | 合成金标 Recall@5 | 合成金标 MRR | 真实问句金标 R@5 | 真实问句金标 MRR |
|---|---|---|---|---|
| like（子串基线） | 77.4% | 0.774 | 0.0% | 0.000 |
| bm25（字符二元组） | 93.8% | 0.905 | 23.7% | 0.140 |
| **hybrid（v0.1.1：+ 同义扩展查询路，加权）** | **97.2%（零回退）** | **0.968** | **44.7%（17/38）** | **0.250** |

**深度召回曲线**（固定 depth=30 的同一份排名逐 k 截取，跨 k 可比）：真实问句上 hybrid **Recall@10 52.6% → Recall@20 68.4% → Recall@30 81.6%**。38 题里 7 题未进 top-30（纯语义等价或同法页块冗余），14 题在 6-30 名内（v0.2 重排通道的工作面）。逐 k 表与排名分布见 [docs/eval_report.md](docs/eval_report.md)——由 `scripts/gen_eval_report.py` 生成，[docs/metrics.json](docs/metrics.json) 是数字的单一来源（CI 校验两份 README 与之一致）。

**三版语料并列（当前 + 历史口径；勿与上表混读）**：

| 语料 | 条数 | hybrid 真实 R@5 | 真实 MRR@5 | 真实 R@10 | 真实 R@30 | 合成 R@5 |
|---|---|---|---|---|---|---|
| **v3（当前）** | 25,273 | **44.7%** | 0.250 | 52.6% | 81.6% | 97.2% |
| v1（历史） | 14,212 | 44.7% | 0.266 | 55.3% | 81.6% | 98.9% |
| v2（历史） | 16,523 | 39.5% | 0.227 | 52.6% | 81.6% | 98.3% |

## 问题

法律问答 / 合规场景对检索的真实要求不是「找个相关文档」，而是：

- 给一句表述或一个关键词，**找到确切的那一条**；
- 每个结果**必须带可回跳的出处**（法名 + 条号 + 原文），答错条文比答不出来更糟；
- 检索质量要有**可复现的数字**，而不是「看起来挺准」。

本项目源自 [legal-wisdom-app](https://github.com/1438388098-glitch/legal-wisdom-app) 的实践：其 SQLite FTS5 在 unicode61 分词下，中文检索实际退化为子串/模糊匹配（有实证测试，见该仓 `tests/test_search.py`）——没有相关度打分，无法排序。statute-rag 从零建立带评测的检索底座。

## 机制

```
legal.db ──importer──> 语料 JSONL（质检三层过滤）
              │            · 过滤 (cid:xx) 字体映射残片
              │            · 过滤 <30 字解析残渣 / 空内容
              │            · 14,344 条 → 14,212 条（弃 0.9%；2026-10 补全至 25,273 条）
              ▼
   检索三件套（统一 Citation 输出：法名+条号+原文+id）
   ├── LikeRetriever   子串精确基线（模拟 unicode61 下中文实际行为）
   ├── BM25Retriever   字符二元组 BM25（倒排 postings，零依赖、无分词器）
   └── HybridRetriever RRF 多路融合：原查询 BM25
                       + 同义扩展查询 BM25（synonyms.json 127 词条
                         口语↔法定表述 + 数字读法归一，query_expansion.py）
                       + LIKE（原查询）
              ▼
   评测（gold.py + eval_harness.py）
   · 金标：IDF 最高且【全库唯一】的 6 字短语 → 关键词查询，seed 固定
   · 指标：Recall@5 / MRR / 多 k 曲线 / 排名分布
```

仓库结构：

```
statute_rag/          核心包（纯标准库）
  ├── importer.py         legal.db → 条文级 JSONL + 质检门
  ├── retrieval.py        Like / BM25（倒排索引）/ Hybrid RRF，深度与 k 解耦
  ├── query_expansion.py  口语↔法定表述词典 + 数字读法归一
  ├── gold.py             合成金标生成（全库唯一短语约束）
  ├── eval_harness.py     Recall@k / MRR / 多 k 评测 / 排名分布
  └── interfaces.py       v0.2 Reranker 接口约定（模型后接）
scripts/              命令行：run_eval、search_cli、bench、gen_eval_report、
                      check_doc_numbers、make_demo_corpus、ablate_retrieval 等
tests/                108 例单测（unittest，临时目录自造语料）
gold/                 入库金标 + 人工复核状态
docs/                 生成的评测报告、metrics.json、实验记录
```

关键设计决策：

- **唯一短语约束**：最初金标用「IDF 最高短语」，实测 74% 的查询短语跨条文复用（法条公式化表述），召回上限失真。改为「全库唯一」约束（倒排交集精确计数 df==1）后，每题只有一个正确答案。
- **质检宁可少导入**：PDF 双栏解析存在串行乱序污染（见「已知失败案例」），质检只过滤可确定性识别的污染，乱序检测是开放问题。
- **查询扩展只增不改（v0.1.1）**：真实问句是口语（坐牢/看望/社保/房东），条文是法言法语（服刑/会见/社会保险/出租人），词法通道无从命中。解法是数据化的领域词典（`statute_rag/synonyms.json`，127 词条通用映射）+ 追加式查询扩展 + RRF 多路融合：**原查询整体保留**，扩展表述另开一路检索，无命中自动省略。防过拟合约束与逐轮消融（含无增益即移除的负结果）见 [docs/retrieval-improvement.md](docs/retrieval-improvement.md)。
- **通道深度与 k 解耦**：通道深度是检索配置，不是返回条数。`search(k)` 用固定深度（已发布数字的口径）；跨 k 对比走 `recall(query, depth)`——同一份排名逐 k 截取（`evaluate_multi_k`）。真实金标实测（当前 v3 口径）Recall@5 44.7% → Recall@30 81.6%：**大多数真实问题其实已经检到，输在排序**——这是 v0.2 重排通道的量化立项依据。

## 边界（先说清不做什么）

- **当前不是语义 RAG**：无 embedding、无向量库。字符二元组 BM25 是词法检索。v0.1.1 用「法律口语↔法定表述同义词典 + 查询扩展」搭了一层词法桥（坐牢→服刑、探视→会见、社保→社会保险、1000元→一千元），把真实问句 Recall@5 从 26.3% 提到 44.7%（v1 语料口径，14,212 条）；但纯语义改写（无词典可桥的表述）仍命中不了，语义通道需要可用的中文 embedding 模型/接口，属 v0.2。
- **金标有两套**：合成金标（唯一短语 → 关键词查询）衡量词法召回上限；真实问句金标（38 题，问句来自网络真实提问，LLM 核验、人工法律复核进行中）衡量真实问句上的表现（当前 v3 口径 44.7%；v0.1 基线 26.3%、v1/v2 的历史口径数字见上文「数字」节）。见 [docs/real-question-eval.md](docs/real-question-eval.md)。
- **语料不随仓库分发**：法律条文来自本地 legal-wisdom 库（legal.db：257 部 / 14,344 条），2026-10 又按法考汇编对账补入官方源 11,061 条；**当前口径：25,273 条干净条文，覆盖 432 部**（v1 为 14,212 条 / 238 部），仓库只含代码、测试与评测产物；复现边界见下文「验证」的三档说明。
- 不构成法律意见；条文内容以官方发布为准。

## 验证（真实数字，非虚构）

**两套金标，两套口径，数字都如实并列**：

- **合成金标口径**：题目由条文中的全库唯一短语机械生成（seed=20260918，N=177），衡量「给定条文中的独特表述，能否把该条文检回来」的词法召回上限。金标见 [gold/gold_synth_seed20260918.jsonl](gold/gold_synth_seed20260918.jsonl)。它同时是查询扩展改进的**留出守门**：真实金标提升的有效性以「合成金标回退 ≤2pt」约束，实测零回退；词典在合成金标上的触发率仅 0.6%（1/177），是防口语词典过拟合的天然哨兵。
- **真实问句金标 v1（LLM 核验，人工法律复核进行中）**：38 条问句来自百度知道真实法律提问（逐条记录来源 URL），query 为问句原文直接送检。v0.1 基线 hybrid Recall@5 = **26.3%**，主因：口语词与法言法语无词法重叠、同部法律内部竞争、公报双栏法律的解析污染。v0.1.1（v1 口径）经通用同义词典 + 查询扩展提升至 **44.7%**。语料补全到 v3 后一度降到 31.6%，经检索侧重标定（见 [docs/retrieval-v3-diagnosis.md](docs/retrieval-v3-diagnosis.md)）又回到 **44.7%**；剩余未命中题如实列出。逐题明细见 [docs/real-question-eval.md](docs/real-question-eval.md)，金标见 [gold/gold_real_38.jsonl](gold/gold_real_38.jsonl)，人工复核底稿见 [docs/gold-review-worksheet.md](docs/gold-review-worksheet.md)。

复现——三档口径，如实说明：

1. **没有语料（所有人）**：上面的 demo 路径验证机制端到端可跑通（CI 每次 push 都断言）。
2. **同源语料（legal-wisdom 同版本 legal.db）**：完整复现两套金标数字：

   ```bash
   # 合成金标
   python scripts/run_eval.py --corpus data/corpus_v3.jsonl --gold gold/gold_synth_seed20260918.jsonl --out-dir data
   # 真实问句金标
   python scripts/build_real_gold.py --corpus data/corpus_v3.jsonl --out gold/gold_real_38.jsonl
   python scripts/run_eval.py --corpus data/corpus_v3.jsonl --gold gold/gold_real_38.jsonl --out-dir data --gold-desc "真实问句金标 v1（LLM 核验，人工法律复核进行中）"
   # 改进机制消融（基线 vs 改进配置，双金标；多 k）
   python scripts/ablate_retrieval.py --corpus data/corpus_v3.jsonl \
       --gold-real gold/gold_real_38.jsonl --gold-synth gold/gold_synth_seed20260918.jsonl \
       --ks 5,10,20,30
   ```

3. **自有中文法条语料**：检索三件套、合成金标与质检门开箱即用；但入库的 `gold_real_38.jsonl` 的 `gold_id` 绑定我们导入版本的分块行 id，换语料无法直接重跑该金标数字——如需复用 38 题，用 `build_real_gold.py` 以自有语料重建行 id（问句与来源 URL 字段可平移）。

- 单元测试 108 例：`python -m unittest discover -s tests`。延迟参考：`python scripts/bench.py`（本机相对口径，只用于前后对比）。

```bash
# 30 秒检索演示（需语料）
python scripts/search_cli.py --db <你的 legal.db> "承诺生效时合同成立" --k 2
# [1] 最高人民法院关于适用《中华人民共和国民法典》合同编通则若干问题的解释 第三条 …
```

## 已知失败案例

- **乱序条文污染**：查询「正当防卫」时，命中了《突发公共卫生事件应对法》的条文——该部 PDF 双栏解析串行，多个栏位的文字交错混排，质检层无法识别乱序（无 `(cid:` 残片、长度正常），异部文字恰好含查询词。真实问句金标中该问题再次显性化：快递丢失赔偿、政府信息公开答复期限等题的金标行文本被串栏打断，查询扩展也桥接不动（见 [docs/real-question-eval.md](docs/real-question-eval.md) 第 10 节）。修复方向：基于「跨部高频引用串」的乱序检测、或对高风险文档换用更可靠的 PDF 提取参数后重建语料。
- **词法检索天花板（已被查询扩展推高一部分）**：剩余 21 题未命中的主因是纯语义等价（无词典可桥的改写）、同法/跨法相似条文竞争与解析污染——这需要 v0.2 的向量语义通道 + 重排，不是继续堆词典能解决的。加权 RRF 与 BM25 k1/b 调参在 v1 语料上实测无增益、已移除（见 [docs/retrieval-improvement.md](docs/retrieval-improvement.md) §4 负结果）；但**语料扩容改变了该前提**：v3 里 100 余字的官方单条与 1,400 字的公报页块混排，BM25 长度归一化参数 b 与扩展通道权重从「惰性」变成「主导」；按 v3 重标定这两个参数（b 0.75→0.6、扩展权重 1→2.5）才把真实 R@5「救」回来，代价是 v1/v2 的 MRR@5 下降——全网格与负结果见 [docs/retrieval-v3-diagnosis.md](docs/retrieval-v3-diagnosis.md) §5–6。
- **合成金标的保守性**：指标衡量「独特表述→条文」的词法召回（当前 v3 口径 97.2%，v1 历史口径 98.9%），真实问句（含错字、口语、多实体）的召回显著低于此（44.7%）——两个数字都如实报告，正因为如此。

## FAQ

- **为什么不用 embedding？** 字符二元组 BM25 刻意做词法检索：零依赖、无分词器、无模型——意义在于一个随处可跑的评测基线。语义通道已为 v0.2 排期，接口先冻结在 [statute_rag/interfaces.py](statute_rag/interfaces.py)（`Reranker` 约定），可选依赖不进核心包。
- **语料能给我用吗？** 不能——不随仓库分发（来源是本地 legal-wisdom 库）。可复用的是代码、金标 schema 与评测协议；复现边界见「验证」的三档说明。
- **为什么 like 在真实问句上是 0.0%？** 真实问句是口语句子，在法条原文中逐字不出现，子串匹配无从命中。见 [docs/real-question-eval.md](docs/real-question-eval.md) §5。
- **数字怎么核对？** [docs/metrics.json](docs/metrics.json) 是单一来源；CI 的 `check_doc_numbers.py` 对账两份 README；所有数字都能用上面的命令重新生成。

## Roadmap

- **v0.2**：在冻结的 `Reranker` 接口上接语义重排通道（本地/远端可插拔，可选依赖）——目标已量化：把真实问句 Recall@5（44.7%）向 Recall@30 上限（81.6%）抬升；真实问句金标扩容（目标 200 题）+ 人工法律复核；为双栏解析污染加逐行解析质量标注。
- **v0.3**：条/款/项多级分块；法条版本对齐（时效性）；「检索不到就拒答」策略与幻觉护栏评测。

## 版本与引用

版本沿革见 [CHANGELOG.md](CHANGELOG.md)（Keep a Changelog 格式）；引用本项目请用 [CITATION.cff](CITATION.cff) 中的元数据（GitHub 仓库页会据此渲染「Cite this repository」按钮）。

## License

[MIT](LICENSE)
