# -*- coding: utf-8 -*-
"""外部隔离题库 → 金标映射。

题库由出题代理在看不到语料/代码的环境里编写（gold/qbank_external_v1.jsonl，
出题完成后归档入库，出题过程在仓库外的 tmp_qbank 进行），本脚本是它与语料
的唯一接触点：把 expect_law + expect_num 映射成语料内 id，产出 run_eval.py /
bench.py 可直接消费的金标 JSONL，并把没对上的题（法名不在语料 / 条号不存在
——通常是版本差异、语料覆盖缺口或题库笔误）逐条报告出来供人工核对，不允许
静默丢弃。

用法：
  python scripts/eval_external_qbank.py \
      --corpus data/corpus_v5.jsonl --out gold/gold_external_v5.jsonl
"""
import argparse
import io
import json
import os
import sys
import unicodedata

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from statute_rag.importer import load_corpus

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

DEFAULT_QBANK = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "..", "gold", "qbank_external_v1.jsonl")


def law_key(name):
    """法名匹配键：NFKC + 去《》/空白/零宽字符。括号保留（解释（一）/（二）不能合并）。"""
    for ch in u"《》 \u3000\u200b\u200c\u200d\ufeff":
        name = name.replace(ch, u"")
    return unicodedata.normalize("NFKC", name)


def num_key(num):
    """条号匹配键：NFKC + 去空白（「第七十八条之一」保持原样，全角数字统一）。"""
    for ch in u" \u3000\u200b\u200c\u200d\ufeff":
        num = num.replace(ch, u"")
    return unicodedata.normalize("NFKC", num)


def build_law_index(corpus):
    """law_key(law) -> {num_key(num): [article, ...]}。"""
    index = {}
    for art in corpus:
        index.setdefault(law_key(art["law"]), {}).setdefault(
            num_key(art["num"]), []).append(art)
    return index


def map_item(item, index):
    """返回 (gold_ids, gold_laws, unmapped_alts)；主命中所列条号不存在时返回 None。"""
    law = index.get(law_key(item["expect_law"]))
    if not law:
        return None, [], []
    arts = law.get(num_key(item["expect_num"]))
    if not arts:
        return None, [], []
    gold_ids = [a["id"] for a in arts]
    gold_laws = [u"%s %s" % (a["law"], a["num"]) for a in arts]
    unmapped_alts = []
    for alt in item.get("expect_alts") or []:
        if u" " not in alt:
            unmapped_alts.append(alt)
            continue
        alt_law, alt_num = alt.rsplit(u" ", 1)
        alt_arts = index.get(law_key(alt_law), {}).get(num_key(alt_num))
        if alt_arts:
            gold_ids.extend(a["id"] for a in alt_arts)
            gold_laws.extend(u"%s %s" % (a["law"], a["num"]) for a in alt_arts)
        else:
            unmapped_alts.append(alt)
    return gold_ids, gold_laws, unmapped_alts


def load_qbank(path):
    out = []
    with io.open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def main():
    parser = argparse.ArgumentParser(description="外部题库 → 金标映射")
    parser.add_argument("--qbank", default=DEFAULT_QBANK, help="外部题库 JSONL（仓库外）")
    parser.add_argument("--corpus", default=os.path.join("data", "corpus_v5.jsonl"))
    parser.add_argument("--out", default=os.path.join("gold", "gold_external_v5.jsonl"))
    args = parser.parse_args()

    qbank = load_qbank(args.qbank)
    corpus = load_corpus(args.corpus)
    index = build_law_index(corpus)

    mapped, missing_law, missing_num = [], [], []
    for item in qbank:
        gold_ids, gold_laws, unmapped_alts = map_item(item, index)
        rec = {
            "qid": item["qid"],
            "query": item["query"],
            "question": item["query"],
            "category": item.get("category", u""),
            "style": item.get("style", u""),
            "difficulty": item.get("difficulty", u""),
            "expect_gist": item.get("expect_gist", u""),
            "verify_url": item.get("verify_url", u""),
            "unverified": bool(item.get("unverified")),
            "version_note": item.get("version_note", u""),
            "corpus": u"v5（外部隔离题库）",
        }
        if gold_ids is None:
            reason = u"law 不在语料" if law_key(item["expect_law"]) not in index else u"law 在语料但 num %s 不存在" % item["expect_num"]
            rec["expect_law"] = item["expect_law"]
            rec["expect_num"] = item["expect_num"]
            rec["unmatched_reason"] = reason
            (missing_law if law_key(item["expect_law"]) not in index else missing_num).append(rec)
            continue
        rec.update({
            "gold_id": gold_ids[0],
            "law": item["expect_law"],
            "num": item["expect_num"],
            "gold_ids": gold_ids,
            "gold_laws": gold_laws,
            "unmapped_alts": unmapped_alts,
        })
        mapped.append(rec)

    with io.open(args.out, "w", encoding="utf-8") as f:
        for rec in mapped:
            f.write(json.dumps(rec, ensure_ascii=False) + u"\n")

    print(u"题库 %d 题 → 命中 %d → %s" % (len(qbank), len(mapped), args.out))
    print(u"未命中 %d 题（law 不在语料 %d / num 不存在 %d）：" % (
        len(missing_law) + len(missing_num), len(missing_law), len(missing_num)))
    for rec in missing_law + missing_num:
        print(u"  %s  %s《%s》%s  %s" % (
            rec["qid"], rec["unmatched_reason"],
            rec.get(u"expect_law", u"?"), rec.get(u"expect_num", u"?"),
            rec["query"]))
    alts_bad = [r for r in mapped if r["unmapped_alts"]]
    if alts_bad:
        print(u"备选条文未命中 %d 题已保留主命中并记录 unmapped_alts：" % len(alts_bad))
        for rec in alts_bad:
            print(u"  %s  %s" % (rec["qid"], u"；".join(rec["unmapped_alts"])))
    for dim in (u"category", u"style", u"difficulty"):
        dist = {}
        for rec in mapped:
            dist[rec[dim] or u"(空)"] = dist.get(rec[dim] or u"(空)", 0) + 1
        print(u"%s 分布：%s" % (dim, u"，".join(
            u"%s %d" % (k, v) for k, v in sorted(dist.items()))))
    unverified = [r for r in mapped if r["unverified"]]
    if unverified:
        print(u"题库自标 unverified %d 题：%s" % (
            len(unverified), u"，".join(r["qid"] for r in unverified)))


if __name__ == "__main__":
    main()
