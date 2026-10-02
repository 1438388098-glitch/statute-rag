# -*- coding: utf-8 -*-
"""生成入库版评测报告（docs/eval_report.md）与机器可读指标（docs/metrics.json）。

需要本地真实语料（data/corpus.jsonl，不入仓库），属维护者步骤：
语料或检索器变更后跑一次本脚本，把两份产物随代码一起提交；CI 的
check_doc_numbers 步骤会校验两份 README 的数字与 metrics.json 一致。

用法：
  python scripts/gen_eval_report.py --corpus data/corpus.jsonl
"""
import argparse
import datetime
import io
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from statute_rag.eval_harness import evaluate, evaluate_multi_k, rank_histogram
from statute_rag.gold import load_gold
from statute_rag.importer import load_corpus
from statute_rag.retrieval import BM25Retriever, HybridRetriever, LikeRetriever

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

RETRIEVERS = [
    ("like（子串基线）", LikeRetriever),
    ("bm25（字符二元组）", BM25Retriever),
    ("hybrid（RRF 融合）", HybridRetriever),
]

SYNTH_GOLD = os.path.join("gold", "gold_synth_seed20260918.jsonl")
REAL_GOLD = os.path.join("gold", "gold_real_38.jsonl")

DEEP_KS = (5, 10, 20, 30)


def _table(rows):
    lines = ["| 检索器 | Recall@5 | MRR |", "|---|---|---|"]
    for name, m in rows:
        lines.append("| %s | %.1f%% | %.3f |" % (name, m["recall_at_k"] * 100, m["mrr"]))
    return "\n".join(lines)


def _deep_table(multi_by_retriever):
    head = "| 检索器 | " + " | ".join("R@%d" % k for k in DEEP_KS) + " |"
    lines = [head, "|---|" + "---|" * len(DEEP_KS)]
    for name, multi in multi_by_retriever:
        cells = " | ".join("%.1f%%" % (multi[k]["recall_at_k"] * 100) for k in DEEP_KS)
        lines.append("| %s | %s |" % (name, cells))
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="生成入库版评测报告与 metrics.json")
    parser.add_argument("--corpus", default="data/corpus.jsonl",
                        help="真实语料 JSONL（不入仓库，维护者本地提供）")
    parser.add_argument("--out-doc", default="docs/eval_report.md")
    parser.add_argument("--out-metrics", default="docs/metrics.json")
    args = parser.parse_args()

    corpus = load_corpus(args.corpus)
    synth_gold = load_gold(SYNTH_GOLD)
    real_gold = load_gold(REAL_GOLD)

    metrics = {"synth": {"n": len(synth_gold)}, "real": {"n": len(real_gold)}}
    report_parts = []

    retrievers = [(name, Ret(corpus)) for name, Ret in RETRIEVERS]
    for gold_key, gold, title in [
        ("synth", synth_gold, "合成金标（seed=20260918，「全库唯一短语 → 关键词查询」）"),
        ("real", real_gold, "真实问句金标 v1（38 题，LLM 核验，人工法律复核进行中）"),
    ]:
        rows = []
        for name, r in retrievers:
            m = evaluate(r, gold, k=5)
            rows.append((name, m))
            metrics[gold_key][name.split("（")[0]] = {
                "recall_at_5": round(m["recall_at_k"], 4),
                "recall_at_5_display": "%.1f%%" % (m["recall_at_k"] * 100),
                "mrr": round(m["mrr"], 3),
                "mrr_display": "%.3f" % m["mrr"],
            }
        report_parts.append("## %s\n\n%s\n\n（N=%d 题，语料 %d 条）"
                            % (title, _table(rows), len(gold), len(corpus)))

    # 深度口径：同一份 depth=30 排名截取各 k（跨 k 可比，见 eval_harness.evaluate_multi_k）
    deep_rows = []
    hist = None
    for name, r in retrievers:
        multi = evaluate_multi_k(r, real_gold, ks=DEEP_KS)
        deep_rows.append((name, multi))
        if name.startswith("hybrid"):
            hist = rank_histogram(r, real_gold, max_k=max(DEEP_KS))
            metrics["real_deep"] = dict(
                ("recall_at_%d" % k, round(multi[k]["recall_at_k"], 4))
                for k in DEEP_KS)
            metrics["real_deep_display"] = dict(
                ("R@%d" % k, "%.1f%%" % (multi[k]["recall_at_k"] * 100))
                for k in DEEP_KS)
            metrics["real_hybrid_rank_histogram"] = hist
    report_parts.append(
        "## 深度口径（真实问句；同一份 depth=30 排名截取各 k，跨 k 可比）\n\n%s\n"
        % _deep_table(deep_rows))
    report_parts.append(
        "### hybrid 排名分布（真实问句，max_k=30）\n\n"
        + "，".join("%s：%d 题" % (label, n) for label, n in hist.items())
        + "\n\n未进前 30 的题属词法/语料层失败，重排救不了；6-30 名的题是 v0.2 重排通道的工作面。")

    commit = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"]).decode().strip()
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    header = "\n".join([
        "# 评测报告",
        "",
        "> **本文件由 `scripts/gen_eval_report.py` 生成（需维护者本地语料），勿手改；**",
        "> 数字单一来源是 [docs/metrics.json](metrics.json)，CI 校验两份 README 与之一致。",
        "> 生成于 %s，commit `%s`，语料 %d 条。" % (now, commit, len(corpus)),
        "> 复现：`python scripts/gen_eval_report.py --corpus data/corpus.jsonl`",
        "",
    ])
    with io.open(args.out_doc, "w", encoding="utf-8", newline="\n") as f:
        f.write(header + "\n\n".join(report_parts) + "\n")
    metrics["generated"] = {"date": now, "commit": commit, "corpus_size": len(corpus)}
    with io.open(args.out_metrics, "w", encoding="utf-8", newline="\n") as f:
        json.dump(metrics, f, ensure_ascii=False, indent=2, sort_keys=True)
    print("已生成：%s / %s" % (args.out_doc, args.out_metrics))


if __name__ == "__main__":
    main()
