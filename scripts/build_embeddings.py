# -*- coding: utf-8 -*-
"""离线构建条文向量文件（语义重排的数据依赖，一次性后台任务）。

对语料每行（法名 + 条号 + 正文，法名重复一次提条文头权重）做 bge 编码
（CLS 池化 + 归一化），落盘 float16 npz：ids + vecs。运行时
statute_rag/semantic_rerank.py 只做查表点积，不需要本脚本重跑。

需 py -3.13 + numpy/torch/transformers（仅离线构建用，运行时查表不需要
torch，但查询编码需要）。用法：
    py -3.13 scripts/build_embeddings.py \
        --corpus data/corpus_v6.jsonl \
        --model-dir data/flk/models/bge-small-zh-v1.5 \
        --out data/corpus_v6.emb.npz
"""
from __future__ import print_function

import argparse
import json
import os
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--model-dir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--batch", type=int, default=128)
    ap.add_argument("--begin", type=int, default=0,
                    help="起止行号（分片并发用，左闭右开）")
    ap.add_argument("--end", type=int, default=None)
    ap.add_argument("--threads", type=int, default=0,
                    help="torch 线程数（0 = 默认全核）")
    args = ap.parse_args()

    import numpy as np
    import torch
    from transformers import AutoModel, AutoTokenizer

    if args.threads:
        torch.set_num_threads(args.threads)

    rows = []
    with open(args.corpus, encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                rows.append(json.loads(line))
    end = args.end if args.end is not None else len(rows)
    rows = rows[args.begin:end]
    print("corpus rows[%d:%d] = %d" % (args.begin, end, len(rows)),
          flush=True)

    tok = AutoTokenizer.from_pretrained(args.model_dir)
    model = AutoModel.from_pretrained(args.model_dir)
    model.eval()

    texts = []
    for row in rows:
        head = u"%s %s" % (row["law"], row["num"])
        texts.append(u"%s %s %s" % (head, head, row["text"]))

    vecs = np.zeros((len(rows), model.config.hidden_size), dtype=np.float32)
    t0 = time.time()
    with torch.no_grad():
        for s in range(0, len(texts), args.batch):
            enc = tok(texts[s:s + args.batch], padding=True, truncation=True,
                      max_length=512, return_tensors="pt")
            h = model(**enc).last_hidden_state[:, 0]
            vecs[s:s + args.batch] = torch.nn.functional.normalize(
                h, p=2, dim=1).numpy()
            if (s // args.batch) % 20 == 0:
                rate = (s + args.batch) / max(time.time() - t0, 1e-6)
                print("  %d / %d (%.0f rows/s)" % (s, len(texts), rate),
                      flush=True)

    ids = np.array([row["id"] for row in rows], dtype=np.int64)
    np.savez(args.out, ids=ids, vecs=vecs.astype(np.float16))
    print("saved %s (%.1f MB, %.1fs)" % (
        args.out, os.path.getsize(args.out) / 1048576.0, time.time() - t0),
        flush=True)


if __name__ == "__main__":
    main()
