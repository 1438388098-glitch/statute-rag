# -*- coding: utf-8 -*-
"""检索改进消融：逐机制前后对比（真实金标 + 合成金标双口径）。

用法：py -3.13 scripts/ablate_retrieval.py --corpus data/corpus.jsonl \
        --gold-real gold/gold_real_38.jsonl --gold-synth gold/gold_synth_seed20260918.jsonl

消融维度（均为通用机制参数，非按题调参）：
  A0 基线（use_expansion=False）—— 与改进前完全一致；
  A1 +同义扩展路（hybrid 默认配置：原查询 BM25 + 扩展查询 BM25 + LIKE）。
  其余曾试机制（替换式变体多路、法名先验加权、trigram 短语加权、通道
  深度扫描）因无 Recall 增量未纳入最终配置，负结果见 docs/retrieval-improvement.md。
"""
import argparse
import io
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from statute_rag.eval_harness import evaluate
from statute_rag.retrieval import BM25Retriever, HybridRetriever, LikeRetriever


def load_jsonl(path):
    out = []
    with io.open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def main():
    parser = argparse.ArgumentParser(description="检索改进消融测量")
    parser.add_argument("--corpus", default="data/corpus.jsonl")
    parser.add_argument("--gold-real", default="gold/gold_real_38.jsonl")
    parser.add_argument("--gold-synth", default="gold/gold_synth_seed20260918.jsonl")
    parser.add_argument("--k", type=int, default=5)
    args = parser.parse_args()

    corpus = load_jsonl(args.corpus)
    golds = [("真实", load_jsonl(args.gold_real)), ("合成", load_jsonl(args.gold_synth))]

    configs = [
        ("A0 基线(exp=off)", dict(use_expansion=False)),
        ("A1 +同义扩展路(exp=on)", dict(use_expansion=True)),
    ]

    print("语料 %d 条；k=%d" % (len(corpus), args.k))
    # 单检索器口径（like / bm25 恒为纯实现，不随 hybrid 配置变化）
    for name, Ret in [("like", LikeRetriever), ("bm25", BM25Retriever)]:
        ret = Ret(corpus)
        row = ["%-26s" % ("单路 " + name)]
        for tag, gold in golds:
            m = evaluate(ret, gold, k=args.k)
            row.append("%s R@%d=%.1f%% MRR=%.3f" % (tag, args.k, m["recall_at_k"] * 100, m["mrr"]))
        print(" ".join(row))

    for label, kwargs in configs:
        ret = HybridRetriever(corpus, **kwargs)
        row = ["%-26s" % label]
        for tag, gold in golds:
            m = evaluate(ret, gold, k=args.k)
            row.append("%s R@%d=%.1f%% MRR=%.3f" % (tag, args.k, m["recall_at_k"] * 100, m["mrr"]))
        print(" ".join(row))


if __name__ == "__main__":
    main()
