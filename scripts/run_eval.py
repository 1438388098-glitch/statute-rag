# -*- coding: utf-8 -*-
"""评测 CLI：构建/加载语料 → 生成或加载金标 → 评测三件套 → 输出对比表。

用法：
  python scripts/run_eval.py --db <legal.db> --out-dir data          # 从源库导入并构建
  python scripts/run_eval.py --corpus data/corpus.jsonl --gold gold/gold_synth_200.jsonl
"""
import argparse
import io
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from statute_rag.eval_harness import evaluate, format_report
from statute_rag.gold import DEFAULT_SEED, DEFAULT_SIZE, load_gold, make_gold, save_gold
from statute_rag.importer import import_articles, load_corpus
from statute_rag.retrieval import BM25Retriever, HybridRetriever, LikeRetriever

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description="statute-rag 评测")
    parser.add_argument("--db", help="legal-wisdom legal.db 路径（导入语料）")
    parser.add_argument("--corpus", help="已有语料 JSONL（与 --db 二选一）")
    parser.add_argument("--gold", help="已有金标 JSONL（缺省则生成合成金标）")
    parser.add_argument("--out-dir", default="data", help="语料/金标输出目录")
    parser.add_argument("--gold-size", type=int, default=DEFAULT_SIZE)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--gold-desc", default="合成金标，种子固定可复现",
                        help="报告页脚的金标口径说明（如真实问句金标）")
    args = parser.parse_args()

    if args.db:
        corpus_path = os.path.join(args.out_dir, "corpus.jsonl")
        stats = import_articles(args.db, corpus_path)
        print("导入统计：", stats)
        corpus = load_corpus(corpus_path)
    elif args.corpus:
        corpus = load_corpus(args.corpus)
    else:
        parser.error("需要 --db 或 --corpus")
        return
    print("语料条文数：", len(corpus))

    if args.gold:
        gold = load_gold(args.gold)
        print("加载金标：", args.gold)
    else:
        gold_path = os.path.join(args.out_dir, "gold_synth_%d.json" % args.gold_size) \
            if args.out_dir != "gold" else "gold_synth_%d.json" % args.gold_size
        gold = make_gold(corpus, size=args.gold_size, seed=args.seed)
        save_gold(gold, gold_path)
        print("合成金标已生成（seed=%d）：" % args.seed)
        print("   ", gold_path)

    results = []
    for name, Ret in [("like（子串基线）", LikeRetriever),
                      ("bm25（字符二元组）", BM25Retriever),
                      ("hybrid（RRF 融合）", HybridRetriever)]:
        m = evaluate(Ret(corpus), gold, k=args.k)
        results.append((name, m))
        print("  %s: Recall@%d=%.1f%% MRR=%.3f" % (name, args.k, m["recall_at_k"] * 100, m["mrr"]))

    report = format_report(results, len(corpus), gold_desc=args.gold_desc)
    print("")
    print(report)
    report_path = os.path.join(args.out_dir, "eval_report.md")
    with io.open(report_path, "w", encoding="utf-8") as f:
        f.write("# 评测报告\n\n```\n%s\n```\n" % report)
    print("报告已写入：", report_path)


if __name__ == "__main__":
    main()
