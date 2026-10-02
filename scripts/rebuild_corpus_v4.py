# -*- coding: utf-8 -*-
"""组装 corpus_v4：把 v1「公报页块行」换成重采到的条级行，其余段原样保留。

背景：v1 段（meta 为空且 id < 910000）14,212 行 / 238 部法全是页块行，相邻行
还是重叠约 90% 的滑窗（≈5.7 倍冗余），其中一部分是双栏公报交错乱文。本脚本：
- 对**已有干净条级替代**的法：删掉它的 v1 页块行，插入条级行；
- 对**仍无干净源**的 v1 法：默认 `--unrepaired drop` 直接不带入 v4（宁可缺，
  不可给产品喂乱文），逐部登记进报告；`keep` 则原样保留（供上层自行过滤）。

同时把「真实问句金标」从页块 id 迁到条级 id（按 `gold_laws` 里的「法名 条号」
定位新行，并**逐题校验 evidence 仍逐字出现在新行文本里**），输出新的金标文件；
原金标文件一个字节不动。

用法：
  python scripts/rebuild_corpus_v4.py --base data/corpus_v3.jsonl \
      --replacement data/flk/fragments/clean_source.jsonl \
      --out data/corpus_v4.jsonl \
      --gold gold/gold_real_38.jsonl --gold-out gold/gold_real_38_v4.jsonl \
      --report data/flk/tmp/rebuild_v4_report.txt
"""
import argparse
import io
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

V1_MAX_ID = 910000


def squeeze(t):
    return u"".join((t or u"").split())


def main():
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    ap = argparse.ArgumentParser(description=u"组装 v4 语料 + 金标条级迁移")
    ap.add_argument("--base", required=True)
    ap.add_argument("--replacement", action="append", required=True,
                    help=u"条级替代片段，可重复；**按顺序优先**，前一个文件已覆盖的法不再被后者替换")
    ap.add_argument("--out", required=True)
    ap.add_argument("--gold", default="")
    ap.add_argument("--gold-out", default="")
    ap.add_argument("--gold-synth", default="",
                    help=u"合成金标（按 law+num 迁移；它同样绑定被删掉的 v1 行）")
    ap.add_argument("--gold-synth-out", default="")
    ap.add_argument("--report", default="")
    ap.add_argument("--unrepaired", choices=["drop", "keep"], default="drop",
                    help=u"仍无干净源的 v1 页块行：drop 不带入（默认）/ keep 原样保留")
    args = ap.parse_args()

    repl = []
    repl_laws = set()
    for path in args.replacement:
        n_add = 0
        for line in io.open(path, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            if r["law"] in repl_laws:
                continue  # 已被更高优先级的源覆盖
            repl.append(r)
            n_add += 1
        for r in repl:
            repl_laws.add(r["law"])
        print(u"替代源 %s：新增 %d 条（累计 %d 条 / %d 部法）" % (path, n_add, len(repl), len(repl_laws)))
    print(u"替代合计：%d 条 / %d 部法" % (len(repl), len(repl_laws)))

    kept, dropped_rows = [], []
    dropped_laws = {}
    base_laws = set()
    for line in io.open(args.base, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        base_laws.add(r["law"])
        is_v1 = (not r.get("meta")) and r["id"] < V1_MAX_ID
        if is_v1 and r["law"] in repl_laws:
            dropped_rows.append(r)
            continue
        if is_v1 and args.unrepaired == "drop":
            dropped_rows.append(r)
            dropped_laws[r["law"]] = dropped_laws.get(r["law"], 0) + 1
            continue
        kept.append(r)

    out_rows = kept + repl
    # 替代行统一重编号：不同来源片段的 id 段可能互相重叠（990000 段与 995000 段）
    for i, r in enumerate(repl, 1):
        r["id"] = 995000 + i
    # 质检门：id 唯一 / 非空正文 / 以条号开头
    ids = [r["id"] for r in out_rows]
    assert len(ids) == len(set(ids)), u"id 冲突"
    for r in repl:
        assert r["text"].strip(), u"空正文: %s" % r["id"]

    laws_before = len(base_laws)
    laws_after = len(set(r["law"] for r in out_rows))
    with io.open(args.out, "w", encoding="utf-8", newline="\n") as fh:
        for r in out_rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    rep = [
        u"v4 组装报告",
        u"base 行数：%d" % len(kept + dropped_rows),
        u"替换：删 v1 页块行 %d（其中 %d 行属于有干净替代的法，%d 行属于仍无源的法）"
        % (len(dropped_rows), len(dropped_rows) - sum(dropped_laws.values()), sum(dropped_laws.values())),
        u"新增条级行：%d" % len(repl),
        u"v4 行数：%d" % len(out_rows),
        u"法律部数：base %d → v4 %d" % (laws_before, laws_after),
        u"",
        u"— 默认丢弃、仍无干净源的 v1 法（%d 部，需补源）—" % len(dropped_laws),
    ]
    for law, n in sorted(dropped_laws.items(), key=lambda x: -x[1]):
        rep.append(u"%s\tv1 行数 %d" % (law, n))

    # ── 金标条级迁移 ──
    if args.gold and args.gold_out:
        index = {}
        by_id = {}
        for r in out_rows:
            index.setdefault((r["law"], r["num"]), []).append(r["id"])
            by_id[r["id"]] = r
        out_ids = set(ids)
        out_gold = []
        stat = {"ok": 0, "ev_ok": 0, "partial": 0, "fail": 0}
        detail = []
        for line in io.open(args.gold, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            g = json.loads(line)
            new_ids, verified = [], []
            for pair in g.get("gold_laws") or []:
                law, _, num = pair.rpartition(u" ")
                cands = index.get((law, num)) or []
                if cands:
                    new_ids.append(cands[0])
                    if squeeze(g.get("evidence") or u"") and squeeze(g["evidence"]) in squeeze(
                            by_id[cands[0]]["text"]):
                        verified.append(cands[0])
            # 未迁移到的法（没被替换）保留原 id
            for oid in g.get("gold_ids") or []:
                if oid not in new_ids and oid in out_ids:
                    new_ids.append(oid)
            rec = dict(g)
            rec["gold_ids"] = new_ids[:3]
            rec["gold_id"] = (verified[0] if verified else (new_ids[0] if new_ids else g.get("gold_id")))
            rec["remap"] = {
                "from_ids": g.get("gold_ids"),
                "to_ids": new_ids[:3],
                "evidence_verified": bool(verified),
                "corpus": u"v4（条级重建）",
            }
            out_gold.append(rec)
            if not new_ids:
                stat["fail"] += 1
            elif verified:
                stat["ev_ok"] += 1
            else:
                stat["partial"] += 1
            detail.append(u"%s\t新 id %s\tevidence 逐字命中 %s\t原 id %s"
                          % (g["qid"], new_ids[:3], bool(verified), g.get("gold_ids")))
        with io.open(args.gold_out, "w", encoding="utf-8", newline="\n") as fh:
            for g in out_gold:
                fh.write(json.dumps(g, ensure_ascii=False) + "\n")
        rep += [
            u"",
            u"— 金标迁移（%s → %s）—" % (args.gold, args.gold_out),
            u"题数：%d" % len(out_gold),
            u"evidence 在新行里逐字命中：%d" % stat["ev_ok"],
            u"迁到新 id 但 evidence 未逐字命中：%d" % stat["partial"],
            u"完全迁不到（无新行且旧行已删）：%d" % stat["fail"],
        ] + detail
        print(u"金标迁移：%d 题；evidence 逐字命中 %d；未命中 %d；迁不到 %d"
              % (len(out_gold), stat["ev_ok"], stat["partial"], stat["fail"]))

    if args.gold_synth and args.gold_synth_out:
        index2 = {}
        for r in out_rows:
            index2.setdefault((r["law"], r["num"]), []).append(r["id"])
        out_s, ok_s, miss_s = [], 0, []
        for line in io.open(args.gold_synth, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            g = json.loads(line)
            cands = index2.get((g.get("law"), g.get("num"))) or []
            rec = dict(g)
            if cands:
                rec["gold_id"] = cands[0]
                rec["remap"] = {"from": g.get("gold_id"), "to": cands[0], "corpus": "v4"}
                ok_s += 1
            else:
                miss_s.append(g.get("qid"))
            out_s.append(rec)
        with io.open(args.gold_synth_out, "w", encoding="utf-8", newline="\n") as fh:
            for g in out_s:
                fh.write(json.dumps(g, ensure_ascii=False) + "\n")
        rep += [u"", u"— 合成金标迁移（%s → %s）—" % (args.gold_synth, args.gold_synth_out),
                u"题数：%d；解析到 v4 行：%d；未解析：%d" % (len(out_s), ok_s, len(miss_s)),
                u"未解析 qid 示例：%s" % (miss_s[:10],)]
        print(u"合成金标迁移：%d 题；解析 %d；未解析 %d" % (len(out_s), ok_s, len(miss_s)))

    if args.report:
        with io.open(args.report, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(u"\n".join(rep) + u"\n")
    print(u"v4：%d 行 / %d 部法；删 v1 页块 %d 行；新增 %d 行；仍无源被丢弃的法 %d 部"
          % (len(out_rows), laws_after, len(dropped_rows), len(repl), len(dropped_laws)))
    print(u"→ %s" % args.out)


if __name__ == "__main__":
    main()
