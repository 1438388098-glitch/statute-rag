# -*- coding: utf-8 -*-
"""检索改进消融：逐机制前后对比（真实金标 + 合成金标双口径，可多语料、多 k）。

用法：
  # 单语料双金标（默认口径）
  python scripts/ablate_retrieval.py --corpus data/corpus.jsonl \
      --gold-real gold/gold_real_38.jsonl --gold-synth gold/gold_synth_seed20260918.jsonl

  # 三版语料并列（v3 退化诊断的复现命令，见 docs/retrieval-v3-diagnosis.md）
  python scripts/ablate_retrieval.py \
      --corpus v1=../statute-rag/data/corpus.jsonl \
      --corpus v2=../statute-rag/data/corpus_v2.jsonl \
      --corpus v3=../statute-rag/data/corpus_v3.jsonl --ks 5,10,20,30

  # 参数全表（诊断文档 §5.1 / §5.2 的原始输出，逐格对应）
  #   §5.1 的 b 网格固定 w=1.0（对照组），故需显式传 --expansion-weight 1.0；
  #   §5.2 的权重网格固定 b=0.6（模块默认值），无需额外开关。
  python scripts/ablate_retrieval.py --corpus v3=... --b-scan --expansion-weight 1.0
  python scripts/ablate_retrieval.py --corpus v3=... --weight-scan

扫描点位与固定条件（与诊断文档表格逐格对齐，改点位即改文档）：
  --b-scan       扫描 BM25_B，点位 B_SCAN；扩展通道权重固定为 --expansion-weight
                 给出的值（缺省即模块当前值 2.5）
  --weight-scan  扫描扩展通道权重，点位 WEIGHT_SCAN；BM25_B 固定为模块当前值 0.6

消融维度（均为通用机制参数，非按题调参）：
  A0 基线（use_expansion=False）—— 与改进前完全一致；
  A1 +同义扩展路（hybrid 默认配置：原查询 BM25 + 加权扩展查询 BM25 + LIKE）。
  其余曾试机制（替换式变体多路、法名先验加权、trigram 短语加权、通道深度、
  同法限流、引用行降权、多视图融合、同 (law,num) 去重折叠）因无 Recall 增量
  或回退未纳入最终配置，负结果见 docs/retrieval-improvement.md §4 与
  docs/retrieval-v3-diagnosis.md §6。

多 k 口径：Recall@k 取自同一份 depth=30 融合排名逐 k 截取（evaluate_multi_k），
MRR 恒为 MRR@5（与已发布口径、docs/metrics.json 一致）。
"""
import argparse
import io
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import statute_rag.retrieval as retrieval
from statute_rag.eval_harness import evaluate, evaluate_multi_k
from statute_rag.retrieval import BM25Retriever, HybridRetriever, LikeRetriever

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

DEFAULT_CORPUS = "data/corpus.jsonl"
# 点位与 docs/retrieval-v3-diagnosis.md §5.1 的表体逐行一致（b 网格，w 固定 1.0）
B_SCAN = (0.75, 0.72, 0.70, 0.68, 0.65, 0.60, 0.50, 0.00)
# 点位与同文 §5.2 的表体逐行一致（扩展通道权重网格，b 固定 0.6）
WEIGHT_SCAN = (1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0, 4.0, 5.0)


def load_jsonl(path):
    out = []
    with io.open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def parse_corpus_arg(raw):
    """--corpus 取值：'PATH' 或 'LABEL=PATH'。LABEL 用于多语料并列展示。"""
    if "=" in raw:
        label, path = raw.split("=", 1)
        return label.strip(), path.strip()
    return os.path.basename(raw), raw


def fmt_multi(real_multi, synth_m5, ks):
    cells = " ".join("R@%d=%.1f%%" % (k, real_multi[k]["recall_at_k"] * 100) for k in ks)
    return "%s MRR@5=%.3f | 合成 R@5=%.1f%% MRR=%.3f" % (
        cells, real_multi[5]["mrr"], synth_m5["recall_at_k"] * 100, synth_m5["mrr"])


def run_config(corpus, gold_real, gold_synth, ks, retriever):
    real_multi = evaluate_multi_k(retriever, gold_real, ks=ks)
    synth = evaluate(retriever, gold_synth, k=5)
    return fmt_multi(real_multi, synth, ks)


def main():
    parser = argparse.ArgumentParser(description="检索改进消融测量")
    parser.add_argument("--corpus", action="append", default=None,
                        metavar="CORPUS",
                        help="语料 JSONL，可重复以并列多云料；写法 \"PATH\" 或 \"LABEL=PATH\"（LABEL 用于并列展示）；缺省 data/corpus.jsonl")
    parser.add_argument("--gold-real", default="gold/gold_real_38.jsonl")
    parser.add_argument("--gold-synth", default="gold/gold_synth_seed20260918.jsonl")
    parser.add_argument("--ks", default="5", help="逗号分隔的 k 列表，如 5,10,20,30")
    parser.add_argument("--b-scan", action="store_true",
                        help="扫描 BM25_B 全表（真实多 k + 合成 R@5/MRR@5）")
    parser.add_argument("--weight-scan", action="store_true",
                        help="扫描扩展查询通道权重全表（真实多 k + 合成 R@5/MRR@5）")
    parser.add_argument("--expansion-weight", type=float, default=None,
                        metavar="W",
                        help="固定扩展查询通道权重（覆盖模块默认值）；"
                             "--b-scan 复现 §5.1 时须传 1.0")
    args = parser.parse_args()

    if args.b_scan and args.weight_scan:
        parser.error("--b-scan 与 --weight-scan 互斥：一次只扫一个参数，"
                     "否则固定条件不同、结果无法归因")

    ks = tuple(int(x) for x in args.ks.split(",") if x.strip())
    if 5 not in ks:
        ks = tuple(sorted(set(ks) | set([5])))  # MRR 口径恒为 k=5
    corpora = [parse_corpus_arg(x) for x in (args.corpus or [DEFAULT_CORPUS])]
    gold_real = load_jsonl(args.gold_real)
    gold_synth = load_jsonl(args.gold_synth)

    if args.expansion_weight is not None:
        retrieval.EXPANSION_CHANNEL_WEIGHT = args.expansion_weight

    for label, path in corpora:
        corpus = load_jsonl(path)
        print("== 语料 %s：%d 条（%s）；k=%s；固定条件：BM25_B=%s，扩展通道权重=%s =="
              % (label, len(corpus), path, list(ks),
                 retrieval.BM25_B, retrieval.EXPANSION_CHANNEL_WEIGHT))
        if args.b_scan or args.weight_scan:
            orig_b, orig_w = retrieval.BM25_B, retrieval.EXPANSION_CHANNEL_WEIGHT
            if args.b_scan:
                name, values, attr = "BM25_B", B_SCAN, "BM25_B"
            else:
                name, values, attr = "扩展通道权重", WEIGHT_SCAN, "EXPANSION_CHANNEL_WEIGHT"
            for value in values:
                setattr(retrieval, attr, value)
                print("  %s=%-5s %s" % (name, value,
                                        run_config(corpus, gold_real, gold_synth, ks,
                                                   HybridRetriever(corpus))))
            retrieval.BM25_B, retrieval.EXPANSION_CHANNEL_WEIGHT = orig_b, orig_w
        else:
            for rname, Ret in (("like", LikeRetriever), ("bm25", BM25Retriever)):
                print("  %-26s %s" % ("单路 " + rname,
                                      run_config(corpus, gold_real, gold_synth, ks, Ret(corpus))))
            for clabel, kwargs in (("A0 基线(exp=off)", dict(use_expansion=False)),
                                   ("A1 +同义扩展路(exp=on)", dict(use_expansion=True))):
                print("  %-26s %s" % (clabel, run_config(
                    corpus, gold_real, gold_synth, ks, HybridRetriever(corpus, **kwargs))))
        print("")


if __name__ == "__main__":
    main()
