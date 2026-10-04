# -*- coding: utf-8 -*-
"""Jev 重排可选层单测：契约（只重排不发明条目）、失败降级、两种判选模式、环境变量配置。

不联网：_post 注入假传输层，全部用夹具 JSON（systemone 响应形状）。
"""
import os
import sys
import unittest

import urllib.error

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from statute_rag.jev_rerank import JevReranker, JevRerankRetriever

CITATION_FIELDS = ("id", "law", "num", "text", "score", "retriever")


def _cites(*ids):
    return [{"id": i, "law": "测试法", "num": "第一条", "text": u"条文%d正文" % i,
             "score": 1.0, "retriever": "test"} for i in ids]


def _choice(probs):
    return {"model": "jev-1.13-free", "cost": "0",
            "answers": {"rank": {"type": "choice", "choice": "1",
                                 "probabilities": probs}}}


def _score(vals):
    ans = {}
    for i, v in enumerate(vals, start=1):
        ans["c%d" % i] = {"type": "score", "score": v}
    return {"model": "jev-1.13-free", "cost": "0", "answers": ans}


def _make(transport, **kw):
    r = JevReranker(base_url="https://x/systemone", **kw)
    r._post = transport
    return r


class JevRerankerChoiceTest(unittest.TestCase):
    def test_reorders_and_keeps_all_entries(self):
        # 概率最高的 3、其次 1，其余并列 0 → 保持原序
        r = _make(lambda p: _choice({"1": 0.1, "2": 0.05, "3": 0.7, "4": 0.1, "5": 0.05}))
        out = r.rerank(u"查询", _cites(1, 2, 3, 4, 5), k=5)
        self.assertEqual([c["id"] for c in out][:2], [3, 1])
        self.assertEqual(sorted(c["id"] for c in out), [1, 2, 3, 4, 5])  # 不丢条目
        for c in out:
            for f in CITATION_FIELDS:
                self.assertIn(f, c)

    def test_ties_keep_original_order(self):
        r = _make(lambda p: _choice({"1": 0.2, "2": 0.2, "3": 0.2}))
        out = r.rerank(u"查询", _cites(1, 2, 3), k=5)
        self.assertEqual([c["id"] for c in out], [1, 2, 3])

    def test_pick_caps_promoted_entries(self):
        r = _make(lambda p: _choice({"1": 0.1, "2": 0.2, "3": 0.3, "4": 0.4}), pick=2)
        out = r.rerank(u"查询", _cites(1, 2, 3, 4), k=10)
        self.assertEqual([c["id"] for c in out][:2], [4, 3])  # 只提前 2 条
        self.assertEqual([c["id"] for c in out][2:], [1, 2])  # 其余保持原序

    def test_empty_input_returns_empty(self):
        r = _make(lambda p: _choice({"1": 1.0}))
        self.assertEqual(r.rerank(u"查询", [], k=5), [])

    def test_missing_probabilities_degrades(self):
        r = _make(lambda p: {"answers": {"rank": {"type": "choice"}}})
        out = r.rerank(u"查询", _cites(1, 2), k=5)
        self.assertEqual([c["id"] for c in out], [1, 2])
        self.assertEqual(r.degraded, 1)

    def test_server_error_degrades_to_original_order(self):
        calls = []

        def transport(payload):
            calls.append(payload)
            raise urllib.error.HTTPError("u", 500, "boom", None, None)

        out = _make(transport).rerank(u"查询", _cites(1, 2, 3), k=5)
        self.assertEqual([c["id"] for c in out], [1, 2, 3])
        self.assertEqual(len(calls), 3)  # 429/5xx 退避重试 3 次

    def test_bad_request_fails_fast_then_degrades(self):
        calls = []

        def transport(payload):
            calls.append(payload)
            raise urllib.error.HTTPError("u", 400, "bad", None, None)

        out = _make(transport).rerank(u"查询", _cites(1, 2), k=5)
        self.assertEqual([c["id"] for c in out], [1, 2])
        self.assertEqual(len(calls), 1)  # 4xx 不重试

    def test_payload_carries_query_candidates_and_model(self):
        seen = {}

        def transport(payload):
            seen["payload"] = payload
            return _choice({"1": 1.0, "2": 0.0})

        _make(transport).rerank(u"喝酒醉驾会坐牢吗", _cites(1, 2), k=5)
        p = seen["payload"]
        self.assertEqual(p["model"], "jev-1.13-free")
        self.assertIn(u"喝酒醉驾会坐牢吗", p["state"])
        crit = p["questions"]["rank"]["criteria"]
        self.assertEqual(set(crit.keys()), {"1", "2"})
        self.assertIn(u"[2]", crit["2"])
        self.assertEqual(p["questions"]["rank"]["type"], "choice")


class JevRerankerScoreTest(unittest.TestCase):
    def test_score_mode_orders_by_score(self):
        r = _make(lambda p: _score([0.1, 0.9, 0.2, 0.0]), mode="score")
        out = r.rerank(u"查询", _cites(1, 2, 3, 4), k=4)
        self.assertEqual([c["id"] for c in out], [2, 3, 1, 4])

    def test_score_mode_payload_has_per_candidate_questions(self):
        seen = {}

        def transport(payload):
            seen["payload"] = payload
            return _score([0.0, 0.0])

        _make(transport, mode="score").rerank(u"查询", _cites(1, 2), k=5)
        qs = seen["payload"]["questions"]
        self.assertEqual(set(qs.keys()), {"c1", "c2"})
        self.assertEqual(qs["c1"]["type"], "score")
        self.assertIn(u"[1]", seen["payload"]["state"])

    def test_score_mode_empty_answers_degrades(self):
        r = _make(lambda p: {"answers": {}}, mode="score")
        out = r.rerank(u"查询", _cites(1, 2), k=5)
        self.assertEqual([c["id"] for c in out], [1, 2])


class JevConfigTest(unittest.TestCase):
    def test_constructed_without_key(self):
        r = JevReranker()
        self.assertEqual(r.model, "jev-1.13-free")
        self.assertEqual(r.mode, "choice")

    def test_invalid_mode_raises(self):
        with self.assertRaises(ValueError):
            JevReranker(mode="nope")

    def test_env_config(self):
        keys = ("STATUTE_RAG_JEV_MODEL", "STATUTE_RAG_JEV_TOP_N",
                "STATUTE_RAG_JEV_MODE", "STATUTE_RAG_JEV_PICK")
        saved = dict((k, os.environ.get(k)) for k in keys)
        os.environ["STATUTE_RAG_JEV_MODEL"] = "m1"
        os.environ["STATUTE_RAG_JEV_TOP_N"] = "7"
        os.environ["STATUTE_RAG_JEV_MODE"] = "score"
        os.environ["STATUTE_RAG_JEV_PICK"] = "3"
        try:
            r = JevReranker()
            self.assertEqual(r._model, "m1")
            self.assertEqual(r._top_n, 7)
            self.assertEqual(r.mode, "score")
            self.assertEqual(r._pick, 3)
            self.assertTrue(r._base_url.startswith("https://"))
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_max_cand_chars_truncates_only_the_prompt(self):
        seen = {}

        def transport(payload):
            seen["payload"] = payload
            return _choice({"1": 1.0})

        long_text = u"字" * 100
        cites = [{"id": 1, "law": "法", "num": "一", "text": long_text,
                  "score": 1.0, "retriever": "t"}]
        r = _make(transport, max_cand_chars=10)
        out = r.rerank(u"q", cites, k=1)
        self.assertEqual(out[0]["text"], long_text)  # 返回的 Citation 原文不动
        self.assertEqual(len(seen["payload"]["questions"]["rank"]["criteria"]["1"].split(u"：")[-1]), 10)


class FakeInner(object):
    def __init__(self, ids):
        self._ids = ids
        self.seen_k = []

    def search(self, query, k=5):
        self.seen_k.append(k)
        return _cites(*self._ids)[:k]


class JevRerankRetrieverTest(unittest.TestCase):
    def test_deepens_recall_and_reranks(self):
        inner = FakeInner(list(range(1, 61)))
        jev = _make(lambda p: _choice({"5": 0.9, "2": 0.8, "1": 0.1}))
        out = JevRerankRetriever(inner, jev).search(u"查询", k=5)
        self.assertEqual(inner.seen_k, [50])  # 按 top_n=50 加深召回
        self.assertEqual([c["id"] for c in out][:2], [5, 2])
        self.assertEqual(len(out), 5)

    def test_degradation_passes_through_inner_order(self):
        def transport(payload):
            raise urllib.error.HTTPError("u", 503, "x", None, None)

        inner = FakeInner(list(range(1, 11)))
        out = JevRerankRetriever(inner, _make(transport)).search(u"查询", k=3)
        self.assertEqual([c["id"] for c in out], [1, 2, 3])


if __name__ == "__main__":
    unittest.main()
