# -*- coding: utf-8 -*-
"""重排通道接口单测：reranker=None 降级等价、重排只动顺序不动形状、坏重排器不破坏引用结构。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from statute_rag.retrieval import HybridRetriever

from test_retrieval import CORPUS, _ids

CITATION_FIELDS = ("id", "law", "num", "text", "score", "retriever")


class OverlapReranker(object):
    """「好重排器」测试桩：按查询字符在条文中的覆盖数重排（确定性、零依赖），
    模拟语义重排对近似命中案例的翻转能力。"""

    def rerank(self, query, citations, k):
        compact = "".join((query or "").split())
        def coverage(cite):
            return sum(1 for ch in set(compact) if ch in cite["text"])
        ranked = sorted(citations, key=lambda c: (-coverage(c), c["id"]))
        return ranked[:k]


class BrokenReranker(object):
    """「坏重排器」测试桩：整列倒序——坏模型最多毁排序，不允许毁引用结构。"""

    def rerank(self, query, citations, k):
        return list(reversed(citations))[:k]


class RerankerNoneBaselineTest(unittest.TestCase):
    def test_reranker_none_is_identical(self):
        """reranker=None 必须与缺省构造逐位一致（降级即现状）。"""
        base = HybridRetriever(CORPUS)
        explicit = HybridRetriever(CORPUS, use_expansion=True, synonyms=None, reranker=None)
        for q in ("正当防卫", "承诺生效时合同成立", "行政处罚的种类"):
            self.assertEqual(_ids(base.search(q, k=5)), _ids(explicit.search(q, k=5)))
            self.assertEqual([c["score"] for c in base.search(q, k=5)],
                             [c["score"] for c in explicit.search(q, k=5)])


class RerankerContractTest(unittest.TestCase):
    def test_rerank_reorders_without_losing_or_inventing(self):
        h = HybridRetriever(CORPUS, reranker=OverlapReranker())
        fused = HybridRetriever(CORPUS).recall("正当防卫")
        reranked = h.recall("正当防卫")
        self.assertEqual(sorted(_ids(reranked)), sorted(_ids(fused)))  # 同一批条文
        for cite in reranked:
            for field in CITATION_FIELDS:
                self.assertIn(field, cite)  # 引用形状完整

    def test_broken_reranker_keeps_citation_shape(self):
        """坏重排器（整列倒序）不丢字段、不丢条目——排序可毁，结构不可毁。"""
        h = HybridRetriever(CORPUS, reranker=BrokenReranker())
        fused = HybridRetriever(CORPUS).recall("正当防卫")
        reranked = h.recall("正当防卫")
        self.assertEqual(len(reranked), len(fused))
        for cite in reranked:
            for field in CITATION_FIELDS:
                self.assertIn(field, cite)

    def test_empty_rerank_result_is_rejected(self):
        """重排器清空排名属契约违规，须显式报错而非静默吞掉引用。"""
        class ClearingReranker(object):
            def rerank(self, query, citations, k):
                return []
        h = HybridRetriever(CORPUS, reranker=ClearingReranker())
        with self.assertRaises(ValueError):
            h.recall("正当防卫")

    def test_search_truncates_reranked_ranking(self):
        h = HybridRetriever(CORPUS, reranker=OverlapReranker())
        self.assertTrue(len(h.search("正当防卫", k=2)) <= 2)

    def test_good_reranker_reorders_by_query_coverage(self):
        """重排器确定性地按自身语义把最相关条文顶到第一（这里用字符覆盖模拟）。

        真实翻转能力属 v0.2 的语义模型；本测试只锁「重排器的排序决定输出
        首位」这一接口行为，不依赖具体夹具下的基线次序。
        """
        corpus = [
            {"id": 1, "law": "测试法", "num": "第一条",
             "text": "Penalty条款：饲养动物，干扰他人正常生活的，处警告。"},
            {"id": 2, "law": "测试法", "num": "第二条",
             "text": "噪声扰民问题另行规定，本法不适用于噪音以外的情形。"},
            {"id": 3, "law": "测试法", "num": "第三条",
             "text": "商品交换应当遵循自愿、平等、公平、诚实信用的原则。"},
        ]
        reranked = HybridRetriever(corpus, reranker=OverlapReranker()).search(
            "邻居半夜噪音扰民可以报警吗", k=2)
        # 查询字符「噪音扰民」在 id=2 条文中的覆盖最高，重排器应把它顶到第一
        self.assertEqual(_ids(reranked)[0], 2)
        self.assertIn(_ids(reranked)[1], (1, 3))


if __name__ == "__main__":
    unittest.main()
