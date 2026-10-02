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


def evaluate_multi_k(retriever, gold, ks=(5, 10, 20, 30)):
    """一次取回 max(ks) 深的排名，在同一份排名上计算各 k 点指标。

    为什么不逐 k 调 search(k=...)：混合检索的通道深度属于检索配置，
    同一份排名下跨 k 比较才有意义——各点差异全部来自「截断位置」，
    度量的是「检到了但排不进前 k」的重排空间。返回 {k: metrics}。
    """
    if not gold:
        return dict((k, {"n": 0, "recall_at_k": 0.0, "mrr": 0.0, "k": k})
                    for k in ks)
    kmax = max(ks)
    hits = dict((k, 0) for k in ks)
    rr = dict((k, 0.0) for k in ks)
    for q in gold:
        gids = _gold_ids(q)
        results = retriever.search(q["query"], k=kmax)
        rank = None
        for i, cite in enumerate(results, start=1):
            if cite["id"] in gids:
                rank = i
                break
        if rank is None:
            continue
        for k in ks:
            if rank <= k:
                hits[k] += 1
                rr[k] += 1.0 / rank
    n = len(gold)
    return dict((k, {"n": n, "k": k,
                     "recall_at_k": hits[k] / float(n),
                     "mrr": rr[k] / float(n)})
                for k in ks)


def rank_histogram(retriever, gold, max_k=30):
    """金标排名分布：命中排名落在 1 / 2-5 / 6-10 / 11-max_k 的题数与未进前 max_k 数。

    排名 6-max_k 的题就是重排通道（v0.2）的直接工作面；全库语料下
    「未进前 max_k」通常意味着词法/语料层失败，重排救不了。
    返回 {"1": n, "2-5": n, "6-10": n, "11-<max_k>": n, "miss": n}。
    """
    buckets = {"1": 0, "2-5": 0, "6-10": 0, "11-%d" % max_k: 0, "miss": 0}
    for q in gold:
        gids = _gold_ids(q)
        results = retriever.search(q["query"], k=max_k)
        rank = None
        for i, cite in enumerate(results, start=1):
            if cite["id"] in gids:
                rank = i
                break
        if rank is None:
            buckets["miss"] += 1
        elif rank == 1:
            buckets["1"] += 1
        elif rank <= 5:
            buckets["2-5"] += 1
        elif rank <= 10:
            buckets["6-10"] += 1
        else:
            buckets["11-%d" % max_k] += 1
    return buckets


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
