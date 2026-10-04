# -*- coding: utf-8 -*-
"""Jev 重排可选层：用 TypeSafe Jev（OpenCode Zen systemone 端点）对候选条文判选重排。

与 llm_rerank.py 的关系与差别
-----------------------------
- llm_rerank 走 OpenAI 兼容 ``/chat/completions``，让通用大模型**自由输出一个 JSON
  序号数组**，程序按该数组把候选提前；
- 本层走 Jev 的**结构化决策端点** ``/zen/v1/systemone``，输入 ``state`` + typed
  ``questions``，返回**每个候选的概率/分数**，程序据分排序。Jev 不产出「排好序的
  列表」，只产出对每个候选的判断——这正是「精选」而非「生成」的形态。

两种判选模式（``mode``）：
- ``choice``（默认）：把 N 个候选作为**单个 choice 问题的 criteria**，一次调用拿回
  覆盖全部候选的概率分布，按概率降序取头部。一次请求即可，最快；
- ``score``：给**每个候选一个 score 问题**（并行问同一 state），按分值降序取头部。
  请求体更大（候选文本进 state），但每个候选独立打分。

契约（statute_rag/interfaces.py）
---------------------------------
- 只重排/截断，不发明条目、不丢条目；输入为空返回空；
- API 失败/超时/解析失败一律**原序返回**（自动降级），绝不抛错打断检索；
- 默认「只提前、不挤掉」：把得分最高的至多 ``pick`` 条提到最前，其余保持原序——
  与 llm_rerank 同形，便于公平对照（合成/真实金标不会被无谓 demote）。

依赖纪律：本模块只用标准库 ``urllib``，且**不在核心 import 链上**——只有显式构造
本类时才会被 import；核心保持零第三方依赖。默认模型 ``jev-1.13-free``（限时免费档，
无需 key；如配置 ``STATUTE_RAG_JEV_KEY`` 则带上鉴权头）。
"""
from __future__ import print_function

import json
import os
import time
import urllib.error
import urllib.request

DEFAULT_BASE = "https://opencode.ai/zen/v1/systemone"
DEFAULT_MODEL = "jev-1.13-free"
DEFAULT_TOP_N = 50
DEFAULT_PICK = 5
DEFAULT_MODE = "choice"
# 单条候选进判选文本的上限（字符）。50 条 × 400 字 ≈ 2 万字符，实测可被免费档接受；
# 设 0 表示不截断。截断只影响送给 Jev 的视图，不改动返回的 Citation 原文。
DEFAULT_MAX_CAND_CHARS = 400
DEFAULT_TIMEOUT = 120


def enabled():
    """Jev 免费档无需 key，默认可用；设 STATUTE_RAG_JEV_DISABLE=1 显式关闭。"""
    return os.environ.get("STATUTE_RAG_JEV_DISABLE") != "1"


class JevReranker(object):
    """Jev 判选重排器。见模块 docstring 的契约与两种模式说明。

    参数未显式给定时依次读环境变量 STATUTE_RAG_JEV_BASE / _KEY / _MODEL /
    _TOP_N / _PICK / _MODE / _MAX_CAND_CHARS / _TIMEOUT。Jev 免费档不需要 key，
    因此与 LLMReranker 不同，本类**允许无 key 构造**。

    运行观测（``last_ms`` / ``last_cost`` / ``last_error`` / ``calls`` / ``degraded``）
    仅供评测与接入诊断，不参与契约。
    """

    def __init__(self, base_url=None, api_key=None, model=None, top_n=None,
                 pick=None, mode=None, max_cand_chars=None, timeout=None):
        self._base_url = (base_url or os.environ.get("STATUTE_RAG_JEV_BASE")
                          or DEFAULT_BASE).rstrip("/")
        self._api_key = api_key or os.environ.get("STATUTE_RAG_JEV_KEY")
        self._model = model or os.environ.get("STATUTE_RAG_JEV_MODEL") or DEFAULT_MODEL
        self._top_n = int(top_n or os.environ.get("STATUTE_RAG_JEV_TOP_N") or DEFAULT_TOP_N)
        self._pick = int(pick or os.environ.get("STATUTE_RAG_JEV_PICK") or DEFAULT_PICK)
        mode = (mode or os.environ.get("STATUTE_RAG_JEV_MODE") or DEFAULT_MODE).lower()
        if mode not in ("choice", "score"):
            raise ValueError("JEV_MODE 只能是 choice 或 score：%r" % mode)
        self._mode = mode
        self._max_cand_chars = int(
            max_cand_chars or os.environ.get("STATUTE_RAG_JEV_MAX_CAND_CHARS")
            or DEFAULT_MAX_CAND_CHARS)
        self._timeout = float(timeout or os.environ.get("STATUTE_RAG_JEV_TIMEOUT")
                              or DEFAULT_TIMEOUT)
        # 测试注入点：_post(payload: dict) -> dict（systemone 响应）
        self._post = self._post_http
        # 公开只读别名：接入方（评测、app）取候选深度与模型名用，与 LLMReranker 对称
        self.top_n = self._top_n
        self.model = self._model
        self.mode = self._mode
        # 运行观测
        self.last_ms = None
        self.last_cost = None
        self.last_error = None
        self.last_confidence = None
        self.last_degraded = None
        self.calls = 0
        self.degraded = 0
        self.input_tokens = 0
        self.output_tokens = 0

    # ---- 契约入口 -------------------------------------------------------

    def rerank(self, query, citations, k):
        """返回 citations 的重排视图（至多 k 条）；失败原序降级，绝不抛错。"""
        if not citations:
            return []
        feed = list(citations)[:self._top_n]
        try:
            scores = self._scores(query, feed)
        except Exception as exc:  # noqa: BLE001 —— 契约要求任何失败都降级
            self.degraded += 1
            self.last_degraded = True
            self.last_error = "%s: %s" % (type(exc).__name__, exc)
            return list(citations)[:k]
        # 得分降序；并列（含全 0）保持原序——稳定排序不因浮点相等打乱既有秩序
        order = sorted(range(len(feed)), key=lambda i: (-scores[i], i))
        jrank = dict((i, pos) for pos, i in enumerate(order, 1))
        picked = order[:self._pick]
        picked_set = set(picked)
        # 附上 Jev 判分视图（interfaces.py 允许新增字段）：界面据此展示 Jev 的判断。
        # 用浅拷贝，避免污染共享的 Citation 字典；原字段一律保留、不改名。
        annotated = []
        for i, cite in enumerate(feed):
            view = dict(cite)
            view["jev_score"] = round(float(scores[i]), 6)
            view["jev_rank"] = jrank[i]
            view["jev_pick"] = 1 if i in picked_set else 0
            annotated.append(view)
        chosen = [annotated[i] for i in picked]
        rest = [annotated[j] for j in range(len(feed)) if j not in picked_set]
        tail = [dict(c) for c in list(citations)[self._top_n:]]
        self.last_degraded = False
        return (chosen + rest + tail)[:k]

    # ---- 实现 -----------------------------------------------------------

    def _scores(self, query, feed):
        t0 = time.time()
        if self._mode == "choice":
            scores = self._scores_choice(query, feed)
        else:
            scores = self._scores_score(query, feed)
        self.last_ms = int((time.time() - t0) * 1000)
        self.calls += 1
        return scores

    def _cand_line(self, i, cite):
        text = cite.get("text") or u""
        if self._max_cand_chars and len(text) > self._max_cand_chars:
            text = text[:self._max_cand_chars]
        return u"[%d] 《%s》%s：%s" % (i, cite.get("law", u""), cite.get("num", u""), text)

    def _scores_choice(self, query, feed):
        """单 choice 问题，criteria = 候选条文；返回覆盖全部候选的概率列表。"""
        criteria = dict((str(i), self._cand_line(i, c))
                        for i, c in enumerate(feed, 1))
        payload = {
            "model": self._model,
            "state": u"用户的法律问题：%s" % query,
            "questions": {
                "rank": {
                    "type": "choice",
                    "instructions": u"候选条文中哪一条最能直接回答用户的法律问题？",
                    "criteria": criteria,
                }
            },
        }
        d = self._request(payload)
        self._record_meta(d)
        ans = (d.get("answers") or {}).get("rank") or {}
        probs = ans.get("probabilities")
        if probs is None:
            raise ValueError(u"Jev choice 响应缺少 probabilities：%r" % (d.get("answers"),)[:120])
        self.last_confidence = ans.get("confidence")
        out = []
        for i in range(1, len(feed) + 1):
            v = probs.get(str(i))
            out.append(float(v) if v is not None else 0.0)
        return out

    def _scores_score(self, query, feed):
        """每候选一个 score 问题（同一 state 并行）；返回每候选分值。"""
        lines = u"\n".join(self._cand_line(i, c) for i, c in enumerate(feed, 1))
        questions = {}
        for i in range(1, len(feed) + 1):
            questions["c%d" % i] = {
                "type": "score",
                "instructions": u"候选[%d]在多大程度上直接回答了用户的法律问题？" % i,
                "criteria": [u"完全不相关", u"弱相关", u"相关", u"直接回答"],
            }
        payload = {
            "model": self._model,
            "state": u"用户的法律问题：%s\n\n候选法条：\n%s" % (query, lines),
            "questions": questions,
        }
        d = self._request(payload)
        self._record_meta(d)
        answers = d.get("answers")
        if not answers:
            raise ValueError(u"Jev score 响应缺少 answers：%r" % (d,)[:120])
        out = []
        for i in range(1, len(feed) + 1):
            a = answers.get("c%d" % i) or {}
            out.append(float(a.get("score") or 0.0))
        return out

    def _record_meta(self, d):
        self.last_cost = d.get("cost")
        usage = d.get("usage") or {}
        self.input_tokens += int(usage.get("input_tokens") or 0)
        self.output_tokens += int(usage.get("output_tokens") or 0)
        self.last_error = None

    def _request(self, payload):
        """带退避重试的请求：429/5xx 与网络错误重试 3 次，其余直接抛给上层降级。"""
        last = None
        for attempt in range(3):
            try:
                return self._post(payload)
            except urllib.error.HTTPError as e:
                if e.code not in (429, 500, 502, 503, 504):
                    raise  # 请求本身非法：重试无益，直接降级
                last = e
            except Exception as e:  # 超时/连接错误：退避重试
                last = e
            if attempt < 2:
                time.sleep(1.0 * (2 ** attempt))
        raise last

    def _post_http(self, payload):
        body = json.dumps(payload).encode("utf-8")
        headers = {
            "content-type": "application/json",
            "accept": "application/json",
            "x-opencode-session": "statute-rag",
            "user-agent": "statute-rag-jev-rerank/1.0",
        }
        if self._api_key:
            headers["authorization"] = "Bearer " + self._api_key
            headers["x-api-key"] = self._api_key
        req = urllib.request.Request(self._base_url, data=body, headers=headers)
        with urllib.request.urlopen(req, timeout=self._timeout) as r:
            return json.loads(r.read().decode("utf-8"))


class JevRerankRetriever(object):
    """包装既有检索器：search() 先按 top_n 深度取完整排名，再交 Jev 重排。

    评测与接入用。inner 需有 search(query, k)；jev 是 JevReranker。与
    llm_rerank.LLMRerankRetriever 同形，便于并列对照。
    """

    def __init__(self, inner, jev):
        self._inner = inner
        self._jev = jev

    def search(self, query, k=5):
        depth = max(int(k), self._jev._top_n)
        results = self._inner.search(query, k=depth)
        return self._jev.rerank(query, results, k)
