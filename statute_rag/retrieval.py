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

# hybrid 融合通道深度下限（消融测量后定值，见 docs/retrieval-improvement.md）
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
    """字符二元组 BM25（零依赖，适合中文无分词场景）。"""

    def __init__(self, corpus):
        self.corpus = corpus
        self._docs = [x["text"] if isinstance(x, dict) else x for x in corpus]
        self._tf = []       # 每篇: {gram: freq}
        self._df = {}       # {gram: 含该 gram 的篇数}
        self._doc_len = []
        total_len = 0
        for text in self._docs:
            grams = char_ngrams(_normalize(text))
            tf = {}
            for g in grams:
                tf[g] = tf.get(g, 0) + 1
            self._tf.append(tf)
            self._doc_len.append(len(grams))
            total_len += len(grams)
            for g in tf:
                self._df[g] = self._df.get(g, 0) + 1
        self._avg_len = (total_len / len(self._docs)) if self._docs else 0.0

    def _idf(self, gram):
        n = len(self._docs)
        df = self._df.get(gram, 0)
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
            for doc_idx, tf in enumerate(self._tf):
                freq = tf.get(gram, 0)
                if not freq:
                    continue
                denom = freq + BM25_K1 * (1 - BM25_B + BM25_B * self._doc_len[doc_idx] / (self._avg_len or 1.0))
                scores[doc_idx] = scores.get(doc_idx, 0.0) + idf * freq * (BM25_K1 + 1) / denom
        ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
        return [_citation(self.corpus[i], s, "bm25") for i, s in ranked[:k]]


def rrf_fuse(result_lists, k=5, rrf_k=RRF_K):
    """Reciprocal Rank Fusion：多路检索结果的排名级融合。

    每条结果的得分 = Σ 1 / (rrf_k + rank_i)；在多路中都出现的条文会
    自然浮到顶部。返回结构与单路一致（retriever 字段记为 "hybrid"）。
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
    return ranked[:k]


class HybridRetriever(object):
    """BM25（原查询 + 同义扩展查询）+ LIKE 的多路 RRF 融合。

    通道构成：
    - bm25(原查询)：词法主路；
    - bm25(扩展查询)：原查询追加同义词典表述与数字读法变体后另检一路，
      让「坐牢」这类口语问句能经「服刑」命中条文（无命中时该路自动省略）；
    - like(原查询)：精确子串路（对合成金标贡献互补命中）。
    原查询信号永不替换、只增不改；通道深度取 max(HYBRID_FUSION_DEPTH, 2k)。
    """

    def __init__(self, corpus, use_expansion=True, synonyms=None):
        self.bm25 = BM25Retriever(corpus)
        self.like = LikeRetriever(corpus)
        self._synonyms = (synonyms if synonyms is not None
                          else load_synonyms()) if use_expansion else None

    def search(self, query, k=5):
        depth = max(HYBRID_FUSION_DEPTH, k * 2)
        channels = [self.bm25.search(query, k=depth),
                    self.like.search(query, k=depth)]
        if self._synonyms:
            expanded = expand_query(query, self._synonyms)
            if expanded != query:
                channels.insert(1, self.bm25.search(expanded, k=depth))
        return rrf_fuse(channels, k=k)
