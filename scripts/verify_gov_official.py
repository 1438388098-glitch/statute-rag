# -*- coding: utf-8 -*-
"""拿官方页面核对（必要时替换）镜像补出来的那批法。

为什么必须核对：镜像（`LawRefBook/Laws`）的文件名日期**不保证等于正文版本**。
实测反例：`婚姻登记条例(2025-04-06).md` 的正文是 2003 年版（第一条引「婚姻法」），
而国务院令第 804 号（2025-04-06 第二次修订）第一条引的是「民法典」。只按文件名
日期匹配，就会把旧版正文当新版发出去。

本脚本用**官方页面**做核对：
- 数据源 `sousuo.www.gov.cn/search-gov/data`（中国政府网政策文件库）检索法名，
  取标题含该法名的最新页面，抓正文 → 按同一套切条门禁切成条级 → 与镜像逐条比对
  （NFKC 归一后比，全角/半角标点差异不算差异）；
- 一致率 ≥ `--agree-min`（默认 0.95）→ 记「官方核对通过」；
- 不一致 → 记「与官方不符」，若官方文本过门且 `--emit-overrides` 给了路径，
  就把官方文本写成替代片段（来源留痕：官方 URL + 页面里的版本行）。

覆盖范围（如实记账）：中国政府网政策文件库主要收**国务院令/行政法规**；**法律**的
现行文本在国家法律法规数据库（flk，脚本被 WAF 挡）与全国人大网，**司法解释**在
最高人民法院官网——这两类本脚本查不到，会记「无官方页面可比对」，不假装核过。

用法：
  python scripts/verify_gov_official.py --frags data/flk/fragments/mirror_strict.jsonl \\
      --cache data/flk/tmp/gov_cache --out-report data/flk/tmp/gov_verify.txt \\
      --emit-overrides data/flk/fragments/gov_official.jsonl
"""
import argparse
import io
import json
import os
import re
import sqlite3
import sys
import time
import unicodedata

try:
    from urllib.parse import quote
    from urllib.request import Request, urlopen
except ImportError:  # pragma: no cover - py2 兜底
    from urllib import quote
    from urllib2 import Request, urlopen

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from scripts.docx_to_articles import articles_from_paragraphs  # noqa: E402
from scripts.lawrefbook_source import refbook_key  # noqa: E402
from scripts.resource_from_clean_files import cn2int, norm_title  # noqa: E402
from scripts.resource_local_remaining import keep_article, normalize_line  # noqa: E402

ID_BASE = 999000  # 命名空间：999000 官方页面（优先级最高）
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0",
      "Referer": "https://www.gov.cn/"}
SEARCH = ("https://sousuo.www.gov.cn/search-gov/data?t=zhengcelibrary&q=%s&p=1&n=8"
          "&sort=score&sortType=1&searchfield=title")
TAG_RE = re.compile(u"(?s)<[^>]+>")
NUM_RE = re.compile(u"^第([一二三四五六七八九十百千零〇]+)条")


def http_get(url, timeout=30):
    return urlopen(Request(url, headers=UA), timeout=timeout).read().decode("utf-8", "replace")


def clean_title(t):
    return TAG_RE.sub(u" ", t or u"").replace(u"&nbsp;", u" ").strip()


DATE_RE = re.compile(u"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")


def page_version_date(paras):
    """页面里版本说明行上的最晚日期（「…2025年4月6日…第二次修订」）。"""
    best = u""
    for p in paras[:40]:
        for m in DATE_RE.finditer(p):
            d = u"%s-%02d-%02d" % (m.group(1), int(m.group(2)), int(m.group(3)))
            if d > best:
                best = d
    return best


def norm(t):
    t = unicodedata.normalize("NFKC", t or u"")
    return re.sub(u"[\\s\u3000]", u"", t)


def search_law(q, n=8):
    """政策文件库检索 → [(日期, 标题, url)]。"""
    d = json.loads(http_get(SEARCH % quote(q.encode("utf-8"))))
    out = []
    catmap = (d.get("searchVO") or {}).get("catMap", {}) or {}
    for v in catmap.values():
        for it in v.get("listVO") or []:
            url = it.get("url") or u""
            title = clean_title(it.get("title"))
            if url and title:
                out.append((it.get("pubtimeStr") or u"", title, url))
    return out[:n]


def title_matches(law_key, title):
    """官方页面标题是否就是这部法（收紧匹配，避免把「劳动合同法」当成「合同法」）。

    政策文件库的标题常写成「中华人民共和国国务院令（第804号）婚姻登记条例」，
    所以判据是**标题以法名结尾**或完全相等；不做任意子串匹配。
    """
    if not law_key:
        return False
    tk = refbook_key(title)
    return tk == law_key or tk.endswith(law_key)


def page_paras(url):
    """官方页面 → 段落序列（政府网把每个字放进单独节点，需要把单字行合并）。"""
    html = http_get(url)
    html = re.sub(u"(?is)<(script|style)[^>]*>.*?</\\1>", u" ", html)
    txt = TAG_RE.sub(u"\n", html)
    for a, b in ((u"&nbsp;", u" "), (u"&amp;", u"&"), (u"&ldquo;", u"“"),
                 (u"&rdquo;", u"”"), (u"&mdash;", u"—")):
        txt = txt.replace(a, b)
    lines = [l.strip() for l in txt.split(u"\n") if l.strip()]
    out, buf = [], u""
    for l in lines:
        if len(l) <= 2:          # 单字/单标点行 → 并入下一段
            buf += l
            continue
        if buf:
            out.append(buf)
            buf = u""
        out.append(l)
    if buf:
        out.append(buf)
    return [normalize_line(p) for p in out]


def articles_of(lines):
    arts, _ = articles_from_paragraphs(lines)
    kept, dropped = [], 0
    for a in arts:
        ok, _why, _soft = keep_article(a)
        if ok:
            kept.append(a)
        else:
            dropped += 1
    nums = []
    for a in kept:
        m = NUM_RE.match(a["num"])
        if m:
            nums.append(cn2int(m.group(1)))
    mono = all(nums[i] < nums[i + 1] for i in range(len(nums) - 1))
    complete = bool(nums) and nums == list(range(1, len(nums) + 1))
    return {"kept": kept, "nums": nums, "mono": mono, "complete": complete,
            "dropped": dropped, "narts": len(arts)}


def agree(a_arts, b_arts):
    """两组条 → 共有条号上 NFKC 归一后的逐字一致率。"""
    a = dict((x["num"], x["text"]) for x in a_arts)
    b = dict((x["num"], x["text"]) for x in b_arts)
    common = set(a) & set(b)
    if not common:
        return 0.0, 0, 0
    same = sum(1 for k in common if norm(a[k]) == norm(b[k]))
    return same / float(len(common)), same, len(common)


def version_line(paras):
    """取页面里形如「（…公布　…修订）」的版本行（版本留痕）。"""
    for p in paras[:40]:
        if u"修订" in p or u"公布" in p:
            return p[:200]
    return u""


def main():
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    ap = argparse.ArgumentParser(description=u"用官方页面核对镜像补出的法")
    ap.add_argument("--frags", required=True, help=u"待核对的条级片段 jsonl（镜像）")
    ap.add_argument("--ldb", required=True, help=u"legal.db（取 publish_date 做版本门）")
    ap.add_argument("--out-report", required=True, help=u"核对报告输出")
    ap.add_argument("--cache", default="", help=u"官方页面文本缓存目录")
    ap.add_argument("--emit-overrides", default="", help=u"与官方不符且官方文本过门时，输出替代片段")
    ap.add_argument("--agree-min", type=float, default=0.95, help=u"判定「核对通过」的一致率下限")
    ap.add_argument("--sleep", type=float, default=0.4, help=u"请求间隔（秒），别给官方站压力")
    ap.add_argument("--limit", type=int, default=0, help=u"只核对前 N 部（0=全部）")
    args = ap.parse_args()

    conn = sqlite3.connect(args.ldb)
    ldb = {}
    for title, pub in conn.execute("select title, publish_date from documents"):
        ldb[norm_title(title)] = pub or u""
    conn.close()

    by_law = {}
    for line in io.open(args.frags, encoding="utf-8"):
        line = line.strip()
        if line:
            f = json.loads(line)
            by_law.setdefault(f["law"], []).append(f)
    laws = sorted(by_law)
    if args.limit:
        laws = laws[:args.limit]

    report = []
    overrides = []
    stat = {"pass": 0, "diff": 0, "nopage": 0, "fail": 0, "stale": 0, "override_laws": 0}
    for law in laws:
        mine = [{"num": f["num"], "text": f["text"]} for f in by_law[law]]
        key = refbook_key(law)
        try:
            hits = search_law(law)
        except Exception as e:
            stat["fail"] += 1
            report.append(u"%s\t检索失败：%s" % (law, e))
            continue
        cands = []
        for date, title, url in hits:
            if title_matches(key, title):
                cands.append((date, title, url))
        if not cands:
            stat["nopage"] += 1
            report.append(u"%s\t无官方页面（政策文件库无同名文书；检索到 %d 条均非该法）"
                          % (law, len(hits)))
            continue
        best = None
        tried = []
        for date, title, url in cands[:3]:
            cache_path = u""
            if args.cache:
                if not os.path.isdir(args.cache):
                    os.makedirs(args.cache)
                cache_path = os.path.join(args.cache, u"%s.json" % re.sub(u"[^0-9A-Za-z]", u"_", url)[-40:])
            if cache_path and os.path.exists(cache_path):
                got = json.load(io.open(cache_path, encoding="utf-8"))
                paras = got["paras"]
            else:
                try:
                    paras = page_paras(url)
                except Exception as e:
                    tried.append(u"%s 抓取失败(%s)" % (url, e))
                    continue
                if cache_path:
                    with io.open(cache_path, "w", encoding="utf-8", newline="\n") as fh:
                        fh.write(json.dumps({"url": url, "date": date, "title": title,
                                             "paras": paras}, ensure_ascii=False))
                time.sleep(args.sleep)
            q = articles_of(paras)
            rate, same, tot = agree(mine, q["kept"])
            tried.append(u"%s 条%d 一致%d/%d" % (date or u"无日期", len(q["kept"]), same, tot))
            if best is None or rate > best[0]:
                best = (rate, same, tot, date, title, url, q, paras)
            if rate >= args.agree_min:
                break
        rate, same, tot, date, title, url, q, paras = best
        mine_max = max([cn2int(NUM_RE.match(x["num"]).group(1)) for x in mine
                        if NUM_RE.match(x["num"])] or [0])
        off_max = max(q["nums"] or [0])
        same_scale = bool(off_max and mine_max and 0.7 <= off_max / float(mine_max) <= 1.4)
        ok_official = (len(q["kept"]) >= 2 and q["mono"] and q["complete"] and same_scale)
        # 版本门：政策文件库里大量是**老公报页**（2004/2006 年的扫描版法律），
        # 页面版本早于库内公布日期的一律不采用，否则就把现行文本换成旧版了。
        vdate = page_version_date(paras)
        want = ldb.get(norm_title(law), u"")
        current = bool(not want or not vdate or vdate >= want)
        if not current:
            stat["stale"] += 1
            report.append(u"%s\t官方页面为旧版（页面 %s < 库内 %s），不采用\t%s" % (
                law, vdate, want, url))
            continue
        if rate >= args.agree_min:
            stat["pass"] += 1
            report.append(u"%s\t与官方逐条一致 %.1f%%（%d/%d）\t页面版本 %s\t%s" % (
                law, 100 * rate, same, tot, vdate or u"未标注", url))
        else:
            stat["diff"] += 1
            report.append(u"%s\t与镜像不一致 %.1f%%（%d/%d）\t官方条%d 全序%s\t页面版本 %s\t%s" % (
                law, 100 * rate, same, tot, len(q["kept"]), q["complete"],
                vdate or u"未标注", url))
        # 官方文本优先：页面版本不旧、文本过门，就用官方文本当该法的正文
        # （镜像的文件名日期不保证等于正文版本，实测有旧版正文挂新日期的情形）
        if ok_official and args.emit_overrides:
            stat["override_laws"] += 1
            for a in q["kept"]:
                overrides.append({
                    "id": 0, "law": law, "num": a["num"], "text": a["text"],
                    "meta": {
                        "source": u"中国政府网政策文件库（官方页面）",
                        "text_source": "gov-cn-official",
                        "url": url,
                        "publish": date,
                        "version_line": version_line(paras),
                        "mirror_agree": round(rate, 3),
                        "numbering": u"第X条",
                    },
                })

    if args.emit_overrides and overrides:
        for i, f in enumerate(overrides, 1):
            f["id"] = ID_BASE + i
        with io.open(args.emit_overrides, "w", encoding="utf-8", newline="\n") as fh:
            for f in overrides:
                fh.write(json.dumps(f, ensure_ascii=False) + "\n")

    head = [
        u"官方页面核对报告（中国政府网政策文件库）",
        u"核对法数：%d" % len(laws),
        u"官方核对通过：%d 部" % stat["pass"],
        u"与镜像不一致（官方现行版）：%d 部" % stat["diff"],
        u"官方页面为旧版（不采用）：%d 部" % stat["stale"],
        u"采用官方文本替代镜像：%d 部" % stat["override_laws"],
        u"无官方页面可比对：%d 部（法律/司法解释不在该库，需 flk 或最高法官网）" % stat["nopage"],
        u"检索或抓取失败：%d 部" % stat["fail"],
        u"",
    ]
    with io.open(args.out_report, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(u"\n".join(head + report) + u"\n")
    print(u"核对 %d 部：与官方一致 %d，与镜像不一致 %d，官方旧版 %d，无官方页面 %d，失败 %d，采用官方替代 %d"
          % (len(laws), stat["pass"], stat["diff"], stat["stale"], stat["nopage"],
             stat["fail"], stat["override_laws"]))
    print(u"→ %s" % args.out_report)


if __name__ == "__main__":
    main()
