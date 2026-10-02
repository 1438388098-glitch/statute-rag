# -*- coding: utf-8 -*-
"""生成入库版评测报告（docs/eval_report.md）与机器可读指标（docs/metrics.json）。

需要本地真实语料（data/corpus.jsonl，不入仓库），属维护者步骤：
语料或检索器变更后跑一次本脚本，把两份产物随代码一起提交；CI 的
check_doc_numbers 步骤会校验两份 README 的数字与 metrics.json 一致。

历史口径：--history LABEL=PATH 可把更早版本的语料同口径重跑，追加一节
「三版语料并列」表（数字只进报告，不进 metrics.json，避免与当前口径混淆）。
用法：
  python scripts/gen_eval_report.py --corpus data/corpus_v3.jsonl \
      --history v1=data/corpus.jsonl --history v2=data/corpus_v2.jsonl
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

# 头条数字的噪声限定（审查要求 M3）：与两份 README 同段同义。
# 写成常量是为了让它同时进 docs/eval_report.md 与 docs/metrics.json，
# 避免「报告里有、机器可读口径里没有」的半截声明。
REAL_R5_NOTE = (
    "噪声限定：真实 R@5 的最后 2.6pt（扩展通道权重 w 2.0→2.5）只等于 1 道题的"
    "名次交换，落在 38 题的量化噪声内（1 题 = 2.6pt）；跨权重稳定、三版语料"
    "一致的硬结论是 R@30（v3 73.7%→81.6%）。"
)


def _data_ref(raw, label=None):
    """报告里的复现命令用维护者约定目录 `data/<文件名>`，不泄漏本机绝对路径。"""
    ref = "data/" + os.path.basename(raw.replace("\\", "/"))
    return "%s=%s" % (label, ref) if label else ref


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


def _history_table(corpus_entries):
    """三版语料并列表：hybrid 在真实/合成金标上的 R@5、MRR@5 与真实深度曲线。

    历史语料只用来展示「同一份代码、不同语料」的口径变化，不写入
    metrics.json（那是当前口径的单一来源），避免两套数字混为一谈。
    """
    lines = ["| 语料 | 条数 | 真实 R@5 | 真实 MRR@5 | 真实 R@10 | 真实 R@30 | 合成 R@5 |",
             "|---|---|---|---|---|---|---|"]
    for label, size, real_m, real_m5, synth_m in corpus_entries:
        lines.append("| %s | %d | %.1f%% | %.3f | %.1f%% | %.1f%% | %.1f%% |" % (
            label, size, real_m[5]["recall_at_k"] * 100, real_m5["mrr"],
            real_m[10]["recall_at_k"] * 100, real_m[30]["recall_at_k"] * 100,
            synth_m["recall_at_k"] * 100))
    return "\n".join(lines)


def _measure(hybrid_retriever, real_gold, synth_gold):
    return (evaluate_multi_k(hybrid_retriever, real_gold, ks=DEEP_KS),
            evaluate(hybrid_retriever, real_gold, k=5),
            evaluate(hybrid_retriever, synth_gold, k=5))


def main():
    parser = argparse.ArgumentParser(description="生成入库版评测报告与 metrics.json")
    parser.add_argument("--corpus", default="data/corpus.jsonl",
                        help="真实语料 JSONL（不入仓库，维护者本地提供）")
    parser.add_argument("--corpus-label", default=None,
                        help="并列表里当前语料的展示名（缺省取文件名）")
    parser.add_argument("--scope-note", default=None,
                        help="报告头部「当前口径语料」后的口径说明（缺省为通用措辞，"
                             "换语料时不应残留上一次的说明）")
    parser.add_argument("--history", action="append", default=None, metavar="CORPUS",
                        help="历史口径语料，可重复；写法 \"PATH\" 或 \"LABEL=PATH\"；仅在报告中追加并列表，不进 metrics.json")
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
        report_parts.append("## %s\n\n%s\n\n（N=%d 题，语料 %s 条）"
                            % (title, _table(rows), len(gold), "{:,}".format(len(corpus))))

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
        "## 深度口径（真实问句；同一份 depth=30 排名截取各 k，跨 k 可比）\n\n%s\n\n> %s\n"
        % (_deep_table(deep_rows), REAL_R5_NOTE))
    report_parts.append(
        "### hybrid 排名分布（真实问句，max_k=30）\n\n"
        + "，".join("%s：%d 题" % (label, n) for label, n in hist.items())
        + "\n\n未进前 30 的题属词法/语料层失败，重排救不了；6-30 名的题是 v0.2 重排通道的工作面。")
    metrics["recall_at_5_note"] = REAL_R5_NOTE

    # 历史口径并列（--history）：同一份代码在不同语料上的口径变化，只进报告
    if args.history:
        entries = [("**当前：%s**" % (args.corpus_label or os.path.basename(args.corpus)),
                    len(corpus),
                    evaluate_multi_k(retrievers[-1][1], real_gold, ks=DEEP_KS),
                    evaluate(retrievers[-1][1], real_gold, k=5),
                    evaluate(retrievers[-1][1], synth_gold, k=5))]
        for raw in args.history:
            label, path = (raw.split("=", 1) if "=" in raw
                           else (os.path.basename(raw), raw))
            hist_corpus = load_corpus(path)
            real_m, real_m5, synth_m = _measure(HybridRetriever(hist_corpus),
                                                real_gold, synth_gold)
            entries.append(("%s（历史口径）" % label, len(hist_corpus),
                            real_m, real_m5, synth_m))
        report_parts.append(
            "## 三版语料并列（hybrid；同一份代码、不同语料，跨语料不可直接比较）\n\n"
            + _history_table(entries)
            + "\n\n语料扩容会改变同批问句的 R@5：v3 扩容后一度降到 31.6%，"
              "按诊断结论重标定 BM25 长度归一化与融合权重后恢复到 44.7%。"
              "机制、逐题迁移与全网格见 [docs/retrieval-v3-diagnosis.md](retrieval-v3-diagnosis.md)。")

    commit = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"]).decode().strip()
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    repro = "python scripts/gen_eval_report.py --corpus %s" % _data_ref(args.corpus)
    if args.corpus_label:
        repro += " --corpus-label %s" % args.corpus_label
    if args.history:
        repro += " " + " ".join("--history %s" % _data_ref(h) for h in args.history)
    if args.scope_note:
        repro += ' --scope-note "%s"' % args.scope_note
    scope = args.scope_note or "（口径说明见文末并列表）"
    header = "\n".join([
        "# 评测报告",
        "",
        "> **本文件由 `scripts/gen_eval_report.py` 生成（需维护者本地语料），勿手改；**",
        "> 数字单一来源是 [docs/metrics.json](metrics.json)，CI 校验两份 README 与之一致。",
        "> **当前口径语料：%s 条**%s" % ("{:,}".format(len(corpus)), scope),
        "> 生成于 %s，commit `%s`。" % (now, commit),
        "> 复现：`%s`" % repro,
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
