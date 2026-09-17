# -*- coding: utf-8 -*-
"""30 秒演示 CLI：问题/关键词 → 条文原文 + 出处（法名 + 条号）。

用法：
  python scripts/search_cli.py --db <legal.db> "正当防卫" [--k 3]
  python scripts/search_cli.py --corpus data/corpus.jsonl "承诺生效" [--retriever bm25]
"""
import argparse
import io
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from statute_rag.importer import import_articles, load_corpus
from statute_rag.retrieval import BM25Retriever, HybridRetriever, LikeRetriever

RETRIEVERS = {"like": LikeRetriever, "bm25": BM25Retriever, "hybrid": HybridRetriever}

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description="statute-rag 检索演示")
    parser.add_argument("query", help="查询词（关键词或短句）")
    parser.add_argument("--db", help="legal-wisdom legal.db 路径")
    parser.add_argument("--corpus", help="已有语料 JSONL")
    parser.add_argument("--retriever", default="hybrid", choices=sorted(RETRIEVERS))
    parser.add_argument("--k", type=int, default=3)
    args = parser.parse_args()

    if args.db:
        corpus_path = os.path.join("data", "corpus.jsonl")
        print("导入语料（首次较慢）…")
        import_articles(args.db, corpus_path)
        corpus = load_corpus(corpus_path)
    elif args.corpus:
        corpus = load_corpus(args.corpus)
    else:
        parser.error("需要 --db 或 --corpus")
        return

    retriever = RETRIEVERS[args.retriever](corpus)
    results = retriever.search(args.query, k=args.k)
    if not results:
        print("（无命中——本检索器为词法检索，请换关键词重试；语义检索在路线图中）")
        return
    print("查询：%s ｜ 检索器：%s" % (args.query, args.retriever))
    print("=" * 60)
    for i, cite in enumerate(results, 1):
        print("[%d] %s %s（id=%d，score=%.4f）" % (i, cite["law"], cite["num"], cite["id"], cite["score"]))
        print("    " + cite["text"][:120] + ("…" if len(cite["text"]) > 120 else ""))
        print("-" * 60)


if __name__ == "__main__":
    main()
