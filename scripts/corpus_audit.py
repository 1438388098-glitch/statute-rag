# -*- coding: utf-8 -*-
"""语料逐行质量旗标与质检报告（**不改语料文件**，输出独立报告 + 机器可读 json）。

v3 扩到 25,273 条 / 432 部后，质量问题不再只是「有没有条文」，而是「哪些行不可靠」。
本脚本对语料逐行算质量旗标，聚合成按法质检报告，并把最需要人工判断的两类
（重复条号、回退映射条号）整理成**可直接交专业复核**的表：

旗标（写入报告的 quality_flags，不改语料）：
- polluted-interleave  疑似双栏交错：v1 公报源（含公报页眉/页码排版标记）且窄栏切片
                        （空白分段中位长度 < 20）。**启发式风险带**，非确证污染——
                        公报 PDF 双栏解析若发生串行，本旗标圈定风险行，需人工复核。
- num-mapped           条号来自回退映射：meta.num_origin 与 num 不一致
                        （「一、」/「N.」→「第X条」，见 docs/corpus-completion.md §3.3）。
- whole-document       num == "全文" 或 meta.unit == "whole-document"（整篇作一条）。
- very-long            文本长度 > 2000 字（正常分块 ~1500）。
- dup-law-num          同 (law, num) 有多行并存（分块重叠/多来源）。
- empty-or-tiny        text 去空白后长度 < 10。

用法：
  python scripts/corpus_audit.py --corpus data/corpus_v3.jsonl \\
      --md docs/corpus-audit.md --json data/flk/tmp/corpus_audit.json
"""
import argparse
import io
import json
import os
import re
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.coverage_report import GAZETTE_SOURCE, classify_source  # noqa: E402

TINY_LEN = 10
VERY_LONG = 2000
NARROW_MED = 20
GAZ_FURNITURE = u"全国人民代表大会常务委员会公报"
PAGE_MARK = re.compile(u"\u2014[\\uFF10-\\uFF19]{1,4}\u2014")  # —７５４— 全角页码

# 复核表在 markdown 中最多列出的行数（完整清单在 json）
MD_REVIEW_LIMIT = 120


def median(xs):
    if not xs:
        return 0
    xs = sorted(xs)
    return xs[len(xs) // 2]


def fragment_lengths(text):
    return [len(f) for f in (text or u"").split() if f]


def is_gazette_furniture(text):
    """公报排版标记：页眉「全国人民代表大会常务委员会公报」或全角页码「—NNN—」。"""
    return (GAZ_FURNITURE in text) or bool(PAGE_MARK.search(text or u""))


def narrow_column(text):
    """窄栏切片：空白分段中位长度低于阈值（公报双栏 PDF 的列宽特征）。"""
    frs = fragment_lengths(text)
    return bool(frs) and median(frs) < NARROW_MED


def row_flags(row, dup_keys):
    """返回该行的旗标列表。"""
    flags = []
    text = row.get("text") or u""
    meta = row.get("meta") or {}
    if len(u"".join(text.split())) < TINY_LEN:
        flags.append("empty-or-tiny")
    if len(text) > VERY_LONG:
        flags.append("very-long")
    if row.get("num") == u"全文" or meta.get("unit") == u"whole-document":
        flags.append("whole-document")
    origin = meta.get("num_origin")
    if origin is not None and origin != row.get("num"):
        flags.append("num-mapped")
    if (row.get("law"), row.get("num")) in dup_keys:
        flags.append("dup-law-num")
    if (classify_source(row) == GAZETTE_SOURCE
            and is_gazette_furniture(text) and narrow_column(text)):
        flags.append("polluted-interleave")
    return flags


def audit(rows):
    """对全部行算旗标并聚合。返回机器可读报告 dict。"""
    pair_count = Counter((r.get("law"), r.get("num")) for r in rows)
    dup_keys = set(k for k, c in pair_count.items() if c > 1)

    flag_counts = Counter()
    per_law = defaultdict(Counter)
    per_source = defaultdict(Counter)
    flagged_rows = defaultdict(list)
    for r in rows:
        flags = row_flags(r, dup_keys)
        src = classify_source(r)
        for f in flags:
            flag_counts[f] += 1
            per_law[r.get("law")][f] += 1
            per_source[src][f] += 1
        if flags:
            flagged_rows[tuple(flags)].append(r)

    def brief(r):
        meta = r.get("meta") or {}
        return {
            "id": r.get("id"),
            "law": r.get("law"),
            "num": r.get("num"),
            "num_origin": meta.get("num_origin"),
            "source": classify_source(r),
            "text_len": len(r.get("text") or u""),
        }

    # 按法聚合（有问题行的法）
    problem_laws = []
    for law, c in per_law.items():
        total = sum(1 for r in rows if r.get("law") == law)
        problem_laws.append({
            "law": law,
            "rows": total,
            "flags": dict(c),
            "flagged": sum(c.values()),
        })
    problem_laws.sort(key=lambda x: (-x["flagged"], x["law"]))

    return {
        "corpus_rows": len(rows),
        "corpus_laws": len(set(r.get("law") for r in rows)),
        "flag_counts": dict(flag_counts),
        "per_source": dict((k, dict(v)) for k, v in per_source.items()),
        "problem_laws": problem_laws,
        "dup_law_num_rows": sorted((brief(r) for r in rows
                                    if (r.get("law"), r.get("num")) in dup_keys),
                                   key=lambda x: (x["law"] or u"", x["num"] or u"")),
        "num_mapped_rows": sorted((brief(r) for r in rows
                                   if (r.get("meta") or {}).get("num_origin") is not None
                                   and (r.get("meta") or {}).get("num_origin") != r.get("num")),
                                  key=lambda x: (x["law"] or u"", x["id"])),
        "whole_document_rows": [brief(r) for r in rows
                                if r.get("num") == u"全文"
                                or (r.get("meta") or {}).get("unit") == u"whole-document"],
        "very_long_rows": [brief(r) for r in rows if len(r.get("text") or u"") > VERY_LONG],
        "empty_or_tiny_rows": [brief(r) for r in rows
                               if len(u"".join((r.get("text") or u"").split())) < TINY_LEN],
    }


FLAG_DOC = [
    ("polluted-interleave", u"疑似双栏交错（启发式风险带，需人工复核）"),
    ("num-mapped", u"条号来自回退映射（num_origin ≠ num）"),
    ("dup-law-num", u"同 (law, num) 多行并存"),
    ("whole-document", u"整篇作一条（num=全文）"),
    ("very-long", u"超长条（> %d 字）" % VERY_LONG),
    ("empty-or-tiny", u"空/超短（< %d 字）" % TINY_LEN),
]


def review_table(rows, keys, headers, json_key, limit):
    if not rows:
        return [u"（无）", u""]
    out = [u"| " + u" | ".join(headers) + u" |",
           u"|" + u"---|" * len(headers)]
    for r in rows[:limit]:
        out.append(u"| " + u" | ".join(_cell(r.get(k)) for k in keys) + u" |")
    if len(rows) > limit:
        out.append(u"")
        out.append(u"（共 %d 行，仅列前 %d；完整清单见 json 报告的 `%s`）"
                   % (len(rows), limit, json_key))
    out.append(u"")
    return out


def _cell(v):
    if v is None:
        return u""
    if isinstance(v, bytes):  # pragma: no cover
        v = v.decode("utf-8")
    return (u"%s" % v).replace(u"|", u"\\|")


def format_markdown(rep):
    L = []
    L.append(u"# 语料逐行质检报告（旗标）")
    L.append(u"")
    L.append(u"> 由 `python scripts/corpus_audit.py` 生成，**不修改语料文件**。"
             u"旗标是分级信号：`polluted-interleave` 为启发式风险带，"
             u"`dup-law-num` 与 `num-mapped` 的清单为**交专业复核**产物。")
    L.append(u"")
    L.append(u"## 总览")
    L.append(u"")
    L.append(u"| 项 | 数值 |")
    L.append(u"|---|---|")
    L.append(u"| 语料条数 | %d |" % rep["corpus_rows"])
    L.append(u"| 法律部数 | %d |" % rep["corpus_laws"])
    L.append(u"")
    L.append(u"## 旗标计数")
    L.append(u"")
    L.append(u"| 旗标 | 含义 | 行数 |")
    L.append(u"|---|---|---|")
    for key, desc in FLAG_DOC:
        L.append(u"| `%s` | %s | **%d** |" % (key, desc, rep["flag_counts"].get(key, 0)))
    L.append(u"")
    L.append(u"## 疑似双栏交错：按法（top 20）")
    L.append(u"")
    L.append(u"> 启发式：v1 公报源 + 公报排版标记 + 窄栏切片（空白分段中位长度 < %d）。"
             u"**该启发式不能区分「窄栏但顺序正确」与「窄栏且串行交错」**，"
             u"只圈定风险带，逐行是否真污染须人工对照官方文本。" % NARROW_MED)
    L.append(u"")
    L.append(u"| 法名 | 行数 | 疑似交错行 |")
    L.append(u"|---|---|---|")
    for p in [x for x in rep["problem_laws"] if x["flags"].get("polluted-interleave")][:20]:
        L.append(u"| %s | %d | %d |" % (p["law"], p["rows"], p["flags"]["polluted-interleave"]))
    L.append(u"")
    L.append(u"## 交专业复核一：重复条号 dup-law-num（%d 行）"
             % len(rep["dup_law_num_rows"]))
    L.append(u"")
    L.extend(review_table(rep["dup_law_num_rows"],
                          ["law", "num", "source", "id", "text_len"],
                          [u"法名", u"条号", u"来源", u"行 id", u"字数"],
                          u"dup_law_num_rows", MD_REVIEW_LIMIT))
    L.append(u"## 交专业复核二：回退映射条号 num-mapped（%d 行）"
             % len(rep["num_mapped_rows"]))
    L.append(u"")
    L.extend(review_table(rep["num_mapped_rows"],
                          ["law", "num", "num_origin", "source", "id"],
                          [u"法名", u"映射后条号", u"原文标记", u"来源", u"行 id"],
                          u"num_mapped_rows", MD_REVIEW_LIMIT))
    L.append(u"## 整篇作一条 whole-document（%d 行）" % len(rep["whole_document_rows"]))
    L.append(u"")
    L.extend(review_table(rep["whole_document_rows"],
                          ["law", "num", "source", "id"],
                          [u"法名", u"条号", u"来源", u"行 id"],
                          u"whole_document_rows", MD_REVIEW_LIMIT))
    L.append(u"## 超长条 very-long（%d 行）" % len(rep["very_long_rows"]))
    L.append(u"")
    L.extend(review_table(rep["very_long_rows"],
                          ["law", "num", "source", "id", "text_len"],
                          [u"法名", u"条号", u"来源", u"行 id", u"字数"],
                          u"very_long_rows", MD_REVIEW_LIMIT))
    L.append(u"## 空/超短 empty-or-tiny（%d 行）" % len(rep["empty_or_tiny_rows"]))
    L.append(u"")
    L.extend(review_table(rep["empty_or_tiny_rows"],
                          ["law", "num", "source", "id", "text_len"],
                          [u"法名", u"条号", u"来源", u"行 id", u"字数"],
                          u"empty_or_tiny_rows", MD_REVIEW_LIMIT))
    L.append(u"---")
    L.append(u"")
    L.append(u"本报告由 `scripts/corpus_audit.py` 生成；复现命令见 "
             u"[corpus-completion.md](corpus-completion.md) §6。")
    return u"\n".join(L) + u"\n"


def load_jsonl(path):
    rows = []
    with io.open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def main():
    parser = argparse.ArgumentParser(description="语料逐行质量旗标与质检报告")
    parser.add_argument("--corpus", required=True)
    parser.add_argument("--md", help="markdown 报告输出（如 docs/corpus-audit.md）")
    parser.add_argument("--json", dest="json_out", help="机器可读 json 输出")
    args = parser.parse_args()

    rows = load_jsonl(args.corpus)
    rep = audit(rows)
    print(u"语料 %d 条 / %d 部" % (rep["corpus_rows"], rep["corpus_laws"]))
    for key, _ in FLAG_DOC:
        print(u"  %-22s %d" % (key, rep["flag_counts"].get(key, 0)))
    if args.md:
        with io.open(args.md, "w", encoding="utf-8", newline="\n") as f:
            f.write(format_markdown(rep))
        print(u"markdown 报告 → %s" % args.md)
    if args.json_out:
        with io.open(args.json_out, "w", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(rep, ensure_ascii=False, indent=2))
            f.write(u"\n")
        print(u"json 报告 → %s" % args.json_out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
