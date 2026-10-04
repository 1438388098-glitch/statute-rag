# -*- coding: utf-8 -*-
"""LLM 重排可选层：把（语义重排后的）候选条文交给大模型挑选重排。

v8 留出盲写题实测（2026-10-04，docs/retrieval-llm-rerank.md）：运行时管线
前 50 名候选 → 大模型挑出至多 5 条按相关性提前，R@5 66.0% → 92.0%
（top50 天花板 92%：26 题救回、0 题挤掉；8 道金标在前 50 之外属召回层
缺口，重排无法触及）。认证配置：longcat-2.5-preview-free（OpenCode 免费
档）与 deepseek-v4.1-flash 均 92.0%，词法+语义+交叉编码器管线原样保留。

契约（statute_rag/interfaces.py）：只重排/截断，不发明条目、不丢条目；
输入为空返回空；API 失败/超时/解析失败一律原序返回（自动降级），绝不
抛错打断检索。依赖纪律：本模块只用标准库 urllib，且不在核心 import
链上——只有显式构造本类时才会被 import，核心保持零第三方依赖。

默认关闭：构造时必须显式给出 api_key（推荐经环境变量 STATUTE_RAG_LLM_KEY），
可配项见 __init__ 参数表。
"""
from __future__ import print_function

import json
import os
import re
import time
import urllib.error
import urllib.request

DEFAULT_BASE = "https://opencode.ai/zen/go/v1"
DEFAULT_MODEL = "longcat-2.5-preview-free"
DEFAULT_TOP_N = 50
DEFAULT_PICK = 5
DEFAULT_THINKING = "disabled"

SYSTEM = (u"你是法律检索评测员。给你一个用户的法律问题和若干候选法条。"
          u"你的任务：挑出最能回答该问题的至多5条。")
USER_TMPL = u"""问题：{q}

候选法条：
{cands}

输出要求：只输出一个 JSON 数组，按相关性从高到低给出至多5个候选序号（如 [7,3,1]）。不要解释，不要输出数组以外的任何内容。"""

_ARRAY_RE = re.compile(r"\[([0-9,\s]+)\]")


def enabled():
    """是否具备启用条件（配置了 API key）。供接入方探测，不抛错。"""
    return bool(os.environ.get("STATUTE_RAG_LLM_KEY"))


class LLMReranker(object):
    """大模型 listwise 重排器。见模块 docstring 的实测口径与契约。

    参数未显式给定时依次读环境变量 STATUTE_RAG_LLM_BASE / _KEY / _MODEL /
    _TOP_N / _PICK / _THINKING / _TIMEOUT；api_key 缺失时抛 RuntimeError
    （本层必须显式启用，不允许静默半开）。
    """

    def __init__(self, base_url=None, api_key=None, model=None,
                 top_n=None, pick=None, thinking=None, timeout=None):
        self._base_url = (base_url or os.environ.get("STATUTE_RAG_LLM_BASE")
                          or DEFAULT_BASE).rstrip("/")
        self._api_key = api_key or os.environ.get("STATUTE_RAG_LLM_KEY")
        if not self._api_key:
            raise RuntimeError(
                "LLMReranker 需要显式 API key（api_key 参数或 STATUTE_RAG_LLM_KEY）；"
                "不配置即不启用本层")
        self._model = model or os.environ.get("STATUTE_RAG_LLM_MODEL") or DEFAULT_MODEL
        self._top_n = int(top_n or os.environ.get("STATUTE_RAG_LLM_TOP_N") or DEFAULT_TOP_N)
        self._pick = int(pick or os.environ.get("STATUTE_RAG_LLM_PICK") or DEFAULT_PICK)
        self._thinking = (thinking or os.environ.get("STATUTE_RAG_LLM_THINKING")
                          or DEFAULT_THINKING)
        self._timeout = float(timeout or os.environ.get("STATUTE_RAG_LLM_TIMEOUT") or 120)
        # 测试注入点：_post(url, body) -> dict（OpenAI chat.completions 形状）
        self._post = self._post_http
        # 公开只读别名：接入方（app 深度模式、评测）取候选深度与模型名用
        self.top_n = self._top_n
        self.model = self._model

    # ---- 契约入口 -------------------------------------------------------

    def rerank(self, query, citations, k):
        """返回 citations 的重排视图（至多 k 条）；失败原序降级，绝不抛错。"""
        if not citations:
            return []
        feed = list(citations)[:self._top_n]
        try:
            picked = self._pick_indices(query, feed)
        except Exception:
            return list(citations)[:k]  # 降级：原序返回，等于本层未启用
        chosen, seen = [], set()
        for i in picked:
            if 1 <= i <= len(feed) and i not in seen:
                chosen.append(feed[i - 1])
                seen.add(i)
            if len(chosen) >= self._pick:
                break
        rest = [c for j, c in enumerate(feed) if (j + 1) not in seen]
        return (chosen + rest + list(citations)[self._top_n:])[:k]

    # ---- 实现 -----------------------------------------------------------

    def _pick_indices(self, query, feed):
        """调 API 解析出候选序号列表（1 起）。任何失败都抛给上层降级。"""
        cands = u"\n".join(u"[%d] 《%s》%s：%s" % (i + 1, c["law"], c["num"], c["text"])
                           for i, c in enumerate(feed))
        content = self._chat(SYSTEM, USER_TMPL.format(q=query, cands=cands))
        m = _ARRAY_RE.search(content or "")
        if not m:
            raise ValueError(u"LLM 重排输出不含 JSON 数组：%r" % (content or "")[:80])
        return [int(x) for x in m.group(1).replace(u"，", u",").split(",") if x.strip()]

    def _chat(self, system, user):
        body = {"model": self._model, "temperature": 0,
                "messages": [{"role": "system", "content": system},
                             {"role": "user", "content": user}]}
        # 思考控制（可关即关，省 token；关不掉的模型不发 max_tokens 打断思考）
        if self._thinking == "disabled":
            body["thinking"] = {"type": "disabled"}
        elif self._thinking == "low":
            body["reasoning_effort"] = "low"
        last = None
        for attempt in range(3):
            try:
                d = self._post(self._base_url + "/chat/completions", body)
                return d["choices"][0]["message"].get("content") or ""
            except urllib.error.HTTPError as e:
                if e.code not in (429, 500, 502, 503, 504):
                    raise  # 请求本身非法（如模型不认参数）：重试无益，直接降级
                last = e
            except Exception as e:  # 超时/连接错误：退避重试
                last = e
            if attempt < 2:
                time.sleep(1.0 * (2 ** attempt))
        raise last

    def _post_http(self, url, body):
        req = urllib.request.Request(
            url, data=json.dumps(body).encode("utf-8"),
            headers={"content-type": "application/json",
                     "authorization": "Bearer " + self._api_key,
                     "x-api-key": self._api_key,
                     "x-opencode-session": "statute-rag",
                     "user-agent": "statute-rag-llm-rerank/1.0"})
        with urllib.request.urlopen(req, timeout=self._timeout) as r:
            return json.loads(r.read().decode("utf-8"))


class LLMRerankRetriever(object):
    """包装既有检索器：search() 先按 top_n 深度取完整排名，再交 LLM 重排。

    评测与接入用。inner 需有 search(query, k)；llm 是 LLMReranker。
    """

    def __init__(self, inner, llm):
        self._inner = inner
        self._llm = llm

    def search(self, query, k=5):
        depth = max(int(k), self._llm._top_n)
        results = self._inner.search(query, k=depth)
        return self._llm.rerank(query, results, k)
