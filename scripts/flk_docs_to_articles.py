# -*- coding: utf-8 -*-
"""把 flk（国家法律法规数据库）官方文件批量转成条级 JSONL 片段。

数据来源：竹马电子法条目录中「有名字但无正文」的法律，按精确标题到 flk
检索（searchType=1，取 sxx=3 最新公布版），下载官方文件。flk 对部分老
司法解释只存 .doc（OLE2）而无 .docx，故：

- .docx：解 zip 读 word/document.xml 段落（docx_paragraphs）
- .doc ：走 antiword -w 0（关闭硬折行，一行一段）

两者都交给 docx_to_articles.articles_from_paragraphs 同一套切条状态机，
保证条号语义一致。文件名用 bbbs（32 位 hex）避免 Windows 路径上限，标题
等元信息在 manifest 里。

用法：
  python scripts/flk_docs_to_articles.py --docs data/flk/docs_v3 \\
      --manifest data/flk/tmp/flk_manifest_a.json --id-base 980000 \\
      --out data/flk/fragments/flk_v3.jsonl --report data/flk/tmp/flk_docs_report.txt
"""
import argparse
import io
import json
import os
import subprocess
import sys

if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.docx_to_articles import (  # noqa: E402
    articles_from_paragraphs, best_effort_articles, docx_paragraphs)


def is_zip(path):
    """按魔数判格式：flk 有文件扩展名写成 .doc 实为 docx（PK 头），不可信。"""
    with open(path, "rb") as f:
        return f.read(2) == b"PK"


def read_paragraphs(path):
    """读文件段落：docx 解 zip，其余走 antiword。扩展名不可信，按魔数分流。"""
    return docx_paragraphs(path) if is_zip(path) else doc_paragraphs(path)


def doc_paragraphs(path):
    """antiword -w 0 提取 .doc 文本，一行一段（关闭硬折行）。"""
    out = subprocess.check_output(["antiword", "-w", "0", path], stderr=subprocess.PIPE)
    lines = out.decode("utf-8", "replace").replace("\f", "\n").split("\n")
    return [ln.strip() for ln in lines if ln.strip()]


def main():
    parser = argparse.ArgumentParser(description="flk 官方文件批量 → 条级 JSONL 片段")
    parser.add_argument("--docs", required=True, help="下载文件目录")
    parser.add_argument("--manifest", required=True, help="bbbs → 元信息 的 manifest.json")
    parser.add_argument("--id-base", type=int, default=980000)
    parser.add_argument("--out", required=True)
    parser.add_argument("--report", required=True)
    args = parser.parse_args()

    manifest = json.load(io.open(args.manifest, encoding="utf-8"))
    fragments = []
    report = []
    empty = []
    for bbbs in sorted(manifest, key=lambda k: manifest[k].get("gbrq") or ""):
        meta = manifest[bbbs]
        ext = meta.get("ext") or "docx"
        path = os.path.join(args.docs, bbbs + "." + ext)
        if not os.path.exists(path):
            report.append("%s\t%s\t文件缺失(%s)" % (bbbs, meta["title"], path))
            empty.append((bbbs, meta["title"], "missing"))
            continue
        try:
            paras = read_paragraphs(path)
        except Exception as exc:  # noqa: BLE001 - 逐文件容错，缺失即如实记报告
            report.append("%s\t%s\t解析失败：%s" % (bbbs, meta["title"], str(exc)[:60]))
            empty.append((bbbs, meta["title"], "parse-error"))
            continue
        arts, skipped = articles_from_paragraphs(paras)
        numbering_used = "第X条"
        whole_doc = False
        if not arts and paras:
            # 回退：修正案用「一、」、指导意见/试行规定用「N.」编号、短法律
            # 解释整篇即一个单元——三选一见 best_effort_articles
            arts, skipped, numbering_used = best_effort_articles(paras)
            whole_doc = numbering_used == "整篇作为一条"
        if not arts:
            report.append("%s\t%s\t0 条（%d 段无正文）" % (bbbs, meta["title"], len(paras)))
            empty.append((bbbs, meta["title"], "empty"))
            continue
        if numbering_used != "第X条":
            report.append("  （%s：%s）" % (meta["title"][:30], numbering_used))
        for art in arts:
            fragments.append({
                "id": 0,
                "law": meta["title"],
                "num": art["num"],
                "text": art["text"],
                "meta": {
                    "source": "flk.npc.gov.cn 国家法律法规数据库",
                    "bbbs": bbbs,
                    "gbrq": meta.get("gbrq", ""),
                    "sxrq": meta.get("sxrq", ""),
                    "flxz": meta.get("flxz", ""),
                    "file": bbbs + "." + ext,
                    "num_origin": art.get("num_origin", art["num"]),
                    "unit": "whole-document" if whole_doc else "article",
                },
            })
        report.append("%s\t%s\t%d 条（%s，丢弃标题行 %d）"
                      % (bbbs, meta["title"], len(arts), ext, skipped))

    for i, frag in enumerate(fragments, 1):
        frag["id"] = args.id_base + i

    with io.open(args.out, "w", encoding="utf-8", newline="\n") as f:
        for frag in fragments:
            f.write(json.dumps(frag, ensure_ascii=False) + "\n")
    with io.open(args.report, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(report) + "\n")

    print("处理 %d 部：产出 %d 条（id %d-%d）→ %s"
          % (len(manifest), len(fragments), args.id_base + 1,
             args.id_base + len(fragments), args.out))
    if empty:
        print("未产出 %d 部：" % len(empty))
        for bbbs, title, why in empty:
            print("  %s %s（%s）" % (bbbs[:10], title[:40], why))


if __name__ == "__main__":
    main()
