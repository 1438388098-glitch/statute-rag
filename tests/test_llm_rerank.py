# -*- coding: utf-8 -*-
"""LLM 重排可选层单测：契约（只重排不发明条目）、失败降级、环境变量配置。

不联网：_post 注入假传输层，全部用夹具 JSON。
"""
import os
import sys
import unittest

import urllib.error

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from statute_rag.llm_rerank import LLMReranker, LLMRerankRetriever

CITATION_FIELDS = ("id", "law", "num", "text", "score", "retriever")


def _cites(*ids):
    return [{"id": i, "law": "测试法", "num": "第一条", "text": u"条文%d正文" % i,
             "score": 1.0, "retriever": "test"} for i in ids]


def _ok(content):
    return {"choices": [{"message": {"content": content}}]}


def _make(transport, **kw):
    r = LLMReranker(api_key="test-key", base_url="https://x/v1", **kw)
    r._post = transport
    return r


class LLMRerankerContractTest(unittest.TestCase):
    def test_reorders_and_truncates(self):
        r = _make(lambda url, body: _ok("[3,1]"))
        out = r.rerank(u"查询", _cites(1, 2, 3, 4, 5), k=5)
        self.assertEqual([c["id"] for c in out][:2], [3, 1])
        self.assertEqual(sorted(c["id"] for c in out), [1, 2, 3, 4, 5])  # 不丢条目
        self.assertTrue(len(out) <= 5)
        for c in out:
            for f in CITATION_FIELDS:
                self.assertIn(f, c)

    def test_ignores_invalid_and_duplicate_indices(self):
        r = _make(lambda url, body: _ok("[9,2,2,0,1]"))
        out = r.rerank(u"查询", _cites(1, 2, 3), k=5)
        self.assertEqual([c["id"] for c in out][:2], [2, 1])
        self.assertEqual(sorted(c["id"] for c in out), [1, 2, 3])

    def test_pick_caps_promoted_entries(self):
        r = _make(lambda url, body: _ok("[4,3,2,1]"), pick=2)
        out = r.rerank(u"查询", _cites(1, 2, 3, 4), k=10)
        self.assertEqual([c["id"] for c in out][:2], [4, 3])  # 只提前 2 条
        self.assertEqual([c["id"] for c in out][2:], [1, 2])  # 其余保持原序

    def test_empty_input_returns_empty(self):
        r = _make(lambda url, body: _ok("[1]"))
        self.assertEqual(r.rerank(u"查询", [], k=5), [])

    def test_server_error_degrades_to_original_order(self):
        calls = []

        def transport(url, body):
            calls.append(url)
            raise urllib.error.HTTPError(url, 500, "boom", None, None)

        cites = _cites(1, 2, 3)
        out = _make(transport).rerank(u"查询", cites, k=5)
        self.assertEqual([c["id"] for c in out], [1, 2, 3])  # 原序降级
        self.assertEqual(len(calls), 3)  # 429/5xx 退避重试 3 次

    def test_bad_request_fails_fast_then_degrades(self):
        calls = []

        def transport(url, body):
            calls.append(url)
            raise urllib.error.HTTPError(url, 400, "bad", None, None)

        out = _make(transport).rerank(u"查询", _cites(1, 2), k=5)
        self.assertEqual([c["id"] for c in out], [1, 2])
        self.assertEqual(len(calls), 1)  # 4xx 不重试

    def test_unparseable_output_degrades(self):
        r = _make(lambda url, body: _ok(u"我认为第二条最相关"))
        out = r.rerank(u"查询", _cites(1, 2), k=5)
        self.assertEqual([c["id"] for c in out], [1, 2])

    def test_prompt_carries_query_and_candidates(self):
        seen = {}

        def transport(url, body):
            seen["body"] = body
            return _ok("[1]")

        _make(transport).rerank(u"喝酒醉驾会坐牢吗", _cites(1, 2), k=5)
        user_msg = seen["body"]["messages"][1]["content"]
        self.assertIn(u"喝酒醉驾会坐牢吗", user_msg)
        self.assertIn(u"[2]", user_msg)
        self.assertEqual(seen["body"]["model"], "longcat-2.5-preview-free")

    def test_thinking_disabled_by_default_and_low_opt_in(self):
        bodies = []

        def transport(url, body):
            bodies.append(body)
            return _ok("[1]")

        _make(transport).rerank(u"q", _cites(1), k=5)
        self.assertEqual(bodies[-1]["thinking"], {"type": "disabled"})
        self.assertNotIn("max_tokens", bodies[-1])  # 不打断思考：不发 max_tokens
        _make(transport, thinking="low").rerank(u"q", _cites(1), k=5)
        self.assertEqual(bodies[-1].get("reasoning_effort"), "low")


class ConfigTest(unittest.TestCase):
    def test_missing_key_raises(self):
        saved = os.environ.pop("STATUTE_RAG_LLM_KEY", None)
        try:
            with self.assertRaises(RuntimeError):
                LLMReranker()
        finally:
            if saved is not None:
                os.environ["STATUTE_RAG_LLM_KEY"] = saved

    def test_env_config(self):
        saved = {k: os.environ.get(k) for k in ("STATUTE_RAG_LLM_KEY", "STATUTE_RAG_LLM_MODEL",
                                                "STATUTE_RAG_LLM_TOP_N")}
        os.environ["STATUTE_RAG_LLM_KEY"] = "env-key"
        os.environ["STATUTE_RAG_LLM_MODEL"] = "m1"
        os.environ["STATUTE_RAG_LLM_TOP_N"] = "7"
        try:
            r = LLMReranker()
            self.assertEqual(r._model, "m1")
            self.assertEqual(r._top_n, 7)
            self.assertTrue(r._base_url.startswith("https://"))
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


class FakeInner(object):
    def __init__(self, ids):
        self._ids = ids
        self.seen_k = []

    def search(self, query, k=5):
        self.seen_k.append(k)
        return _cites(*self._ids)[:k]


class LLMRerankRetrieverTest(unittest.TestCase):
    def test_deepens_recall_and_reranks(self):
        inner = FakeInner(list(range(1, 61)))  # 60 条
        llm = _make(lambda url, body: _ok("[5,2]"))
        out = LLMRerankRetriever(inner, llm).search(u"查询", k=5)
        self.assertEqual(inner.seen_k, [50])  # 按 top_n=50 加深召回
        self.assertEqual([c["id"] for c in out][:2], [5, 2])
        self.assertEqual(len(out), 5)

    def test_degradation_passes_through_inner_order(self):
        def transport(url, body):
            raise urllib.error.HTTPError(url, 503, "x", None, None)

        inner = FakeInner(list(range(1, 11)))
        out = LLMRerankRetriever(inner, _make(transport)).search(u"查询", k=3)
        self.assertEqual([c["id"] for c in out], [1, 2, 3])


if __name__ == "__main__":
    unittest.main()
