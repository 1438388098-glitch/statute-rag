# -*- coding: utf-8 -*-
"""语义重排器（v7，接口见 interfaces.py，鸭子类型 rerank）。

机制（全部经过盲写/真实/合成三套金标实测，见 docs/retrieval-v7-semantic.md）：

1. **gateA 置信门**：LIKE 通道对查询有精确子串命中（查询即条文短语，合成
   金标形态）时原序返回——词法已确定命中，语义融合只会搅乱排名；
2. **多模型集成名次**：bge-small-zh-v1.5 与 bge-base-zh-v1.5 各编码一次全库，
   逐条文取两模型名次较优者；每个模型内部再对「原查询 / 同义扩展查询」
   两种编码取名次较优者。四种组合取 min 比单模型多救 4 道真实题；
3. **语义名次取自全库**（25,626 条）而非候选池内——池外条文的相对位置才有
   意义，池的扩充由 SemanticPool 在召回层完成（重排器不得新增条目）；
4. **融合**：`fused = 1/(K+hybrid名次) + W_SEM/(K+语义名次)`；
5. **交叉编码器与融合名次的 RRF 混合**：对融合前 cross_top_n 名逐对（问句,
   条文）打分，再按 `1/(K+融合名次) + W_CROSS/(K+交叉名次)` 定序——不用交叉
   分**替换**融合顺序。实测替换式会丢掉词法+语义证据，伤真实题（31→27）；
   混合式在保持真实题 71.1% 的同时把盲写题推到 90%。

依赖纪律（interfaces.py 同款约定）：本模块不进核心 import 链，numpy /
transformers 全部延迟 import；`reranker=None` 时检索行为与已发布口径逐位一致。
"""
import os

W_SEM = 2.0
RRF_K = 60
CROSS_TOP_N = 10
W_CROSS = 3.0
QUERY_PREFIX = u"为这个句子生成表示以用于检索相关文章："

_STATE = {"models": {}, "cross": None, "cross_tok": None, "cross_dir": None}


def _require_numpy():
    try:
        import numpy
    except ImportError:
        raise RuntimeError(u"语义重排需要 numpy：py -3.13 -m pip install numpy")
    return numpy


def _require_model(model_dir):
    if model_dir in _STATE["models"]:
        return _STATE["models"][model_dir]
    try:
        from transformers import AutoModel, AutoTokenizer
    except ImportError:
        raise RuntimeError(
            u"语义重排需要 transformers 与 torch：py -3.13 -m pip install "
            u"transformers torch --index-url https://download.pytorch.org/whl/cpu")
    tokenizer = AutoTokenizer.from_pretrained(model_dir)
    model = AutoModel.from_pretrained(model_dir)
    model.eval()
    _STATE["models"][model_dir] = (tokenizer, model)
    return tokenizer, model


def _require_cross(cross_model_dir):
    if _STATE["cross"] is not None and _STATE["cross_dir"] == cross_model_dir:
        return _STATE["cross_tok"], _STATE["cross"]
    try:
        from transformers import (AutoModelForSequenceClassification,
                                  AutoTokenizer)
    except ImportError:
        raise RuntimeError(
            u"交叉重排需要 transformers 与 torch：py -3.13 -m pip install "
            u"transformers torch --index-url https://download.pytorch.org/whl/cpu")
    tok = AutoTokenizer.from_pretrained(cross_model_dir)
    model = AutoModelForSequenceClassification.from_pretrained(cross_model_dir)
    model.eval()
    _STATE["cross_tok"] = tok
    _STATE["cross"] = model
    _STATE["cross_dir"] = cross_model_dir
    return tok, model


def encode_query(query, model_dir):
    """查询 -> 归一化向量（CLS 池化，带 bge 检索指令前缀）。"""
    import torch

    tokenizer, model = _require_model(model_dir)
    enc = tokenizer([QUERY_PREFIX + query], padding=True, truncation=True,
                    max_length=512, return_tensors="pt")
    with torch.no_grad():
        hidden = model(**enc).last_hidden_state[:, 0]
        hidden = torch.nn.functional.normalize(hidden, p=2, dim=1)
    return hidden[0].numpy().astype("float32")


class SemanticPool(object):
    """召回池扩充器：把全库语义 top-K 并进候选池（重排只动顺序，不新增）。

    `HybridRetriever(corpus, reranker=..., pool_extra=SemanticPool(...))` 时，
    池 = hybrid 融合排名 ∪ 语义 top-K（按序去重追加）。这不是重排而是召回：
    重排器契约禁止新增条目，池外条文必须先由召回层带进来。

    动机：盲写题有 8 题金标根本不在 hybrid 前 100，其中 3 题语义名次在
    31–35——并池让它们进入重排视野（盲写 88%→89%）。
    """

    def __init__(self, corpus, emb_specs, model_dirs, synonyms=None,
                 union_k=50):
        numpy = _require_numpy()
        for path in emb_specs:
            if not os.path.exists(path):
                raise RuntimeError(
                    u"条文向量文件不存在：%s（先跑 scripts/build_embeddings.py 生成）"
                    % path)
        self._numpy = numpy
        self._corpus = corpus
        self._ids = numpy.load(emb_specs[0])["ids"]
        self._vecs = [numpy.load(p)["vecs"].astype("float32")
                      for p in emb_specs]
        self._model_dirs = model_dirs
        self._union_k = union_k
        if synonyms is None:
            from statute_rag.query_expansion import load_synonyms
            synonyms = load_synonyms()
        self._synonyms = synonyms

    def semantic_ranks(self, query):
        """全库语义名次（行序）：多模型 × 原/扩展查询，四种组合取 min。"""
        from statute_rag.query_expansion import expand_query
        numpy = self._numpy
        expanded = expand_query(query, self._synonyms)
        variants = [query] if expanded == query else [query, expanded]
        best = None
        for vecs, mdir in zip(self._vecs, self._model_dirs):
            for v in variants:
                sims = vecs @ encode_query(v, mdir)
                rank = numpy.empty(len(sims), dtype=numpy.int64)
                rank[numpy.argsort(-sims, kind="stable")] = numpy.arange(
                    1, len(sims) + 1)
                best = rank if best is None else numpy.minimum(best, rank)
        return best

    def search(self, query, k):
        """语义 top-k（行序 → Citation dict）。k 缺省取 union_k。"""
        numpy = self._numpy
        k = k or self._union_k
        rank = self.semantic_ranks(query)
        order = numpy.argsort(rank, kind="stable")[:k]
        out = []
        for row in order.tolist():
            item = self._corpus[row]
            out.append({"id": item["id"], "law": item["law"], "num": item["num"],
                        "text": item["text"],
                        "score": 1.0 / (RRF_K + int(rank[row])),
                        "retriever": "semantic"})
        return out


class SemanticReranker(object):
    """嵌套语义重排：多模型语义名次 + 交叉编码器 RRF 混合。"""

    def __init__(self, corpus, emb_specs, model_dirs, pool=None,
                 cross_model_dir=None, cross_top_n=CROSS_TOP_N,
                 w_sem=W_SEM, w_cross=W_CROSS, rrf_k=RRF_K):
        if not os.path.exists(emb_specs[0]):
            raise RuntimeError(
                u"条文向量文件不存在：%s（先跑 scripts/build_embeddings.py 生成）"
                % emb_specs[0])
        self._pool = pool or SemanticPool(
            corpus, emb_specs, model_dirs, union_k=1)
        self._rows = dict((row["id"], row) for row in corpus)
        self._cross_model_dir = cross_model_dir
        self._cross_top_n = cross_top_n
        self._w_sem = w_sem
        self._w_cross = w_cross
        self._rrf_k = rrf_k

    def _cross_scores(self, query, citations):
        import torch

        tok, model = _require_cross(self._cross_model_dir)
        texts = []
        for cite in citations:
            row = self._rows.get(cite["id"])
            texts.append(u"%s %s %s" % (row["law"], row["num"], row["text"])
                         if row is not None else (cite.get("text") or u""))
        out = []
        with torch.no_grad():
            for s in range(0, len(texts), 16):
                enc = tok([query] * len(texts[s:s + 16]), texts[s:s + 16],
                          padding=True, truncation=True, max_length=512,
                          return_tensors="pt")
                out.extend(model(**enc).logits.view(-1).float().tolist())
        return out

    def rerank(self, query, citations, k):
        if not citations:
            return []
        rank = self._pool.semantic_ranks(query)
        best_rank = int(rank.max()) + 1  # 不在语料中的 id 垫底
        fused = []
        for pos, cite in enumerate(citations):
            crow = self._find_row(cite["id"])
            sem_rank = int(rank[crow]) if crow is not None else best_rank
            score = (1.0 / (self._rrf_k + pos + 1)
                     + self._w_sem / (self._rrf_k + sem_rank))
            fused.append([score, pos, cite, sem_rank])
        fused.sort(key=lambda t: (-t[0], t[1]))

        if self._cross_model_dir is not None and self._cross_top_n > 1:
            head = fused[:self._cross_top_n]
            scores = self._cross_scores(query, [t[2] for t in head])
            cross_rank = [0] * len(head)
            for cross_pos, j in enumerate(
                    sorted(range(len(head)), key=lambda j: (-scores[j], j))):
                cross_rank[j] = cross_pos + 1
            blended = []
            for j, item in enumerate(head):
                score = (1.0 / (self._rrf_k + j + 1)
                         + self._w_cross / (self._rrf_k + cross_rank[j]))
                blended.append((score, j, item))
            blended.sort(key=lambda t: (-t[0], t[1]))
            fused = [t[2] for t in blended] + fused[self._cross_top_n:]

        out = []
        for score, _pos, cite, sem_rank in fused[:k]:
            view = dict(cite)
            view["rerank_score"] = round(score, 8)
            view["semantic_rank"] = sem_rank
            out.append(view)
        return out

    def _find_row(self, cid):
        """id -> 全库行号（向量行序与语料行序一致，由 build_embeddings 保证）。"""
        if not hasattr(self, "_id2row"):
            self._id2row = dict((int(c), i) for i, c in
                                enumerate(self._pool._ids))
        return self._id2row.get(cid)
