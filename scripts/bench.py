# -*- coding: utf-8 -*-
"""延迟基准：索引构建耗时 + 单查询延迟（p50/p95）。

口径声明：本机数字只用于相对比较与回归观察（如倒排重构前后对比），
不代表固定配置的绝对性能；不同机器/语料规模差异大，不在 CI 断言具体数值。

用法：
  python scripts/bench.py                                  # demo 语料
  python scripts/bench.py --corpus data/corpus.jsonl --queries 50
"""
import argparse
import io
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from statute_rag.importer import load_corpus
from statute_rag.retrieval import BM25Retriever, HybridRetriever, LikeRetriever

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

RETRIEVERS = [
    ("like", LikeRetriever),
    ("bm25", BM25Retriever),
    ("hybrid", HybridRetriever),
]


def _percentile(sorted_values, pct):
    if not sorted_values:
        return 0.0
    idx = min(len(sorted_values) - 1, int(round(pct / 100.0 * len(sorted_values) + 0.5)) - 1)
    return sorted_values[max(0, idx)]


def _load_queries(args, corpus):
    """查询集：优先金标文件的真实 query 字段；缺省取语料条文前缀模拟关键词查询。"""
    queries = []
    if args.gold and os.path.exists(args.gold):
        with io.open(args.gold, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    queries.append(line.split('"query":')[1].split('"')[1])
    if not queries:
        queries = [item["text"][:12] for item in corpus]
    return queries[:args.queries]


def main():
    parser = argparse.ArgumentParser(description="statute-rag 延迟基准")
    parser.add_argument("--corpus", default="demo_corpus/corpus.jsonl",
                        help="语料 JSONL（缺省 demo 语料；真实语料不入库）")
    parser.add_argument("--gold", default="demo_corpus/gold.jsonl",
                        help="金标 JSONL（用其 query 字段作查询集；找不到则取条文前缀）")
    parser.add_argument("--queries", type=int, default=30, help="计时查询条数")
    parser.add_argument("--k", type=int, default=5)
    args = parser.parse_args()

    corpus = load_corpus(args.corpus)
    queries = _load_queries(args, corpus)
    print("语料 %d 条，计时查询 %d 条，k=%d\n" % (len(corpus), len(queries), args.k))

    header = "| 检索器 | 索引构建 (s) | p50 (ms) | p95 (ms) |"
    sep = "|---|---|---|---|"
    print(header)
    print(sep)
    for name, Ret in RETRIEVERS:
        t0 = time.perf_counter()
        retriever = Ret(corpus)
        build_s = time.perf_counter() - t0
        latencies = []
        for q in queries:
            t0 = time.perf_counter()
            retriever.search(q, k=args.k)
            latencies.append((time.perf_counter() - t0) * 1000.0)
        latencies.sort()
        print("| %s | %.2f | %.1f | %.1f |" % (
            name, build_s,
            _percentile(latencies, 50), _percentile(latencies, 95)))
    print("")
    print("（本机相对口径，仅用于前后对比观察；不含绝对性能结论）")


if __name__ == "__main__":
    main()
