# -*- coding: utf-8 -*-
"""把「仍是页块/交错」的法，用本地源按证据重采为条级行（补 v4 的缺口）。

v4 已用「干净源件（docx/md）」重采了 132 部法，但还剩 ~104 部法在语料里仍是
v1 页块行。本脚本对这批法按优先级依次试**本地已有**的三种源，谁过门用谁：

  ① `zhuma`：竹马电子法条（`data/flk/tmp/zhuma_laws_raw.json`，242 部法）。
     v3 当时为了「v1/v2 行冻结、append-only」把已在语料里的法整部跳过了，
     但那些法正是现在要修的——所以本脚本**不加排除**，直接用。
  ② `ldb-raw`：`legal.db` 的 `documents.content` **按行重切**（行即段落）。
     关键发现：legal.db 的行结构是完好的（每行一个 PDF 文本行、行间空行），
     语料里的「页块」是**入库时的定长切块**造成的，不是文本本身坏。实测拿
     已由干净源件修好的 130 部法做金样，`ldb-raw` 重切**逐字命中 7339/7340
     = 100.0%**、129/130 部整法全中——所以对「文本没交错」的法，这条最稳。
  ③ `ldb-deinter`：把 `legal.db` 的双栏交错文本按行结构还原（切页 + 隔行分股，
     见 `scripts/deinterleave_gazette.py`）。只对真交错的法有效（用户报障那一部
     「侵权责任编解释（一）」即此路，还原出 1..25 全序）。

过门条件（不过就换下一源，都不行则如实记为「仍需官方源」）：
- 条数 ≥ 2、条号严格单调；
- 正文覆盖率（条文本字数 / 全部行字数）≥ 0.90；
- 单条门禁（`resource_from_clean_files.clean`：条号开头、句末标点、空格密度、
  无公报排版垃圾）丢弃比例 ≤ 5%；
- 条号 1..N 全序（缺号如实登记，不静默拼凑）。

用法：
  # 校验：v3 语料 + 干净源件金样，量 ldb-raw / ldb-deinter 的准确率
  python scripts/resource_local_remaining.py --ldb ...legal.db --corpus data/corpus_v3.jsonl \\
      --covered data/flk/fragments/clean_source.jsonl --validate \\
      --report data/flk/tmp/local_remaining_validate.txt

  # 产出：v4 语料里仍缺的那批
  python scripts/resource_local_remaining.py --ldb ...legal.db --corpus data/corpus_v4.jsonl \\
      --covered data/flk/fragments/clean_source.jsonl \\
      --out data/flk/fragments/local_remaining.jsonl \\
      --report data/flk/tmp/local_remaining_report.txt
"""
import argparse
import io
import json
import os
import re
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from scripts.deinterleave_gazette import deinterleave, is_garbage  # noqa: E402
from scripts.docx_to_articles import articles_from_paragraphs  # noqa: E402
from scripts.resource_from_clean_files import (  # noqa: E402
    ART_RE, V1_MAX_ID, clean, cn2int, norm_title)
from scripts.zhuma_to_articles import strip_prefix  # noqa: E402

ID_BASE = 997000  # 命名空间：995000 干净源件段、996000 去交错段
COVERAGE_MIN = 0.90
DROP_MAX_RATIO = 0.05
NUM_RE = re.compile(u"^第([一二三四五六七八九十百千零〇]+)条")


# 公式/表格尾：有些条以算式收尾（如「…计算公式如下：抵免限额＝…应纳税所得总额」），
# 句末没有标点，但正文没坏。「结尾非句末标点 + 含算式符号」按软保留处理。
FORMULA_RE = re.compile(u"[＝=×÷%％＋+]")


def keep_article(art):
    """逐条门禁 + 公式尾豁免 → (是否保留, 拒收原因, 是否软保留)。"""
    why = clean(art)
    if why is None:
        return True, None, False
    if why.startswith(u"结尾非句末标点") and FORMULA_RE.search(art["text"]):
        return True, None, True
    return False, why, False


def quality(lines):
    """按行切条 + 逐条门禁，返回质量指标（供选源与记账）。"""
    arts, _ = articles_from_paragraphs(lines)
    kept = []
    dropped = 0
    soft = 0
    for a in arts:
        ok, _why, is_soft = keep_article(a)
        if ok:
            kept.append(a)
            if is_soft:
                soft += 1
        else:
            dropped += 1
    nums = []
    for a in kept:
        m = NUM_RE.match(a["num"])
        if m:
            nums.append(cn2int(m.group(1)))
    mono = all(nums[i] < nums[i + 1] for i in range(len(nums) - 1))
    complete = bool(nums) and nums == list(range(1, len(nums) + 1))
    total_chars = sum(len(l) for l in lines)
    cov = sum(len(a["text"]) for a in kept) / float(total_chars or 1)
    return {"arts": arts, "kept": kept, "nums": nums, "mono": mono,
            "complete": complete, "cov": cov, "dropped": dropped, "soft": soft,
            "cid": sum(1 for a in kept if u"(cid:" in a["text"])}


def passes(q):
    if len(q["kept"]) < 2 or not q["mono"]:
        return False
    if q["cov"] < COVERAGE_MIN:
        return False
    if q["dropped"] > max(2, int(DROP_MAX_RATIO * len(q["arts"]))):
        return False
    return True


# 零宽字符 / 不换行空格：竹马数据里若干条正文尾部带 U+200B，会把「以句末标点
# 结尾」这条判据顶掉（正文本身没问题）。归一化只删不可见字符，不动语义。
ZW_RE = re.compile(u"[\u200b\u200c\u200d\ufeff]")


def normalize_line(s):
    return ZW_RE.sub(u"", s).replace(u"\u00a0", u" ").strip()


def ldb_lines(cont):
    return [l.strip() for l in (cont or u"").split(u"\n") if l.strip()]


# —— 整篇单元（小体量批复/规定：本来就没有「第X条」结构，整篇就是一个引用单位）——
WHOLE_MIN, WHOLE_MAX = 150, 3000
TITLE_HINT = re.compile(u"批复|规定|解释|决定|办法|纪要")
JUNK_CHARS = re.compile(u"[\ue000-\uf8ff\U00010000-\U0010ffff]")


def whole_document(lines, law):
    """文档没有条号结构时的整篇单元：从标题行起取到文末，剔掉公报页眉页脚。

    适用前提（严格）：正文 150~3000 字、无 PUA/私用区装饰字符、标题行能在文首
    找到。不要拿它当「解析失败的兜底」——那就是把乱文原样喂给产品。
    """
    body = [l for l in lines if not JUNK_CHARS.search(l)]
    text = u"".join(body)
    if not (WHOLE_MIN <= len(text) <= WHOLE_MAX):
        return None
    start = None
    for i, l in enumerate(body[:40]):
        if not TITLE_HINT.search(l):
            continue
        if re.search(u"已于|现予公布|起施行|通过）", l) or l.endswith(u"。"):
            continue        # 那是公报「公告」里的版本说明行，不是文书标题
        start = i
        break
    if start is None:
        return None
    # 往前收标题块的其余行（短、不以句末标点收尾）——标题常被拆成两三行
    while start > 0:
        prev = body[start - 1]
        if len(prev) <= 40 and prev[-1] not in u"。；！？”』）":
            start -= 1
        else:
            break
    unit = u"".join(body[start:])
    if len(unit) < WHOLE_MIN or unit[-1] not in u"。；！？”』）":
        return None
    return unit


def whole_document_deinter(cont, law):
    """整篇单元·去交错路线：先把双栏交错还原、剔掉公报页眉页脚，再取整篇。

    只在 raw 行取不出整篇单元时才用（本条路线的前提是正文确实交错了）。
    """
    lines = [l for l in deinterleave(cont) if not is_garbage(l)]
    return whole_document(lines, law)


def main():
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    ap = argparse.ArgumentParser(description=u"本地源按证据补齐仍为页块的法")
    ap.add_argument("--ldb", required=True, help=u"legal.db（只读）")
    ap.add_argument("--corpus", required=True, help=u"现有语料（取待修法清单）")
    ap.add_argument("--zhuma", default="data/flk/tmp/zhuma_laws_raw.json",
                    help=u"竹马 raw json（缺省 data/flk/tmp/zhuma_laws_raw.json）")
    ap.add_argument("--covered", action="append", default=[],
                    help=u"已覆盖的条级片段（金样/跳过），可重复")
    ap.add_argument("--out", default="", help=u"条级片段输出 jsonl")
    ap.add_argument("--report", required=True, help=u"文本报告输出")
    ap.add_argument("--validate", action="store_true", help=u"对 covered 的法逐条比对金样")
    args = ap.parse_args()

    # 1) 待修法：v1 段里页块占比 ≥ 0.5 的法，去掉已被 --covered 覆盖的
    v1 = {}
    pb = {}
    for line in io.open(args.corpus, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        if r.get("meta") or r["id"] >= V1_MAX_ID:
            continue
        v1.setdefault(r["law"], 0)
        v1[r["law"]] += 1
        if len(ART_RE.findall(r["text"])) > 1:
            pb[r["law"]] = pb.get(r["law"], 0) + 1
    broken = sorted(l for l in v1 if pb.get(l, 0) / float(v1[l]) >= 0.5)
    covered = {}
    for path in args.covered:
        if path and os.path.exists(path):
            for line in io.open(path, encoding="utf-8"):
                line = line.strip()
                if line:
                    f = json.loads(line)
                    covered.setdefault(f["law"], []).append(f)
    todo = [l for l in broken if l not in covered]

    # 2) 源：legal.db 文档 + 竹马 raw
    conn = sqlite3.connect(args.ldb)
    docs = {}
    for did, title, cont in conn.execute("select id,title,content from documents"):
        docs.setdefault(norm_title(title), (did, title, cont or u""))
    conn.close()
    zhuma = {}
    if os.path.exists(args.zhuma):
        raw = json.load(io.open(args.zhuma, encoding="utf-8"))
        for lid, title in raw["meta"].items():
            chs = raw["laws"].get(lid) or []
            paras = []
            for ch in chs:
                paras.extend(p.strip() for p in (ch.get("content") or "").split("\n") if p.strip())
            if paras:
                zhuma.setdefault(strip_prefix(title), (title, paras))

    def candidates(law):
        """按优先级给出 (源名, 行序列) 候选。"""
        out = []
        key = strip_prefix(law)
        if key in zhuma:
            out.append((u"zhuma", [normalize_line(p) for p in zhuma[key][1]]))
        doc = docs.get(norm_title(law))
        if doc:
            out.append((u"ldb-raw", ldb_lines(doc[2])))
            out.append((u"ldb-deinter", [normalize_line(l) for l in deinterleave(doc[2])]))
        return out

    frags = []
    report = []
    stat = {"picked": {}, "left": [], "laws": 0}
    vstat = {}
    for law in todo + ([] if not args.validate else []):
        cands = candidates(law)
        if not cands:
            stat["left"].append((law, u"无任何本地源"))
            report.append(u"%s\t无任何本地源" % law)
            continue
        chosen = None
        line_note = []
        for name, lines in cands:
            q = quality(lines)
            line_note.append(u"%s(条%d,单调%s,全序%s,cov%.2f,丢%d)" % (
                name, len(q["kept"]), q["mono"], q["complete"], q["cov"], q["dropped"]))
            if chosen is None and passes(q):
                chosen = (name, lines, q)
        if chosen is None:
            # 整篇单元回退：小体量批复/规定本来就没有「第X条」结构，
            # 与其让它整部缺席，不如如实作为一个引用单位（unit=whole-document）。
            doc = docs.get(norm_title(law))
            unit = whole_document(ldb_lines(doc[2]), law) if doc else None
            unit_src = u"legal.db 公报文本·整篇单元"
            if unit is None and doc:
                unit = whole_document_deinter(doc[2], law)
                unit_src = u"legal.db 公报文本·去交错整篇单元"
            if unit:
                stat["laws"] += 1
                stat["picked"][u"whole-document"] = stat["picked"].get(u"whole-document", 0) + 1
                report.append(u"%s\t用 整篇单元（%d 字）\t无条号结构，按整篇一个单位入库\t| %s"
                              % (law, len(unit), u" ".join(line_note)))
                frags.append({
                    "id": 0, "law": law, "num": u"全文", "text": unit,
                    "meta": {"source": unit_src,
                             "text_source": ("local-ldb-whole-deinter"
                                             if u"去交错" in unit_src else "local-ldb-whole"),
                             "unit": "whole-document",
                             "numbering": u"全文"},
                })
                continue
            stat["left"].append((law, u"；".join(line_note)))
            report.append(u"%s\t%s\t仍缺源" % (law, u" ".join(line_note)))
            continue
        name, lines, q = chosen
        stat["laws"] += 1
        stat["picked"][name] = stat["picked"].get(name, 0) + 1
        report.append(u"%s\t用 %s\t条=%d\t全序1..N=%s\t覆盖=%.2f\t丢弃=%d\tcid残留=%d\t| %s" % (
            law, name, len(q["kept"]), q["complete"], q["cov"], q["dropped"], q["cid"],
            u" ".join(line_note)))
        for a in q["kept"]:
            frags.append({
                "id": 0, "law": law, "num": a["num"], "text": a["text"],
                "meta": {"source": u"本地源·%s（按行重切）" % name,
                         "text_source": "local-%s" % name,
                         "numbering": u"第X条"},
            })

    # 3) 金样校验：对 covered 里的法，看还原结果与干净源件是否逐字一致
    if args.validate and covered:
        import collections
        agg = collections.defaultdict(lambda: {"gold": 0, "hit": 0, "laws": 0, "full": 0})
        for law, golds in covered.items():
            g = dict((f["num"], f["text"]) for f in golds)
            agg["_all"]["gold"] += len(g)
            for name, lines in candidates(law):
                q = quality(lines)
                hit = 0
                for a in q["kept"]:
                    t = g.get(a["num"])
                    if t is not None and u"".join(a["text"].split()) == u"".join(t.split()):
                        hit += 1
                s = agg[name]
                s["laws"] += 1
                s["gold"] += len(g)
                s["hit"] += hit
                if hit == len(g):
                    s["full"] += 1
        vstat = agg

    if args.out and frags:
        for i, f in enumerate(frags, 1):
            f["id"] = ID_BASE + i
        with io.open(args.out, "w", encoding="utf-8", newline="\n") as fh:
            for f in frags:
                fh.write(json.dumps(f, ensure_ascii=False) + "\n")

    head = [
        u"本地源补齐报告（仍为页块的法）",
        u"待修法（v1 页块占比 ≥ 0.5 且未被干净源件覆盖）：%d 部" % len(todo),
        u"本次补齐：%d 部 / %d 条" % (stat["laws"], len(frags)),
        u"仍缺源：%d 部" % len(stat["left"]),
        u"各源命中：" + (u"，".join(u"%s %d 部" % kv for kv in sorted(stat["picked"].items())) or u"无"),
        u"产出条级行：%d" % len(frags),
    ]
    if vstat:
        head += [u"", u"— 金样校验（与干净源件重采结果逐条比对）—"]
        for name in sorted(k for k in vstat if k != "_all"):
            s = vstat[name]
            head.append(u"%s：法 %d，金样条 %d，逐字命中 %d（%.1f%%），整法全中 %d"
                        % (name, s["laws"], s["gold"], s["hit"],
                           100.0 * s["hit"] / max(1, s["gold"]), s["full"]))
    with io.open(args.report, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(u"\n".join(head + [u""] + report) + u"\n")
    print(u"待修 %d 部；补齐 %d 部 / %d 条；仍缺源 %d 部" % (
        len(todo), stat["laws"], len(frags), len(stat["left"])))
    if vstat:
        for name in sorted(k for k in vstat if k != "_all"):
            s = vstat[name]
            print(u"金样 %s：逐字命中 %d / %d（%.1f%%），整法全中 %d 部" % (
                name, s["hit"], s["gold"], 100.0 * s["hit"] / max(1, s["gold"]), s["full"]))
    print(u"→ %s" % args.report)


if __name__ == "__main__":
    main()
