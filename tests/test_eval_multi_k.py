# -*- coding: utf-8 -*-
"""多 k 评测与排名分布单测：同一份排名下跨 k 可比、解耦后行为不变。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from statute_rag.eval_harness import evaluate, evaluate_multi_k, rank_histogram
from statute_rag.retrieval import HybridRetriever, LikeRetriever, rrf_fuse

from test_retrieval import CORPUS, _ids

MULTI_HIT_CORPUS = [
    {"id": 1, "law": "测试法一", "num": "第一条",
     "text": "承诺生效时合同成立，但法律另有规定或者当事人另有约定的除外。"},
    {"id": 2, "law": "测试法二", "num": "第九条",
     "text": "本条是关于合同订立时间的一般规定。依照前述规则，承诺生效时合同成立，特此明确。"},
    {"id": 3, "law": "测试法三", "num": "第一条",
     "text": "行政处罚的种类包括警告、罚款、没收违法所得等。"},
]

GOLD = [
    {"query": "承诺生效时合同成立", "gold_id": 1},
    {"query": "行政处罚的种类", "gold_id": 3},
]


class EvaluateMultiKTest(unittest.TestCase):
    def test_multi_k_k5_matches_evaluate(self):
        """同一检索器：multi_k 的 k=5 点必须与单 k evaluate 逐位一致。"""
        r = LikeRetriever(MULTI_HIT_CORPUS)
        single = evaluate(r, GOLD, k=5)
        multi = evaluate_multi_k(r, GOLD, ks=(5,))
        self.assertAlmostEqual(single["recall_at_k"], multi[5]["recall_at_k"])
        self.assertAlmostEqual(single["mrr"], multi[5]["mrr"])

    def test_recall_is_monotone_across_k(self):
        """同一份排名下 Recall@k 随 k 单调不减（跨 k 可比的基础性质）。"""
        r = LikeRetriever(MULTI_HIT_CORPUS)
        multi = evaluate_multi_k(r, GOLD, ks=(1, 2, 3))
        recalls = [multi[k]["recall_at_k"] for k in (1, 2, 3)]
        self.assertEqual(recalls, sorted(recalls))


class HybridDecouplingTest(unittest.TestCase):
    def test_recall_prefix_matches_search(self):
        """recall 完整排名的前缀必须等于 search 的结果（解耦的核心不变量）。"""
        h = HybridRetriever(CORPUS)
        self.assertEqual(_ids(h.recall("正当防卫")[:5]),
                         _ids(h.search("正当防卫", k=5)))

    def test_search_monotone_prefix_across_k(self):
        """search(k=2) 必须是 search(k=5) 的前缀——k 只截断，不改排名。"""
        h = HybridRetriever(CORPUS)
        self.assertEqual(_ids(h.search("正当防卫", k=2))[:2],
                         _ids(h.search("正当防卫", k=5))[:2])

    def test_rrf_fuse_k_none_returns_full_ranking(self):
        a = [{"id": i, "law": "L", "num": str(i), "text": "t", "score": 1,
              "retriever": "bm25"} for i in range(10)]
        self.assertEqual(len(rrf_fuse([a], k=None)), 10)


class RankHistogramTest(unittest.TestCase):
    def test_buckets_counted_by_rank(self):
        r = LikeRetriever(MULTI_HIT_CORPUS)
        gold = [
            {"query": "承诺生效时合同成立", "gold_id": 1},   # rank 1
            {"query": "行政处罚的种类", "gold_id": 3},       # rank 1
            {"query": "此处没有的词组", "gold_id": 3},       # miss
        ]
        buckets = rank_histogram(r, gold, max_k=30)
        self.assertEqual(buckets["1"], 2)
        self.assertEqual(buckets["miss"], 1)
        self.assertEqual(sum(buckets.values()), 3)


if __name__ == "__main__":
    unittest.main()
