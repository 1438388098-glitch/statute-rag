[English](./README.md) · 简体中文

# statute-rag · 法条混合检索底座

把「条」当检索单元、把「可验证的出处」当硬约束的法条检索 pipeline，配套**可复现的离线评测**。为上层法条问答提供「强制条文引用」的检索地基：结构化分条 → 字符二元组 BM25（原查询 + 同义扩展查询）/ LIKE 多路融合（RRF）→ 强制条文级引用。

**当前版本 v0.1.1：词法检索三件套 + 真实评测数字 + 面向真实问句的查询扩展改进（真实问句 Recall@5 26.3% → 44.7%，合成金标零回退）。语义向量通道在路线图（见下），不在当前宣称范围内。**

## 快速跑通（无需真实语料）

仓库不含真实法条文本（见「边界」）。没有 legal.db 也能用脚本生成的合成演示语料，三条命令跑通全流程（纯标准库，无需模型 / API / GPU）：

```bash
# 1) 生成合成演示语料：约 100 条程序生成的假条文 + 合成金标（固定种子，可重复）
python scripts/make_demo_corpus.py

# 2) 在演示语料上评测检索三件套
python scripts/run_eval.py --corpus demo_corpus/corpus.jsonl --gold demo_corpus/gold.jsonl --out-dir demo_corpus

# 3) 检索演示
python scripts/search_cli.py --corpus demo_corpus/corpus.jsonl "台账公示" --k 2
```

> 演示语料上的分数**只验证管线能跑通**（模板生成的假条文彼此极易区分），不代表真实法条检索质量。真实评测数字见下文「验证」。

## 问题

法律问答 / 合规场景对检索的真实要求不是「找个相关文档」，而是：

- 给一句表述或一个关键词，**找到确切的那一条**；
- 每个结果**必须带可回跳的出处**（法名 + 条号 + 原文），答错条文比答不出来更糟；
- 检索质量要有**可复现的数字**，而不是「看起来挺准」。

本项目源自 [legal-wisdom-app](https://github.com/1438388098-glitch/legal-wisdom-app) 的实践：其 SQLite FTS5 在 unicode61 分词下，中文检索实际退化为子串/模糊匹配（有实证测试，见该仓 `tests/test_search.py`）——没有相关度打分，无法排序。statute-rag 从零建立带评测的检索底座。

## 边界（先说清不做什么）

- **当前不是语义 RAG**：无 embedding、无向量库。字符二元组 BM25 是词法检索。v0.1.1 用「法律口语↔法定表述同义词典 + 查询扩展」搭了一层词法桥（坐牢→服刑、探视→会见、社保→社会保险、1000元→一千元），把真实问句 Recall@5 从 26.3% 提到 44.7%；但纯语义改写（无词典可桥的表述）仍命中不了，语义通道需要可用的中文 embedding 模型/接口，属 v0.2。
- **金标有两套**：合成金标（唯一短语 → 关键词查询）衡量词法召回上限；真实问句金标 v1（38 题，问句来自网络真实提问，LLM 核验、人工法律复核待做）衡量真实问句上的表现（hybrid 26.3% → 44.7%）。见 [docs/real-question-eval.md](docs/real-question-eval.md)。
- **语料不随仓库分发**：法律条文来自本地 legal-wisdom 库（legal.db：257 部 / 14,344 条；经本仓导入质检后：14,212 条干净条文，覆盖 238 部），仓库只含代码、测试与评测产物；复现需自备语料。
- 不构成法律意见；条文内容以官方发布为准。

## 机制

```
legal.db ──importer──> 语料 JSONL（质检三层过滤）
              │            · 过滤 (cid:xx) 字体映射残片
              │            · 过滤 <30 字解析残渣 / 空内容
              │            · 14,344 条 → 14,212 条（弃 0.9%）
              ▼
   检索三件套（统一 Citation 输出：法名+条号+原文+id）
   ├── LikeRetriever   子串精确基线（模拟 unicode61 下中文实际行为）
   ├── BM25Retriever   字符二元组 BM25（零依赖、不需要分词器）
   └── HybridRetriever RRF 多路融合：原查询 BM25
                       + 同义扩展查询 BM25（synonyms.json 127 词条
                         口语↔法定表述 + 数字读法归一，query_expansion.py）
                       + LIKE（原查询）
              ▼
   评测（gold.py + eval_harness.py）
   · 金标：IDF 最高且【全库唯一】的 6 字短语 → 关键词查询，seed 固定
   · 指标：Recall@5 / MRR
```

关键设计决策：

- **唯一短语约束**：最初金标用「IDF 最高短语」，实测 74% 的查询短语跨条文复用（法条公式化表述），召回上限失真。改为「全库唯一」约束（倒排交集精确计数 df==1）后，每题只有一个正确答案。
- **质检宁可少导入**：PDF 双栏解析存在串行乱序污染（见下方失败案例），质检只过滤可确定性识别的污染，乱序检测是开放问题。
- **查询扩展只增不改（v0.1.1）**：真实问句是口语（坐牢/看望/社保/房东），条文是法言法语（服刑/会见/社会保险/出租人），词法通道无从命中。解法是数据化的领域词典（`statute_rag/synonyms.json`，127 词条通用映射）+ 追加式查询扩展 + RRF 多路融合：**原查询整体保留**，扩展表述另开一路检索，无命中自动省略。防过拟合约束与逐轮消融（含无增益即移除的负结果）见 [docs/retrieval-improvement.md](docs/retrieval-improvement.md)。

## 验证（真实数字，非虚构）

**两套金标，两套口径，数字都如实并列**（hybrid 行同时给出 v0.1 基线 → v0.1.1 改进后）：

| 检索器 | 合成金标 Recall@5 | 合成金标 MRR | 真实问句金标 v1 Recall@5 | 真实问句金标 v1 MRR |
|---|---|---|---|---|
| like（子串基线） | 77.4% | 0.774 | 0.0% | 0.000 |
| bm25（字符二元组） | 96.6% | 0.954 | 26.3% | 0.180 |
| hybrid（v0.1 基线：BM25+LIKE 双路 RRF） | 98.9% | 0.984 | 26.3%（10/38） | 0.180 |
| **hybrid（v0.1.1：+ 同义扩展查询路）** | **98.9%（零回退）** | **0.984** | **44.7%（17/38，+18.4pt）** | **0.312** |

- **合成金标口径**：题目由条文中的全库唯一短语机械生成（seed=20260918，N=177），衡量「给定条文中的独特表述，能否把该条文检回来」的词法召回上限。金标见 [gold/gold_synth_seed20260918.jsonl](gold/gold_synth_seed20260918.jsonl)，报告见 [docs/eval_report.md](docs/eval_report.md)。它同时是查询扩展改进的**留出守门**：真实金标提升的有效性以「合成金标回退 ≤2pt」约束，实测零回退。
- **真实问句金标 v1（LLM 核验，人工法律复核待做）**：38 条问句来自百度知道真实法律提问（逐条记录来源 URL），query 为问句原文直接送检。v0.1 基线 hybrid Recall@5 = **26.3%**，主因：口语词与法言法语无词法重叠、同部法律内部竞争、公报双栏法律的解析污染。v0.1.1 经通用同义词典 + 查询扩展提升至 **44.7%**；剩余 21 题未命中（纯语义等价、同法/跨法竞争、双栏解析污染）如实列出。逐题明细、失败案例与覆盖缺口见 [docs/real-question-eval.md](docs/real-question-eval.md)，金标见 [gold/gold_real_38.jsonl](gold/gold_real_38.jsonl)，改进实验记录见 [docs/retrieval-improvement.md](docs/retrieval-improvement.md)。
- 复现（需自备语料 `data/corpus.jsonl`）：

```bash
# 合成金标
python scripts/run_eval.py --corpus data/corpus.jsonl --out-dir data
# 真实问句金标
python scripts/build_real_gold.py --corpus data/corpus.jsonl --out gold/gold_real_38.jsonl
python scripts/run_eval.py --corpus data/corpus.jsonl --gold gold/gold_real_38.jsonl --out-dir data --gold-desc "真实问句金标 v1（LLM 核验，人工法律复核待做）"
# 改进机制消融（基线 vs 改进配置，双金标）
python scripts/ablate_retrieval.py --corpus data/corpus.jsonl \
    --gold-real gold/gold_real_38.jsonl --gold-synth gold/gold_synth_seed20260918.jsonl
```

- 单元测试 34 例：`python -m unittest discover -s tests`
- 30 秒演示：

```bash
python scripts/search_cli.py --db <你的 legal.db> "承诺生效时合同成立" --k 2
# [1] 最高人民法院关于适用《中华人民共和国民法典》合同编通则若干问题的解释 第三条 …
```

### 已知失败案例

- **乱序条文污染**：查询「正当防卫」时，命中了《突发公共卫生事件应对法》的条文——该部 PDF 双栏解析串行，多个栏位的文字交错混排，质检层无法识别乱序（无 `(cid:` 残片、长度正常），异部文字恰好含查询词。修复方向：基于「跨部高频引用串」的乱序检测、或对高风险文档换用更可靠的 PDF 提取参数后重建语料。真实问句金标中该问题再次显性化：快递丢失赔偿、政府信息公开答复期限等题的金标行文本被串栏打断，查询扩展也桥接不动（见 [docs/real-question-eval.md](docs/real-question-eval.md) 第 10 节）。
- **词法检索天花板（已被查询扩展推高一部分）**：口语问句 → 法言法语条文的词法桥已由同义词典搭起一层（真实问句 Recall@5 26.3% → 44.7%）；剩余 21 题未命中的主因是纯语义等价（无词典可桥的改写）、同法/跨法相似条文竞争与解析污染——这仍需 v0.2 的向量语义通道，不是继续堆词典能解决的。
- **合成金标的保守性**：指标衡量「独特表述→条文」的词法召回，真实问句（含错字、口语、多实体）的召回显著低于此数字——实测：v0.1 基线 26.3%，v0.1.1 改进后 44.7%。

## Roadmap

- **v0.2**：中文 embedding 通道（接口抽象，可选本地/远端）+ 重排 → 真语义混合检索；真实问句金标扩容（目标 200 题）+ 人工法律复核
- **v0.3**：条/款/项多级分块；法条版本对齐（时效性）；「检索不到就拒答」策略与幻觉护栏评测

## License

[MIT](LICENSE)
