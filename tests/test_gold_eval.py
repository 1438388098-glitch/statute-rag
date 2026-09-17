# -*- coding: utf-8 -*-
"""gold 生成与 eval 框架单测。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from statute_rag.eval_harness import evaluate, format_report
from statute_rag.gold import make_gold
from statute_rag.retrieval import BM25Retriever, LikeRetriever

CORPUS = [
    {"id": 1, "law": "测试法", "num": "第一条",
     "text": "正当防卫的成立要求不法侵害正在进行，防卫行为针对不法侵害人本人。"},
    {"id": 2, "law": "测试法", "num": "第二条",
     "text": "合同的成立需要要约与承诺两个阶段，承诺通知到达要约人时生效。"},
    {"id": 3, "law": "测试法", "num": "第三条",
     "text": "行政处罚的决定必须经过法定程序，行政机关应当告知当事人陈述和申辩的权利。"},
]


class MakeGoldTest(unittest.TestCase):
    def test_phrase_from_target_and_valid(self):
        gold = make_gold(CORPUS, size=3, seed=7)
        self.assertEqual(len(gold), 3)
        for q in gold:
            target = [c for c in CORPUS if c["id"] == q["gold_id"]][0]
            # 查询短语必须真的出现在目标条文中（金标有效性）
            self.assertIn(q["query"], "".join(target["text"].split()))
            self.assertIn("「", q["question"])  # 模板问句仅作文档字段

    def test_reproducible_with_seed(self):
        a = make_gold(CORPUS, size=3, seed=42)
        b = make_gold(CORPUS, size=3, seed=42)
        self.assertEqual(a, b)

    def test_size_capped_by_corpus(self):
        self.assertEqual(len(make_gold(CORPUS, size=100, seed=1)), 3)


class EvalHarnessTest(unittest.TestCase):
    def _gold(self):
        return [
            {"qid": "q1", "query": "正当防卫的成立", "gold_id": 1},
            {"qid": "q2", "query": "承诺通知到达要约人", "gold_id": 2},
        ]

    def test_perfect_and_failed_retriever(self):
        gold = self._gold()

        class Perfect(object):
            def search(self, q, k=5):
                gid = 1 if "正当防卫" in q else 2
                return [{"id": gid, "law": "L", "num": "1", "text": "t", "score": 1, "retriever": "x"}]

        class AlwaysWrong(object):
            def search(self, q, k=5):
                return [{"id": 999, "law": "L", "num": "9", "text": "t", "score": 1, "retriever": "x"}]

        perfect = evaluate(Perfect(), gold, k=5)
        self.assertEqual(perfect["recall_at_k"], 1.0)
        self.assertAlmostEqual(perfect["mrr"], 1.0)
        wrong = evaluate(AlwaysWrong(), gold, k=5)
        self.assertEqual(wrong["recall_at_k"], 0.0)
        self.assertEqual(wrong["mrr"], 0.0)

    def test_mrr_accounts_for_rank(self):
        gold = self._gold()

        class RankTwo(object):
            def search(self, q, k=5):
                gid = 1 if "正当防卫" in q else 2
                filler = [{"id": 100 + i, "law": "L", "num": "x", "text": "t",
                           "score": 2, "retriever": "x"} for i in range(3)]
                return filler + [{"id": gid, "law": "L", "num": "1", "text": "t",
                                  "score": 1, "retriever": "x"}]

        m = evaluate(RankTwo(), gold, k=5)
        self.assertEqual(m["recall_at_k"], 1.0)
        self.assertAlmostEqual(m["mrr"], 1.0 / 4)  # gold 都排在第 4 位

    def test_real_retrievers_on_synthetic_gold(self):
        gold = make_gold(CORPUS, size=3, seed=11)
        for Ret in (LikeRetriever, BM25Retriever):
            m = evaluate(Ret(CORPUS), gold, k=5)
            self.assertGreater(m["recall_at_k"], 0.5)  # 词法检索对合成金标应当大部分命中

    def test_format_report(self):
        m = {"n": 2, "k": 5, "recall_at_k": 0.5, "mrr": 0.25}
        text = format_report([("bm25", m)], corpus_size=3)
        self.assertIn("50.0%", text)
        self.assertIn("0.250", text)


if __name__ == "__main__":
    unittest.main()
