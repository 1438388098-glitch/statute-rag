# -*- coding: utf-8 -*-
"""CI 数字一致性检查：两份 README 的评测数字必须与 docs/metrics.json 一致。

metrics.json 是数字的单一来源（由 gen_eval_report.py 生成）；改检索器后
数字若变，必须重跑生成脚本并同步 README，否则本检查在 CI 挡下。
本脚本只读文本，不需要语料，可在 CI 跑。

退出码 0 = 一致；1 = 漂移清单。
"""
import io
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")

README_FILES = ["README.md", "README.zh-CN.md"]


def _display_values(metrics):
    """从 metrics.json 抽取应出现在 README 中的展示口径数字。"""
    values = set()
    for gold_key in ("synth", "real"):
        for name in ("like", "bm25", "hybrid"):
            entry = metrics[gold_key].get(name)
            if entry:
                values.add(entry["recall_at_5_display"])
                values.add(entry["mrr_display"])
    values.update(metrics.get("real_deep_display", {}).values())
    return values


def main():
    metrics_path = os.path.join(ROOT, "docs", "metrics.json")
    with io.open(metrics_path, "r", encoding="utf-8") as f:
        metrics = json.load(f)
    values = _display_values(metrics)

    # eval_report.md 是生成产物，只核对存在与新鲜（同一 commit 标记）
    report_path = os.path.join(ROOT, "docs", "eval_report.md")
    problems = []
    with io.open(report_path, "r", encoding="utf-8") as f:
        report = f.read()
    for value in sorted(values):
        if value not in report:
            problems.append("docs/eval_report.md 缺少数字 %s（报告可能过期，重跑 gen_eval_report）" % value)

    for readme in README_FILES:
        path = os.path.join(ROOT, readme)
        with io.open(path, "r", encoding="utf-8") as f:
            text = f.read()
        for value in sorted(values):
            if value not in text:
                problems.append("%s 缺少数字 %s（与 docs/metrics.json 漂移）" % (readme, value))

    if problems:
        print("数字一致性检查未通过：")
        for p in problems:
            print("  -", p)
        print("单一来源：docs/metrics.json；同步方式：跑 scripts/gen_eval_report.py 并更新 README。")
        sys.exit(1)
    print("数字一致性检查 OK：%d 个展示口径数字在两份 README 与评测报告中均一致。" % len(values))


if __name__ == "__main__":
    main()
