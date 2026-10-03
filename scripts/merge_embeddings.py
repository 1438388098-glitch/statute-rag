# -*- coding: utf-8 -*-
"""合并分片编码产物：emb_shard{0,1,2}.npz -> ctx_doc_cache_base.npz。

分片按语料行号连续切（--begin/--end），合并即按序拼接 ids 与 vecs，
行序与 corpus_v6.jsonl 一致（语义重排按行序查表的前提）。
"""
from __future__ import print_function

import os

import numpy as np

TMP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "data", "flk", "tmp")


def main():
    parts = []
    for i in range(3):
        path = os.path.join(TMP, "emb_shard%d.npz" % i)
        blob = np.load(path)
        parts.append((blob["ids"], blob["vecs"]))
        print("shard%d: %d rows" % (i, len(blob["ids"])), flush=True)
    ids = np.concatenate([p[0] for p in parts])
    vecs = np.concatenate([p[1] for p in parts], axis=0)
    out = os.path.join(TMP, "ctx_doc_cache_base.npz")
    np.savez(out, ids=ids, vecs=vecs)
    print("saved %s (%d rows, %.1f MB)" % (
        out, len(ids), os.path.getsize(out) / 1048576.0), flush=True)


if __name__ == "__main__":
    main()
