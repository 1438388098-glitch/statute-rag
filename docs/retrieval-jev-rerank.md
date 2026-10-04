# Jev 替换重排裁判的全链路实测（2026-10-04）

> **一句话**：把现行 `llm_rerank` 的 chat 裁判换成本地判选的 **Jev**，在 v8 留出 100 题上
> **R@5 从 66.0% 提到 92.0%（打满 top50 天花板），R@1 达 84.0%（高于 chat 裁判 longcat 的
> 82.0%，略低于 DeepSeek 的 85–86%）**；单次判选中位 **~1.3 s**，快于 longcat（3.96 s）与
> deepseek-v4.1-flash（2.75 s），单 key 实测 **64 并发零失败**；付费 `jev-1.13` 约
> **$0.00037/题**、免费档 $0 但**会限流**。前端已接入：每条结果展示「Jev 判分」与判选参数。

## 0. 任务与口径

把可选层 `statute_rag/llm_rerank.py`（OpenAI 兼容 `/chat/completions`，让通用大模型自由
输出 JSON 序号数组）的**裁判模型**换成 **Jev**（TypeSafe System One；OpenCode Zen
`POST /zen/v1/systemone`，免费档 `jev-1.13-free` / 付费 `jev-1.13`），在**同一条全链路**上测量：

```
HybridRetriever(corpus_v7, reranker=SemanticReranker, pool_extra=SemanticPool)
  → 前 50 名候选
  → 【裁判层】Jev 判选（本层，替换 llm_rerank 的 chat 裁判）
  → 只提前至多 5 条、其余保持原序
```

- **语料/金标**：`data/corpus_v7.jsonl`（25,987 条 / 444 部）；留出金标
  `gold/gold_blind_v8_v7.jsonl`（100 题，v8 第二轮隔离盲写题，**从未用于调参**）。
- **对照**（同一份候选池，只差裁判）：`local`（现行 v7 管线，校准基线）、
  `jev`（本层，choice/score 两模式、免费/付费两档）、`llm`（chat 裁判 `longcat-2.5-preview-free`），
  外加项目历史 pilot 存档里的 **DeepSeek** 两档。
- **指标**：R@1/5/10/30、MRR@5、救回/挤掉、候选池覆盖率、延迟、token、费用、降级率。

**校准先行**（项目既定纪律）：先复刻现行 v7 管线在留出金标上的权威数字，再采信任何结论。
本机复现：local **R@5 66.0%（66/100）、R@30 89.0%、R@10 72.0%、MRR@5 0.455**——与
`docs/metrics.json` 的 `blind_v8_holdout` 逐位一致，实验台校准通过。
**一处如实披露**：本次量到的 local **R@1 是 32.0%**，而 `docs/retrieval-llm-rerank.md` §1
记的是 33.0%，差 1 题（落在 ±2 题的单次抖动范围内，不影响任何结论：R@5/R@10/R@30/MRR
四项完全一致）。本报告一律用本次实测值，README 里原有的 33.0% 未改动。

## 1. Jev 是什么、为什么不能直接替换

`llm_rerank` 的裁判是**生成式**的：把候选编号列表发给 chat 模型，模型**输出一个自由文本
JSON 数组**（`[7,3,1]`）。Jev 是**判选式**的 System One 模型：

| | 现行 LLM 层 | Jev |
|---|---|---|
| 端点 | `/zen/go/v1/chat/completions` | `/zen/v1/systemone` |
| 输入 | `messages` | `state` + typed `questions` |
| 输出 | 自由文本 JSON 数组 | typed 答案：`choice` 标签+概率 / `score` 分值 / `noul` 概率 |
| 能吐「排好序的列表」吗 | 能 | **不能**——只给每个候选一个判断 |

所以 Jev **不能一行替换**。本层把「候选排序」转成「候选判选」，两种模式：

- **choice（默认）**：把 N 个候选作为**单个 choice 问题的 criteria**，一次调用拿回
  **覆盖全部候选的概率分布**，按概率降序取头部——最贴合重排语义，一次请求、最快；
- **score**：给**每个候选一个 score 问题**（并行问同一 state），按分值降序取头部。

两模式都遵守 `interfaces.py` 契约：只重排/截断、不发明条目、不丢条目、失败原序降级；
默认「只提前、不挤掉」（把得分最高的至多 `pick` 条提到最前，其余保持原序）。

## 2. 实现

- 新模块 `statute_rag/jev_rerank.py`：`JevReranker` / `JevRerankRetriever`。
  - 依赖纪律：只用标准库 `urllib`，**不在核心 import 链上**；
  - 契约：`rerank(query, citations, k)` → 只提前至多 `pick` 条、其余原序，失败原序降级；
  - **判分视图**：返回的每条 Citation 附 `jev_score`（0–1）、`jev_rank`（Jev 内部名次）、
    `jev_pick`（是否被提前），供前端展示（shallow copy，不改原字段）；
  - 配置：`STATUTE_RAG_JEV_BASE / _KEY / _MODEL / _TOP_N / _PICK / _MODE /
    _MAX_CAND_CHARS / _TIMEOUT`；免费档无 key 也可构造，付费档带 Bearer key。
- 新评测脚本 `scripts/eval_jev_rerank.py`：同一次运行内并列 `local / jev / llm`，
  候选池可缓存复用；输出每题名次 + 指标 + 延迟 + token + 费用。
- 新单测 `tests/test_jev_rerank.py`（17 例，不联网）：契约、并列稳定、pick 上限、
  两模式、失败降级（4xx 快失败 / 5xx 退避重试）、环境变量配置、`top_n` 加深召回。

## 3. 结果：效果（v8 留出 100 题，同一份候选池）

| 裁判 | R@1 | R@5 | R@10 | R@30 | MRR@5 | 救回/挤掉 | 降级 |
|---|---|---|---|---|---|---|---|
| local（现行 v7 管线，无裁判） | 32.0% | 66.0% | 72.0% | 89.0% | 0.455 | — | — |
| Jev choice（免费 `jev-1.13-free`） | 84.0% | 91.0% | 92.0% | 92.0% | 0.872 | 26 / 1 | 0 |
| Jev score（免费） | 81.0% | 92.0% | 92.0% | 92.0% | 0.859 | 26 / 0 | 0 |
| **Jev choice（付费 `jev-1.13`）** | **84.0%** | **92.0%** | 92.0% | 92.0% | **0.874** | 26 / 0 | 0 |
| **Jev score（付费）** | 82.0% | **92.0%** | 92.0% | 92.0% | 0.864 | 26 / 0 | 0 |
| chat longcat-2.5-preview-free（同批） | 82.0% | 92.0% | 92.0% | 92.0% | 0.865 | 26 / 0 | 0 |
| DeepSeek deepseek-v4.1-flash（历史 pilot） | 85.0% | 92.0% | 92.0% | — | — | — | 0 |
| DeepSeek deepseek-flash top50（历史 pilot） | 86.0% | 92.0% | 92.0% | — | — | — | 0 |
| DeepSeek deepseek-flash top100（历史 pilot） | 85.0% | 93.0% | 93.0% | — | — | — | 0 |

要点：

- **Jev（付费）与 chat 裁判、DeepSeek 三档在 R@5 上同处 92.0%（=top50 天花板，8 道金标在
  前 50 之外，属召回缺口，重排无法触及）；R@1 上 Jev choice 的 84.0% 高于 chat 裁判 longcat
  的 82.0%，仅略低于 DeepSeek 的 85–86%**。
- 免费与付费差异（choice 91 vs 92、demote 1 vs 0）在 ±2 题噪声内；**付费档两模式都 0 挤掉**。
- 与现行 LLM 层同形：只提前、不丢条目，救回 26 条全部落在 6–50 名区间。

## 4. 结果：速度

| 裁判 | 单次延迟 p50 | p95 | 备注 |
|---|---|---|---|
| local 管线（CPU 检索，不含裁判） | 1940 ms/题 | — | 本机 CPU 单线程，100 题 193.9 s |
| **Jev choice（付费）** | **1438 ms** | 2408 ms | 并发 5 下墙钟 |
| Jev score（付费） | 1642 ms | 2900 ms | 载荷更大（候选进 state） |
| Jev choice（免费） | 1414 ms | 2577 ms | 干净跑次（degraded=0） |
| chat longcat-2.5-preview-free（同批实测） | 3614 ms | **22984 ms** | 历史 pilot p50 3960 ms；**p95 达 23 s，长尾极重** |
| DeepSeek deepseek-v4.1-flash | 2750 ms | — | 历史 pilot |
| DeepSeek deepseek-flash（付费平台） | 690 ms | — | 历史 pilot |
| space-bunny-free | 2980 ms | — | 历史 pilot |

**单次判断速度专项探针**（付费 `jev-1.13`，顺序调用，线上真实量级载荷）：
- choice：**min 1167 / p50 1278 / p95 2464 ms**（输入 8030 / 输出 408 tokens）；
- score：min 1222 / p50 1332 / p95 2538 ms（输入 10260 / 输出 745 tokens）。

即**一次「给定问题 + 最多 50 条候选」的判选，中位约 1.2–1.3 秒**；并发不抬高单次延迟（见 §6）。

> 值得单独记一笔：**Jev 的 p95（2.4–2.9 s）比 chat 裁判稳得多**——同批 longcat 虽然 p50
> 3.6 s，但 p95 冲到 **23 s**（长尾请求）。对交互式界面来说「最坏那几秒」比中位数更影响体感。

## 5. 结果：成本

按各自模型响应里回传的 token 计（各模型分词器不同，跨模型 token 数不可直接比，费用按各自单价）：

| 裁判 | 输入 tokens / 题 | 输出 tokens / 题 | 100 题费用 | 说明 |
|---|---|---|---|---|
| **Jev choice（付费）** | **8755** | 408 | **$0.0368**（$0.00037/题） | 实测 875,537 / 40,826 |
| Jev score（付费） | 11723 | 745 | $0.0492（$0.00049/题） | 实测 1,172,291 / 74,500 |
| Jev（免费档） | — | — | **$0** | 限时免费，但有限流（见 §6） |
| chat longcat（同批实测，免费档） | 4176 | 15 | $0 | 实测 417,573 / 1,466；限时免费档 |
| DeepSeek ds-v4.1-flash | 4242 | 13 | 套餐内；列表价折 $0.0644 | OpenCode Go $10/月订阅内 |
| DeepSeek deepseek-flash | 4242 | 13 | 平台付费≈0.9 分/次（≈¥0.009） | 项目既有文档记的参照价，非本次实测 |

- Jev 的计价：**输入 $0.042/M、输出免费**（Zen/Console 价目表）。
- **单次成本 = 一次请求的全部候选判选**；Jev choice 约 $0.00037/题，比 DeepSeek 列表价折算还低，
  且输出免费（概率数组不计费）。
- **关于「0.9 分/次」这个看着离谱的数**：它来自项目既有文档
  `docs/retrieval-llm-rerank.md`（标注"高峰价"），单位是平台计价的**分**（≈¥0.01），
  **不是本次实测**。按 pilot 实测 token 复核：每次 4242 输入 + 13 输出；top100 版本输入 token
  翻倍（886,125）时费用也从 0.9 涨到 1.8 分——与「费用线性于 token」自洽。所以它不是错价，而是
  **重排这种小请求本来就极便宜**：一个 prompt + 十几 token 的 JSON 序号数组，输出几乎不花钱。

## 6. 可靠性与并发（重点披露）

- **免费档会限流**：持续调用后（本任务当天数百次）端点返回
  `HTTP 429 FreeUsageLimitError: Rate limit exceeded`。实测：早段干净跑次 degraded=0，
  后段加压跑次 **62/100、100/100 降级**（降级 = 原序返回 = 该题等于没接裁判）。
  → **生产必须用付费 key，或接受降级兜底**。
- **付费档稳定**：2×100 次调用 degraded=0。

单次速度与并发上限专项探针（付费 `jev-1.13`，真实 50 候选载荷，单 key 单机）：

| 档位 | 结果 |
|---|---|
| 顺序单次（choice） | min 1167 / **p50 1278** / p95 2464 ms（输入 8030 / 输出 408 tokens） |
| 顺序单次（score） | min 1222 / p50 1332 / p95 2538 ms（输入 10260 / 输出 745 tokens） |
| 并发 1 / 2 / 4 / 8 | 全成功、**0×429**，墙钟 1.2–2.2 s，成功请求 p50 1202–1292 ms |
| 并发 16 | 16/16 成功，墙钟 1.5 s，≈10.5 req/s，p50 1237 ms |
| 并发 32 | 32/32 成功，墙钟 3.6 s，≈8.8 req/s，p50 1246 ms |
| **并发 64** | **64/64 成功，0×429**，墙钟 2.8 s，**≈22.5 req/s**，p50 1282 ms |
| 顺序连续 60 次 | 60/60 成功，79.6 s，0.75 req/s（延迟受限） |

结论：**付费档单 key 实测到 64 并发零失败、零 429，且单次延迟不随并发上升（稳定在
1.2–1.3 s）**——服务端横向扩展好，适合把重排层并发化（评测里我们正是并发 5 跑完 100 题）。
64 并发瞬时约 *64×8k≈0.5M tokens / 2.8 s ≈ 18 万 tokens/s*，已接近 TypeSafe 公布的
25 万 tokens/s 量级，**再往上可能触发 token/秒限流**，建议生产把并发控制在几十以内。
**免费档完全不同**：并发很低就会 429（本任务当天即被打满），不能用于生产。

## 7. 失败分析

- **8 道召回缺口（local 与 Jev 相同，重排无解）**：`b8b-010` 非法吸收公众存款、`b8b-019`
  缓刑、`b8c-002` 侵权管辖、`b8c-004` 诉前保全、`b8c-009` 行诉受案范围、`b8c-018` 不执行拘留、
  `b8c-023` 人民调解自愿、`b8d-018` 商业秘密——与 `docs/retrieval-llm-rerank.md` §4 记录完全一致，
  属**召回侧**缺口（编/章标题进索引、法律领域向量、问句改写）。
- **免费 choice 唯一挤掉题**：`b8d-021`「保健品吹成治百病」金标《反不正当竞争法》第九条，
  local 第 3 → Jev 第 7；Jev 把「消保条例第15条」「食品安全法第140条」等**语义近邻**排到了前面。
  该题多部法都规范「虚假宣传」，属真实歧义；**付费档 choice/score 均未挤掉**。

## 8. 前端展示（本次新增）

- `scripts/app.py`：`build_state(..., auto_rerank=..., prefer=...)` 默认优先 Jev（免费档），
  新增 `--reranker {auto,jev,llm,off}`；`deep_rerank` 返回
  `kind / model / mode / pick / ms / input_tokens / output_tokens / confidence / degraded / score_scale`；
  每条结果带 `jev_score / jev_rank / jev_pick`。
- `app/index.html`：每条结果底部显示 **「Jev 判分 0.72 ▮▮▮」**（细条 = 分数，被选中的前 5 条标蓝），
  悬停显示「Jev 内部排名第 N / 是否被提前」；状态行显示 **「Jev 判选 · jev-1.13 · choice 模式」**
  与图例「Jev 判分 0–1（越高越可能最相关），据此把前 5 条提前」，悬停状态标签可见本次重排
  ms 与 token 用量。前端与后端同一份数据，不引入第二套逻辑。
- 兼容：`llm_reranker` 旧参数名保留；未启用重排时前端照旧显示「深度精排未启用」。
- 单测：`tests/test_jev_rerank.py` 17 例 + `tests/test_app.py` 新增 2 例（不联网，注入桩 Jev）。

## 9. 结论与建议

1. **Jev 可以替代 chat 裁判**：付费 `jev-1.13` 在留出集上 R@5=92.0%（=天花板）、0 挤掉，
   与 longcat / DeepSeek 同档；**R@1（choice 84.0%）在 chat 裁判里最高（longcat 82.0%），
   仅略低于 DeepSeek 的 85–86%**；单次更快（1.3 s vs 2.75–3.96 s）、更便宜，且并发扩展好。
2. **生产用付费档**：免费档会 429，只适合试用。
3. **模式选择**：追求 R@1/头部用 **choice**（84%）；追求 R@5 用 **score**（92%，但更慢更贵）。
4. **Jev 更适合的位置其实是「是/否门」**（其设计主场）：gateA 置信门、v0.3「检索失败就拒答」
   护栏、领域/通道路由——这些是单次、便宜的 typed 判断，比重排更贴 Jev 形态。
5. 重排只能吃「检到了但排不进前五」的红利；**8 道召回缺口仍是召回侧工作**。

## 10. 复现

```bash
# 单测（不联网）
py -3.13 -m unittest tests.test_jev_rerank
py -3.13 -m unittest tests.test_app

# 全链路：本地管线取候选池 + Jev 判选（首跑落盘候选池，其后复用）
#   免费档：不设 STATUTE_RAG_JEV_KEY
#   付费档：STATUTE_RAG_JEV_KEY=<oc_sk_...> STATUTE_RAG_JEV_MODEL=jev-1.13
py -3.13 scripts/eval_jev_rerank.py --workers 5 --pools data/flk/tmp/pools --tag paid_choice
py -3.13 scripts/eval_jev_rerank.py --workers 5 --pools data/flk/tmp/pools --tag paid_score --jev-mode score

# 同批复核 chat 裁判基线（需 STATUTE_RAG_LLM_KEY）
py -3.13 scripts/eval_jev_rerank.py --workers 5 --pools data/flk/tmp/pools --tag v8llm --llm

# 单次速度 + 并发探针
STATUTE_RAG_JEV_MODEL=jev-1.13 STATUTE_RAG_JEV_KEY=... \
  py -3.13 data/flk/tmp/jev_speed_probe.py

# Web 前端（默认优先 Jev）
py -3.13 scripts/app.py --corpus data/corpus_v7.jsonl
```

> 数字随模型版本变化（本文件注明日期 2026-10-04；付费 `jev-1.13` 单次测量，temperature 0
> 仍有 ±2 题噪声）；重排数字不逐位可复现。
