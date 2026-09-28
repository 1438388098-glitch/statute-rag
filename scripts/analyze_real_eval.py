# -*- coding: utf-8 -*-
"""逐题分析真实问句金标的命中情况，输出报告用的明细表（markdown）。

用法：py -3.13 scripts/analyze_real_eval.py --corpus data/corpus.jsonl --gold data/gold_real_38.json
"""
import argparse
import io
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from statute_rag.retrieval import BM25Retriever, LikeRetriever, HybridRetriever

RETRIEVERS = [("hybrid", HybridRetriever), ("bm25", BM25Retriever), ("like", LikeRetriever)]


def main():
    parser = argparse.ArgumentParser(description="真实问句评测逐题分析")
    parser.add_argument("--corpus", default="data/corpus.jsonl")
    parser.add_argument("--gold", default="data/gold_real_38.json")
    parser.add_argument("--k", type=int, default=5)
    args = parser.parse_args()

    corpus = []
    with io.open(args.corpus, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                corpus.append(json.loads(line))
    by_id = {a["id"]: a for a in corpus}
    gold = []
    with io.open(args.gold, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                gold.append(json.loads(line))

    retd = {}
    for name, Ret in RETRIEVERS:
        retd[name] = Ret(corpus)

    lines = []
    lines.append("| # | 真实问句 | 金标（法名 条号，LLM 核验） | hybrid | bm25 | like |")
    lines.append("|---|---|---|---|---|---|")
    stat = {n: 0 for n, _ in RETRIEVERS}
    misses = []
    for i, q in enumerate(gold, start=1):
        gids = set(q.get("gold_ids") or [q["gold_id"]])
        cells = []
        ranks = {}
        for name, _ in RETRIEVERS:
            results = retd[name].search(q["query"], k=args.k)
            rank = None
            for r, cite in enumerate(results, start=1):
                if cite["id"] in gids:
                    rank = r
                    break
            ranks[name] = rank
            if rank:
                stat[name] += 1
            cells.append("✅ #%d" % rank if rank else "❌")
        label = "；".join(q.get("gold_laws") or ["%s %s" % (by_id[g]["law"], by_id[g]["num"]) for g in gids])
        # 法名太长，压缩显示
        label = label.replace("中华人民共和国", "").replace("最高人民法院、最高人民检察院", "两高").replace("最高人民法院关于审理", "最高法：").replace("最高人民法院关于", "最高法：").replace("案件适用法律若干问题的解释", "案件解释").replace("适用法律若干问题的解释", "解释").replace("适用法律问题的解释", "解释")
        lines.append("| %s | %s | %s | %s | %s | %s |" % (i, q["query"], label, cells[0], cells[1], cells[2]))
        if ranks["hybrid"] is None:
            misses.append((q, ranks))

    print("\n".join(lines))
    n = len(gold)
    print("")
    print("命中统计：hybrid %d/%d, bm25 %d/%d, like %d/%d" % (stat["hybrid"], n, stat["bm25"], n, stat["like"], n))
    print("")
    print("hybrid 未命中 %d 题：" % len(misses))
    for q, ranks in misses:
        print("  - %s | 来源: %s | bm25rank=%s likerank=%s" % (q["query"], q["source_url"], ranks["bm25"], ranks["like"]))


if __name__ == "__main__":
    main()
