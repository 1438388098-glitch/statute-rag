# -*- coding: utf-8 -*-
"""从「真实问句金标」gold_real_38.jsonl 生成人工法律复核工作表 docs/gold-review-worksheet.md。

用途：docs/real-question-eval.md 第 3 节声明「人工法律复核尚未做」，本脚本把金标
整理成一张可逐题勾选的复核底稿（内容全部来自 jsonl，不做任何人工改写）。

复核方法（写在工作表头部）：对每题打开 source_url 原问句 → 判断金标条文是否
确实回答该问 → 在复核列标 ✓ / ✗ / 存疑。38 题全 ✓ 即「人工复核完成」；
任何 ✗ 需修改金标并重跑评测。

复核结果的持久载体是 gold/real38_review_status.json（人工填写）：
本脚本读取它预填「复核」列并统计进度，可重复执行且**不丢人工结果**；
行标签偏移行（num 与证据句口径不一致）按状态文件的 label_offset 标 ⚠。

用法：py -3.13 scripts/make_gold_review_worksheet.py
"""
import argparse
import datetime
import io
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

REQUIRED_FIELDS = ["qid", "query", "law", "num", "gold_laws", "evidence", "source_site", "source_url"]

SNAPSHOT_NOTE = "本表由脚本从 {gold_label} 生成，{date} 快照；「复核」列预填自 {status_label}。"


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


def load_status(path):
    """读取复核状态文件；键名以 _ 开头的是说明字段，不是题目。"""
    if not os.path.exists(path):
        return {}
    with io.open(path, encoding="utf-8") as f:
        data = json.load(f)
    return dict((k, v) for k, v in data.items() if not k.startswith("_"))


def statute_label(row, status):
    """金标条文标签：主金标（law + num）+ 其余 gold_laws，全部取自 jsonl 原文。

    行标签偏移行加 ⚠（num 是分块行标签，与证据句所属条文可能不一致）。
    """
    primary = ("%s %s" % (row["law"], row["num"])).strip()
    others = [g for g in row["gold_laws"] if g != primary]
    label = primary
    if others:
        label += "；另 " + "、".join(others)
    if status.get("label_offset"):
        label += " ⚠行标签偏移"
    return label


def render(rows, statuses, snapshot_date, eval_doc_name, gold_label=None, status_label=None):
    gold_label = gold_label or "gold/gold_real_38.jsonl"
    status_label = status_label or "real38_review_status.json"
    status_links = "、".join("[gold/%s](../gold/%s)" % (s, s) for s in status_label.split("、"))
    done = [q for q, s in statuses.items() if s.get("status")]
    counts = {}
    for s in statuses.values():
        st = s.get("status")
        if st:
            counts[st] = counts.get(st, 0) + 1
    if done:
        progress = "（已复核 %d/%d：%s）" % (
            len(done), len(rows),
            "、".join("%s %d" % (k, v) for k, v in sorted(counts.items())))
    else:
        progress = "（已复核 0/%d）" % len(rows)
    offset_rows = [q for q, s in statuses.items() if s.get("label_offset")]

    lines = []
    lines.append("# 金标人工复核工作表（%d 题）" % len(rows))
    lines.append("")
    lines.append("> **用途**：本文档是 `%s`（真实问句金标）的**人工法律复核工作底稿**。"
                 "金标由 LLM 对照条文核验生成（见 [real-question-eval.md](%s) 第 3 节），"
                 "**人工法律复核尚未完成**，本表用于逐题勾选完成该复核。%s" % (gold_label, eval_doc_name, progress))
    lines.append(">")
    lines.append("> **复核结果写在 %s**"
                 "（qid 条目的 status/reviewer/date/note 字段），再重跑本脚本——重新生成不丢人工结果。"
                 % status_links)
    lines.append(">")
    lines.append("> **复核方法**（对每一题）：")
    lines.append("> 1. 点击「来源站点」链接，打开 source_url 原提问页，读原问句；")
    lines.append("> 2. 对照金标条文与 evidence 摘要，判断**金标条文是否确实回答该问**；")
    lines.append("> 3. 把结论写入状态文件该题的 status 字段：`✓`（确实回答）／`✗`（未回答或答非所问）／`存疑`（无法确定）。")
    lines.append(">")
    lines.append("> **完成判定**：%d 题全部 `✓` 即「人工复核完成」；**任何 `✗` 需修改金标并重跑评测**"
                 "（评测命令见 [real-question-eval.md](%s) 文末）。" % (len(rows), eval_doc_name))
    lines.append(">")
    lines.append("> **⚠行标签偏移行**（%s）：金标 `num` 是语料分块行标签，与证据句所属条文的实际条号"
                 "可能不一致（分块跨条 + 双栏 PDF 解析污染，见 real-question-eval.md §3/§8）。"
                 "复核这些行时以 evidence 在干净文本中定位真实条文，并在状态文件回填确认条号。" % "、".join(offset_rows))
    lines.append("")
    lines.append("| qid | 问句（截断 30 字） | 金标条文（法名+条号） | evidence 摘要（截断 40 字） | 来源站点 | 复核 |")
    lines.append("|---|---|---|---|---|---|")
    for row in rows:
        site = "[%s](%s)" % (cell(row["source_site"]), row["source_url"])
        st = statuses.get(row["qid"], {}).get("status") or "☐"
        lines.append("| %s | %s | %s | %s | %s | %s |" % (
            cell(row["qid"]),
            cell(trunc(row["query"], 30)),
            cell(statute_label(row, statuses.get(row["qid"], {}))),
            cell(trunc(row["evidence"], 40)),
            site,
            st,
        ))
    offset_notes = [(q, s.get("offset_note")) for q, s in sorted(statuses.items())
                    if s.get("label_offset") and s.get("offset_note")]
    if offset_notes:
        lines.append("")
        lines.append("### ⚠ 行标签偏移明细")
        lines.append("")
        for qid, note in offset_notes:
            lines.append("- **%s**：%s" % (qid, note))
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 相关文档")
    lines.append("")
    lines.append("- 评测报告与金标构建方法：[real-question-eval.md](%s)（该文档第 3 节反向链接至本表）" % eval_doc_name)
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(SNAPSHOT_NOTE.format(date=snapshot_date, gold_label=gold_label, status_label=status_label))
    lines.append("")
    return "\n".join(lines)


def main():
    repo = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    parser = argparse.ArgumentParser(description="生成金标人工复核工作表")
    parser.add_argument("--gold", nargs="+", default=[os.path.join(repo, "gold", "gold_real_38.jsonl")],
                        help="金标文件（可多个，合并成一张表；用于新增批次）")
    parser.add_argument("--out", default=os.path.join(repo, "docs", "gold-review-worksheet.md"))
    parser.add_argument("--status", nargs="+", default=[os.path.join(repo, "gold", "real38_review_status.json")],
                        help="复核状态文件（可多个，按序合并；预填复核列，不丢人工结果）")
    parser.add_argument("--eval-doc", default="real-question-eval.md", help="评测报告文件名（同目录相对链接）")
    parser.add_argument("--date", default=datetime.date.today().isoformat(), help="快照日期")
    args = parser.parse_args()

    rows = []
    for g in args.gold:
        rows.extend(load_gold(g))
    statuses = {}
    for s in args.status:
        statuses.update(load_status(s))
    gold_label = "、".join("gold/" + os.path.basename(g) for g in args.gold)
    status_label = "、".join(os.path.basename(s) for s in args.status)
    md = render(rows, statuses, args.date, args.eval_doc, gold_label, status_label)
    with io.open(args.out, "w", encoding="utf-8", newline="\n") as f:
        f.write(md)
    filled = sum(1 for s in statuses.values() if s.get("status"))
    print("已生成 %s（%d 题，已复核 %d，偏移警示 %d）"
          % (args.out, len(rows), filled,
             sum(1 for s in statuses.values() if s.get("label_offset"))))


if __name__ == "__main__":
    main()
