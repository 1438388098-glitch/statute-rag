# -*- coding: utf-8 -*-
"""余弦原始分融合实验：hybrid 名次分 + α·余弦（取代名次分）。

假设：名次融合（RRF）只保留顺序，丢掉了语义通道的置信度幅度——语义第 1
名与第 13 名的名次分差被 k=60 抹平，而余弦差保留了「语义有多确定」。
gateA（LIKE 精确命中→不融合）沿用。

变体：
- cosA   score = 1/(K+hyb) + α·cos_best   （cos_best = 两路查询编码余弦取大）
- mixAB  score = 1/(K+hyb) + α·cos_best + β/(K+sem_rank)

输出：data/flk/tmp/exp_cosfusion.json。
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
CTX_CACHE = os.path.join(REPO, "data", "flk", "tmp", "ctx_doc_cache.npz")
MODEL_DIR = os.path.join(REPO, "data", "flk", "models", "bge-small-zh-v1.5")
OUT = os.path.join(REPO, "data", "flk", "tmp", "exp_cosfusion.json")

GOLD_FILES = [
    ("real38", os.path.join(REPO, "gold", "gold_real_38_v6.jsonl")),
    ("blind100", os.path.join(REPO, "gold", "gold_external_v6.jsonl")),
    ("synth177", os.path.join(REPO, "gold", "gold_synth_v6_seed20260918.jsonl")),
]

BGE_QUERY_PREFIX = u"为这个句子生成表示以用于检索相关文章："
POOL = 100
K_RRF = 60


def load_jsonl(path):
    return [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]


def gold_by_set(corpus):
    by_law_num = {}
    for row in corpus:
        by_law_num.setdefault((row["law"], row["num"]), []).append(row["id"])
    sets = []
    for name, path in GOLD_FILES:
        pairs = []
        for r in load_jsonl(path):
            if "gold_ids" in r:
                gold = set(r["gold_ids"])
            else:
                gold = set(by_law_num.get((r["law"], r["num"]), []))
            pairs.append((r, gold))
        sets.append((name, pairs))
    return sets


def encode_queries(queries, model_dir):
    import torch
    from transformers import AutoModel, AutoTokenizer
    tok = AutoTokenizer.from_pretrained(model_dir)
    model = AutoModel.from_pretrained(model_dir)
    model.eval()
    out = np.zeros((len(queries), model.config.hidden_size), dtype=np.float32)
    with torch.no_grad():
        enc = tok([BGE_QUERY_PREFIX + q for q in queries], padding=True,
                  truncation=True, max_length=512, return_tensors="pt")
        h = model(**enc).last_hidden_state[:, 0]
        out = torch.nn.functional.normalize(h, p=2, dim=1).numpy()
    return out


def main():
    t0 = time.time()
    corpus = load_jsonl(CORPUS)
    row_index = dict((r["id"], i) for i, r in enumerate(corpus))
    doc_mat = np.load(CTX_CACHE)["vecs"].astype(np.float32)

    from statute_rag.importer import load_corpus
    from statute_rag.retrieval import HybridRetriever
    from statute_rag.query_expansion import expand_query, load_synonyms
    syn = load_synonyms()
    hyb = HybridRetriever(load_corpus(CORPUS))

    gold_sets = gold_by_set(corpus)
    results = {}
    for name, pairs in gold_sets:
        qs = [r["query"] for r, _ in pairs]
        golds = [g for _, g in pairs]

        pool_ids, hyb_ranks, like_hits = [], [], []
        for q in qs:
            cits = hyb.recall(q, depth=POOL)
            pool_ids.append([c["id"] for c in cits])
            hyb_ranks.append(dict((c["id"], i + 1) for i, c in enumerate(cits)))
            like_hits.append(bool(hyb.like.search(q, k=1)))

        expanded = [expand_query(q, syn) for q in qs]
        changed = [i for i, e in enumerate(expanded) if e != qs[i]]
        qv = encode_queries(qs, MODEL_DIR)
        qve = qv.copy()
        if changed:
            qve[changed] = encode_queries([expanded[i] for i in changed],
                                          MODEL_DIR)

        cos_best = []
        for i in range(len(qs)):
            s1 = doc_mat @ qv[i]
            s2 = doc_mat @ qve[i]
            cos_best.append(np.maximum(s1, s2))
        sem_rank_rows = []
        for i in range(len(qs)):
            rank = np.empty(len(cos_best[i]), dtype=np.int64)
            rank[np.argsort(-cos_best[i], kind="stable")] = np.arange(
                1, len(cos_best[i]) + 1)
            sem_rank_rows.append(rank)

        def hits5(alpha, beta):
            h = 0
            for i in range(len(qs)):
                if like_hits[i]:
                    top = set(pool_ids[i][:5])
                else:
                    sc = {}
                    for cid in pool_ids[i]:
                        s = (1.0 / (K_RRF + hyb_ranks[i][cid])
                             + alpha * float(cos_best[i][row_index[cid]]))
                        if beta:
                            s += beta / (K_RRF
                                         + sem_rank_rows[i][row_index[cid]])
                        sc[cid] = s
                    top = set(sorted(sc, key=lambda c: (-sc[c],
                                      pool_ids[i].index(c)))[:5])
                if golds[i] & top:
                    h += 1
            return h

        entry = {"hyb": hits5(0.0, 0.0)}
        for a in (0.02, 0.05, 0.1, 0.2, 0.3, 0.5):
            entry["cos%.2f" % a] = hits5(a, 0.0)
        for a, b in ((0.05, 2.0), (0.1, 2.0), (0.1, 1.0), (0.2, 1.0)):
            entry["cos%.2f_w%d" % (a, int(b))] = hits5(a, b)
        results[name] = entry
        print(name, json.dumps(entry), flush=True)

    out = {"elapsed_s": round(time.time() - t0, 1), "results": results}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("saved", OUT, flush=True)


if __name__ == "__main__":
    main()
