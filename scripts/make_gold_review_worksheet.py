# -*- coding: utf-8 -*-
"""从「真实问句金标」gold_real_38.jsonl 生成人工法律复核工作表 docs/gold-review-worksheet.md。

用途：docs/real-question-eval.md 第 3 节声明「人工法律复核尚未做」，本脚本把金标
整理成一张可逐题勾选的复核底稿（内容全部来自 jsonl，不做任何人工改写）。

复核方法（写在工作表头部）：对每题打开 source_url 原问句 → 判断金标条文是否
确实回答该问 → 在复核列标 ✓ / ✗ / 存疑。38 题全 ✓ 即「人工复核完成」；
任何 ✗ 需修改金标并重跑评测。

用法：py -3.13 scripts/make_gold_review_worksheet.py
可重复执行，输出覆盖写（幂等）。
"""
import argparse
import io
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

REQUIRED_FIELDS = ["qid", "query", "law", "num", "gold_laws", "evidence", "source_site", "source_url"]

SNAPSHOT_NOTE = "本表由脚本从 gold_real_38.jsonl 生成，{date} 快照。"


def trunc(text, limit):
    """按字数截断（中文一字一符），超出加省略号。"""
    text = text.strip()
    return text if len(text) <= limit else text[:limit] + "…"


def cell(text):
    """转义 Markdown 表格竖线，保证列对齐不被破坏。"""
    return str(text).replace("|", "\\|").replace("\n", " ")


def load_gold(path):
    rows = []
    with io.open(path, encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            missing = [k for k in REQUIRED_FIELDS if not row.get(k)]
            if missing:
                raise SystemExit("gold 第 %d 行缺少字段 %s" % (i, missing))
            rows.append(row)
    if not rows:
        raise SystemExit("gold 文件为空：%s" % path)
    return rows


def statute_label(row):
    """金标条文标签：主金标（law + num）+ 其余 gold_laws。全部取自 jsonl 原文。"""
    primary = ("%s %s" % (row["law"], row["num"])).strip()
    others = [g for g in row["gold_laws"] if g != primary]
    label = primary
    if others:
        label += "；另 " + "、".join(others)
    return label


def render(rows, snapshot_date, eval_doc_name):
    lines = []
    lines.append("# 金标人工复核工作表（38 题）")
    lines.append("")
    lines.append("> **用途**：本文档是 `gold/gold_real_38.jsonl`（真实问句金标）的**人工法律复核工作底稿**。"
                 "金标由 LLM 对照条文核验生成（见 [real-question-eval.md](%s) 第 3 节），"
                 "**人工法律复核尚未完成**，本表用于逐题勾选完成该复核。" % eval_doc_name)
    lines.append(">")
    lines.append("> **复核方法**（对每一题）：")
    lines.append("> 1. 点击「来源站点」链接，打开 source_url 原提问页，读原问句；")
    lines.append("> 2. 对照金标条文与 evidence 摘要，判断**金标条文是否确实回答该问**；")
    lines.append("> 3. 在「复核」列把 ☐ 改标：`✓`（确实回答）／`✗`（未回答或答非所问）／`存疑`（无法确定）。")
    lines.append(">")
    lines.append("> **完成判定**：38 题全部 `✓` 即「人工复核完成」；**任何 `✗` 需修改金标并重跑评测**"
                 "（评测命令见 [real-question-eval.md](%s) 文末）。" % eval_doc_name)
    lines.append("")
    lines.append("| qid | 问句（截断 30 字） | 金标条文（法名+条号） | evidence 摘要（截断 40 字） | 来源站点 | 复核 |")
    lines.append("|---|---|---|---|---|---|")
    for row in rows:
        site = "[%s](%s)" % (cell(row["source_site"]), row["source_url"])
        lines.append("| %s | %s | %s | %s | %s | ☐ |" % (
            cell(row["qid"]),
            cell(trunc(row["query"], 30)),
            cell(statute_label(row)),
            cell(trunc(row["evidence"], 40)),
            site,
        ))
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 相关文档")
    lines.append("")
    lines.append("- 评测报告与金标构建方法：[real-question-eval.md](%s)（该文档第 3 节反向链接至本表）" % eval_doc_name)
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(SNAPSHOT_NOTE.format(date=snapshot_date))
    lines.append("")
    return "\n".join(lines)


def main():
    repo = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    parser = argparse.ArgumentParser(description="生成金标人工复核工作表")
    parser.add_argument("--gold", default=os.path.join(repo, "gold", "gold_real_38.jsonl"))
    parser.add_argument("--out", default=os.path.join(repo, "docs", "gold-review-worksheet.md"))
    parser.add_argument("--eval-doc", default="real-question-eval.md", help="评测报告文件名（同目录相对链接）")
    parser.add_argument("--date", default="2026-09-29", help="快照日期")
    args = parser.parse_args()

    rows = load_gold(args.gold)
    md = render(rows, args.date, args.eval_doc)
    with io.open(args.out, "w", encoding="utf-8", newline="\n") as f:
        f.write(md)
    print("已生成 %s（%d 题）" % (args.out, len(rows)))


if __name__ == "__main__":
    main()
