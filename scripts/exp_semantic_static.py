# -*- coding: utf-8 -*-
"""语义重排离线实验：静态词向量表 vs 全模型编码（只做实验，不进运行时）。

目的：在写运行时代码之前，先验证「静态词向量表」（BERT word_embeddings
查表取均值，运行时零第三方依赖可实现）能否吃下「检到了但排不进前 5」的
分数；全模型编码（transformer 前向）作为上界参照，运行时不可行，仅用
来判断静态表损失了多少。

三种打分模式：
- static      查询向量 = 查询字/词 token 的词向量加权均值；条文向量同法（同表同权）
- contextual  查询与条文都过完整 bge 编码（CLS 池化 + 归一化）——上界参照
- mismatch    查询用静态表、条文用全模型编码——验证两种向量空间能否混用

评测口径与 eval_harness 一致：金标任一 gold_id 进 top-k 即命中。
融合实验：hybrid 深召回池（depth=100）内，语义分与 RRF 名次分再融合。

用法（py -3.13，需 numpy/torch/transformers，仅离线）：
    py -3.13 scripts/exp_semantic_static.py --mode static
    py -3.13 scripts/exp_semantic_static.py --mode contextual   # 慢，后台跑
    py -3.13 scripts/exp_semantic_static.py --mode mismatch
输出：data/flk/tmp/sem_exp_<mode>.json + 控制台摘要。
"""
from __future__ import print_function

import argparse
import json
import os
import sys
import time
import unicodedata

import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

CORPUS = os.path.join(REPO, "data", "corpus_v6.jsonl")
MODEL_DIR = os.path.join(REPO, "data", "flk", "models", "bge-small-zh-v1.5")
OUT_DIR = os.path.join(REPO, "data", "flk", "tmp")

GOLD_FILES = [
    ("real38", os.path.join(REPO, "gold", "gold_real_38_v6.jsonl")),
    ("blind100", os.path.join(REPO, "gold", "gold_external_v6.jsonl")),
    ("synth177", os.path.join(REPO, "gold", "gold_synth_v6_seed20260918.jsonl")),
]

BGE_QUERY_PREFIX = u"为这个句子生成表示以用于检索相关文章："


def is_cjk(ch):
    o = ord(ch)
    return (0x4E00 <= o <= 0x9FFF) or (0x3400 <= o <= 0x4DBF) or (0xF900 <= o <= 0xFAFF)


def char_tokens(text):
    """BERT BasicTokenizer 的中文近似：小写化、CJK 逐字、ASCII 词成段、其余当分隔。"""
    text = unicodedata.normalize("NFKC", text).lower()
    out, buf = [], []

    def flush():
        if buf:
            w = u"".join(buf)
            out.append(w)
            del buf[:]

    for ch in text:
        if is_cjk(ch):
            flush()
            out.append(ch)
        elif ch.isascii() and (ch.isalnum()):
            buf.append(ch)
        else:
            flush()
    flush()
    return out


def load_jsonl(path):
    return [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]


def gold_by_set(corpus):
    """合成金标只带 (law, num)，用语料反查所有对应行 id（与 gold_ids 口径一致）。"""
    by_law_num = {}
    for row in corpus:
        by_law_num.setdefault((row["law"], row["num"]), []).append(row["id"])
    sets = []
    for name, path in GOLD_FILES:
        rows = load_jsonl(path)
        pairs = []
        for r in rows:
            if "gold_ids" in r:
                gold = set(r["gold_ids"])
            else:
                gold = set(by_law_num.get((r["law"], r["num"]), []))
            pairs.append((r["query"], gold))
        sets.append((name, pairs))
    return sets


def doc_token_lists(corpus):
    """每条条文的 token 串：条文头（法名+条号）+ 正文，法名重复一次提权重。"""
    out = []
    for row in corpus:
        head = u"%s %s" % (row["law"], row["num"])
        toks = char_tokens(head) + char_tokens(head) + char_tokens(row["text"])
        out.append(toks)
    return out


def build_idf(doc_toks):
    df = {}
    for toks in doc_toks:
        for t in set(toks):
            df[t] = df.get(t, 0) + 1
    n = len(doc_toks)
    idf = {t: float(np.log(n / c)) for t, c in df.items()}
    return idf


def static_matrix(token_lists, vocab, emb, idf, use_idf):
    """每份 token 列表 -> 归一化加权均值向量。查表缺失的 token 跳过。"""
    mat = np.zeros((len(token_lists), emb.shape[1]), dtype=np.float32)
    for i, toks in enumerate(token_lists):
        acc = np.zeros(emb.shape[1], dtype=np.float32)
        wsum = 0.0
        for t in toks:
            vi = vocab.get(t)
            if vi is None:
                continue
            w = idf.get(t, 1.0) if use_idf else 1.0
            acc += w * emb[vi]
            wsum += w
        if wsum > 0:
            acc /= wsum
        norm = float(np.linalg.norm(acc))
        if norm > 0:
            acc /= norm
        mat[i] = acc
    return mat


CTX_CACHE = os.path.join(OUT_DIR, "ctx_doc_cache.npz")


def doc_embed_texts(corpus):
    """条文编码文本：法名+条号头部重复一次再接正文（与 build_embeddings.py 一致）。"""
    return [u"%s %s %s %s" % (r["law"], r["num"], r["law"], r["num"]) + u" " + r["text"]
            for r in corpus]


def contextual_matrix(texts, batch=128):
    import torch
    from transformers import AutoModel, AutoTokenizer

    tok = AutoTokenizer.from_pretrained(MODEL_DIR)
    model = AutoModel.from_pretrained(MODEL_DIR)
    model.eval()
    out = np.zeros((len(texts), model.config.hidden_size), dtype=np.float32)
    with torch.no_grad():
        for s in range(0, len(texts), batch):
            chunk = texts[s:s + batch]
            enc = tok(chunk, padding=True, truncation=True, max_length=512,
                      return_tensors="pt")
            h = model(**enc).last_hidden_state[:, 0]
            h = torch.nn.functional.normalize(h, p=2, dim=1)
            out[s:s + batch] = h.numpy()
            if (s // batch) % 20 == 0:
                print("  encoded %d / %d" % (s, len(texts)), flush=True)
    return out


def contextual_doc_matrix_cached(corpus, batch=128):
    """条文全模型编码，带 npz 缓存（20 分钟一次，后续调参直接查表）。"""
    if os.path.exists(CTX_CACHE):
        blob = np.load(CTX_CACHE)
        if len(blob["ids"]) == len(corpus):
            print("ctx cache hit: %s" % CTX_CACHE, flush=True)
            return blob["vecs"].astype(np.float32)
    vecs = contextual_matrix(doc_embed_texts(corpus), batch=batch)
    np.savez(CTX_CACHE, ids=np.array([r["id"] for r in corpus], dtype=np.int64),
             vecs=vecs.astype(np.float16))
    print("ctx cache saved: %s" % CTX_CACHE, flush=True)
    return vecs


def recall_at_k(rank_ids_sets, gold_sets, k):
    hits = 0
    for ids, gold in zip(rank_ids_sets, gold_sets):
        if gold & set(ids[:k]):
            hits += 1
    return hits, len(gold_sets)


def rrf_fuse(hyb_ranks, sem_sims, pool_ids, k_rrf=60):
    """hybrid 名次分 + 语义余弦分融合：语义分先在池内转名次再 RRF。"""
    n = len(pool_ids)
    sem_rank = np.empty(n, dtype=np.int64)
    sem_rank[np.argsort(-sem_sims, kind="stable")] = np.arange(1, n + 1)
    fused = np.empty(n, dtype=np.float64)
    for i in range(n):
        hr = hyb_ranks[i]  # 1-based，池外无值
        fused[i] = 1.0 / (k_rrf + hr) + 1.0 / (k_rrf + sem_rank[i])
    return fused


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["static", "contextual", "mismatch"], default="static")
    ap.add_argument("--no-idf", action="store_true")
    args = ap.parse_args()
    use_idf = not args.no_idf
    t0 = time.time()

    corpus = load_jsonl(CORPUS)
    corpus_index = {row["id"]: i for i, row in enumerate(corpus)}
    doc_toks = doc_token_lists(corpus)
    print("corpus %d rows, tok+head %.1fs" % (len(corpus), time.time() - t0), flush=True)

    gold_sets = gold_by_set(corpus)

    sem_doc = None
    ctx_doc = None
    vocab = None
    emb = None
    idf = None

    if args.mode in ("static", "mismatch"):
        from transformers import AutoModel, AutoTokenizer
        tok = AutoTokenizer.from_pretrained(MODEL_DIR)
        vocab = tok.vocab
        m = AutoModel.from_pretrained(MODEL_DIR)
        emb = m.embeddings.word_embeddings.weight.detach().numpy().astype(np.float32)
        del m
        idf = build_idf(doc_toks)
        sem_doc = static_matrix(doc_toks, vocab, emb, idf, use_idf)
        print("static doc matrix done %.1fs" % (time.time() - t0), flush=True)
    if args.mode in ("contextual", "mismatch"):
        ctx_doc = contextual_doc_matrix_cached(corpus)
        print("contextual doc matrix ready %.1fs" % (time.time() - t0), flush=True)

    # hybrid 深召回池（纯 Python 组件，py3.13 可直接 import）
    from statute_rag.importer import load_corpus as load_hybrid
    from statute_rag.retrieval import HybridRetriever
    hyb_corpus = load_hybrid(CORPUS)
    hyb = HybridRetriever(hyb_corpus)
    print("hybrid ready %.1fs" % (time.time() - t0), flush=True)

    POOL = 100
    results = {}
    for name, queries in gold_sets:
        qs = [q for q, _ in queries]
        golds = [g for _, g in queries]

        # hybrid 池：id 列表 + 池内名次
        pool_ids, hyb_ranks = [], []
        for q in qs:
            cits = hyb.recall(q, depth=POOL)
            ids = [c["id"] for c in cits]
            pool_ids.append(ids)
            hyb_ranks.append({cid: i + 1 for i, cid in enumerate(ids)})
        pool_hit = recall_at_k(pool_ids, golds, POOL)[0]

        # 查询向量
        if args.mode == "static":
            q_toks = [char_tokens(q) for q in qs]
            sem_q = static_matrix(q_toks, vocab, emb, idf, use_idf)
            doc_mat = sem_doc
        elif args.mode == "mismatch":
            q_toks = [char_tokens(BGE_QUERY_PREFIX + q) for q in qs]
            sem_q = static_matrix(q_toks, vocab, emb, idf, use_idf)
            doc_mat = ctx_doc
        else:
            enc_texts = [BGE_QUERY_PREFIX + q for q in qs]
            sem_q = contextual_matrix(enc_texts)
            doc_mat = ctx_doc

        # 池内语义分（余弦；静态向量已归一，contextual 也归一）
        sem_only_ids, fused_ids, raw_ids = [], [], []
        for i, q in enumerate(qs):
            idx = [corpus_index[cid] for cid in pool_ids[i]]
            sims = doc_mat[idx] @ sem_q[i]
            order = np.argsort(-sims, kind="stable")
            sem_only_ids.append([pool_ids[i][j] for j in order[:30]])
            raw_ids.append([pool_ids[i][j] for j in order])

            hyb_r = [hyb_ranks[i][cid] for cid in pool_ids[i]]
            fused = rrf_fuse(hyb_r, sims, pool_ids[i])
            forder = np.argsort(-fused, kind="stable")
            fused_ids.append([pool_ids[i][j] for j in forder[:30]])

        entry = {"pool_hit_at_%d" % POOL: pool_hit}
        for k in (5, 10):
            entry["hybrid_pool_R@%d" % k] = recall_at_k(pool_ids, golds, k)
            entry["sem_only_R@%d" % k] = recall_at_k(sem_only_ids, golds, k)
            entry["fused_rrf_R@%d" % k] = recall_at_k(fused_ids, golds, k)
        results[name] = entry
        print(name, json.dumps(entry, ensure_ascii=False), flush=True)

    out = {"mode": args.mode, "idf": use_idf, "pool": POOL,
           "elapsed_s": round(time.time() - t0, 1), "results": results}
    out_path = os.path.join(OUT_DIR, "sem_exp_%s.json" % args.mode)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print("saved", out_path, flush=True)


if __name__ == "__main__":
    main()
