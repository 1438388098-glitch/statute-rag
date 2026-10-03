# -*- coding: utf-8 -*-
"""语义重排 v7 权威评测（真实运行时配置，三套金标）。

配置 = HybridRetriever(corpus, reranker=SemanticReranker(...),
                       pool_extra=SemanticPool(...))：
- 双模型向量（bge-small-zh-v1.5 + bge-base-zh-v1.5）
- 并池：语义 top-50 并入 hybrid 前 100
- 语义名次：双模型 × 原/扩展查询 四种组合取 min
- 融合 w_sem=2.0；交叉级 top-10 + RRF 混合 w_cross=3.0
- gateA：LIKE 精确命中时原序返回

用法：
  py -3.13 scripts/eval_semantic_rerank.py
可选：--cross / --no-cross、--w-sem、--w-cross、--union-k、--tag
      --gold NAME=PATH（可重复）追加金标，用于在新留出金标上复核
输出：data/flk/tmp/eval_v7_<tag>.json
"""
from __future__ import print_function

import argparse
import json
import os
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from statute_rag.eval_harness import evaluate_multi_k
from statute_rag.gold import load_gold
from statute_rag.importer import load_corpus
from statute_rag.retrieval import BM25Retriever, HybridRetriever, LikeRetriever

MODELS = os.path.join(REPO, "data", "flk", "models")
TMP = os.path.join(REPO, "data", "flk", "tmp")

GOLD_FILES = [
    ("real38", os.path.join(REPO, "gold", "gold_real_38_v6.jsonl")),
    ("blind100", os.path.join(REPO, "gold", "gold_external_v6.jsonl")),
    ("synth177", os.path.join(REPO, "gold", "gold_synth_v6_seed20260918.jsonl")),
]

EMB_SPECS = [
    (os.path.join(TMP, "ctx_doc_cache.npz"),
     os.path.join(MODELS, "bge-small-zh-v1.5")),
    (os.path.join(TMP, "ctx_doc_cache_base.npz"),
     os.path.join(MODELS, "bge-base-zh-v1.5")),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cross", default=os.path.join(MODELS, "bge-reranker-base"))
    ap.add_argument("--no-cross", action="store_true")
    ap.add_argument("--w-sem", type=float, default=None)
    ap.add_argument("--w-cross", type=float, default=None)
    ap.add_argument("--union-k", type=int, default=50)
    ap.add_argument("--gold", action="append", default=[], metavar="NAME=PATH",
                    help="追加金标（可重复，路径可给仓库外）。用于在新留出金标上复核")
    ap.add_argument("--no-default-golds", action="store_true",
                    help="只评 --gold 指定的金标（跳过三套默认金标，复核新金标时省时间）")
    ap.add_argument("--lexical", action="store_true",
                    help="同一批金标上另跑冻结词法基线（like/bm25/hybrid），供对照")
    ap.add_argument("--corpus", default=os.path.join(REPO, "data", "corpus_v6.jsonl"),
                    help="语料路径（补法版本用 data/corpus_v7.jsonl）")
    ap.add_argument("--emb", default=os.path.join(TMP, "ctx_doc_cache.npz"),
                    help="小模型条文向量（补法版本用 ctx_doc_cache_v7.npz）")
    ap.add_argument("--emb-base", default=os.path.join(TMP, "ctx_doc_cache_base.npz"),
                    help="大模型条文向量（补法版本用 ctx_doc_cache_base_v7.npz）")
    ap.add_argument("--tag", default="v7")
    args = ap.parse_args()
    t0 = time.time()

    golds = [] if args.no_default_golds else list(GOLD_FILES)
    for spec in args.gold:
        name, sep, path = spec.partition("=")
        if not sep or not name or not path:
            ap.error("--gold 需要 NAME=PATH 形式，收到：%s" % spec)
        golds.append((name, os.path.abspath(path)))
    if not golds:
        ap.error("没有可评的金标：--no-default-golds 时必须给 --gold")

    import statute_rag.semantic_rerank as sr
    if args.w_sem is not None:
        sr.W_SEM = args.w_sem
    if args.w_cross is not None:
        sr.W_CROSS = args.w_cross

    corpus = load_corpus(args.corpus)
    emb_specs = [(args.emb, os.path.join(MODELS, "bge-small-zh-v1.5")),
                 (args.emb_base, os.path.join(MODELS, "bge-base-zh-v1.5"))]
    pool = sr.SemanticPool(corpus, [p for p, _ in emb_specs],
                           [d for _, d in emb_specs], union_k=args.union_k)
    reranker = sr.SemanticReranker(
        corpus, [p for p, _ in emb_specs], [d for _, d in emb_specs],
        pool=pool, cross_model_dir=None if args.no_cross else args.cross)
    hyb = HybridRetriever(corpus, reranker=reranker, pool_extra=pool)

    results = {}
    gold_cache = {}
    for name, path in golds:
        gold = load_gold(path)
        gold_cache[name] = gold
        multi = evaluate_multi_k(hyb, gold, ks=(5, 10, 20, 30))
        results[name] = dict(
            (str(k), {"R": m["recall_at_k"], "MRR": m["mrr"]})
            for k, m in multi.items())
        line = " ".join("R@%d=%.1f%%" % (k, m["recall_at_k"] * 100)
                        for k, m in sorted(multi.items()))
        print("%s %s (MRR@5=%.3f)" % (name, line, multi[5]["mrr"]), flush=True)

    if args.lexical:
        for rname, ret in [("like", LikeRetriever(corpus)),
                           ("bm25", BM25Retriever(corpus)),
                           ("hybrid", HybridRetriever(corpus))]:
            for name, _ in golds:
                multi = evaluate_multi_k(ret, gold_cache[name], ks=(5, 10, 20, 30))
                key = "lexical:%s:%s" % (rname, name)
                results[key] = dict(
                    (str(k), {"R": m["recall_at_k"], "MRR": m["mrr"]})
                    for k, m in multi.items())
                line = " ".join("R@%d=%.1f%%" % (k, m["recall_at_k"] * 100)
                                for k, m in sorted(multi.items()))
                print("%s %s (MRR@5=%.3f)" % (key, line, multi[5]["mrr"]), flush=True)

    out = {"w_sem": sr.W_SEM, "w_cross": sr.W_CROSS,
           "union_k": args.union_k,
           "cross": None if args.no_cross else os.path.basename(args.cross),
           "elapsed_s": round(time.time() - t0, 1), "results": results}
    out_path = os.path.join(TMP, "eval_%s.json" % args.tag)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("saved", out_path, "%.0fs" % (time.time() - t0), flush=True)


if __name__ == "__main__":
    main()
