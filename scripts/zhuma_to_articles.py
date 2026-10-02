# -*- coding: utf-8 -*-
"""把竹马（zhumavip.com）电子法条汇编转成条级 JSONL 片段。

输入 data/flk/tmp/zhuma_laws_raw.json 由浏览器辅助获取：电子法条阅读器
在登录墙后，接口为 POST /java-api/api/laws/list（目录树）与
POST /java-api/api/laws/info（按法律 id 返回全部章节全文），抓取时以
businessTypeId=104（法考）为口径。原始 JSON 转存方法同 flk：页面内
fetch → 分块 base64 → 本地解码（见 data/flk/MANIFEST.md）。

与 docx_to_articles 共用切条状态机（条号单调递增防误切、之N后缀、
编章节标题丢弃）；本脚本额外处理竹马目录结构：
- 学科→法律→章节三层树，同一法律可能在多个学科重复（如立法法），按
  目录序去重只保留首个；
- 标题与既有语料法律做「去『中华人民共和国』前缀」别名匹配，命中的
  视为已有法律直接跳过（v1/v2 行冻结，append-only）；
- 解析出 0 条的法律如实列出不静默丢弃，由人工决定去留。

用法：
  python scripts/zhuma_to_articles.py --raw data/flk/tmp/zhuma_laws_raw.json \\
      --catalog data/flk/tmp/zhuma_catalog.json --id-base 970000 \\
      --out data/flk/fragments/zhuma_v3.jsonl --report data/flk/tmp/zhuma_report.txt
"""
import argparse
import io
import json
import os
import sys

if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.docx_to_articles import (  # noqa: E402
    articles_from_paragraphs, best_effort_articles)


def strip_prefix(title):
    """去「中华人民共和国」前缀，用于竹马简称与语料全名的别名匹配。"""
    return title[len("中华人民共和国"):] if title.startswith("中华人民共和国") else title


def split_chapter(content):
    """章节全文 → (articles, skipped_headings)。规则与 docx 来源共用。"""
    paras = [ln.strip() for ln in content.split("\n")]
    return articles_from_paragraphs([p for p in paras if p])


def main():
    parser = argparse.ArgumentParser(description="竹马电子法条 → 条级 JSONL 片段")
    parser.add_argument("--raw", required=True, help="zhuma_laws_raw.json 路径")
    parser.add_argument("--catalog", required=True, help="zhuma_catalog.json 路径")
    parser.add_argument("--corpus-laws", default="data/corpus_v2.jsonl",
                        help="既有语料（用于已有法律排除），缺省 corpus_v2")
    parser.add_argument("--id-base", type=int, default=970000)
    parser.add_argument("--out", required=True)
    parser.add_argument("--report", required=True, help="逐法律条数报告输出路径")
    args = parser.parse_args()

    corpus_laws = set()
    with io.open(args.corpus_laws, encoding="utf-8") as f:
        for line in f:
            corpus_laws.add(json.loads(line)["law"])
    corpus_base = {}
    for name in corpus_laws:
        corpus_base.setdefault(strip_prefix(name), name)

    catalog = json.load(io.open(args.catalog, encoding="utf-8"))
    raw = json.load(io.open(args.raw, encoding="utf-8"))
    laws = raw["laws"]

    # 目录序去重 + 别名排除
    seen_titles = set()
    todo = []       # (law_id, title, subject)
    skipped_both = []
    for subj in catalog:
        for law in (subj.get("children") or []):
            title = law["title"]
            if title in seen_titles:
                continue
            seen_titles.add(title)
            if strip_prefix(title) in corpus_base:
                skipped_both.append((law["id"], title))
                continue
            todo.append((law["id"], title, subj["title"]))

    fragments = []
    no_source = []   # 目录有名字但接口无正文（竹马 web 端未配内容）
    no_articles = []  # 有正文但解析不出条号
    report = []
    for law_id, title, subject in todo:
        chapters = laws.get(str(law_id)) or laws.get(law_id) or []
        if not chapters:
            no_source.append((law_id, title))
            report.append("%s\t%s\t无正文（竹马 web 端目录未配内容，需另行补源）" % (law_id, title))
            continue
        paras = []
        for ch in chapters:
            paras.extend(p.strip() for p in (ch.get("content") or "").split("\n") if p.strip())
        arts, skipped = split_chapter("\n".join(paras))
        numbering_used = "第X条"
        whole_doc = False
        if not arts and paras:
            # 与 flk 同口径的回退（「一、」/「N.」/整篇三选一）
            arts, skipped, numbering_used = best_effort_articles(paras)
            whole_doc = numbering_used == "整篇作为一条"
        if not arts:
            no_articles.append((law_id, title, len(chapters)))
            report.append("%s\t%s\t0 条（%d 章有正文但无条号，未入库）" % (law_id, title, len(chapters)))
            continue
        if numbering_used != "第X条":
            report.append("  （%s：%s）" % (title[:30], numbering_used))
        for art in arts:
            fragments.append({
                "id": 0,  # 占位，最后统一编号
                "law": title,
                "num": art["num"],
                "text": art["text"],
                "meta": {
                    "source": "zhumavip.com 电子法条（法考汇编 businessTypeId=104）",
                    "subject": subject,
                    "law_id": law_id,
                    "num_origin": art.get("num_origin", art["num"]),
                    "unit": "whole-document" if whole_doc else "article",
                },
            })
        report.append("%s\t%s\t%d 条（丢弃标题行 %d）" % (law_id, title, len(arts), skipped))

    for i, frag in enumerate(fragments, 1):
        frag["id"] = args.id_base + i

    with io.open(args.out, "w", encoding="utf-8", newline="\n") as f:
        for frag in fragments:
            f.write(json.dumps(frag, ensure_ascii=False) + "\n")
    with io.open(args.report, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(report) + "\n")

    print("目录法律 %d 部：已有跳过 %d，待合入 %d" % (len(seen_titles), len(skipped_both), len(todo)))
    print("合入 %d 条（id %d-%d）→ %s" % (len(fragments), args.id_base + 1,
          args.id_base + len(fragments), args.out))
    if no_source:
        print("竹马无正文 %d 部（未入库）：" % len(no_source))
        for law_id, title in no_source:
            print("  NOSOURCE %s %s" % (law_id, title))
    if no_articles:
        print("有正文但无条号 %d 部（未入库）：" % len(no_articles))
        for law_id, title, nch in no_articles:
            print("  NOARTICLE %s %s（%d 章）" % (law_id, title, nch))


if __name__ == "__main__":
    main()
