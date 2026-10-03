# -*- coding: utf-8 -*-
"""语义融合置信门 + 查询扩展编码实验。

背景（tune_semantic.json）：加权 RRF（w≈2）把真实 38 拉到 26/38、盲写 100
拉到 83/100，但合成 177 掉到 118——语义通道会把「条文短语查询」的成功
排名搅乱，需要只在语义融合有增益的查询形态上启用。

两个门的假设：
- gateA  LIKE 通道对查询无精确子串命中 → 启用语义融合。合成题按构造是
  条文短语的精确子串（LIKE 必中），真实/盲写题是自然问句（LIKE 几乎不中）。
- gateB  gateA 且查询长度 ≤12 字（合成短语短，真实问句长，双保险）。

另测查询表示变体：扩展查询（同义词典）编码后与原查询编码取语义名次并
集/max，看能否再抬几题。

输出：data/flk/tmp/exp_gate.json。
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
OUT = os.path.join(REPO, "data", "flk", "tmp", "exp_gate.json")

GOLD_FILES = [
    ("real38", os.path.join(REPO, "gold", "gold_real_38_v6.jsonl")),
    ("blind100", os.path.join(REPO, "gold", "gold_external_v6.jsonl")),
    ("synth177", os.path.join(REPO, "gold", "gold_synth_v6_seed20260918.jsonl")),
]

BGE_QUERY_PREFIX = u"为这个句子生成表示以用于检索相关文章："
POOL = 100
W_SEM = 2.0
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


def rrf_top5(pool_ids, hyb_ranks, sem_rank_rows, gate_on, row_index,
             w_sem=W_SEM):
    """池内 hybrid 名次 + 语义全库名次；gate_on[i]=False 时该题不融合（纯 hybrid）。

    sem_rank_rows[i]: 语料行号 -> 语义名次（None = 不用语义）。
    """
    hits = []
    for i, ids in enumerate(pool_ids):
        scores = {}
        for cid in ids:
            s = 1.0 / (K_RRF + hyb_ranks[i][cid])
            if gate_on[i] and sem_rank_rows is not None:
                s += w_sem / (K_RRF + sem_rank_rows[i][row_index[cid]])
            scores[cid] = s
        top = sorted(scores, key=lambda c: (-scores[c], ids.index(c)))[:5]
        hits.append(top)
    return hits


def hits_at_5(tops, golds):
    return sum(1 for top, gold in zip(tops, golds) if gold & set(top))


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
            like = [c["id"] for c in hyb.like.search(q, k=3)]
            like_hits.append(bool(like))

        qvec = encode_queries(qs, MODEL_DIR)
        # 扩展查询编码（与原查询相同的才跳过编码，省时间）
        expanded = [expand_query(q, syn) for q in qs]
        changed = [i for i, e in enumerate(expanded) if e != qs[i]]
        qvec_exp = qvec.copy()
        if changed:
            qvec_exp[changed] = encode_queries([expanded[i] for i in changed],
                                               MODEL_DIR)

        # 全库语义名次（每题一次 25k 维 argsort）
        sem_rank_rows = []
        for i in range(len(qs)):
            sims = doc_mat @ qvec[i]
            rank = np.empty(len(sims), dtype=np.int64)
            rank[np.argsort(-sims, kind="stable")] = np.arange(1, len(sims) + 1)
            sem_rank_rows.append(rank)
        sem_rank_rows_exp = []
        for i in range(len(qs)):
            sims = doc_mat @ qvec_exp[i]
            rank = np.empty(len(sims), dtype=np.int64)
            rank[np.argsort(-sims, kind="stable")] = np.arange(1, len(sims) + 1)
            sem_rank_rows_exp.append(rank)
        # 语义名次取两路更优（名次小者）
        sem_rank_rows_best = [np.minimum(sem_rank_rows[i], sem_rank_rows_exp[i])
                         for i in range(len(qs))]

        gateA = [not h for h in like_hits]
        gateB = [(not h) and len(q) <= 12 for h, q in zip(like_hits, qs)]

        entry = {
            "hyb": hits_at_5(rrf_top5(pool_ids, hyb_ranks, None,
                                      [False] * len(qs), row_index), golds),
            "fusion_w2_nogate": hits_at_5(
                rrf_top5(pool_ids, hyb_ranks, sem_rank_rows,
                         [True] * len(qs), row_index), golds),
            "fusion_w2_gateA": hits_at_5(
                rrf_top5(pool_ids, hyb_ranks, sem_rank_rows, gateA,
                                       row_index), golds),
            "fusion_w2_gateB": hits_at_5(
                rrf_top5(pool_ids, hyb_ranks, sem_rank_rows, gateB,
                                       row_index), golds),
            "sem_exp_only": hits_at_5(
                rrf_top5(pool_ids, hyb_ranks, sem_rank_rows_exp,
                         [True] * len(qs), row_index,
                                      w_sem=1000.0), golds),
            "fusion_w2_sem_best": hits_at_5(
                rrf_top5(pool_ids, hyb_ranks, sem_rank_rows_best,
                         [True] * len(qs), row_index), golds),
            "like_hit_rate": sum(like_hits) / float(len(qs)),
            "gateA_rate": sum(gateA) / float(len(qs)),
            "gateB_rate": sum(gateB) / float(len(qs)),
        }
        results[name] = entry
        print(name, json.dumps(entry, ensure_ascii=False), flush=True)

    out = {"elapsed_s": round(time.time() - t0, 1), "results": results}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("saved", OUT, flush=True)


if __name__ == "__main__":
    main()
