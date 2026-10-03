# -*- coding: utf-8 -*-
"""用指定交叉编码器在最优融合配置上重排（默认盲写题）。

融合固定为 base 向量 + min 双路名次 + w（默认 2.0），只换交叉模型与
top_n / 查询形态，用于比较 bge-reranker-base 与 bge-reranker-large。

用法：
  py -3.13 scripts/exp_cross_model.py --cross <模型目录> --top-n 10
"""
from __future__ import print_function

import argparse
import json
import os
import sys
import time

import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

CORPUS = os.path.join(REPO, "data", "corpus_v6.jsonl")
SMALL_EMB = os.path.join(REPO, "data", "flk", "tmp", "ctx_doc_cache.npz")
BASE_EMB = os.path.join(REPO, "data", "flk", "tmp", "ctx_doc_cache_base.npz")
SMALL_DIR = os.path.join(REPO, "data", "flk", "models", "bge-small-zh-v1.5")
BASE_DIR = os.path.join(REPO, "data", "flk", "models", "bge-base-zh-v1.5")
POOL, K_RRF = 100, 60


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cross", required=True)
    ap.add_argument("--top-n", type=int, default=10)
    ap.add_argument("--w", type=float, default=2.0)
    ap.add_argument("--gold", default=os.path.join(
        REPO, "gold", "gold_external_v6.jsonl"))
    args = ap.parse_args()
    t0 = time.time()

    import torch
    from transformers import (AutoModelForSequenceClassification,
                              AutoTokenizer)
    import statute_rag.semantic_rerank as sr
    from statute_rag.importer import load_corpus
    from statute_rag.query_expansion import expand_query, load_synonyms
    from statute_rag.retrieval import HybridRetriever

    corpus = [json.loads(l) for l in open(CORPUS, encoding="utf-8") if l.strip()]
    row_index = dict((r["id"], i) for i, r in enumerate(corpus))
    TXT = [u"%s %s %s" % (r["law"], r["num"], r["text"]) for r in corpus]
    vs = np.load(SMALL_EMB)["vecs"].astype(np.float32)
    vb = np.load(BASE_EMB)["vecs"].astype(np.float32)
    hyb = HybridRetriever(load_corpus(CORPUS))
    syn = load_synonyms()
    gold = [json.loads(l) for l in open(args.gold, encoding="utf-8") if l.strip()]

    ctok = AutoTokenizer.from_pretrained(args.cross)
    cmodel = AutoModelForSequenceClassification.from_pretrained(args.cross)
    cmodel.eval()

    def ranks_of(vecs, qvec):
        sims = vecs @ qvec
        r = np.empty(len(sims), dtype=np.int64)
        r[np.argsort(-sims, kind="stable")] = np.arange(1, len(sims) + 1)
        return r

    def cross_scores(query, texts):
        out = []
        with torch.no_grad():
            for s in range(0, len(texts), 16):
                enc = ctok([query] * len(texts[s:s + 16]), texts[s:s + 16],
                           padding=True, truncation=True, max_length=512,
                           return_tensors="pt")
                out.extend(cmodel(**enc).logits.view(-1).float().tolist())
        return out

    no_cross = 0
    with_cross = 0
    for r in gold:
        q = r["query"]
        g = set(r.get("gold_ids") or [])
        e = expand_query(q, syn)
        variants = [q] if e == q else [q, e]
        cits = hyb.recall(q, depth=POOL)
        pool = [c["id"] for c in cits]
        hyr = dict((c["id"], i + 1) for i, c in enumerate(cits))
        rank = None
        for v in variants:
            for vecs, mdir in ((vs, SMALL_DIR), (vb, BASE_DIR)):
                rr = ranks_of(vecs, sr.encode_query(v, mdir))
                rank = rr if rank is None else np.minimum(rank, rr)
        sc = {}
        for cid in pool:
            sc[cid] = (1.0 / (K_RRF + hyr[cid])
                       + args.w / (K_RRF + rank[row_index[cid]]))
        fused = sorted(sc, key=lambda c: (-sc[c], pool.index(c)))
        if g & set(fused[:5]):
            no_cross += 1
        cand = fused[:args.top_n]
        scores = cross_scores(q, [TXT[row_index[c]] for c in cand])
        order = sorted(range(len(cand)), key=lambda j: (-scores[j], j))
        if g & set(cand[j] for j in order[:5]):
            with_cross += 1

    print(json.dumps({
        "gold": os.path.basename(args.gold),
        "cross": os.path.basename(args.cross),
        "w": args.w, "top_n": args.top_n,
        "nocross": no_cross, "with_cross": with_cross,
        "n": len(gold), "elapsed_s": round(time.time() - t0, 1),
    }, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
