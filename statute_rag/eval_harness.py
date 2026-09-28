# -*- coding: utf-8 -*-
"""评测框架：对任意 retriever 计算 Recall@k / MRR。

指标口径：
- Recall@k：gold 条文出现在 top-k 中的问题占比；
- MRR：gold 条文首命中排名倒数的均值。
两个指标对「多个条文都含题目短语」的歧义问题一视同仁地计 0/低分，
对合成金标这是已知保守偏差（对不同检索器公平）。

多金标支持：金标条目可用 gold_ids（id 列表，缺省回退 gold_id），
典型场景是真实问句金标——一句问话常对应多个相关条文，且语料分块
重叠会使同一答案文本出现在多行。此时命中其中任一条即算命中，
MRR 取首个命中行的排名。
"""


def _gold_ids(question):
    """金标 id 集合：优先 gold_ids 列表，回退单个 gold_id。"""
    ids = question.get("gold_ids")
    if not ids:
        ids = [question["gold_id"]]
    return set(ids)


def evaluate(retriever, gold, k=5):
    """retriever 需实现 search(query, k)；gold 条目须含 query 与 gold_id/gold_ids。"""
    if not gold:
        return {"n": 0, "recall_at_k": 0.0, "mrr": 0.0, "k": k}
    hits = 0
    rr_sum = 0.0
    for q in gold:
        gids = _gold_ids(q)
        results = retriever.search(q["query"], k=k)
        rank = None
        for i, cite in enumerate(results, start=1):
            if cite["id"] in gids:
                rank = i
                break
        if rank is not None:
            hits += 1
            rr_sum += 1.0 / rank
    n = len(gold)
    return {
        "n": n,
        "k": k,
        "recall_at_k": hits / float(n),
        "mrr": rr_sum / float(n),
    }


def format_report(results, corpus_size, gold_desc="合成金标，种子固定可复现"):
    """results: [("like", metrics), ...] → 对比表文本。gold_desc 说明金标口径。"""
    lines = []
    lines.append("| 检索器 | Recall@%d | MRR |" % results[0][1]["k"])
    lines.append("|---|---|---|")
    for name, m in results:
        lines.append("| %s | %.1f%% | %.3f |" % (name, m["recall_at_k"] * 100, m["mrr"]))
    lines.append("")
    lines.append("（N=%d 题，语料 %d 条；%s）" % (results[0][1]["n"], corpus_size, gold_desc))
    return "\n".join(lines)
