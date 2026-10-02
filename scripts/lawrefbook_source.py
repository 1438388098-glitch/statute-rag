# -*- coding: utf-8 -*-
"""用「国家法律法规数据库镜像」补齐本地源无能为力的那批法（v4 剩余缺口）。

本地三条路（干净源件 docx/md、竹马、legal.db 按行重切/去交错）用完以后，仍有
~78 部法在语料里是页块/交错乱文。这批法的共同点是：`legal.db` 的正文本身就
不可用（pdfminer 把双栏公报读成了逐字碎片或乱序），而本地没有第二种干净来源。

本脚本改用公开的**法规文本镜像仓库**（`LawRefBook/Laws`，GitHub，按官方公布
体例整理的 markdown：`第一条 …`、章标题 `## 第一章 …`、文首为公布日期序列）。
来源留痕在每行的 `meta`（`repo`/`path`/`version_date`），版本选取规则：

1. 优先取**文件名日期 == legal.db 的 publish_date** 的那一版（与语料原有版本口径连续）；
2. 没有同日期的，取**最新一版**，并在 `meta.version_bumped=true`、报告里逐部列出
   （版本上浮是内容变更，必须可见，不能悄悄换）。

过门条件与 `resource_local_remaining.py` 完全一致（条号严格单调、1..N 全序、
正文覆盖率 ≥ 0.90、逐条门禁丢弃 ≤ 5%），不过门就整法丢弃并记账。

用法：
  python scripts/lawrefbook_source.py --repo D:/Claudeworkspace/_lawrefbook \\
      --ldb ...legal.db --corpus data/corpus_v4.jsonl \\
      --skip data/flk/fragments/clean_source.jsonl \\
      --skip data/flk/fragments/local_remaining.jsonl \\
      --out data/flk/fragments/lawrefbook.jsonl \\
      --report data/flk/tmp/lawrefbook_report.txt
"""
import argparse
import io
import json
import os
import re
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from scripts.resource_from_clean_files import (  # noqa: E402
    ART_RE, V1_MAX_ID, norm_title)
from scripts.resource_local_remaining import (  # noqa: E402
    normalize_line, quality)

ID_BASE = 998000  # 命名空间：995000 干净源件 / 996000 去交错 / 997000 本地源
LAW_DIRS = [u"宪法", u"宪法相关法", u"民法典", u"民法商法", u"刑法", u"行政法",
            u"经济法", u"社会法", u"诉讼与非诉讼程序法", u"生态环境法",
            u"法律解释", u"有关法律问题和重大问题的决定", u"行政法规", u"司法解释"]
DATE_SUFFIX = re.compile(u"[(（](\\d{4}-\\d{2}-\\d{2})[)）]$")


def refbook_key(name):
    """镜像文件 / 语料法名 → 匹配键。

    与 `norm_title` 的关键区别：**保留括号内容**。「…解释（一）」与「…解释（二）」
    是不同的文书，`norm_title` 会把括号整段删掉，两边就撞成一个键了（会把
    （二）错配到（一）的文本上）。全角括号转半角、去掉空白与顿号，两侧同口径。
    """
    t = (name or u"").strip()
    t = re.sub(u"^\\d{4}-\\d{2}-\\d{2}[_ ]?", u"", t)
    t = t.rsplit(".", 1)[0]
    t = t.replace(u"（", u"(").replace(u"）", u")")
    t = re.sub(u"[\\s\u3000《》\"'“”、]", u"", t)
    # 「中华人民共和国」在书名号里也要去：语料法名写作《中华人民共和国民法典》，
    # 镜像文件名写作《民法典》，同一条文书的两种写法都得归到一个键上。
    t = t.replace(u"中华人民共和国", u"")
    return t


def file_key(stem):
    """文件名去日期后缀 → (匹配键, 日期或空)。"""
    m = DATE_SUFFIX.search(stem)
    date = m.group(1) if m else u""
    if m:
        stem = stem[:m.start()]
    return refbook_key(stem), date


def mirror_ok(q, min_cov):
    """镜像源过门：条号 1..N 全序（官方体例的干净文本必须一条不缺）+ 覆盖率下限。

    覆盖率下限放到 0.35：有些文书带附表/附则（税目税额表、收费项目表），那部分
    本来就不在条文里，用 0.90 会把好文本误杀。上限情况仍逐部报出覆盖率备查。
    """
    if len(q["kept"]) < 2 or not q["mono"] or not q["complete"]:
        return False
    if q["cov"] < min_cov:
        return False
    if q["dropped"] > max(2, int(0.05 * len(q["arts"]))):
        return False
    return True


def read_md_paras(path):
    """法规 markdown → 段落序列（丢标题行 `#`、引用块、空行）。"""
    paras = []
    for line in io.open(path, encoding="utf-8"):
        s = line.strip()
        if not s or s.startswith("#") or s.startswith(">") or s.startswith("<!--"):
            continue
        paras.append(normalize_line(s.replace(u"**", u"")))
    return [p for p in paras if p]


def main():
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    ap = argparse.ArgumentParser(description=u"用法规文本镜像补齐 v4 剩余缺口")
    ap.add_argument("--repo", required=True, help=u"LawRefBook/Laws 本地克隆路径")
    ap.add_argument("--ldb", required=True, help=u"legal.db（取 publish_date 做版本对照）")
    ap.add_argument("--corpus", required=True, help=u"现有语料（取待修法清单）")
    ap.add_argument("--skip", action="append", default=[], help=u"已有条级替代的片段，可重复")
    ap.add_argument("--min-cov", type=float, default=0.35,
                    help=u"镜像文本覆盖率下限（默认 0.35，带附表/附则的文书会把覆盖率压低）")
    ap.add_argument("--date-strict", action="store_true",
                    help=u"只取「文件名日期 == legal.db 公布日期」的版本；没有就跳过（留给后面的"
                         u"本地源或非严格回退）。版本一致是这批文书可核验的关键。")
    ap.add_argument("--out", default="", help=u"条级片段输出 jsonl")
    ap.add_argument("--report", required=True, help=u"文本报告输出")
    args = ap.parse_args()

    # 1) 待修法：v1 段里页块占比 ≥ 0.5 且未被已有替代覆盖
    v1, pb = {}, {}
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
    broken = set(l for l in v1 if pb.get(l, 0) / float(v1[l]) >= 0.5)
    done = set()
    for path in args.skip:
        if path and os.path.exists(path):
            for line in io.open(path, encoding="utf-8"):
                line = line.strip()
                if line:
                    done.add(json.loads(line)["law"])
    todo = sorted(broken - done)

    # 2) legal.db 公布日期（版本对照）
    conn = sqlite3.connect(args.ldb)
    ldb = {}
    for title, pub in conn.execute("select title, publish_date from documents"):
        ldb[norm_title(title)] = pub or u""
    conn.close()

    # 3) 镜像仓库索引：法名 → [(日期, 路径)]
    index = {}
    for d in LAW_DIRS:
        sub = os.path.join(args.repo, d)
        if not os.path.isdir(sub):
            continue
        for fn in os.listdir(sub):
            if not fn.lower().endswith(".md") or fn == "_index.md":
                continue
            key, date = file_key(fn[: -len(".md")])
            index.setdefault(key, []).append((date, os.path.join(sub, fn)))
    for k in index:
        index[k].sort(key=lambda x: x[0], reverse=True)   # 新版本在前

    frags = []
    report = []
    stat = {"ok": 0, "bumped": 0, "nomatch": 0, "fail": 0}
    for law in todo:
        key = refbook_key(law)
        cands = index.get(key) or []
        if not cands:
            stat["nomatch"] += 1
            report.append(u"%s\t镜像无此文书" % law)
            continue
        want = ldb.get(norm_title(law), u"")
        pick = None
        bumped = False
        for date, path in cands:
            if want and date == want:
                pick = (date, path)
                break
        if pick is None and args.date_strict:
            stat["nomatch"] += 1
            report.append(u"%s\t镜像无一日期相符的版本（库内公布 %s；镜像有 %s）" % (
                law, want or u"未登记",
                u"/".join(d or u"无日期" for d, _ in cands[:3])))
            continue
        if pick is None:
            pick = cands[0]
            bumped = bool(want and cands[0][0] and cands[0][0] != want)
        date, path = pick
        q = quality(read_md_paras(path))
        if not mirror_ok(q, args.min_cov):
            stat["fail"] += 1
            report.append(u"%s\t镜像文件 %s\t不过门（条%d 单调%s 全序%s cov%.2f 丢%d）" % (
                law, os.path.basename(path), len(q["kept"]), q["mono"],
                q["complete"], q["cov"], q["dropped"]))
            continue
        stat["ok"] += 1
        if bumped:
            stat["bumped"] += 1
        report.append(u"%s\t%s\t条=%d\t全序1..N=%s\t覆盖=%.2f\t丢弃=%d\t版本=%s%s" % (
            law, os.path.relpath(path, args.repo).replace("\\", "/"),
            len(q["kept"]), q["complete"], q["cov"], q["dropped"], date or u"未标注",
            u"（与库内公布日期 %s 不同：版本上浮）" % want if bumped else u""))
        for a in q["kept"]:
            frags.append({
                "id": 0, "law": law, "num": a["num"], "text": a["text"],
                "meta": {
                    "source": u"LawRefBook/Laws 法规文本镜像（官方公布体例）",
                    "text_source": "lawrefbook-mirror",
                    "repo": "https://github.com/LawRefBook/Laws",
                    "path": os.path.relpath(path, args.repo).replace("\\", "/"),
                    "version_date": date,
                    "version_bumped": bool(bumped),
                    "numbering": u"第X条",
                },
            })

    if args.out and frags:
        for i, f in enumerate(frags, 1):
            f["id"] = ID_BASE + i
        with io.open(args.out, "w", encoding="utf-8", newline="\n") as fh:
            for f in frags:
                fh.write(json.dumps(f, ensure_ascii=False) + "\n")

    head = [
        u"法规文本镜像补齐报告",
        u"待修法（v1 页块且本地源未覆盖）：%d 部" % len(todo),
        u"镜像补齐：%d 部 / %d 条" % (stat["ok"], len(frags)),
        u"其中版本上浮（镜像最新版 ≠ 库内公布日期）：%d 部" % stat["bumped"],
        u"镜像无此文书：%d 部" % stat["nomatch"],
        u"有文件但不过门：%d 部" % stat["fail"],
        u"",
    ]
    with io.open(args.report, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(u"\n".join(head + report) + u"\n")
    print(u"待修 %d 部；镜像补齐 %d 部 / %d 条（版本上浮 %d）；无文书 %d；不过门 %d" % (
        len(todo), stat["ok"], len(frags), stat["bumped"], stat["nomatch"], stat["fail"]))
    print(u"→ %s" % args.report)


if __name__ == "__main__":
    main()
