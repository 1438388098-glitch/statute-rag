# -*- coding: utf-8 -*-
"""CI 数字一致性检查：两份 README 的评测数字必须与 docs/metrics.json 一致。

metrics.json 是数字的单一来源（由 gen_eval_report.py 生成）；改检索器后
数字若变，必须重跑生成脚本并同步 README，否则本检查在 CI 挡下。
本脚本只读文本，不需要语料，可在 CI 跑。

除展示口径数字外，另校验**口径语料条数**：metrics.json 的
generated.corpus_size 必须出现在两份 README 与评测报告里（千分位写法）。
语料扩容后忘记同步 README 是本仓发生过的一类口径漂移（README 仍宣称
旧版语料条数），这条检查专门挡它。

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


def _corpus_size_display(metrics):
    """当前口径语料的千分位写法（如 25,273）；无记录时返回 None。"""
    size = metrics.get("generated", {}).get("corpus_size")
    return "{:,}".format(size) if size else None


def main():
    metrics_path = os.path.join(ROOT, "docs", "metrics.json")
    with io.open(metrics_path, "r", encoding="utf-8") as f:
        metrics = json.load(f)
    values = _display_values(metrics)
    size_display = _corpus_size_display(metrics)

    # eval_report.md 是生成产物：只做数字存在性核对（commit 新鲜性由生成脚本写入，此处不校验）
    report_path = os.path.join(ROOT, "docs", "eval_report.md")
    problems = []
    with io.open(report_path, "r", encoding="utf-8") as f:
        report = f.read()
    for value in sorted(values):
        if value not in report:
            problems.append("docs/eval_report.md 缺少数字 %s（报告可能过期，重跑 gen_eval_report）" % value)
    if size_display and size_display not in report:
        problems.append("docs/eval_report.md 缺少当前口径语料条数 %s（报告与语料口径不一致）" % size_display)

    for readme in README_FILES:
        path = os.path.join(ROOT, readme)
        with io.open(path, "r", encoding="utf-8") as f:
            text = f.read()
        for value in sorted(values):
            if value not in text:
                problems.append("%s 缺少数字 %s（与 docs/metrics.json 漂移）" % (readme, value))
        if size_display and size_display not in text:
            problems.append("%s 缺少当前口径语料条数 %s（口径漂移：README 可能仍在宣称旧版语料）"
                            % (readme, size_display))

    if problems:
        print("数字一致性检查未通过：")
        for p in problems:
            print("  -", p)
        print("单一来源：docs/metrics.json；同步方式：跑 scripts/gen_eval_report.py 并更新 README。")
        sys.exit(1)
    print("数字一致性检查 OK：%d 个展示口径数字与口径语料条数 %s 在两份 README 与评测报告中均一致。"
          % (len(values), size_display))


if __name__ == "__main__":
    main()
