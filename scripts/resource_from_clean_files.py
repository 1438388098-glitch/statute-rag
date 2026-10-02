# -*- coding: utf-8 -*-
"""从「干净本地源」重采 v1 页块行为条级正文。

背景（已查实）：语料 corpus_v3.jsonl 里 meta 为空的 v1 段（16,523 行 / 244 部法）
全是**页块行**（每行条数中位数 10）、相邻行还是**重叠约 90% 的滑窗**，其中一部分
是双栏公报 PDF 被 pdfminer 普通 extract_text 读出的**交错乱文**（用户报障：
id=70879 最高法关于适用民法典侵权责任编的解释（一）第十九条）。

本脚本用本地已有的干净源 `rag-server/documents/`（148 docx + 15 md，标准法条体例）
把能对上的那一批法**整部重采为条级行**，并做逐条/逐法校验与版本对照；对不上的
（版本过时、无源件）如实列进缺口清单，不硬凑。

用法：
  python scripts/resource_from_clean_files.py \
      --docs D:/Claudeworkspace/rag-server/documents \
      --ldb  D:/Claudeworkspace/legal-wisdom-app/data/database/legal.db \
      --corpus data/corpus_v3.jsonl \
      --out data/flk/fragments/clean_source.jsonl \
      --map data/flk/tmp/clean_source_row_map.json \
      --report data/flk/tmp/clean_source_report.txt \
      --md docs/resource-clean-source.md
"""
import argparse
import io
import json
import os
import re
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from scripts.docx_to_articles import (  # noqa: E402
    articles_from_paragraphs,
    best_effort_articles,
    docx_paragraphs,
)

ID_BASE = 995000
V1_MAX_ID = 910000  # v1 段 id 上界：v2 官方条级行从 910001 起（民法典…行政处罚法 六部）
SENT_END = u"。；：！？”』）"
# 行内空格密度：实测 v1 页块行（14k 行）中位 0.0333 / p90 0.0673，干净条级行
# （7.3k 行）中位 0.0094 / p90 0.0213 / 最大 0.0625。两者分布**重叠**，所以这里
# 只当一个「只抓最离谱那批」的保守门（阈值取在干净行最大值之上），真正的质量依据
# 是「源件本身的行首条号是否单调」与「逐法条号是否全序递增」。
SPACE_DENSITY_MAX = 0.065
YEAR_RE = re.compile(u"(19|20)\\d{2}")
ART_RE = re.compile(u"第[一二三四五六七八九十百千零〇]+条")


def norm_title(t):
    """法名规范化：去日期前缀/扩展名/括注/空白与书名号，去「中华人民共和国」前缀。"""
    t = (t or "").strip()
    t = re.sub(u"^\\d{4}-\\d{2}-\\d{2}[_ ]?", u"", t)
    t = t.rsplit(".", 1)[0]
    t = re.sub(u"[（(][^（()）]*[)）]", u"", t)
    t = re.sub(u"[\\s\u3000《》\"'“”]", u"", t)
    t = re.sub(u"^中华人民共和国", u"", t)
    return t


def read_md(path):
    """md 源件 → 段落序列（丢掉标题行、引用块与 HTML 注释里的元信息）。

    md 体例：条号被加粗（`**第一条**　正文`），章标题以 `#` 开头，元信息在
    `<!-- … -->` 或 `>` 引用块里。
    """
    paras = []
    for line in io.open(path, encoding="utf-8"):
        s = line.strip()
        if not s or s.startswith("#") or s.startswith("<!--") or s.startswith("-->") or s.startswith(">"):
            continue
        s = s.replace(u"**", u"").replace(u"*", u"")
        s = s.replace(u"\u3000", u" ")
        s = re.sub(u"\\s+", u" ", s).strip()
        if s:
            paras.append(s)
    return paras


def read_paras(path):
    if path.lower().endswith(".docx"):
        return docx_paragraphs(path)
    return read_md(path)


def source_year(paras, fname):
    """取源件声明的年份（头部元信息 + 文件名日期）里的最大值。"""
    years = [int(m.group(0)) for m in YEAR_RE.finditer(fname)]
    for p in paras[:4]:
        years += [int(m.group(0)) for m in YEAR_RE.finditer(p)]
    return max(years) if years else None


def split_articles(law, paras):
    arts, _ = articles_from_paragraphs(paras)
    if len(arts) >= 2:
        return arts, u"第X条"
    arts, _, label = best_effort_articles(paras)
    return arts, label


def clean(art):
    """逐条硬校验：返回 None 表示通过，否则返回拒收原因。"""
    t = art["text"]
    if not t or len(t) < 6:
        return u"过短"
    if not t.startswith(art["num"]):
        return u"不以条号开头"
    if t[-1] not in SENT_END:
        return u"结尾非句末标点(%s)" % t[-1]
    if t.count(u" ") / float(len(t)) > SPACE_DENSITY_MAX:
        return u"行内空格密度过高(疑交错)"
    if u"中华人民共和国最高人民法院公报" in t or re.search(u"^\\s*-\\s*\\d+\\s*-", t):
        return u"夹带公报排版垃圾"
    return None


def cn2int(s):
    dig = dict(zip(u"零一二三四五六七八九", range(10)))
    unit = {u"十": 10, u"百": 100, u"千": 1000}
    section = 0
    number = 0
    for ch in s:
        if ch in dig:
            number = dig[ch]
        elif ch in unit:
            number = number or 1
            section += number * unit[ch]
            number = 0
    return section + number


def main():
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    ap = argparse.ArgumentParser(description=u"从干净本地源重采 v1 页块行为条级行")
    ap.add_argument("--docs", required=True, help=u"干净源目录（docx/md）")
    ap.add_argument("--ldb", required=True, help=u"legal.db（只读，取 publish_date）")
    ap.add_argument("--corpus", required=True, help=u"现有语料（取 v1 行与法名口径）")
    ap.add_argument("--out", required=True, help=u"条级片段输出 jsonl")
    ap.add_argument("--map", required=True, help=u"原行 id → 新条 id 映射输出")
    ap.add_argument("--report", required=True, help=u"文本报告输出")
    ap.add_argument("--md", default="", help=u"如需，另写一份 markdown 报告")
    args = ap.parse_args()

    # 1) 语料里的 v1 行（meta 为空且 id < 910000，即 v3 之前的老段），按法名归组
    #    注：v2 六部官方条级行的 meta 也是 null，靠 id 段区分（910001 起）
    v1_laws = {}
    v1_pageblock = {}
    v1_sample = {}
    for line in io.open(args.corpus, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        if r.get("meta") or r["id"] >= V1_MAX_ID:
            continue
        v1_laws.setdefault(r["law"], []).append(r["id"])
        if len(ART_RE.findall(r["text"])) > 1:
            v1_pageblock[r["law"]] = v1_pageblock.get(r["law"], 0) + 1
            v1_sample.setdefault(r["law"], r["text"])
    print(u"语料 v1 段：%d 部法 / %d 行" % (len(v1_laws), sum(len(v) for v in v1_laws.values())))

    # 2) legal.db 的公布日期（版本对照用）
    conn = sqlite3.connect(args.ldb)
    ldb = {}
    for title, pub in conn.execute("select title, publish_date from documents"):
        ldb[norm_title(title)] = (title, pub or "")
    conn.close()

    # 3) 源件索引
    src = {}
    for fn in sorted(os.listdir(args.docs)):
        if fn.lower().endswith((".docx", ".md")):
            src.setdefault(norm_title(fn), []).append(fn)

    frags = []
    row_map = {}
    report = []
    used_src = set()
    stale = []
    no_src = []
    already_ok = []
    parse_fail = []
    per_law = []

    for law in sorted(v1_laws):
        # 已是条级的法（v1 行里页块占比 < 0.5，多为小体量批复/规定）无需重采
        ratio = v1_pageblock.get(law, 0) / float(len(v1_laws[law]))
        if ratio < 0.5:
            already_ok.append((law, len(v1_laws[law]), ratio))
            continue
        key = norm_title(law)
        cands = src.get(key) or []
        if not cands:
            no_src.append((law, len(v1_laws[law])))
            continue
        fn = cands[0]
        fpath = os.path.join(args.docs, fn)
        try:
            paras = read_paras(fpath)
        except Exception as e:
            parse_fail.append((law, fn, str(e)))
            continue
        sy = source_year(paras, fn)
        ldb_title, ldb_pub = ldb.get(key, ("", ""))
        ly = int(ldb_pub[:4]) if ldb_pub[:4].isdigit() else None
        if sy and ly and sy < ly:
            stale.append((law, fn, sy, ldb_pub))
            continue
        arts, label = split_articles(law, paras)
        kept = []
        dropped = []
        for a in arts:
            why = clean(a)
            if why:
                dropped.append((a["num"], why))
            else:
                kept.append(a)
        nums = [cn2int(re.match(u"^第([一二三四五六七八九十百千零〇]+)条", a["num"]).group(1)) for a in kept]
        mono = all(nums[i] < nums[i + 1] for i in range(len(nums) - 1))
        gaps = []
        if nums:
            for n in range(1, max(nums) + 1):
                if n not in nums:
                    gaps.append(n)
        for a in kept:
            frags.append({
                "id": 0,
                "law": law,
                "num": a["num"],
                "text": a["text"],
                "meta": {
                    "source": u"rag-server/documents 干净源件",
                    "text_source": "clean-local-file",
                    "file": fn,
                    "file_year": sy,
                    "ldb_publish_date": ldb_pub,
                    "numbering": label,
                    "origin_row_ids": list(v1_laws[law]),
                },
            })
        used_src.add(fn)
        per_law.append((law, fn, sy, ldb_pub, len(kept), len(dropped), mono, len(gaps)))
        report.append(u"%s\t%s\t源件年份=%s\tldb=%s\t条=%d\t丢弃=%d\t单调=%s\t缺号=%d" % (
            law, fn, sy, ldb_pub, len(kept), len(dropped), mono, len(gaps)))

    for i, f in enumerate(frags, 1):
        f["id"] = ID_BASE + i
    for f in frags:
        for rid in f["meta"]["origin_row_ids"]:
            row_map.setdefault(str(rid), []).append(f["id"])

    with io.open(args.out, "w", encoding="utf-8", newline="\n") as fh:
        for f in frags:
            fh.write(json.dumps(f, ensure_ascii=False) + "\n")
    with io.open(args.map, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(row_map, ensure_ascii=False))

    head = [
        u"干净本地源重采报告",
        u"源件目录：%s" % args.docs,
        u"v1 待修（页块为主）法数：%d" % (len(v1_laws) - len(already_ok)),
        u"覆盖法数：%d" % len(per_law),
        u"产出条数：%d" % len(frags),
        u"因版本过时未采用：%d 部" % len(stale),
        u"无源件：%d 部" % len(no_src),
        u"v1 本身已是条级、无需重采：%d 部" % len(already_ok),
        u"解析失败：%d 部" % len(parse_fail),
        u"",
        u"— 逐法 —",
    ]
    tail = [u"", u"— 因版本过时未采用（源件比 legal.db 旧）—"]
    tail += [u"%s\t%s\t源件年份 %s < ldb %s" % s for s in stale]
    tail += [u"", u"— 无源件（需另找源）—"]
    tail += [u"%s\tv1 行数 %d" % s for s in no_src]
    tail += [u"", u"— v1 本身已是条级（无需重采，多为小体量批复/规定）—"]
    tail += [u"%s\tv1 行数 %d\t页块比例 %.2f" % s for s in already_ok]
    tail += [u"", u"— 解析失败 —"]
    tail += [u"%s\t%s\t%s" % s for s in parse_fail]
    with io.open(args.report, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(u"\n".join(head + report + tail) + u"\n")

    print(u"产出 %d 条；覆盖 %d 部法；版本过时未采用 %d；无源件 %d；已是条级跳过 %d；解析失败 %d" % (
        len(frags), len(per_law), len(stale), len(no_src), len(already_ok), len(parse_fail)))
    print(u"→ %s" % args.out)

    if args.md:
        lines = [
            u"# 干净本地源 → 条级语料（去交错 / 去页块 / 去滑窗冗余）",
            u"",
            u"> 用户报障：语料里 61% 的行不是「条」而是公报**页块**（每行条数中位数 10），"
            u"相邻行还是重叠约 90% 的滑窗，其中双栏公报 PDF 被 pdfminer 普通 `extract_text` "
            u"读出的那部分正文**左右栏交错**（例：`id=70879` 最高法关于适用民法典侵权责任编的解释（一）第十九条）。",
            u"",
            u"本报告记录用**本地干净源**（`rag-server/documents/`，148 docx + 15 md，标准法条体例）"
            u"把能对上的法整部重采为条级行的结果；源件版本比库里旧的**一律不用**，对不上的如实列入缺口。",
            u"",
            u"## 总账",
            u"",
            u"| 项 | 数 |",
            u"|---|---|",
            u"| v1 待修（页块为主）法数 | %d |" % (len(v1_laws) - len(already_ok)),
            u"| 本次重采覆盖法数 | %d |" % len(per_law),
            u"| 产出条级行 | %d |" % len(frags),
            u"| v1 本身已是条级（跳过） | %d |" % len(already_ok),
            u"| 源件版本过时（拒绝使用） | %d |" % len(stale),
            u"| 无源件（需另找源） | %d |" % len(no_src),
            u"| 解析失败 | %d |" % len(parse_fail),
            u"| 逐法条号单调 | %d / %d |" % (sum(1 for x in per_law if x[6]), len(per_law)),
            u"",
            u"## 前后对照（原文页块 → 重建条级）",
            u"",
        ]
        for law, fn, sy, ldb_pub, kept, dropped, mono, gaps in per_law[:10]:
            same = [f for f in frags if f["law"] == law]
            lines.append(u"### %s" % law)
            lines.append(u"")
            lines.append(u"- 源件：`%s`（源件年份 %s；legal.db 公布日期 %s）" % (fn, sy, ldb_pub))
            lines.append(u"- 产出：%d 条（丢弃 %d，条号单调 %s）" % (kept, dropped, mono))
            lines.append(u"")
            lines.append(u"**改造前（v1 页块行，截断 300 字）**")
            lines.append(u"")
            lines.append(u"```")
            lines.append((v1_sample.get(law) or u"")[:300])
            lines.append(u"```")
            lines.append(u"")
            lines.append(u"**改造后（条级，前 3 条）**")
            lines.append(u"")
            lines.append(u"```")
            for f in same[:3]:
                lines.append(f["text"][:300])
            lines.append(u"```")
            lines.append(u"")
        lines.append(u"## 缺口清单")
        lines.append(u"")
        lines.append(u"### 源件版本过时，未采用（%d 部）" % len(stale))
        lines.append(u"")
        for law, fn, sy, ldb_pub in stale:
            lines.append(u"- %s：源件 `%s`（%s）早于库内版本（%s）" % (law, fn, sy, ldb_pub))
        lines.append(u"")
        lines.append(u"### 无源件（%d 部，需 flk 官方库等另找源）" % len(no_src))
        lines.append(u"")
        for law, n in no_src:
            lines.append(u"- %s（v1 行数 %d）" % (law, n))
        lines.append(u"")
        with io.open(args.md, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(u"\n".join(lines) + u"\n")
        print(u"→ %s" % args.md)


if __name__ == "__main__":
    main()
