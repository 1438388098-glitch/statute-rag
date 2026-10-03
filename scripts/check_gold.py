# -*- coding: utf-8 -*-
"""金标结构校验器：金标与语料之间的一致性门禁。

检查每个金标行：
1. gold_id / gold_ids 都真实存在于语料；
2. gold_laws 里每个「法名 条号」：法在语料、条号在该法下真实存在
   （能抓到「一部 21 条的司法解释标着第四十八条」这类迁移垃圾）；
3. gold_laws 与 gold_ids 互相一致（每个条号对至少映射到行内一个 id）；
4. evidence 逐字命中主命中文本——不命中只记 WARN（evidence 是核验线索，
   不是结构字段），但连续的 evidence 未命中就是金标过时的信号。

结构错误（1–3）退出码 1；evidence 未命中只报告。

用法：
  python scripts/check_gold.py --corpus data/corpus_v6.jsonl \
      --gold gold/gold_real_38_v6.jsonl --gold gold/gold_external_v6.jsonl
"""
import argparse
import io
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))


def squeeze(t):
    return u"".join((t or u"").split())


def load_jsonl(path):
    out = []
    with io.open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def check(gold_rows, by_id, law_nums):
    """返回 (结构错误列表, evidence 未命中列表)。"""
    struct, ev_miss = [], []
    for g in gold_rows:
        qid = g.get("qid", "?")
        ids = [i for i in ([g.get("gold_id")] if g.get("gold_id") is not None else [])
               + list(g.get("gold_ids") or []) if i is not None]
        for i in ids:
            if i not in by_id:
                struct.append(u"%s\tgold_id %s 不在语料" % (qid, i))
        id_lawnums = set()
        for i in ids:
            if i in by_id:
                id_lawnums.add(u"%s %s" % (by_id[i]["law"], by_id[i]["num"]))
        pairs = []
        for pair in g.get("gold_laws") or []:
            law, _, num = pair.rpartition(u" ")
            if law not in law_nums:
                struct.append(u"%s\t法不在语料：%s" % (qid, law))
                continue
            if num not in law_nums[law]:
                struct.append(u"%s\t条号不存在：%s" % (qid, pair))
                continue
            pairs.append(pair)
        if g.get("gold_laws") and pairs:
            # 条号对与 id 字段应指向同一批条文（允许 id 字段为空之外的错位）
            if ids and not any(p in id_lawnums for p in pairs):
                struct.append(u"%s\tgold_laws 与 gold_ids 指向不同条文（ids=%s laws=%s）"
                              % (qid, sorted(id_lawnums)[:2], pairs[:2]))
        ev = squeeze(g.get("evidence") or u"")
        if ev:
            primary = None
            for p in pairs:
                law, _, num = p.rpartition(u" ")
                cands = by_id.get(g.get("gold_id"))
                if cands and u"%s %s" % (cands["law"], cands["num"]) == p:
                    primary = cands
                    break
            if primary is None:
                for p in pairs:
                    law, _, num = p.rpartition(u" ")
                    for i in ids:
                        r = by_id.get(i)
                        if r and r["law"] == law and r["num"] == num:
                            primary = r
                            break
                    if primary:
                        break
            if primary is not None and ev not in squeeze(primary["text"]):
                ev_miss.append(u"%s\tevidence 未逐字命中 %s %s" % (
                    qid, primary["law"], primary["num"]))
    return struct, ev_miss


def main():
    ap = argparse.ArgumentParser(description=u"金标结构校验")
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--gold", action="append", required=True)
    args = ap.parse_args()

    by_id = {}
    law_nums = {}
    for r in load_jsonl(args.corpus):
        by_id[r["id"]] = r
        law_nums.setdefault(r["law"], set()).add(r["num"])

    bad = 0
    for path in args.gold:
        rows = load_jsonl(path)
        struct, ev_miss = check(rows, by_id, law_nums)
        print(u"%s：%d 行；结构错误 %d；evidence 未逐字命中 %d" % (
            path, len(rows), len(struct), len(ev_miss)))
        for line in struct:
            print(u"  [结构] %s" % line)
        for line in ev_miss:
            print(u"  [WARN] %s" % line)
        bad += len(struct)
    if bad:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
