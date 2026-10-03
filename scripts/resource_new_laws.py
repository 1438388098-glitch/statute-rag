# -*- coding: utf-8 -*-
"""按名单从法规文本镜像取源：外部题库量出的语料覆盖缺口（新增法律 + 刑法整合版）。

外部隔离题库（gold/qbank_external_v1.jsonl）映射时有 11 题落空，量出两类缺口：
1. 7 部法律正文不在语料（社会保险法、工伤保险条例、著作权法、专利法、
   环境保护法、税收征收管理法、消费者权益保护法——语料里只有它们的
   实施细则/实施条例）；
2. 刑法主文是 1997 基础文本，十二个修正案插入的「之一」条文（醉驾、帮信罪、
   侵犯公民个人信息罪等现行常用罪名）全部缺失——修正案只以「在刑法第X条后
   增加一条」的立法语言存着。

本脚本按显式名单从 LawRefBook/Laws 镜像取源（与 lawrefbook_source.py 同一套
quality/mirror_ok 门禁），产出条级片段；legal.db 里没有这批法，不走
publish_date 对照，一律取镜像最新版并在 meta 记 version_date。

用法：
  python scripts/resource_new_laws.py --repo D:/Claudeworkspace/lawrefbook-clone \
      --law 中华人民共和国刑法 --law 中华人民共和国社会保险法 … \
      --out data/flk/fragments/new_laws.jsonl --report data/flk/tmp/new_laws_report.txt
"""
import argparse
import io
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from scripts.lawrefbook_source import (  # noqa: E402
    LAW_DIRS, file_key, mirror_ok, read_md_paras, refbook_key)
from scripts.resource_local_remaining import quality  # noqa: E402

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")


def build_index(repo):
    """镜像仓库索引：refbook_key → [(日期, 路径)]，新版本在前。"""
    index = {}
    for d in LAW_DIRS:
        sub = os.path.join(repo, d)
        if not os.path.isdir(sub):
            continue
        for fn in os.listdir(sub):
            if not fn.lower().endswith(".md") or fn == "_index.md":
                continue
            key, date = file_key(fn[: -len(".md")])
            index.setdefault(key, []).append((date, os.path.join(sub, fn)))
    for k in index:
        index[k].sort(key=lambda x: x[0], reverse=True)
    return index


def main():
    ap = argparse.ArgumentParser(description=u"按名单从镜像取源补语料覆盖缺口")
    ap.add_argument("--repo", required=True, help=u"LawRefBook/Laws 本地克隆路径")
    ap.add_argument("--law", action="append", required=True,
                    help=u"要取源的法律名（语料写法），可重复")
    ap.add_argument("--min-cov", type=float, default=0.35)
    ap.add_argument("--out", required=True)
    ap.add_argument("--report", required=True)
    args = ap.parse_args()

    index = build_index(args.repo)
    frags, report = [], []
    stat = {"ok": 0, "nomatch": 0, "fail": 0}
    for law in args.law:
        cands = index.get(refbook_key(law)) or []
        if not cands:
            stat["nomatch"] += 1
            report.append(u"%s\t镜像无此文书" % law)
            continue
        # 无 publish_date 对照，取最新版；日期只作留痕
        date, path = cands[0]
        q = quality(read_md_paras(path))
        if not mirror_ok(q, args.min_cov):
            stat["fail"] += 1
            report.append(u"%s\t%s\t不过门（条%d 单调%s 全序%s cov%.2f 丢%d）" % (
                law, os.path.basename(path), len(q["kept"]), q["mono"],
                q["complete"], q["cov"], q["dropped"]))
            continue
        stat["ok"] += 1
        report.append(u"%s\t%s\t条=%d\t全序1..N=%s\t覆盖=%.2f\t丢弃=%d\t版本=%s" % (
            law, os.path.relpath(path, args.repo).replace("\\", "/"),
            len(q["kept"]), q["complete"], q["cov"], q["dropped"], date or u"未标注"))
        for a in q["kept"]:
            frags.append({
                "id": 0, "law": law, "num": a["num"], "text": a["text"],
                "meta": {
                    "source": u"LawRefBook/Laws 法规文本镜像（官方公布体例）",
                    "text_source": "lawrefbook-mirror",
                    "repo": "https://github.com/LawRefBook/Laws",
                    "path": os.path.relpath(path, args.repo).replace("\\", "/"),
                    "version_date": date,
                    "numbering": u"第X条",
                },
            })

    for i, f in enumerate(frags, 1):
        f["id"] = 999000 + i
    with io.open(args.out, "w", encoding="utf-8", newline="\n") as fh:
        for f in frags:
            fh.write(json.dumps(f, ensure_ascii=False) + "\n")

    head = [
        u"镜像取源补缺口报告",
        u"名单 %d 部：取源 %d 部 / %d 条；无文书 %d；不过门 %d" % (
            len(args.law), stat["ok"], len(frags), stat["nomatch"], stat["fail"]),
        u"",
    ]
    with io.open(args.report, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(u"\n".join(head + report) + u"\n")
    print(u"名单 %d 部：取源 %d 部 / %d 条；无文书 %d；不过门 %d" % (
        len(args.law), stat["ok"], len(frags), stat["nomatch"], stat["fail"]))
    print(u"→ %s" % args.report)


if __name__ == "__main__":
    main()
