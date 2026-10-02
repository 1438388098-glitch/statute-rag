# -*- coding: utf-8 -*-
"""覆盖率复算：语料（任意版本） × 竹马法考法律法规汇编目录。

背景：v3 语料「219/242 = 90.5% 覆盖竹马目录」此前只是 docs/corpus-completion.md
里的一句话，而竹马目录树（zhuma_catalog.json）在本地 data/ 里不入库，没人能复算。
本脚本把覆盖率变成一条可复算命令：

- 对账基准用**只含公开事实**的法律名称清单 corpus/zhuma_catalog_titles.json
  （法名 + 竹马目录 id + 所属科目 + 是否有正文；**不含任何条文正文**），
  由本脚本 --emit-titles 从本地目录树提取后入库，CI 里无需 data/ 也能复算；
- 输出：条数/部数/覆盖部数/覆盖率、按来源分组的条数与部数、未覆盖清单、
  「名同法不同源」重复法清单；
- 目录清单文件缺失时**优雅降级**（打印明确提示、跳过覆盖率、退出码 0），
  不因 CI 没有 data/ 而崩。

法名归一：去掉全部空白 + 去「中华人民共和国」前缀（民法典 ↔ 中华人民共和国民法典
等全称/简称差异按此归一）。

用法：
  # 从本地竹马目录树提取公开清单（含正文与否），入库为 corpus/zhuma_catalog_titles.json
  python scripts/coverage_report.py --emit-titles \\
      --catalog-source data/flk/tmp/zhuma_catalog.json \\
      --catalog corpus/zhuma_catalog_titles.json

  # 复算覆盖率（md 与 json 均可选）
  python scripts/coverage_report.py --corpus data/corpus_v3.jsonl \\
      --catalog corpus/zhuma_catalog_titles.json --md docs/coverage-report.md
"""
import argparse
import io
import json
import os

PREFIX = u"中华人民共和国"
GAZETTE_SOURCE = u"公报源（v1，本地 legal-wisdom 库）"
V2_SOURCE = u"官方 docx（v2 六部大法，flk）"
ZHUMA_SOURCE = u"竹马电子法条（zhumavip.com）"
FLK_SOURCE = u"flk 官方文件（v3）"


def normalize_title(title):
    """法名归一：去空白 + 去「中华人民共和国」前缀。"""
    s = u"".join((title or u"").split())
    if s.startswith(PREFIX):
        s = s[len(PREFIX):]
    return s


def classify_source(row):
    """按既有 meta.source 与保留 id 段判定片段来源（v1/v2 无 meta.source）。"""
    meta = row.get("meta") or {}
    src = meta.get("source") or u""
    if u"zhumavip" in src:
        return ZHUMA_SOURCE
    if u"flk" in src:
        return FLK_SOURCE
    rid = row.get("id")
    if isinstance(rid, int):
        if rid >= 980000:
            return FLK_SOURCE
        if rid >= 970000:
            return ZHUMA_SOURCE
        if rid >= 900000:
            return V2_SOURCE
    return GAZETTE_SOURCE


def collect_corpus_stats(rows):
    """语料条数、部数、按来源分组、同名多来源清单。"""
    laws = {}
    source_rows = {}
    source_laws = {}
    norm_sources = {}
    for r in rows:
        law = r.get("law") or u""
        laws[law] = laws.get(law, 0) + 1
        src = classify_source(r)
        source_rows[src] = source_rows.get(src, 0) + 1
        source_laws.setdefault(src, set()).add(normalize_title(law))
        norm_sources.setdefault(normalize_title(law), {})
        norm_sources[normalize_title(law)][src] = norm_sources[normalize_title(law)].get(src, 0) + 1
    multi = []
    for name in sorted(norm_sources):
        by_src = norm_sources[name]
        if len(by_src) > 1:
            multi.append({"name": name, "sources": by_src})
    return {
        "rows": len(rows),
        "laws": len(laws),
        "source_rows": source_rows,
        "source_laws": source_laws,
        "multi_source": multi,
    }


def compute_coverage(corpus_law_titles, catalog_laws):
    """覆盖率：按归一法名匹配目录清单。

    部数口径与 docs/corpus-completion.md 一致：目录 243 个节点按**标题字符串**
    去重为 242 部（竹马目录里「中华人民共和国立法法」出现两次）；归一化后另有
    「环境污染刑事案件解释」空白变体重复，单独在 normalized_duplicates 里披露。
    """
    corpus_norm = set(normalize_title(t) for t in corpus_law_titles)

    seen = set()
    unique = []
    for spec in catalog_laws:
        raw = spec.get("title") or u""
        if raw in seen:
            continue
        seen.add(raw)
        unique.append(spec)

    norm_groups = {}
    for spec in unique:
        norm_groups.setdefault(normalize_title(spec.get("title")), []).append(spec.get("title"))
    normalized_duplicates = [
        {"name": k, "titles": v} for k, v in sorted(norm_groups.items()) if len(v) > 1
    ]

    covered = [s for s in unique if normalize_title(s.get("title")) in corpus_norm]
    uncovered = [s for s in unique if normalize_title(s.get("title")) not in corpus_norm]
    total = len(unique)
    return {
        "nodes": len(catalog_laws),
        "unique_titles": total,
        "covered": len(covered),
        "uncovered": len(uncovered),
        "rate": (100.0 * len(covered) / total) if total else 0.0,
        "uncovered_list": [
            {
                "title": s.get("title"),
                "subject": s.get("subject"),
                "zhuma_id": s.get("zhuma_id"),
                "has_body": bool(s.get("has_body")),
            }
            for s in uncovered
        ],
        "duplicate_titles": normalized_duplicates,
    }


def extract_catalog_titles(tree):
    """竹马目录树（学科 type0 → 法律 type1/无正文 type4）→ 公开法名清单。"""
    laws = []
    subjects = []
    for top in tree:
        subject = (top.get("title") or u"").strip()
        if subject not in subjects:
            subjects.append(subject)
        for node in top.get("children") or []:
            laws.append({
                "title": (node.get("title") or u"").strip(),
                "zhuma_id": node.get("id"),
                "subject": subject,
                "has_body": bool(node.get("children")),
            })
    return {
        "_readme": (u"竹马（zhumavip.com）法考法律法规汇编 businessTypeId=104 的法律名称清单，"
                    u"仅含公开事实（法名 + 竹马目录 id + 所属科目 + 是否有正文节点），"
                    u"不含任何条文正文。由 scripts/coverage_report.py --emit-titles 生成。"),
        "source": u"zhumavip.com 法考法律法规汇编（businessTypeId=104）",
        "subjects": subjects,
        "laws": laws,
    }


def load_jsonl(path):
    rows = []
    with io.open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def format_markdown(rep):
    corpus = rep["corpus"]
    lines = []
    lines.append(u"# 覆盖率复算报告")
    lines.append(u"")
    lines.append(u"> 由 `python scripts/coverage_report.py` 生成；对账基准为 "
                 u"`corpus/zhuma_catalog_titles.json`（竹马法考汇编法律名称，公开事实，不含正文）。")
    lines.append(u"")
    lines.append(u"## 总览")
    lines.append(u"")
    lines.append(u"| 项 | 数值 |")
    lines.append(u"|---|---|")
    lines.append(u"| 语料条数 | %d |" % corpus["rows"])
    lines.append(u"| 语料法律部数 | %d |" % corpus["laws"])
    cov = rep.get("coverage")
    if cov is None:
        lines.append(u"| 竹马目录覆盖率 | 清单缺失，未计算 |")
    else:
        lines.append(u"| 竹马目录部数（去重后） | **%d** |" % cov["unique_titles"])
        lines.append(u"| 已覆盖部数 | **%d** |" % cov["covered"])
        lines.append(u"| 未覆盖部数 | **%d** |" % cov["uncovered"])
        lines.append(u"| 覆盖率 | **%.1f%%** |" % cov["rate"])
    lines.append(u"")
    lines.append(u"## 按来源分组")
    lines.append(u"")
    lines.append(u"| 来源 | 条数 | 部数 |")
    lines.append(u"|---|---|---|")
    for src in [GAZETTE_SOURCE, V2_SOURCE, ZHUMA_SOURCE, FLK_SOURCE]:
        if src in corpus["source_rows"]:
            lines.append(u"| %s | %d | %d |" % (
                src, corpus["source_rows"][src], len(corpus["source_laws"].get(src, set()))))
    lines.append(u"")
    if cov is not None:
        lines.append(u"## 未覆盖清单（%d 部）" % cov["uncovered"])
        lines.append(u"")
        lines.append(u"| 法名 | 科目 | 竹马 id | 竹马有正文 |")
        lines.append(u"|---|---|---|---|")
        for u_ in cov["uncovered_list"]:
            lines.append(u"| %s | %s | %s | %s |" % (
                u_["title"], u_["subject"], u_["zhuma_id"],
                u"是" if u_["has_body"] else u"否"))
        lines.append(u"")
        lines.append(u"## 名同法不同源（%d 部）" % len(corpus["multi_source"]))
        lines.append(u"")
        if corpus["multi_source"]:
            lines.append(u"| 法名 | 各来源行数 |")
            lines.append(u"|---|---|")
            for m in corpus["multi_source"]:
                desc = u"、".join(u"%s %d 行" % (k, v) for k, v in sorted(m["sources"].items()))
                lines.append(u"| %s | %s |" % (m["name"], desc))
        else:
            lines.append(u"（无）")
        lines.append(u"")
    return u"\n".join(lines) + u"\n"


def main():
    parser = argparse.ArgumentParser(description="语料覆盖率复算")
    parser.add_argument("--corpus", help="语料 JSONL（绝对路径或相对路径）")
    parser.add_argument("--catalog", default="corpus/zhuma_catalog_titles.json",
                        help="公开法名清单（默认 corpus/zhuma_catalog_titles.json）")
    parser.add_argument("--emit-titles", action="store_true",
                        help="从 --catalog-source 提取公开法名清单写入 --catalog，不跑覆盖率")
    parser.add_argument("--catalog-source", help="本地竹马目录树 zhuma_catalog.json")
    parser.add_argument("--md", help="markdown 报告输出路径（可选）")
    parser.add_argument("--json", dest="json_out", help="机器可读报告输出路径（可选）")
    args = parser.parse_args()

    if args.emit_titles:
        if not args.catalog_source:
            parser.error("--emit-titles 需要 --catalog-source")
        tree = json.load(io.open(args.catalog_source, encoding="utf-8"))
        payload = extract_catalog_titles(tree)
        with io.open(args.catalog, "w", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(payload, ensure_ascii=False, indent=2))
            f.write(u"\n")
        print(u"已写出公开法名清单 %d 部 → %s" % (len(payload["laws"]), args.catalog))
        return 0

    if not args.corpus:
        parser.error("需要 --corpus（或 --emit-titles）")
    rows = load_jsonl(args.corpus)
    rep = {"corpus": collect_corpus_stats(rows)}
    # 冗余的来源部数集合不能进 json
    rep["corpus"]["source_laws"] = dict(
        (k, sorted(v)) for k, v in rep["corpus"]["source_laws"].items())

    catalog_path = args.catalog
    if not os.path.exists(catalog_path):
        print(u"[提示] 未能找到对账清单 %s（CI 无 data/ 属正常）——"
              u"跳过覆盖率计算，仅输出语料侧统计。" % catalog_path)
        rep["coverage"] = None
    else:
        catalog = json.load(io.open(catalog_path, encoding="utf-8"))
        corpus_titles = set(r.get("law") or u"" for r in rows)
        rep["coverage"] = compute_coverage(corpus_titles, catalog.get("laws") or [])
        print(u"语料 %d 条 / %d 部；竹马目录去重 %d 部，已覆盖 %d 部（%.1f%%），未覆盖 %d 部。"
              % (rep["corpus"]["rows"], rep["corpus"]["laws"],
                 rep["coverage"]["unique_titles"], rep["coverage"]["covered"],
                 rep["coverage"]["rate"], rep["coverage"]["uncovered"]))
        print(u"名同法不同源：%d 部" % len(rep["corpus"]["multi_source"]))

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
