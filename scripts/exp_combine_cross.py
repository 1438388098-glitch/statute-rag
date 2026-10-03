# -*- coding: utf-8 -*-
"""交叉分与融合名次的组合规则扫描（最后一公里）。

动机：交叉编码器目前是「替换」前 N 名的顺序，丢掉了 hybrid+语义的证据；
实测它救盲写（86→89）却伤真实题（31→28）。改为把交叉名次与融合名次
做 RRF 混合，两类证据都保留。

流程（base 交叉模型，缓存打分避免重复计算）：
1. 每题：hybrid 池 ∪ 语义前 50 → 融合排名（多模型 min 名次 + w=2）；
2. 取前 10，算交叉分（缓存到 npz）；
3. 免费评估组合规则：
   - cross_only            纯交叉顺序
   - rrf_fusion_cross      RRF(融合名次, 交叉名次)
   - rrf_fc_w1.5 / w0.5    交叉名次权重扫描
   - fusion_only           纯融合
输出：data/flk/tmp/exp_combine.json
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
OUT = os.path.join(REPO, "data", "flk", "tmp", "exp_combine.json")
CACHE = os.path.join(REPO, "data", "flk", "tmp", "cross_scores_cache.json")
POOL, K_RRF, W, UNION_K, TOP_N = 100, 60, 2.0, 50, 10

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
    cache = {}
    if os.path.exists(CACHE):
        cache = json.load(open(CACHE, encoding="utf-8"))
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

    results = {}
    for name, path in GOLDS:
        gold = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
        qs = [r["query"] for r in gold]
        golds = [set(r.get("gold_ids") or []) for r in gold]
        plans = []
        for i, q in enumerate(qs):
            cits = hyb.recall(q, depth=POOL)
            pool = [c["id"] for c in cits]
            hyr = dict((c["id"], j + 1) for j, c in enumerate(cits))
            e = expand_query(q, syn)
            rank = None
            for v in ([q] if e == q else [q, e]):
                for vecs, mdir in ((vs, SMALL_DIR), (vb, BASE_DIR)):
                    rr = ranks_of(vecs, sr.encode_query(v, mdir))
                    rank = rr if rank is None else np.minimum(rank, rr)
            sem_ids = [corpus[int(j)]["id"]
                       for j in np.argsort(rank, kind="stable").tolist()]
            extra = [cid for cid in sem_ids[:UNION_K] if cid not in hyr]
            pool = pool + extra
            sc = {}
            for cid in pool:
                h = hyr.get(cid)
                s = (1.0 / (K_RRF + h)) if h else 0.0
                sc[cid] = s + W / (K_RRF + rank[row_index[cid]])
            fused = sorted(sc, key=lambda c: (-sc[c], pool.index(c)))
            cand = fused[:TOP_N]
            key = "%s|%d" % (name, i)
            if key in cache:
                cs = cache[key]
            else:
                cs = cross_scores(q, [TXT[row_index[c]] for c in cand])
                cache[key] = cs
            plans.append((cand, cs, golds[i]))
        print(name, "plans ready %.0fs" % (time.time() - t0), flush=True)

        def hit(rule):
            h = 0
            for cand, cs, g in plans:
                if rule == "fusion_only":
                    top = set(cand[:5])
                elif rule == "cross_only":
                    order = sorted(range(len(cand)), key=lambda j: (-cs[j], j))
                    top = set(cand[j] for j in order[:5])
                else:
                    wc = float(rule.split("_w")[1])
                    cross_rank = [0] * len(cand)
                    for pos, j in enumerate(sorted(range(len(cand)),
                                                   key=lambda j: (-cs[j], j))):
                        cross_rank[j] = pos + 1
                    sc2 = [1.0 / (K_RRF + j + 1) + wc / (K_RRF + cross_rank[j])
                           for j in range(len(cand))]
                    order = sorted(range(len(cand)), key=lambda j: (-sc2[j], j))
                    top = set(cand[j] for j in order[:5])
                if g & top:
                    h += 1
            return h

        entry = {"fusion_only": hit("fusion_only"), "cross_only": hit("cross_only")}
        for wc in (0.5, 1.0, 1.5, 2.0, 3.0):
            entry["rrf_w%.1f" % wc] = hit("rrf_w%.1f" % wc)
        results[name] = entry
        print(name, json.dumps(entry), flush=True)

    json.dump(cache, open(CACHE, "w", encoding="utf-8"))
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"elapsed_s": round(time.time() - t0, 1), "results": results},
                  f, ensure_ascii=False, indent=2)
    print("saved", OUT, "%.0fs" % (time.time() - t0), flush=True)


if __name__ == "__main__":
    main()
