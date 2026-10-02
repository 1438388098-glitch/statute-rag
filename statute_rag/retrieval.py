# -*- coding: utf-8 -*-
"""检索器三件套：LIKE 基线 / BM25 字符二元组 / RRF 混合融合。

设计约定：
- 语料单元是「条」而非「部」——法律检索的天然粒度（见 importer.py）；
- 所有检索器的 search() 返回统一的引用结构（Citation dict），
  保证「任何命中都带条文出处」，这是上层问答强制引用的基础；
- 全部零第三方依赖：BM25 用纯 dict 倒排，中文不依赖分词器（字符二元组）。

已知边界：
- 字符二元组 BM25 是无语义的词法检索；向量语义检索（embedding + 重排）
  属于 v0.2 的规划内容，需要可用的中文 embedding 通道后接入；
- LIKE 基线模拟的是 legal-wisdom 在 unicode61 分词下「中文子串实际走
  模糊匹配」的行为，作为对比下界。

真实问句改进（v0.1.1，机制级，非按题调参；逐轮数字与消融见
docs/retrieval-improvement.md）：
- 多查询扩展召回：hybrid 在 RRF 中增加「替换式变体」与「追加式扩展」
  BM25 路（口语↔法言法语词典 + 数字读法归一，见 query_expansion.py /
  synonyms.json）——这是实测带来真实问句召回提升的核心机制；
- 曾试过「法名先验加权」「查询 trigram 短语加权」两个 BM25 旋钮：
  真实金标 Recall@5 零增量、MRR 略降，已按消融结论移除（负结果记录在
  docs/retrieval-improvement.md）。
"""

import math

from statute_rag.query_expansion import expand_query, load_synonyms

BM25_K1 = 1.5
BM25_B = 0.75
RRF_K = 60  # RRF 常数：抑制排名靠后结果的权重

# hybrid 融合通道深度（消融测量后定值，见 docs/retrieval-improvement.md）。
# 深度属于检索配置而非返回条数：跨 k 评测须用同一深度取排名再截取。
HYBRID_FUSION_DEPTH = 30


def _normalize(text):
    """检索前归一化：压缩空白。"""
    return " ".join((text or "").split())


def char_ngrams(text, n=2):
    """中文字符 n-gram（去除空白与标点干扰后切分）。

    单字查询自动退化为 unigram，保证短查询也能命中。
    """
    s = "".join((text or "").split())
    if len(s) < n:
        return list(s)
    return [s[i:i + n] for i in range(len(s) - n + 1)]


def query_grams(query, n=2):
    """查询侧 n-gram：按空格分段切 gram，gram 不跨段。

    与 char_ngrams 的差别仅在带空格的输入（如扩展查询「原词 同义词」）：
    gram 不跨词边界，避免产生「询服」这类边界噪声 gram。语料侧索引仍用
    char_ngrams（无空格语义），金标生成逻辑不受影响。
    """
    grams = []
    for part in (query or "").split():
        if len(part) < n:
            grams.extend(list(part))
        else:
            grams.extend(part[i:i + n] for i in range(len(part) - n + 1))
    return grams


def _citation(item, score, retriever):
    return {
        "id": item["id"],
        "law": item["law"],
        "num": item["num"],
        "text": item["text"],
        "score": round(float(score), 6),
        "retriever": retriever,
    }


class LikeRetriever(object):
    """子串匹配基线：query 是 article text 的子串才算命中。

    对应「精确子串」这一最保守的检索口径；排名按命中位置靠前优先。
    """

    def __init__(self, corpus):
        self.corpus = [_normalize(x) if isinstance(x, str) else x for x in corpus]
        # 允许传入 dict（importer 产物）或 str
        self._texts = [x["text"] if isinstance(x, dict) else x for x in self.corpus]

    def search(self, query, k=5):
        q = _normalize(query)
        hits = []
        for i, text in enumerate(self._texts):
            pos = text.find(q)
            if pos >= 0:
                hits.append((pos, i))  # 位置越靠前越相关（同位置按下标稳定次序）
        hits.sort()
        return [_citation(self.corpus[i], 1.0, "like") for _, i in hits[:k]]


class BM25Retriever(object):
    """字符二元组 BM25（零依赖，适合中文无分词场景）。

    索引用倒排 postings：{gram: {doc 下标: 频次}}。search 只遍历含查询
    gram 的文档，复杂度 O(命中文档数) 而非 O(全库)；df 由 len(postings[g])
    派生，不再单独维护。评分公式与累加顺序与全量扫描版逐位一致。
    """

    def __init__(self, corpus):
        self.corpus = corpus
        self._docs = [x["text"] if isinstance(x, dict) else x for x in corpus]
        self._postings = {}  # {gram: {doc_idx: freq}}，按 doc 下标升序插入
        self._doc_len = []
        total_len = 0
        for doc_idx, text in enumerate(self._docs):
            grams = char_ngrams(_normalize(text))
            tf = {}
            for g in grams:
                tf[g] = tf.get(g, 0) + 1
            for g, freq in tf.items():
                self._postings.setdefault(g, {})[doc_idx] = freq
            self._doc_len.append(len(grams))
            total_len += len(grams)
        self._avg_len = (total_len / len(self._docs)) if self._docs else 0.0

    def _idf(self, gram):
        n = len(self._docs)
        df = len(self._postings.get(gram, ()))
        if df == 0:
            return 0.0
        return math.log((n - df + 0.5) / df + 1.0)

    def search(self, query, k=5):
        query_norm = _normalize(query)
        grams = query_grams(query_norm)
        if not grams:
            return []
        scores = {}
        for gram in grams:
            idf = self._idf(gram)
            if idf <= 0:
                continue
            for doc_idx, freq in self._postings.get(gram, {}).items():
                denom = freq + BM25_K1 * (1 - BM25_B + BM25_B * self._doc_len[doc_idx] / (self._avg_len or 1.0))
                scores[doc_idx] = scores.get(doc_idx, 0.0) + idf * freq * (BM25_K1 + 1) / denom
        ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
        return [_citation(self.corpus[i], s, "bm25") for i, s in ranked[:k]]


def rrf_fuse(result_lists, k=5, rrf_k=RRF_K):
    """Reciprocal Rank Fusion：多路检索结果的排名级融合。

    每条结果的得分 = Σ 1 / (rrf_k + rank_i)；在多路中都出现的条文会
    自然浮到顶部。返回结构与单路一致（retriever 字段记为 "hybrid"）。
    k=None 时返回完整融合排名（供跨 k 评测截取，见 HybridRetriever.recall）。
    """
    fused = {}
    for results in result_lists:
        for rank, cite in enumerate(results, start=1):
            key = cite["id"]
            entry = fused.get(key)
            if entry is None:
                entry = dict(cite)
                entry["score"] = 0.0
                fused[key] = entry
            entry["score"] += 1.0 / (rrf_k + rank)
            entry["retriever"] = "hybrid"
    ranked = sorted(fused.values(), key=lambda c: (-c["score"], c["id"]))
    if k is None:
        return ranked
    return ranked[:k]


class HybridRetriever(object):
    """BM25（原查询 + 同义扩展查询）+ LIKE 的多路 RRF 融合。

    通道构成：
    - bm25(原查询)：词法主路；
    - bm25(扩展查询)：原查询追加同义词典表述与数字读法变体后另检一路，
      让「坐牢」这类口语问句能经「服刑」命中条文（无命中时该路自动省略）；
    - like(原查询)：精确子串路（对合成金标贡献互补命中）。
    原查询信号永不替换、只增不改。

    通道深度与 k 解耦：HYBRID_FUSION_DEPTH 是检索配置的一部分，search(k≤15)
    恒用 depth=30（v0.1.1 全部已发布数字的口径，逐位不变）；跨 k 的可比
    排名（Recall@5/10/20/30）走 recall(query, depth) 取完整融合排名后截取，
    见 eval_harness.evaluate_multi_k。仅当 k 超过该深度时才临时加深通道喂饱 k。

    重排通道（v0.2，接口见 interfaces.py）：reranker=None 时与 v0.1.1 完全
    一致；传入实现 rerank(query, citations, k) 的重排器后，recall 返回重排
    视图——重排只动顺序不动 Citation 形状，语义依赖不得进入核心 import 链。
    """

    def __init__(self, corpus, use_expansion=True, synonyms=None, reranker=None):
        self.bm25 = BM25Retriever(corpus)
        self.like = LikeRetriever(corpus)
        self._reranker = reranker
        self._synonyms = (synonyms if synonyms is not None
                          else load_synonyms()) if use_expansion else None

    def _channels(self, query, depth):
        channels = [self.bm25.search(query, k=depth),
                    self.like.search(query, k=depth)]
        if self._synonyms:
            expanded = expand_query(query, self._synonyms)
            if expanded != query:
                channels.insert(1, self.bm25.search(expanded, k=depth))
        return channels

    def recall(self, query, depth=HYBRID_FUSION_DEPTH):
        """固定通道深度召回：返回完整 RRF 融合排名，不按 k 截断。

        跨 k 对比评测必须取自同一份排名——混合检索里通道深度决定「检到
        什么」，截断位置决定「呈现多少」，两者混在 search(k) 里会让
        Recall@k 各点变成不同检索配置下的数字。
        """
        ranking = rrf_fuse(self._channels(query, depth), k=None)
        if self._reranker is not None:
            ranking = self._reranker.rerank(query, ranking, k=len(ranking))
        return ranking

    def search(self, query, k=5):
        return self.recall(query, depth=max(HYBRID_FUSION_DEPTH, k))[:k]
