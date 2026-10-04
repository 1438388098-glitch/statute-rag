# -*- coding: utf-8 -*-
"""Jev 重排全链路评测：把「LLM 重排可选层」的裁判换成本地判选的 Jev，同一次运行内并列三路。

链路（与运行时一致）
--------------------
``HybridRetriever(corpus, reranker=SemanticReranker(...), pool_extra=SemanticPool(...))``
先给出前 ``top_n`` 名候选（词法三通道 → 语义并池 → 交叉编码器混合），再把这份候选分别交给：

- ``local``：不接任何裁判（现行 v7 管线）——**校准基线**（v8 留出目标 66.0%）；
- ``jev``：``JevReranker``（systemone，choice/score 两种判选模式）；
- ``llm``（可选 ``--llm``）：现行 ``LLMReranker`` chat 裁判（复刻 92.0% 参照）。

契约一致：三路都「只提前至多 pick 条、其余保持原序」，失败原序降级。

输出
----
``data/flk/tmp/eval_jev_<tag>.json``：每题三路名次 + 指标 + 延迟/费用，供报告取数。

用法
----
  py -3.13 scripts/eval_jev_rerank.py                       # v8 留出 100 题，local + jev
  py -3.13 scripts/eval_jev_rerank.py --llm                 # 另跑 chat 裁判对照
  py -3.13 scripts/eval_jev_rerank.py --limit 5             # 冒烟
"""
from __future__ import print_function

import argparse
import json
import os
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from statute_rag.gold import load_gold  # noqa: E402
from statute_rag.importer import load_corpus  # noqa: E402
from statute_rag.retrieval import HybridRetriever  # noqa: E402

MODELS = os.path.join(REPO, "data", "flk", "models")
TMP = os.path.join(REPO, "data", "flk", "tmp")

DEFAULT_GOLDS = [("b8v7", os.path.join(REPO, "gold", "gold_blind_v8_v7.jsonl"))]

# 每 1M token 的（输入, 输出）美元价，用于把 token 用量折成费用估计；
# 免费档记 0，套餐内模型按 models.dev 标价列出（仅作参照，实际以套餐/账单为准）。
PRICES = {
    "jev-1.13-free": (0.0, 0.0),
    "jev-1.13": (0.042, 0.0),
    "longcat-2.5-preview-free": (0.0, 0.0),
    "space-bunny-free": (0.0, 0.0),
    "deepseek-v4.1-flash": (0.15, 0.60),
}


def _lat_stats(ms):
    if not ms:
        return {}
    s = sorted(ms)
    return {"mean_ms": round(sum(s) / len(s), 1),
            "p50_ms": round(statistics.median(s), 1),
            "p95_ms": round(s[min(len(s) - 1, int(len(s) * 0.95))], 1)}


def _cost_usd(model, in_tok, out_tok):
    price = PRICES.get(model)
    if price is None:
        return None
    return round(in_tok / 1e6 * price[0] + out_tok / 1e6 * price[1], 6)


def _gold_ids(q):
    ids = q.get("gold_ids")
    if not ids:
        ids = [q.get("gold_id")]
    return set(i for i in ids if i is not None)


def _rank_of(results, gids):
    for i, c in enumerate(results, start=1):
        if c["id"] in gids:
            return i
    return None


def _metrics(ranks):
    n = len(ranks)
    out = {"n": n}
    for k in (1, 5, 10, 20, 30):
        out["R@%d" % k] = sum(1 for r in ranks if r is not None and r <= k) / float(n)
    out["MRR@5"] = sum(1.0 / r for r in ranks if r is not None and r <= 5) / float(n)
    out["miss_in_pool"] = sum(1 for r in ranks if r is None)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", default=os.path.join(REPO, "data", "corpus_v7.jsonl"))
    ap.add_argument("--emb", default=os.path.join(TMP, "ctx_doc_cache_v7.npz"))
    ap.add_argument("--emb-base", default=os.path.join(TMP, "ctx_doc_cache_base_v7.npz"))
    ap.add_argument("--cross", default=os.path.join(MODELS, "bge-reranker-base"))
    ap.add_argument("--no-cross", action="store_true")
    ap.add_argument("--union-k", type=int, default=50)
    ap.add_argument("--gold", action="append", default=[], metavar="NAME=PATH")
    ap.add_argument("--no-default-golds", action="store_true")
    ap.add_argument("--top-n", type=int, default=50)
    ap.add_argument("--pick", type=int, default=5)
    ap.add_argument("--jev-mode", default="choice", choices=["choice", "score"])
    ap.add_argument("--jev-model", default=None)
    ap.add_argument("--model", default="longcat-2.5-preview-free")
    ap.add_argument("--llm", action="store_true", help="同跑 chat LLM 裁判对照")
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 题（冒烟）")
    ap.add_argument("--pools", default="", help="候选池缓存目录：命中即跳过昂贵的本地管线")
    ap.add_argument("--force-pools", action="store_true", help="忽略缓存重算候选池")
    ap.add_argument("--tag", default="jev")
    args = ap.parse_args()

    golds = [] if args.no_default_golds else list(DEFAULT_GOLDS)
    for spec in args.gold:
        name, sep, path = spec.partition("=")
        if not sep:
            ap.error("--gold 需要 NAME=PATH 形式：%s" % spec)
        golds.append((name, os.path.abspath(path)))

    import statute_rag.semantic_rerank as sr
    from statute_rag.jev_rerank import JevReranker

    t0 = time.time()
    corpus = load_corpus(args.corpus)
    print("语料：%d 条" % len(corpus), flush=True)
    emb_specs = [(args.emb, os.path.join(MODELS, "bge-small-zh-v1.5")),
                 (args.emb_base, os.path.join(MODELS, "bge-base-zh-v1.5"))]
    pool = sr.SemanticPool(corpus, [p for p, _ in emb_specs],
                           [d for _, d in emb_specs], union_k=args.union_k)
    reranker = sr.SemanticReranker(
        corpus, [p for p, _ in emb_specs], [d for _, d in emb_specs],
        pool=pool, cross_model_dir=None if args.no_cross else args.cross)
    hyb = HybridRetriever(corpus, reranker=reranker, pool_extra=pool)

    jev = JevReranker(mode=args.jev_mode, top_n=args.top_n, pick=args.pick,
                      model=args.jev_model)
    print("Jev 重排：%s 模式 top%d pick%d 模型=%s"
          % (jev.mode, jev.top_n, args.pick, jev.model), flush=True)
    llm = None
    if args.llm:
        from statute_rag.llm_rerank import LLMReranker
        llm = LLMReranker(model=args.model, top_n=args.top_n, pick=args.pick)
        print("LLM 对照：%s top%d" % (llm._model, llm._top_n), flush=True)

    for name, path in golds:
        gold = load_gold(path)
        if args.limit:
            gold = gold[:args.limit]
        print("金标 %s：%d 题 (%s)" % (name, len(gold), path), flush=True)

        # 阶段 1：本地管线取候选池（顺序，torch CPU）；有缓存则直接复用
        local_s = 0.0
        pools = None
        pool_path = os.path.join(args.pools, "pools_%s.json" % name) if args.pools else ""
        if args.pools and os.path.exists(pool_path) and not args.force_pools:
            with open(pool_path, "r", encoding="utf-8") as f:
                pools = json.load(f)["pools"]
            if len(pools) != len(gold):
                print("  候选池缓存题数不符，重算", flush=True)
                pools = None
        if pools is None:
            t_local = time.time()
            pools = []
            for q in gold:
                pools.append(hyb.search(q["query"], k=args.top_n))
            local_s = time.time() - t_local
            if args.pools:
                if not os.path.isdir(args.pools):
                    os.makedirs(args.pools)
                with open(pool_path, "w", encoding="utf-8") as f:
                    json.dump({"gold": name, "top_n": args.top_n,
                               "qids": [q.get("qid") for q in gold],
                               "pools": pools}, f, ensure_ascii=False)
            print("  本地管线 %d 题 %.1fs（缓存 %s）"
                  % (len(gold), local_s, pool_path or "-"), flush=True)
        else:
            print("  复用候选池缓存 %s" % pool_path, flush=True)
        gid_list = [_gold_ids(q) for q in gold]
        base_ranks = [_rank_of(pools[i], gid_list[i]) for i in range(len(gold))]

        # 阶段 2：Jev / LLM 重排（可并发）
        def run_jev(i):
            t = time.time()
            out = jev.rerank(gold[i]["query"], pools[i], k=args.top_n)
            return i, out, (time.time() - t) * 1000.0

        jev_res = [None] * len(gold)
        jev_ms = [0.0] * len(gold)
        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
            for i, out, ms in ex.map(run_jev, range(len(gold))):
                jev_res[i] = out
                jev_ms[i] = ms
        jev_ranks = [_rank_of(jev_res[i], gid_list[i]) for i in range(len(gold))]

        llm_ranks = [None] * len(gold)
        llm_ms = [0.0] * len(gold)
        llm_tok = {"in": 0, "out": 0}
        if llm is not None:
            _orig_post = llm._post

            def _wrapped_post(url, body):
                d = _orig_post(url, body)
                u = d.get("usage") or {}
                llm_tok["in"] += int(u.get("prompt_tokens") or u.get("input_tokens") or 0)
                llm_tok["out"] += int(u.get("completion_tokens") or u.get("output_tokens") or 0)
                return d

            llm._post = _wrapped_post

            def run_llm(i):
                t = time.time()
                out = llm.rerank(gold[i]["query"], pools[i], k=args.top_n)
                return i, out, (time.time() - t) * 1000.0
            with ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
                for i, out, ms in ex.map(run_llm, range(len(gold))):
                    llm_ranks[i] = _rank_of(out, gid_list[i])
                    llm_ms[i] = ms

        base_m = _metrics(base_ranks)
        jev_m = _metrics(jev_ranks)
        llm_m = _metrics(llm_ranks) if llm is not None else None

        rescue = sum(1 for b, j in zip(base_ranks, jev_ranks)
                     if b is not None and b > 5 and j is not None and j <= 5)
        demote = sum(1 for b, j in zip(base_ranks, jev_ranks)
                     if b is not None and b <= 5 and (j is None or j > 5))

        rows = []
        for i, q in enumerate(gold):
            rows.append({
                "qid": q.get("qid"), "query": q["query"],
                "base_rank": base_ranks[i], "jev_rank": jev_ranks[i],
                "llm_rank": llm_ranks[i], "jev_ms": round(jev_ms[i], 1),
            })

        out = {
            "gold": name, "path": path, "n": len(gold),
            "corpus": args.corpus, "corpus_size": len(corpus),
            "top_n": args.top_n, "pick": args.pick,
            "workers": args.workers,
            "jev": {"model": jev.model, "mode": jev.mode,
                    "degraded": jev.degraded, "calls": jev.calls,
                    "lat": _lat_stats(jev_ms),
                    "input_tokens": jev.input_tokens,
                    "output_tokens": jev.output_tokens,
                    "cost_usd": _cost_usd(jev.model, jev.input_tokens, jev.output_tokens),
                    "last_cost": jev.last_cost},
            "local": {"metrics": base_m, "elapsed_s": round(local_s, 1),
                      "mean_ms_per_q": round(local_s * 1000.0 / max(1, len(gold)), 1)},
            "jev_arm": {"metrics": jev_m, "rescue": rescue, "demote": demote},
            "llm_arm": ({"metrics": llm_m, "lat": _lat_stats(llm_ms),
                         "input_tokens": llm_tok["in"],
                         "output_tokens": llm_tok["out"],
                         "cost_usd": _cost_usd(llm._model, llm_tok["in"], llm_tok["out"])}
                        if llm is not None else None),
            "rows": rows,
        }
        out_path = os.path.join(TMP, "eval_jev_%s_%s.json" % (args.tag, name))
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)

        def fmt(m):
            return " ".join("R@%d=%.1f%%" % (k, m["R@%d" % k] * 100)
                            for k in (1, 5, 10, 30)) + " MRR@5=%.3f" % m["MRR@5"]
        jl = _lat_stats(jev_ms)
        print("  local %s  %.0fms/题(本地管线)" % (fmt(base_m), local_s * 1000.0 / max(1, len(gold))), flush=True)
        print("  jev   %s  rescue=%d demote=%d degraded=%d 中位%.0fms p95=%.0fms "
              "tok=%d/%d cost=%s"
              % (fmt(jev_m), rescue, demote, jev.degraded, jl["p50_ms"], jl["p95_ms"],
                 jev.input_tokens, jev.output_tokens,
                 _cost_usd(jev.model, jev.input_tokens, jev.output_tokens)), flush=True)
        if llm_m:
            ll = _lat_stats(llm_ms)
            print("  llm   %s  中位%.0fms p95=%.0fms tok=%d/%d cost=%s"
                  % (fmt(llm_m), ll["p50_ms"], ll["p95_ms"], llm_tok["in"],
                     llm_tok["out"], _cost_usd(llm._model, llm_tok["in"], llm_tok["out"])),
                  flush=True)
        print("  saved", out_path, flush=True)

    print("总用时 %.0fs" % (time.time() - t0), flush=True)


if __name__ == "__main__":
    main()
