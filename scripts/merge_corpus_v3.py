# -*- coding: utf-8 -*-
"""把 v3 片段（竹马电子法条 + flk 官方文件）合入语料 v3，append-only。

纪律：v1/v2 的既有行字节冻结——真实金标 gold_ids 绑定这些行 id，重编号会
让历史评测不可复现。新片段用保留 id 段（970000 竹马、980000 flk），合入只
追加不改写既有行。

质检门（任一不过即整体失败，不产出半成品）：
- id 与既有语料冲突；
- (law, num) 在合入集合内重复；
- text 为空或含 PDF 残片标记 (cid:)；
- 合入后总条数应等于 既有 + 新增。

用法：
  python scripts/merge_corpus_v3.py --base data/corpus_v2.jsonl \\
      --fragments data/flk/fragments/zhuma_v3.jsonl data/flk/fragments/flk_v3.jsonl \\
      --out data/corpus_v3.jsonl --report data/flk/tmp/merge_v3_report.txt
"""
import argparse
import io
import json
import sys

BAD_MARKERS = ("(cid:",)


def load_jsonl(path):
    rows = []
    with io.open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def main():
    parser = argparse.ArgumentParser(description="合入语料 v3（append-only + 质检门）")
    parser.add_argument("--base", required=True)
    parser.add_argument("--fragments", nargs="+", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--report", required=True)
    args = parser.parse_args()

    base = load_jsonl(args.base)
    base_ids = set(r["id"] for r in base)

    new_rows = []
    for path in args.fragments:
        new_rows.extend(load_jsonl(path))

    errors = []
    # 1) id 冲突
    dup_ids = [r["id"] for r in new_rows if r["id"] in base_ids]
    if dup_ids:
        errors.append("id 与既有语料冲突 %d 个：%s" % (len(dup_ids), dup_ids[:5]))
    seen_id = set()
    for r in new_rows:
        if r["id"] in seen_id:
            errors.append("片段内 id 重复：%s" % r["id"])
            break
        seen_id.add(r["id"])

    # 2) (law, num) 重复（合入集合内）
    seen_pair = {}
    dup_pairs = []
    for r in new_rows:
        key = (r["law"], r["num"])
        if key in seen_pair:
            dup_pairs.append(key)
        seen_pair[key] = r["id"]
    if dup_pairs:
        errors.append("合入集合内 (law, num) 重复 %d 组：%s"
                      % (len(dup_pairs), ["%s %s" % p for p in dup_pairs[:5]]))

    # 3) 空文本 / PDF 残片
    bad_text = [r["id"] for r in new_rows if not (r["text"] or "").strip()]
    if bad_text:
        errors.append("空文本 %d 条：%s" % (len(bad_text), bad_text[:5]))
    bad_marker = [r["id"] for r in new_rows
                  if any(m in r["text"] for m in BAD_MARKERS)]
    if bad_marker:
        errors.append("含 PDF 残片标记 %d 条：%s" % (len(bad_marker), bad_marker[:5]))

    if errors:
        sys.stderr.write("质检未通过，未产出：\n")
        for e in errors:
            sys.stderr.write("  - %s\n" % e)
        return 1

    merged = base + new_rows
    if len(merged) != len(base) + len(new_rows):
        sys.stderr.write("质检未通过：合入后条数不等于 既有+新增\n")
        return 1

    with io.open(args.out, "w", encoding="utf-8", newline="\n") as f:
        for r in merged:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    law_count = {}
    for r in new_rows:
        law_count[r["law"]] = law_count.get(r["law"], 0) + 1
    with io.open(args.report, "w", encoding="utf-8", newline="\n") as f:
        f.write("既有语料 %d 条 → 合入 %d 条 → 总计 %d 条\n"
                % (len(base), len(new_rows), len(merged)))
        f.write("新增法律 %d 部\n\n" % len(law_count))
        for law in sorted(law_count, key=lambda k: -law_count[k]):
            f.write("%5d\t%s\n" % (law_count[law], law))

    print("既有 %d 条 + 新增 %d 条（%d 部法律）= %d 条 → %s"
          % (len(base), len(new_rows), len(law_count), len(merged), args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
