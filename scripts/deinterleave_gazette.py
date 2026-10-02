# -*- coding: utf-8 -*-
"""把「公报 PDF 被 pdfminer 读成的双栏交错文本」按行结构还原成条级正文。

背景（已查实）：v1 语料来自 `legal-wisdom-app/data/database/legal.db` 的
`documents.content`。入库用的 `pdf_parser.py` 调 pdfminer `extract_text`，
**不做分栏**：公报是双栏排版，输出行按纵坐标排序，左右两栏的行就逐行交替
（L1 R1 L2 R2 …），再被按长度切块成「页块行」——用户报障的
`id=70879`「最高法关于适用民法典侵权责任编的解释（一）第十九条」正文交错即此。

关键事实（实测 257 部文档）：**`documents.content` 保留了行结构**（每行是 PDF 的
一个文本行，行间有空行），且 255/257 部是「纯隔行交替」（同一输出行里不含两栏
合并），因此交错**可以精确还原，不需要猜**：一个页内把非空行按奇偶分成两股，
先左栏后右栏拼起来即可。这比「算法去交错（按 token 奇偶猜）」可靠得多——
后者在 corpus 里行结构已被切块破坏，只能猜。

本脚本做的事：
1. 切页/去页眉页脚：公报横行「…公报」「年第」「期」「２０２５·６」「- 17 -」、
   `(cid:NNNN)` 装饰线、私用区字符行——按「连续 ≥2 行的垃圾块」判定页界；
2. 页内隔行分股 → 两股各拼成一栏 → 按「先左栏」拼成该页正文（顺序用条号
   单调性裁决，见 `pick_order`）；
3. 行即段落，交给 `docx_to_articles.articles_from_paragraphs` 切条
   （条号单调递增约束天然挡住「本法第X条」式行首引用被误切）；
4. 逐条门禁（复用 `resource_from_clean_files.clean`）+ 逐法条号 1..N 全序校验，
   不过门**整法丢弃**、如实记账，不静默凑数；
5. `--validate-against` 给一份「已由干净源件重建好的条级片段」，逐条对金样比对，
   量出这条还原路线的**准确率**（这是本脚本敢不敢用的依据）。

用法：
  # 校验：拿已经用 docx 干净源修好的 132 部法做金样，量还原准确率
  python scripts/deinterleave_gazette.py --ldb ...legal.db \\
      --corpus data/corpus_v4.jsonl \\
      --covered data/flk/fragments/clean_source.jsonl \\
      --validate --report data/flk/tmp/deinterleave_validate.txt

  # 产出：只处理干净源没覆盖到的那些法
  python scripts/deinterleave_gazette.py --ldb ...legal.db \\
      --corpus data/corpus_v4.jsonl \\
      --covered data/flk/fragments/clean_source.jsonl \\
      --out data/flk/fragments/gazette_deinterleave.jsonl \\
      --report data/flk/tmp/deinterleave_report.txt
"""
import argparse
import io
import json
import os
import re
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from scripts.docx_to_articles import articles_from_paragraphs  # noqa: E402
from scripts.resource_from_clean_files import (  # noqa: E402
    ART_RE, V1_MAX_ID, clean, cn2int, norm_title)

ID_BASE = 996000  # 命名空间：995000 段已给干净源件重采行

# 行首条号（resource_from_clean_files 的 ART_RE 只用于计数，没有捕获组）
ART_NUM_RE = re.compile(u"^第([一二三四五六七八九十百千零〇]+)条")

# —— 页眉页脚/装饰线（公报横行）判据 ——
# 全 cid 装饰线，如 (cid:2943)(cid:2943)…
CID_ONLY = re.compile(u"^(?:\\(cid:\\d+\\)\\s*)+$")
# 私用区字符 / 全角数字装饰线
PUA_ONLY = re.compile(u"^[\ue000-\uf8ff\uff10-\uff19\s\-—–·.]+$")
GAZ_PATTERNS = [
    # 公报横行：整行只有报名、没有句读（正文里引用「在公报上刊登」的行带标点，
    # 不能跟着一起丢）
    re.compile(u"^[^，。；：！？]*公报[^，。；：！？]*$"),
    re.compile(u"^年第\\s*\\d*\\s*期$"),         # 年第 期 的碎片
    re.compile(u"^年第$|^期$|^第\\s*\\d+\\s*期$"),
    re.compile(u"^\\d{4}[·.．]\\d{1,2}$"),      # ２０２５·６ / 2025.6
    re.compile(u"^\\d{4}$"),                    # 年份碎片
    re.compile(u"^-\\s*\\d{1,4}\\s*-?$|^\\d{1,4}\\s*-$|^-$"),  # 页码碎片
]
HEAD_MAX_LEN = 40  # 公报横行都很短；含「公报」的正文行不会这么短


def is_garbage(s):
    """页眉/页脚/装饰线碎片（不是法条正文）。"""
    if CID_ONLY.match(s):
        return True
    if u"(cid:" in s:      # 混排 cid = 字体缺 ToUnicode 映射，正文已残，交给逐条门禁
        return False
    if PUA_ONLY.match(s) and not re.search(u"[\u4e00-\u9fff]", s):
        return True
    if len(s) <= HEAD_MAX_LEN:
        for r in GAZ_PATTERNS:
            if r.search(s):
                return True
    return False


def content_blocks(lines):
    """非空行序列 → 被垃圾块隔开的正文块列表。

    页界判据：连续 ≥2 行都是垃圾（公报横行被 pdfminer 拆成若干碎片）。
    单行垃圾不切块——它可能只是某页边缘的孤立碎片，切成块反而会打乱栏股。
    """
    blocks = []
    cur = []
    run = []
    for s in lines:
        if is_garbage(s):
            run.append(s)
            if len(run) >= 2:          # 进入垃圾块：结算当前块
                if cur:
                    blocks.append(cur)
                    cur = []
            continue
        run = []
        cur.append(s)
    if cur:
        blocks.append(cur)
    return blocks


def _violations(seq, prev_max):
    """读一段行序列，统计「条号倒退」次数（正常法条体例里条号必须单调不减）。"""
    bad = 0
    cur = prev_max
    for l in seq:
        m = ART_NUM_RE.match(l)
        if not m:
            continue
        n = cn2int(m.group(1))
        if cur is not None and n < cur:
            bad += 1
        cur = n if cur is None else max(cur, n)
    return bad


def _run_max(seq, prev_max):
    cur = prev_max
    for l in seq:
        m = ART_NUM_RE.match(l)
        if m:
            n = cn2int(m.group(1))
            cur = n if cur is None else max(cur, n)
    return cur


def pick_order(a, b, prev_max):
    """一页两股的先后顺序：正常是「先左栏」。

    页首股序的判断用条号单调性裁决——法条体例里条号必须单调不减，所以把
    (a,b) 与 (b,a) 两种拼法各数一遍「倒退」次数，取少的；打平按「先左栏」。
    这样既不靠猜排版，也不依赖「页首一定是左栏」这种可能被整行标题打乱的假设。
    """
    va = _violations(a, prev_max) + _violations(b, _run_max(a, prev_max))
    vb = _violations(b, prev_max) + _violations(a, _run_max(b, prev_max))
    if vb < va:
        return b, a
    return a, b


def page_lines(block, prev_max=None):
    """把正文块按「先左栏后右栏」重排；返回 (行序列, 新的已见最大条号)。"""
    a, b = block[0::2], block[1::2]
    first, second = pick_order(a, b, prev_max)
    return first + second, _run_max(a + b, prev_max)


def deinterleave(content):
    """legal.db 的 content → 还原后的行序列（未切条）。"""
    lines = [l.strip() for l in (content or u"").split(u"\n")]
    lines = [l for l in lines if l]
    out = []
    prev = None
    for blk in content_blocks(lines):
        seq, prev = page_lines(blk, prev)
        out.extend(seq)
    return out


def main():
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    ap = argparse.ArgumentParser(description=u"公报交错文本按行结构还原为条级正文")
    ap.add_argument("--ldb", required=True, help=u"legal.db（只读）")
    ap.add_argument("--corpus", required=True, help=u"现有语料（取 v1 页块法名与口径）")
    ap.add_argument("--covered", default="", help=u"已由干净源件覆盖的条级片段 jsonl（跳过/做金样）")
    ap.add_argument("--out", default="", help=u"条级片段输出 jsonl（缺省只校验不产出）")
    ap.add_argument("--report", required=True, help=u"文本报告输出")
    ap.add_argument("--validate", action="store_true", help=u"对 covered 里的法做逐条金样比对")
    ap.add_argument("--limit", type=int, default=0, help=u"只处理前 N 部法（0=全部）")
    args = ap.parse_args()

    # 1) v1 待修法（页块占比 ≥ 0.5）与已覆盖法
    v1_laws = {}
    pageblock = {}
    for line in io.open(args.corpus, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        if r.get("meta") or r["id"] >= V1_MAX_ID:
            continue
        v1_laws.setdefault(r["law"], []).append(r["id"])
        if len(ART_RE.findall(r["text"])) > 1:
            pageblock[r["law"]] = pageblock.get(r["law"], 0) + 1
    broken = set(l for l in v1_laws
                 if pageblock.get(l, 0) / float(len(v1_laws[l])) >= 0.5)
    covered_frags = {}
    if args.covered and os.path.exists(args.covered):
        for line in io.open(args.covered, encoding="utf-8"):
            line = line.strip()
            if line:
                f = json.loads(line)
                covered_frags.setdefault(f["law"], []).append(f)
    covered_laws = set(covered_frags)

    # 2) legal.db 文档（法名规范化后索引）
    conn = sqlite3.connect(args.ldb)
    docs = {}
    for did, title, cont in conn.execute("select id,title,content from documents"):
        docs[norm_title(title)] = (did, title, cont or u"")
    conn.close()

    todo = sorted(broken)
    if args.limit:
        todo = todo[:args.limit]
    report = []
    frags = []
    stats = {u"还原文数": 0, u"丢弃法数": 0, u"无文档": 0, u"逐法条号全序": 0,
             u"丢弃条数": 0, u"金样法数": 0, u"金样条数": 0, u"金样逐字相同": 0,
             u"金样缺条": 0, u"金样不符": 0}
    mismatch_examples = []

    for law in todo:
        key = norm_title(law)
        doc = docs.get(key)
        if not doc:
            stats[u"无文档"] += 1
            report.append(u"%s\t无对应文档（legal.db 名对不上）" % law)
            continue
        did, title, cont = doc
        lines = deinterleave(cont)
        arts, _ = articles_from_paragraphs(lines)
        kept, dropped = [], []
        for a in arts:
            why = clean(a)
            if why:
                dropped.append((a["num"], why))
            else:
                kept.append(a)
        nums = []
        for a in kept:
            m = re.match(u"^第([一二三四五六七八九十百千零〇]+)条", a["num"])
            if m:
                nums.append(cn2int(m.group(1)))
        mono = all(nums[i] < nums[i + 1] for i in range(len(nums) - 1))
        complete = bool(nums) and nums == list(range(1, len(nums) + 1))
        stats[u"丢弃条数"] += len(dropped)
        if not kept or not mono:
            stats[u"丢弃法数"] += 1
            report.append(u"%s\tid=%d\t条=%d\t丢弃=%d\t单调=%s\t整法丢弃" % (
                law, did, len(kept), len(dropped), mono))
            continue
        stats[u"还原文数"] += 1
        if complete:
            stats[u"逐法条号全序"] += 1
        report.append(u"%s\tid=%d\t条=%d\t丢弃=%d\t单调=%s\t全序1..N=%s\tcid残留=%d" % (
            law, did, len(kept), len(dropped), mono, complete,
            sum(1 for a in kept if u"(cid:" in a["text"])))
        for i, a in enumerate(kept, 1):
            frags.append({
                "id": 0, "law": law, "num": a["num"], "text": a["text"],
                "meta": {"source": u"legal.db 公报文本·按行结构还原",
                         "text_source": "legal-db-line-deinterleave",
                         "ldb_doc_id": did, "ldb_title": title,
                         "numbering": u"第X条"},
            })
        # 3) 金样比对（同一部法：干净源件产出的条 vs 本条还原的条）
        if args.validate and law in covered_laws:
            gold = {}
            for f in covered_frags[law]:
                gold[f["num"]] = f["text"]
            hit = miss = absent = 0
            for a in kept:
                g = gold.get(a["num"])
                if g is None:
                    absent += 1
                elif _same(a["text"], g):
                    hit += 1
                else:
                    miss += 1
                    if len(mismatch_examples) < 8:
                        mismatch_examples.append((law, a["num"], g, a["text"]))
            stats[u"金样法数"] += 1
            stats[u"金样条数"] += len(kept)
            stats[u"金样逐字相同"] += hit
            stats[u"金样缺条"] += absent
            stats[u"金样不符"] += miss
            report.append(u"    └ 金样比对：逐字相同 %d / 不符 %d / 金样无此条 %d（金样条数 %d）"
                          % (hit, miss, absent, len(gold)))

    if args.out and frags:
        for i, f in enumerate(frags, 1):
            f["id"] = ID_BASE + i
        with io.open(args.out, "w", encoding="utf-8", newline="\n") as fh:
            for f in frags:
                fh.write(json.dumps(f, ensure_ascii=False) + "\n")

    head = [
        u"公报交错文本·按行结构还原报告",
        u"待修法（v1 页块占比 ≥ 0.5）：%d 部" % len(broken),
        u"本次处理：%d 部" % len(todo),
        u"还原成功：%d 部 / 丢条 %d" % (stats[u"还原文数"], stats[u"丢弃条数"]),
        u"逐法条号 1..N 全序：%d 部" % stats[u"逐法条号全序"],
        u"整法丢弃：%d 部" % stats[u"丢弃法数"],
        u"legal.db 无对应文档：%d 部" % stats[u"无文档"],
        u"产出条级行：%d" % len(frags),
    ]
    if args.validate:
        head += [
            u"",
            u"— 金样校验（与干净源件重采结果逐条比对）—",
            u"金样法数：%d，金样条数：%d" % (stats[u"金样法数"], stats[u"金样条数"]),
            u"逐字相同：%d（%.1f%%）" % (
                stats[u"金样逐字相同"],
                100.0 * stats[u"金样逐字相同"] / max(1, stats[u"金样条数"])),
            u"文本不符：%d" % stats[u"金样不符"],
            u"金样无此条号：%d" % stats[u"金样缺条"],
        ]
        for law, num, g, t in mismatch_examples:
            head += [u"",
                     u"【不符样例】%s %s" % (law, num),
                     u"  金样：%s" % g[:200],
                     u"  还原：%s" % t[:200]]
    with io.open(args.report, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(u"\n".join(head + [u""] + report) + u"\n")
    print(u"处理 %d 部；还原 %d 部；整法丢弃 %d 部；无文档 %d 部；产出 %d 条"
          % (len(todo), stats[u"还原文数"], stats[u"丢弃法数"], stats[u"无文档"], len(frags)))
    if args.validate:
        print(u"金样逐字相同 %d / %d（%.1f%%），不符 %d，缺条 %d" % (
            stats[u"金样逐字相同"], stats[u"金样条数"],
            100.0 * stats[u"金样逐字相同"] / max(1, stats[u"金样条数"]),
            stats[u"金样不符"], stats[u"金样缺条"]))
    print(u"→ %s" % args.report)


def _same(x, y):
    return u"".join(x.split()) == u"".join(y.split())


if __name__ == "__main__":
    main()
