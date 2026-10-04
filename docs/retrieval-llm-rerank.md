# LLM 重排可选层（2026-10-04）

## 0. 这层是什么、诚实在哪

在 v7 语义重排管线**之后**再插一层大模型裁判：取运行时完整管线（词法三通道 → 语义并池 + 交叉编码器）的**前 50 名**候选，连同用户问题发给大模型，让它挑出「最能回答该问题」的至多 5 条并排序，程序把这几条提到最前，其余保持原序。契约与 `statute_rag/interfaces.py` 一致：**只重排、不发明条目、不丢条目**；API 失败/超时/解析失败一律原序返回（自动降级），绝不打断检索。

诚实声明（先于数字）：

- 调参只发生在调参集（p31 / real38）；**v8 留出盲写题每个配置只测一次**。
- temperature=0 的 API 调用不逐位可复现，单次测量有 ±2 题噪声；本文数字注明模型与日期（2026-10-04）。
- 本层默认关闭，构造需显式 API key（环境变量）；核心运行时零第三方依赖不变（本模块只用标准库 urllib，且不在核心 import 链上）。
- 92.0% 恰好是候选深度的天花板（见 §4）：重排器只能重排送进来的候选，**8 道金标不在前 50 的题它无法触及**——这是召回缺口，不是排序缺口。

## 1. v8 留出盲写题认证结果（100 题，gold/gold_blind_v8_v7.jsonl）

基线（不接 LLM，v7 管线）：R@5 66.0%（66/100）、R@1 33.0%、R@10 72.0%。

| 配置 | R@5 | R@1 | 救回/挤掉 | 延迟 p50 | 费用 |
|---|---|---|---|---|---|
| longcat-2.5-preview-free（OpenCode 免费档） | **92.0%（92/100）** | 82.0% | 26 / 0 | 3.9s | 免费（限时免费档） |
| deepseek-v4.1-flash（OpenCode Go 套餐内） | **92.0%（92/100）** | 85.0% | 26 / 0 | 2.8s | 套餐内（本轮评测耗滚动窗口 1%） |
| space-bunny-free（OpenCode 免费档） | 91.0%（91/100） | 82.0% | 25 / 0 | 3.0s | 免费 |
| deepseek-flash 平台付费参照（top50） | 92.0%（92/100） | 85.0% | 26 / 0 | 0.7s | ≈0.9 分/次（高峰价） |
| deepseek-flash 平台付费参照（top100） | 93.0%（93/100） | 85.0% | 27 / 0 | ~1s | ≈1.8 分/次（高峰价） |

所有配置共同参数：top_n=50、pick=5、temperature=0、关思考（space-bunny 只认 `reasoning_effort=low`，实际零思考输出）、并发 5（评测）、3 次 429/5xx 退避重试。**92.0% 已打满 top50 天花板**（§4）。

调参集（配置选择只在这里发生）：

| 集合 | 基线 | longcat | deepseek-v4.1-flash | space-bunny |
|---|---|---|---|---|
| p31（31 题真转述） | 74.2% | 83.9%（26/31） | 83.9% | 83.9% |
| real38（38 题真实问句） | 71.1% | 86.8%（33/38） | 89.5%（34/38） | 84.2%（32/38） |

deepseek-v4.1-flash 经 OpenCode 网关与平台付费版 deepseek-flash 在 p31/real38 **逐题一致**（同一模型的网关转发）。附带效果：交叉编码器层对真实问句的净亏（71.1% 对关掉交叉的 81.6%）被 LLM 层填平并反超。

## 2. 为什么是这层：诊断回放

v8 优化轮（docs/retrieval-v8-optimization.md）扫了 15 个本地变体全部无效，诊断结论：瓶颈是 CPU 小模型（bge-reranker-base，110M 参数）对「语义近邻 vs 真正答案」的判别力——34 道未进前五的题里 29 道的答案在语义前 30 名。LLM 裁判读条文全文做判断，判别力高一个量级，实测把送进来的候选几乎无损地排对了位（26/26 救回、0 挤掉）。

## 3. 接入与配置

```python
from statute_rag.llm_rerank import LLMReranker, LLMRerankRetriever, enabled

if enabled():  # env STATUTE_RAG_LLM_KEY 已配置
    llm = LLMReranker()               # 其余配置读 STATUTE_RAG_LLM_BASE/_MODEL/_TOP_N/_PICK/_THINKING/_TIMEOUT
    retriever = LLMRerankRetriever(base_retriever, llm)
```

环境变量（均有默认，见 `statute_rag/llm_rerank.py` 模块头）：

| 变量 | 默认 | 说明 |
|---|---|---|
| `STATUTE_RAG_LLM_KEY` | （无，必填） | API key；不配置即不启用本层 |
| `STATUTE_RAG_LLM_BASE` | `https://opencode.ai/zen/go/v1` | OpenAI chat/completions 兼容端点 |
| `STATUTE_RAG_LLM_MODEL` | `longcat-2.5-preview-free` | 认证模型；deepseek-v4.1-flash 同分更快（套餐内） |
| `STATUTE_RAG_LLM_TOP_N` | 50 | 送入 LLM 的候选深度 |
| `STATUTE_RAG_LLM_PICK` | 5 | LLM 挑出提前的条数 |
| `STATUTE_RAG_LLM_THINKING` | `disabled` | `disabled`/`low`/其他=不发自定义参数 |
| `STATUTE_RAG_LLM_TIMEOUT` | 120 | 单次请求超时（秒） |

注意：思考模型**不设 max_tokens**——设了上限会被 reasoning 烧穿、正文截断（deepseek 800 与 glm 2048/8192 均实测验证）。输出只解析一个 JSON 序号数组，越界/重复/负数序号一律丢弃。

## 4. 天花板：92% 的 8 道漏题在哪

候选深度的天花板离线可算：v8 的金标在前 30 名的 89 题、前 50 名 92 题、前 100 名 93 题。重排器只能重排送进来的候选，因此 top50 配置的 R@5 上限就是 92%。92.0% 的实测 = 天花板打满：26 道原在 6–50 名的题全部救回，0 道被挤掉；剩 8 道金标不在前 50（7 道不在前 100、1 道第 57 名），全是**抽象规则的口语转述**：

| 题面 | 金标 | 管线名次 |
|---|---|---|
| 向不特定的人承诺还本付息来借钱，最后还不上，这算什么罪 | 刑法 第176条（非法吸收公众存款） | 前100外 |
| 判了两年刑，什么情况下可以不用马上进监狱服刑 | 刑法 第72条（缓刑） | 57 |
| 被人网上造谣诽谤，我可以到哪里的法院去告他 | 民诉法 第29条（侵权管辖） | 前100外 |
| 怕对方偷偷把房子卖了，还没起诉能不能先请法院冻结他的财产 | 民诉法 第104条（诉前保全） | 前100外 |
| 对政府部门作出的处罚或者拘留，当事人能否到法院起诉 | 行诉法 第12条（受案范围） | 前100外 |
| 按规定本应送拘的人，哪些情况可以不实际关押 | 治安管理处罚法 第23条（不执行拘留） | 前100外 |
| 调解组织能不能硬逼我接受结果，我还能去法院告吗 | 人民调解法 第3条（自愿原则） | 前100外 |
| 员工跳槽后带走原公司的客户名单，算不算违法 | 反不正当竞争法 第10条（商业秘密） | 前100外 |

这 8 道与 v8 优化轮记录的「已知失败」完全重合：语义近邻全是同领域程序性条文（侵权管辖 vs 诉前保全、受案范围的长列举……），金标挤不进候选池头部。**改进方向在召回侧**（编/章标题进索引、法律领域向量模型、LLM 问句改写），换更好的裁判无济于事——本层对送进来的 92 题已零损耗。

## 5. 复现

```bash
# 评测（需 STATUTE_RAG_LLM_KEY 等环境变量；v8 金标 + 三套默认金标）
py -3.13 scripts/eval_semantic_rerank.py --corpus data/corpus_v7.jsonl \
  --emb data/flk/tmp/ctx_doc_cache_v7.npz --emb-base data/flk/tmp/ctx_doc_cache_base_v7.npz \
  --llm --tag llm
# 单测（13 例契约/降级/配置测试，不联网）
py -3.13 -m unittest discover -s tests
```

逐题明细与参数矩阵（top30/50/100、思考开关、截断/去前缀、GLM 免费档对照）见仓库外评测存档 `data/flk/tmp/llm_pilot_result_*.json` 与 `llm_free_eval_report.html`（data/ 不入库，报告随仓库文档归档为准的是本文件与 metrics.json）。
