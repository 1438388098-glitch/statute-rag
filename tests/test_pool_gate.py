# -*- coding: utf-8 -*-
"""召回层并池（pool_extra）与 gateA 置信门的接线测试。

契约：
- pool_extra=None 时行为与缺省构造逐位一致；
- 并池把语义候选追加到 hybrid 排名之后（不改变 hybrid 部分的相对顺序）；
- gateA：LIKE 对原查询精确命中时，既不并池也不重排（合成金标形态）。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from statute_rag.retrieval import HybridRetriever

from test_retrieval import CORPUS, _ids


class StubPool(object):
    """固定返回指定 id 的并池桩（模拟语义 top-K）。"""

    def __init__(self, ids):
        self.ids = ids
        self.calls = 0

    def search(self, query, k=None):
        self.calls += 1
        out = []
        for cid in self.ids:
            row = [x for x in CORPUS if x["id"] == cid][0]
            out.append({"id": row["id"], "law": row["law"], "num": row["num"],
                        "text": row["text"], "score": 0.5,
                        "retriever": "semantic"})
        return out


class RecordingReranker(object):
    def __init__(self):
        self.calls = 0

    def rerank(self, query, citations, k):
        self.calls += 1
        return list(reversed(citations))[:k]


class PoolExtraTest(unittest.TestCase):
    # 用「词法可命中但不是连续子串」的查询（含空格），避开 gateA：
    # 「罚款 停业」的两个词都在 id=4 条文里，但连起来不是子串 → LIKE 不命中。
    NON_LIKE_QUERY = "罚款 停业"

    def test_pool_none_is_identical_to_default(self):
        base = HybridRetriever(CORPUS)
        explicit = HybridRetriever(CORPUS, pool_extra=None)
        for q in ("正当防卫", "承诺生效时合同成立", "行政处罚的种类"):
            self.assertEqual(_ids(base.recall(q, 30)), _ids(explicit.recall(q, 30)))

    def test_pool_appends_after_hybrid_ranking(self):
        # 「罚款 停业」只命中 id=4；并池再补 id=1（hybrid 词法未命中）
        pool = StubPool([1])
        hyb = HybridRetriever(CORPUS, pool_extra=pool)
        ranked = _ids(hyb.recall(self.NON_LIKE_QUERY, 30))
        self.assertEqual(ranked[0], 4, "hybrid 命中必须排在并池条目之前")
        self.assertIn(1, ranked)
        self.assertGreater(ranked.index(1), 0)

    def test_pool_does_not_duplicate_existing_ids(self):
        pool = StubPool([4, 1])
        hyb = HybridRetriever(CORPUS, pool_extra=pool)
        ranked = _ids(hyb.recall(self.NON_LIKE_QUERY, 30))
        self.assertEqual(len(ranked), len(set(ranked)))
        self.assertEqual(ranked[0], 4)

    def test_pool_items_keep_citation_shape(self):
        hyb = HybridRetriever(CORPUS, pool_extra=StubPool([1]))
        for cite in hyb.recall(self.NON_LIKE_QUERY, 30):
            for field in ("id", "law", "num", "text", "score", "retriever"):
                self.assertIn(field, cite)


class GateATest(unittest.TestCase):
    def test_like_hit_skips_pool_and_rerank(self):
        """查询是条文原文片段（LIKE 必中）时：不并池、不重排。"""
        pool = StubPool([1])
        reranker = RecordingReranker()
        hyb = HybridRetriever(CORPUS, pool_extra=pool, reranker=reranker)
        # 完整条文片段 → LIKE 精确命中
        query = "正当防卫明显超过必要限度造成重大损害的"
        ranked = _ids(hyb.recall(query, 30))
        self.assertEqual(pool.calls, 0, "gateA 命中时不得并池")
        self.assertEqual(reranker.calls, 0, "gateA 命中时不得重排")
        self.assertEqual(ranked[0], 3)

    def test_like_miss_runs_pool_and_rerank(self):
        pool = StubPool([1])
        reranker = RecordingReranker()
        hyb = HybridRetriever(CORPUS, pool_extra=pool, reranker=reranker)
        hyb.recall("罚款 停业", 30)
        self.assertEqual(pool.calls, 1)
        self.assertEqual(reranker.calls, 1)


if __name__ == "__main__":
    unittest.main()
