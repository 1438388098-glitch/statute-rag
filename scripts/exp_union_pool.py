# -*- coding: utf-8 -*-
"""并池实验：候选池 = hybrid 前 100 ∪ 语义前 K，再交叉重排。

动机（docs/retrieval-v7-semantic.md 诊断）：盲写题有 8 题的金标根本不在
hybrid 前 100（池外），嵌套重排永远看不到它们；其中 3 题语义名次在
31–35，把语义前 K 并进候选池即可让它们进入重排视野。

对照：不并池（纯 hybrid 池）、并池 K=20/50/100，各带交叉重排。
输出：data/flk/tmp/exp_union_pool.json
"""
from __future__ import print_function

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
CROSS_DIR = os.path.join(REPO, "data", "flk", "models", "bge-reranker-base")
OUT = os.path.join(REPO, "data", "flk", "tmp", "exp_union_pool.json")
POOL, K_RRF, W = 100, 60, 2.0

GOLDS = [
    ("real38", os.path.join(REPO, "gold", "gold_real_38_v6.jsonl")),
    ("blind100", os.path.join(REPO, "gold", "gold_external_v6.jsonl")),
]


def main():
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
    ctok = AutoTokenizer.from_pretrained(CROSS_DIR)
    cmodel = AutoModelForSequenceClassification.from_pretrained(CROSS_DIR)
    cmodel.eval()

    def ranks_of(vecs, qvec):
        sims = vecs @ qvec
        r = np.empty(len(sims), dtype=np.int64)
        r[np.argsort(-sims, kind="stable")] = np.arange(1, len(sims) + 1)
        return r

    def cross_scores(query, texts):
        out = []
        with torch.no_grad():
            for s in range(0, len(texts), 32):
                enc = ctok([query] * len(texts[s:s + 32]), texts[s:s + 32],
                           padding=True, truncation=True, max_length=512,
                           return_tensors="pt")
                out.extend(cmodel(**enc).logits.view(-1).float().tolist())
        return out

    result = {}
    for name, path in GOLDS:
        gold = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
        qs, golds, pools, hyrs, semtop = [], [], [], [], {}
        for r in gold:
            q = r["query"]
            qs.append(q)
            golds.append(set(r.get("gold_ids") or []))
            cits = hyb.recall(q, depth=POOL)
            pools.append([c["id"] for c in cits])
            hyrs.append(dict((c["id"], i + 1) for i, c in enumerate(cits)))
            e = expand_query(q, syn)
            rank = None
            for v in ([q] if e == q else [q, e]):
                for vecs, mdir in ((vs, SMALL_DIR), (vb, BASE_DIR)):
                    rr = ranks_of(vecs, sr.encode_query(v, mdir))
                    rank = rr if rank is None else np.minimum(rank, rr)
            order = np.argsort(rank, kind="stable")
            semtop[len(qs) - 1] = (rank,
                                   [corpus[int(j)]["id"] for j in order.tolist()])
        print(name, "ready %.0fs" % (time.time() - t0), flush=True)

        entry = {}
        for K in (0, 20, 50, 100):
            fused_hits = 0
            cross_hits = 0
            for i in range(len(qs)):
                rank = semtop[i][0]
                sem_ids = semtop[i][1]
                pool = list(pools[i])
                if K:
                    extra = [cid for cid in sem_ids[:K] if cid not in hyrs[i]]
                    pool = pool + extra
                sc = {}
                for cid in pool:
                    h = hyrs[i].get(cid)
                    s = (1.0 / (K_RRF + h)) if h else 0.0
                    sc[cid] = s + W / (K_RRF + rank[row_index[cid]])
                fused = sorted(sc, key=lambda c: (-sc[c], pool.index(c)))
                if golds[i] & set(fused[:5]):
                    fused_hits += 1
                cand = fused[:10]
                scores = cross_scores(qs[i], [TXT[row_index[c]] for c in cand])
                order2 = sorted(range(len(cand)), key=lambda j: (-scores[j], j))
                if golds[i] & set(cand[j] for j in order2[:5]):
                    cross_hits += 1
            entry["K%d_fused" % K] = fused_hits
            entry["K%d_cross" % K] = cross_hits
            print(name, "K=%d fused=%d cross=%d" % (K, fused_hits, cross_hits),
                  flush=True)
        result[name] = entry

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"elapsed_s": round(time.time() - t0, 1), "results": result},
                  f, ensure_ascii=False, indent=2)
    print("saved", OUT, "%.0fs" % (time.time() - t0), flush=True)


if __name__ == "__main__":
    main()
