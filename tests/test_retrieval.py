# -*- coding: utf-8 -*-
"""retrieval 单测：构造小语料验证三件套行为与引用结构。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from statute_rag.retrieval import (
    BM25Retriever, HybridRetriever, LikeRetriever, char_ngrams, rrf_fuse,
)

CORPUS = [
    {"id": 1, "law": "测试法一", "num": "第一条",
     "text": "为了使国家、公共利益、本人或者他人的人身、财产和其他权利免受正在进行的不法侵害，而采取的制止不法侵害的行为，对不法侵害人造成损害的，属于正当防卫，不负刑事责任。"},
    {"id": 2, "law": "测试法一", "num": "第二条",
     "text": "当事人订立合同，采取要约、承诺方式。承诺生效时合同成立，但法律另有规定或者当事人另有约定的除外。"},
    {"id": 3, "law": "测试法一", "num": "第三条",
     "text": "正当防卫明显超过必要限度造成重大损害的，应当负刑事责任，但是应当减轻或者免除处罚。"},
    {"id": 4, "law": "测试法二", "num": "第一条",
     "text": "行政处罚的种类包括警告、罚款、没收违法所得、责令停产停业等。"},
]


def _ids(results):
    return [r["id"] for r in results]


class CharNgramsTest(unittest.TestCase):
    def test_bigrams_and_unigram_fallback(self):
        self.assertEqual(char_ngrams("正当防卫"), ["正当", "当防", "防卫"])
        self.assertEqual(char_ngrams("法"), ["法"])
        self.assertEqual(char_ngrams(""), [])


class LikeRetrieverTest(unittest.TestCase):
    def test_substring_hit_with_citation(self):
        r = LikeRetriever(CORPUS).search("承诺生效时合同成立")
        self.assertEqual(len(r), 1)
        self.assertEqual(r[0]["id"], 2)
        self.assertEqual(r[0]["law"], "测试法一")
        self.assertEqual(r[0]["num"], "第二条")
        self.assertIn("要约", r[0]["text"])  # 引用带条文原文

    def test_miss_returns_empty(self):
        self.assertEqual(LikeRetriever(CORPUS).search("此处没有的词组"), [])


class BM25RetrieverTest(unittest.TestCase):
    def test_lexically_close_article_ranks_first(self):
        r = BM25Retriever(CORPUS).search("正当防卫的限度", k=4)
        self.assertTrue(r)
        # 两条「正当防卫」条文都应排在行政处罚条文之前
        ids = _ids(r)
        self.assertIn(1, ids)
        self.assertIn(3, ids)
        self.assertNotIn(4, ids[:2])

    def test_all_results_have_citation_fields(self):
        for cite in BM25Retriever(CORPUS).search("行政处罚的种类", k=3):
            for field in ("id", "law", "num", "text", "score", "retriever"):
                self.assertIn(field, cite)


class RrfFuseTest(unittest.TestCase):
    def test_doc_in_both_lists_floats_to_top(self):
        a = [{"id": 10, "law": "L", "num": "1", "text": "t", "score": 9, "retriever": "bm25"},
             {"id": 11, "law": "L", "num": "2", "text": "t", "score": 5, "retriever": "bm25"}]
        b = [{"id": 12, "law": "L", "num": "3", "text": "t", "score": 7, "retriever": "like"},
             {"id": 10, "law": "L", "num": "1", "text": "t", "score": 1, "retriever": "like"}]
        fused = rrf_fuse([a, b], k=3)
        self.assertEqual(fused[0]["id"], 10)  # 两路都命中 → 顶部
        self.assertEqual(fused[0]["retriever"], "hybrid")

    def test_k_limits_output(self):
        a = [{"id": i, "law": "L", "num": str(i), "text": "t", "score": 1, "retriever": "bm25"}
             for i in range(10)]
        self.assertEqual(len(rrf_fuse([a], k=3)), 3)


class HybridRetrieverTest(unittest.TestCase):
    def test_hybrid_hits_exact_substring_first(self):
        r = HybridRetriever(CORPUS).search("承诺生效时合同成立", k=4)
        self.assertEqual(r[0]["id"], 2)  # LIKE 精确命中的条文经融合后居首

    def test_hybrid_finds_lexical_matches(self):
        ids = _ids(HybridRetriever(CORPUS).search("正当防卫", k=4))
        self.assertIn(1, ids)
        self.assertIn(3, ids)


if __name__ == "__main__":
    unittest.main()
