# -*- coding: utf-8 -*-
"""semantic_rerank 单元测试：mock 查询编码，不依赖模型文件。"""

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import numpy  # noqa: F401
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False

from statute_rag import semantic_rerank as sr
from statute_rag.semantic_rerank import SemanticPool, SemanticReranker

CORPUS_ROWS = [
    {"id": 101, "law": u"测试法", "num": u"第一条", "text": u"甲条正文"},
    {"id": 102, "law": u"测试法", "num": u"第二条", "text": u"乙条正文"},
    {"id": 103, "law": u"测试法", "num": u"第三条", "text": u"丙条正文"},
]


def _cite(cid):
    return {"id": cid, "law": u"测试法", "num": u"第%d条" % cid,
            "text": u"条文%d" % cid, "score": 0.01, "retriever": "bm25"}


@unittest.skipUnless(HAS_NUMPY, "numpy 不可用")
class TestSemanticReranker(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.emb = os.path.join(self.tmp, "emb.npz")
        # 三个 id，向量两两正交，便于断言顺序
        numpy.savez(self.emb, ids=numpy.array([101, 102, 103],
                                              dtype=numpy.int64),
                    vecs=numpy.array([[1.0, 0.0], [0.0, 1.0], [0.5, 0.5]],
                                     dtype=numpy.float16))
        self._orig_encode = sr.encode_query

    def tearDown(self):
        sr.encode_query = self._orig_encode
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _pool(self, union_k=50):
        return SemanticPool(CORPUS_ROWS, [self.emb], ["unused"], synonyms={},
                            union_k=union_k)

    def _make(self, cross=None, union_k=50):
        return SemanticReranker(CORPUS_ROWS, [self.emb], ["unused"],
                                pool=self._pool(union_k),
                                cross_model_dir=cross)

    def test_missing_emb_file_raises_with_hint(self):
        with self.assertRaises(RuntimeError) as ctx:
            SemanticPool(CORPUS_ROWS, [os.path.join(self.tmp, "nope.npz")],
                         ["unused"], synonyms={})
        self.assertIn("build_embeddings", str(ctx.exception))

    def test_semantic_ranks_follows_cosine_order(self):
        # 查询向量贴 102：全库名次应为 102 → 103 → 101
        sr.encode_query = (lambda q, m: numpy.array([0.0, 1.0],
                                                    dtype=numpy.float32))
        rank = self._pool().semantic_ranks(u"问句")
        self.assertEqual(int(rank[1]), 1)   # 102
        self.assertEqual(int(rank[2]), 2)   # 103
        self.assertEqual(int(rank[0]), 3)   # 101

    def test_pool_search_returns_citations_with_corpus_text(self):
        sr.encode_query = (lambda q, m: numpy.array([1.0, 0.0],
                                                    dtype=numpy.float32))
        out = self._pool(union_k=2).search(u"问句", k=None)
        self.assertEqual([c["id"] for c in out], [101, 103])
        for cite in out:
            for field in ("id", "law", "num", "text", "score", "retriever"):
                self.assertIn(field, cite)
            self.assertEqual(cite["retriever"], "semantic")

    def test_rerank_puts_semantic_best_on_top(self):
        # 语义冠军（102，候选里排第 2）应能翻到顶部
        sr.encode_query = (lambda q, m: numpy.array([0.0, 1.0],
                                                    dtype=numpy.float32))
        out = self._make().rerank(u"问句", [_cite(101), _cite(102), _cite(103)],
                                  k=3)
        self.assertEqual(out[0]["id"], 102)

    def test_rerank_keeps_citation_shape_and_adds_scores(self):
        sr.encode_query = (lambda q, m: numpy.array([1.0, 0.0],
                                                    dtype=numpy.float32))
        out = self._make().rerank(u"问句", [_cite(101), _cite(102)], k=2)
        self.assertEqual(len(out), 2)
        for cite in out:
            for field in ("id", "law", "num", "text", "score", "retriever"):
                self.assertIn(field, cite)
            self.assertIn("rerank_score", cite)
            self.assertIn("semantic_rank", cite)

    def test_empty_citations_returns_empty(self):
        self.assertEqual(self._make().rerank(u"问句", [], k=5), [])

    def test_rerank_never_adds_entries(self):
        """重排契约：只动顺序，不得新增条目（并池是召回层的职责）。"""
        sr.encode_query = (lambda q, m: numpy.array([0.0, 1.0],
                                                    dtype=numpy.float32))
        out = self._make(union_k=3).rerank(u"问句", [_cite(101)], k=10)
        self.assertEqual([c["id"] for c in out], [101])

    def test_unknown_id_gets_worst_semantic_rank_but_survives(self):
        sr.encode_query = (lambda q, m: numpy.array([1.0, 0.0],
                                                    dtype=numpy.float32))
        out = self._make().rerank(u"问句", [_cite(101), _cite(999)], k=2)
        self.assertEqual(len(out), 2)
        unknown = [c for c in out if c["id"] == 999][0]
        self.assertEqual(unknown["semantic_rank"], 4)  # 语料 3 条，垫底

    def test_k_truncates(self):
        sr.encode_query = (lambda q, m: numpy.array([1.0, 0.0],
                                                    dtype=numpy.float32))
        out = self._make().rerank(u"问句", [_cite(101), _cite(102), _cite(103)],
                                  k=2)
        self.assertEqual(len(out), 2)

    def test_multi_model_rank_takes_the_better_of_both(self):
        """两个模型名次取较优者：单模型排第三的 101 因第二模型排第一而登顶。"""
        emb2 = os.path.join(self.tmp, "emb2.npz")
        numpy.savez(emb2, ids=numpy.array([101, 102, 103], dtype=numpy.int64),
                    vecs=numpy.array([[0.0, 1.0], [1.0, 0.0], [0.5, 0.5]],
                                     dtype=numpy.float16))
        pool = SemanticPool(CORPUS_ROWS, [self.emb, emb2],
                            ["model_a", "model_b"], synonyms={})
        sr.encode_query = (lambda q, m: numpy.array([1.0, 0.0],
                                                    dtype=numpy.float32))
        rank = pool.semantic_ranks(u"问句")
        # model_a: 101 第一；model_b: 102 第一 → min 后 101 与 102 并列第一
        self.assertEqual(int(rank[0]), 1)
        self.assertEqual(int(rank[1]), 1)


if __name__ == "__main__":
    unittest.main()
