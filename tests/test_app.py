# -*- coding: utf-8 -*-
"""Web 应用单测：真起一个服务（合成演示语料，端口 0 随机），按 HTTP 打接口。

不依赖真实语料——真实语料不随仓库分发；这里验证的是服务契约：
路由、JSON 结构、参数边界、以及「界面拿到的排名与检索器给出的排名一致」。
"""
import json
import os
import sys
import threading
import unittest
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import urlopen

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts.app import MAX_K, MAX_QUERY_CHARS, build_state, make_server  # noqa: E402

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
DEMO_CORPUS = os.path.join(ROOT, "demo_corpus", "corpus.jsonl")


def _get(port, path):
    """返回 (status, body)。4xx/5xx 也要能拿到 body，故捕获 HTTPError。"""
    url = "http://127.0.0.1:%d%s" % (port, path)
    try:
        resp = urlopen(url, timeout=10)
        return resp.getcode(), resp.read().decode("utf-8")
    except HTTPError as exc:
        return exc.code, exc.read().decode("utf-8")


class AppServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.path.exists(DEMO_CORPUS):
            raise unittest.SkipTest("缺少演示语料：先跑 python scripts/make_demo_corpus.py")
        cls.state = build_state(DEMO_CORPUS)
        cls.server = make_server(cls.state, "127.0.0.1", 0)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever)
        cls.thread.daemon = True
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def test_index_serves_single_page_app(self):
        status, body = _get(self.port, "/")
        self.assertEqual(status, 200)
        self.assertIn("法条检索", body)
        # 界面不开外链：没有外域脚本、没有外域样式
        self.assertNotIn("http://", body.split("<script>")[0].replace("http://www.w3.org", ""))

    def test_meta_reports_corpus_size_from_the_file(self):
        status, body = _get(self.port, "/api/meta")
        self.assertEqual(status, 200)
        meta = json.loads(body)
        self.assertEqual(meta["corpus"]["articles"], len(self.state.corpus))
        self.assertEqual(meta["corpus"]["laws"], len(set(x["law"] for x in self.state.corpus)))
        self.assertEqual(meta["max_k"], MAX_K)

    def test_search_returns_citations_in_rank_order(self):
        status, body = _get(self.port, "/api/search?q=%E6%BC%94%E7%A4%BA&k=3")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(data["k"], 3)
        self.assertTrue(data["results"], "演示语料应能命中「演示」")
        self.assertEqual([r["rank"] for r in data["results"]], list(range(1, len(data["results"]) + 1)))
        for r in data["results"]:
            for field in ("id", "law", "num", "text", "score", "channels"):
                self.assertIn(field, r)
            self.assertTrue(r["channels"], "每条命中都应交代来自哪个通道")

    def test_search_ranking_matches_retriever_bit_for_bit(self):
        """界面看到的顺序必须就是检索器给出的顺序——不存在演示版另一套逻辑。"""
        query = "演示"
        status, body = _get(self.port, "/api/search?q=" + quote(query) + "&k=5")
        self.assertEqual(status, 200)
        data = json.loads(body)
        expected = [c["id"] for c in self.state.retriever.search(query, k=5)]
        self.assertEqual([r["id"] for r in data["results"]], expected)
        self.assertEqual(data["candidates"], len(self.state.retriever.recall(query)))

    def test_k_is_clamped_into_a_sane_range(self):
        _status, body = _get(self.port, "/api/search?q=%E6%BC%94%E7%A4%BA&k=9999")
        self.assertEqual(json.loads(body)["k"], MAX_K)
        _status, body = _get(self.port, "/api/search?q=%E6%BC%94%E7%A4%BA&k=0")
        self.assertEqual(json.loads(body)["k"], 1)
        _status, body = _get(self.port, "/api/search?q=%E6%BC%94%E7%A4%BA&k=abc")
        self.assertEqual(json.loads(body)["k"], self.state.k)  # 非法值退回默认，不报 500

    def test_overlong_query_is_truncated_and_flagged(self):
        long_q = "%E6%BC%94%E7%A4%BA" * (MAX_QUERY_CHARS // 2 + 20)
        _status, body = _get(self.port, "/api/search?q=" + long_q)
        data = json.loads(body)
        self.assertTrue(data["truncated"])
        self.assertEqual(len(data["query"]), MAX_QUERY_CHARS)

    def test_empty_query_is_a_client_error_not_a_crash(self):
        status, body = _get(self.port, "/api/search?q=%20%20")
        self.assertEqual(status, 400)
        self.assertIn("error", json.loads(body))

    def test_unknown_path_is_404_json(self):
        status, body = _get(self.port, "/etc/passwd")
        self.assertEqual(status, 404)
        self.assertIn("error", json.loads(body))

    def test_laws_endpoint_covers_corpus_and_sorts_by_count(self):
        status, body = _get(self.port, "/api/laws")
        self.assertEqual(status, 200)
        data = json.loads(body)
        laws = data["laws"]
        self.assertEqual(data["total"], len(laws))
        self.assertEqual(laws, sorted(laws, key=lambda x: (-x["count"], x["law"])))
        # 目录必须与语料严格对齐：法名集合与每部法的条数
        expected = {}
        for row in self.state.corpus:
            expected[row["law"]] = expected.get(row["law"], 0) + 1
        self.assertEqual({x["law"]: x["count"] for x in laws}, expected)
        self.assertEqual(sum(x["count"] for x in laws), len(self.state.corpus))

    def test_articles_endpoint_returns_articles_in_corpus_order(self):
        law = self.state.corpus[0]["law"]
        status, body = _get(self.port, "/api/articles?law=" + quote(law))
        self.assertEqual(status, 200)
        data = json.loads(body)
        expected = [r for r in self.state.corpus if r["law"] == law]
        self.assertEqual(data["law"], law)
        self.assertEqual(data["count"], len(expected))
        self.assertEqual([a["id"] for a in data["articles"]],
                         [r["id"] for r in expected])
        for field in ("id", "num", "text"):
            self.assertIn(field, data["articles"][0])

    def test_articles_unknown_law_is_empty_not_404(self):
        status, body = _get(self.port, "/api/articles?law=" + quote("不存在的法"))
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(data["count"], 0)
        self.assertEqual(data["articles"], [])


class _DeepServerMixin(object):
    @classmethod
    def _start(cls, state):
        cls.state = state
        cls.server = make_server(cls.state, "127.0.0.1", 0)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever)
        cls.thread.daemon = True
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)


class DeepModeUnconfiguredTest(_DeepServerMixin, unittest.TestCase):
    """未配置 LLM key 时 deep=1 必须优雅降级：正常返回结果并说明未启用。"""

    @classmethod
    def setUpClass(cls):
        if not os.path.exists(DEMO_CORPUS):
            raise unittest.SkipTest("缺少演示语料：先跑 python scripts/make_demo_corpus.py")
        cls._start(build_state(DEMO_CORPUS))

    def test_meta_reports_llm_unavailable(self):
        _status, body = _get(self.port, "/api/meta")
        data = json.loads(body)
        self.assertFalse(data["llm_rerank"]["available"])
        self.assertIsNone(data["llm_rerank"]["model"])

    def test_deep_degrades_with_reason(self):
        status, body = _get(self.port, "/api/search?q=%E6%BC%94%E7%A4%BA&k=3&deep=1")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertFalse(data["deep_rerank"]["applied"])
        self.assertIn("reason", data["deep_rerank"])
        self.assertEqual(len(data["results"]), 3)


class DeepModeWithStubTest(_DeepServerMixin, unittest.TestCase):
    """注入桩重排器：deep=1 走重排、响应带模型名、召回池加深到 top_n。"""

    @classmethod
    def setUpClass(cls):
        if not os.path.exists(DEMO_CORPUS):
            raise unittest.SkipTest("缺少演示语料：先跑 python scripts/make_demo_corpus.py")

        class StubReranker(object):
            top_n = 50
            model = "stub-model"

            def rerank(self, query, citations, k):
                return list(reversed(citations))[:k]

        cls._start(build_state(DEMO_CORPUS, llm_reranker=StubReranker()))

    def test_meta_reports_llm_available(self):
        _status, body = _get(self.port, "/api/meta")
        data = json.loads(body)
        self.assertTrue(data["llm_rerank"]["available"])
        self.assertEqual(data["llm_rerank"]["model"], "stub-model")

    def test_deep_applies_reranker_and_deepens_recall(self):
        _status, plain = _get(self.port, "/api/search?q=%E6%BC%94%E7%A4%BA&k=3")
        _status, deep = _get(self.port, "/api/search?q=%E6%BC%94%E7%A4%BA&k=3&deep=1")
        data = json.loads(deep)
        self.assertTrue(data["deep_rerank"]["applied"])
        self.assertEqual(data["deep_rerank"]["model"], "stub-model")
        self.assertEqual(data["depth"], 50)  # max(默认融合深度, top_n)
        self.assertEqual(len(data["results"]), 3)


if __name__ == "__main__":
    unittest.main()
