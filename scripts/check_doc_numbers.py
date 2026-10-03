# -*- coding: utf-8 -*-
"""CI 文档一致性检查：README/docs 里声明的评测数字与计数必须与实跑一致。

三类检查（均为「实跑/单一来源 vs 文档声明」的对账，不需要本地语料）：

1) 展示口径数字：docs/metrics.json（由 gen_eval_report.py 生成）里的每个展示
   数字都必须出现在两份 README 与 docs/eval_report.md 中；
2) 口径语料条数：metrics.json 的 generated.corpus_size 必须出现在两份 README
   与评测报告里（千分位写法）——语料扩容后忘记同步 README 是本仓发生过的一类
   口径漂移（README 仍宣称旧版语料）；
3) 单测例数：两份 README 与 docs/retrieval-improvement.md 里声明的例数必须等于
   `unittest discover` 的实跑数——这条漂移本轮已发生过两次（62→108→119）。

另加一条针对「当前口径表」的漂移检查：README 的当前口径数字表里不得出现已
退役的旧口径数值（如 v1 时代的 96.6% / 0.954、被重标定前的 31.6% / 0.232）。
只查「表头含 Recall@5 的那张当前口径表」，因此不会误伤文末的历代口径并列表。

退出码 0 = 一致；1 = 漂移清单。
"""
import io
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")

README_FILES = ["README.md", "README.zh-CN.md"]
# 声明了单测例数、且由本线维护的文件（改这些文件的例数不需要别线同意）
TEST_COUNT_FILES = README_FILES + ["docs/retrieval-improvement.md"]
# 同样声明了例数但归语料线维护的文件：只提示、不判失败（改不了别人的文件）
OUT_OF_SCOPE_TEST_COUNT_FILES = ["docs/real-question-eval.md"]

# 已退役的「当前口径」数值：出现在当前口径数字表里即为口径漂移。
# 0.984 是 v1 合成金标 MRR、26.3%/0.180 是 v0.1 基线、31.6%/0.232 是 v3 重标定
# 前的真实金标——它们只在历代并列表里合法。
# （曾列 96.6%/0.954/98.9%，v6 起与现行 like/bm25 合成值撞车——退役名单只留
#   与当前口径不可能重合的值，撞车的由 metrics.json 对账兜住。）
RETIRED_CURRENT_VALUES = ("0.984", "26.3%", "0.180", "31.6%", "0.232")

# 单测例数的声明形态：'119 unit tests' / '(119 cases)' / '119 例单测' / '单元测试 119 例'
TEST_COUNT_PATTERNS = (
    re.compile(r"(\d+)\s+unit tests"),
    re.compile(r"\((\d+)\s+cases\)"),
    re.compile(r"(\d+)\s+例单测"),
    re.compile(r"单元测试[（(]?\s*(\d+)\s*例"),
)


def _read(path):
    with io.open(os.path.join(ROOT, path), "r", encoding="utf-8") as f:
        return f.read()


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


def _actual_test_count():
    """实跑单测取例数（unittest 把 'Ran N tests' 写到 stderr）。"""
    proc = subprocess.Popen(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests"],
        cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    _out, err = proc.communicate()
    text = err.decode("utf-8", "replace")
    m = re.search(r"Ran (\d+) tests?", text)
    if proc.returncode != 0 or not m:
        return None, text.strip().splitlines()[-3:]
    return int(m.group(1)), None


def _declared_test_counts(text):
    found = set()
    for pattern in TEST_COUNT_PATTERNS:
        for m in pattern.finditer(text):
            found.add(int(m.group(1)))
    return found


def _current_numbers_table(text):
    """取「当前口径数字表」：表头首次出现 Recall@5 的那张 markdown 表。"""
    lines = text.replace("\r\n", "\n").split("\n")
    for idx, line in enumerate(lines):
        if line.startswith("|") and "Recall@5" in line:
            block = []
            for row in lines[idx:]:
                if not row.startswith("|"):
                    break
                block.append(row)
            return "\n".join(block)
    return None


def main():
    metrics = json.loads(_read("docs/metrics.json"))
    values = _display_values(metrics)
    size_display = _corpus_size_display(metrics)
    problems = []
    notes = []

    # ── 1) 数字存在性（eval_report.md 是生成产物，只做存在性核对）
    report = _read("docs/eval_report.md")
    for value in sorted(values):
        if value not in report:
            problems.append("docs/eval_report.md 缺少数字 %s（报告可能过期，重跑 gen_eval_report）" % value)
    if size_display and size_display not in report:
        problems.append("docs/eval_report.md 缺少当前口径语料条数 %s（报告与语料口径不一致）" % size_display)

    # ── 2) 两份 README：数字 + 语料条数 + 当前口径表不得含退役值
    for readme in README_FILES:
        text = _read(readme)
        for value in sorted(values):
            if value not in text:
                problems.append("%s 缺少数字 %s（与 docs/metrics.json 漂移）" % (readme, value))
        if size_display and size_display not in text:
            problems.append("%s 缺少当前口径语料条数 %s（口径漂移：README 可能仍在宣称旧版语料）"
                            % (readme, size_display))
        table = _current_numbers_table(text)
        if table is None:
            problems.append("%s 找不到当前口径数字表（表头应含 Recall@5）" % readme)
        else:
            for retired in RETIRED_CURRENT_VALUES:
                if retired in table:
                    problems.append("%s 的当前口径数字表里出现已退役数值 %s"
                                    "（历史口径数字只应出现在历代并列表）" % (readme, retired))

    # ── 3) 单测例数一致性（实跑）
    actual, tail = _actual_test_count()
    if actual is None:
        problems.append("无法取得实跑单测例数（unittest discover 失败）：%s" % tail)
    else:
        for path in TEST_COUNT_FILES:
            declared = _declared_test_counts(_read(path))
            if not declared:
                continue
            wrong = sorted(n for n in declared if n != actual)
            if wrong:
                problems.append("%s 声明单测例数 %s，实跑为 %d 例（计数漂移）"
                                % (path, "/".join(str(n) for n in wrong), actual))
        for path in OUT_OF_SCOPE_TEST_COUNT_FILES:
            declared = _declared_test_counts(_read(path))
            wrong = sorted(n for n in declared if n != actual)
            if wrong:
                notes.append("%s 声明单测例数 %s，实跑 %d 例（该文件归语料线维护，本检查不判失败，"
                             "请其维护者同步）" % (path, "/".join(str(n) for n in wrong), actual))

    if problems:
        print("数字一致性检查未通过：")
        for p in problems:
            print("  -", p)
        for n in notes:
            print("  ! ", n)
        print("单一来源：docs/metrics.json 与 unittest 实跑；"
              "同步方式：跑 scripts/gen_eval_report.py 并更新 README。")
        sys.exit(1)
    print("数字一致性检查 OK：%d 个展示口径数字、口径语料条数 %s、单测 %d 例，"
          "在两份 README 与评测报告中均一致（当前口径表无退役数值）。"
          % (len(values), size_display, actual if actual else -1))
    for n in notes:
        print("  ! ", n)


if __name__ == "__main__":
    main()
