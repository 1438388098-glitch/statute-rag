# -*- coding: utf-8 -*-
"""重排通道接口约定（v0.2 预留，本文件只含约定与说明，不引入依赖）。

背景（实测，见 docs/eval_report.md 深度口径；当前口径 = v3 语料 25,273 条 / 432 部）：
真实问句上 hybrid 的 Recall@5=44.7% 而 Recall@30=81.6%——38 题中 14 题
已检到但排不进前 5，输在排序而非召回。重排通道（embedding 相似度 /
cross-encoder 等）是吃下这段「检到了但排不进前 5」红利的机制，接口在此
先冻结，模型后接。（v1 语料上这两项数字**完全相同**：R@5 44.7%、
R@30 81.6%、同样 14 题位于 6–30 名；v3 的诊断与重标定见
docs/retrieval-v3-diagnosis.md。）

Reranker 约定（鸭子类型，与检索三件套同风格）：

    class MyReranker(object):
        def rerank(self, query, citations, k):
            ...

- 入参：query (str)；citations（统一 Citation dict 列表，通常来自
  HybridRetriever.recall 的完整融合排名）；k (int)。
- 出参：同一批条文的**重新排序视图**（至多 k 条）。每条仍须含
  id/law/num/text/score/retriever 字段——上层强制引用与回跳校验依赖
  它们，不得删除或改名；允许新增字段（如 rerank_score）。
- 语义：只改变顺序（及截断），不得凭空造条目或丢弃金标可达性。

依赖纪律：语义模型/API 客户端（embedding、HTTP 等）不得进入
statute_rag 核心 import 链——放在独立模块内延迟 import，ImportError
时报带安装指引的异常；核心保持零第三方依赖（见 pyproject.toml）。

接入点：HybridRetriever(corpus, reranker=MyReranker())。reranker=None
时与已发布评测口径（k≤15）逐位一致（降级即现状）；接重排器后建议用
recall(query, depth=100) 加深召回池喂饱重排（深度扫描见
docs/retrieval-improvement.md R2d）。
"""
