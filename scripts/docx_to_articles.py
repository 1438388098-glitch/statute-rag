# -*- coding: utf-8 -*-
"""把国家法律法规数据库（flk.npc.gov.cn）下载的官方 docx 转成条级 JSONL 片段。

输入文件由浏览器辅助获取（站点有 WAF，纯脚本无法直连；获取方法与来源
记录见 data/flk/MANIFEST.md）。docx 是官方原生文本，无 (cid:) 残片、
无双栏交错污染，质量高于公报 PDF 解析源。

切条规则：段落以「第X条」开头即开新条（配「条号单调递增」约束，防止
条文内部「本法第X条」类行首引用被误切成新条），非条首段落并入当前条；
「第X编/章/节」标题行丢弃。条号解析支持「之一/之二」修正案后缀。

用法：
  python scripts/docx_to_articles.py --docx data/flk/民法典_20200528.docx \\
      --law "中华人民共和国民法典" --publish 2020-05-28 --effective 2021-01-01 \\
      --source-url "https://flk.npc.gov.cn/detail?id=..." \\
      --id-base 900000 --out data/flk/fragments/民法典.jsonl
"""
import argparse
import io
import json
import re
import sys
import zipfile

sys.path.insert(0, "..") if __name__ == "__main__" and "." not in sys.path else None

_CN_NUM = {"零": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
           "六": 6, "七": 7, "八": 8, "九": 9}
_CN_UNIT = {"十": 10, "百": 100, "千": 1000}
_ARTICLE_RE = re.compile(r"^第([零一二三四五六七八九十百千]+)条(之[一二三四五六七八九十]+)?")
_HEADING_RE = re.compile(r"^第[零一二三四五六七八九十百千]+[编章节]")


def cn_to_int(s):
    """中文数字 → 整数（支持 十/百/千 组合，如 二百六十六）。"""
    total, digit = 0, 0
    for ch in s:
        if ch in _CN_NUM:
            digit = _CN_NUM[ch]
        elif ch in _CN_UNIT:
            unit = _CN_UNIT[ch]
            total += (digit if digit else 1) * unit
            digit = 0
    return total + digit


def article_num_label(line):
    """若段落以「第X条(之N)?」开头返回完整条号标签，否则 None。"""
    m = _ARTICLE_RE.match(line)
    return (m.group(1), m.group(2) or "") if m else None


def docx_paragraphs(path):
    """按文档顺序返回 docx 非空段落文本（纯标准库解 zip + 正则去标签）。"""
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml").decode("utf-8")
    out = []
    for p in re.findall(r"<w:p[ >].*?</w:p>", xml, re.S):
        text = "".join(re.findall(r"<w:t[^>]*>([^<]*)</w:t>", p))
        text = " ".join(text.split())
        if text:
            out.append(text)
    return out


def split_articles(path, source_law):
    """docx → [{num, text}]。返回 (articles, skipped_headings)。"""
    articles = []
    last_num = 0
    skipped = 0
    for para in docx_paragraphs(path):
        label = article_num_label(para)
        if label is not None:
            num_int = cn_to_int(label[0])
            # 带修正案后缀（之N）的条与同号条并存，允许同级开新条；
            # 无后缀的条号必须严格递增（防「本法第X条」行首引用误切）
            is_new = (num_int >= last_num) if label[1] else (num_int > last_num)
            if is_new:
                articles.append({"num": "第%s条%s" % label, "text": para})
                last_num = num_int
                continue
            # 条号未递增：视为条文内引用，并入当前条
            if articles:
                articles[-1]["text"] += para
            continue
        if _HEADING_RE.match(para):
            skipped += 1
            continue
        if articles:
            articles[-1]["text"] += para
    return articles, skipped


def main():
    parser = argparse.ArgumentParser(description="flk 官方 docx → 条级 JSONL 片段")
    parser.add_argument("--docx", required=True, help="官方 docx 文件路径")
    parser.add_argument("--law", required=True, help="法律名（与语料 law 字段口径一致）")
    parser.add_argument("--publish", default="", help="公布日期 YYYY-MM-DD")
    parser.add_argument("--effective", default="", help="施行日期 YYYY-MM-DD")
    parser.add_argument("--source-url", default="", help="flk 详情页 URL（来源留痕）")
    parser.add_argument("--id-base", type=int, default=900000,
                        help="片段 id 起始（append-only 命名空间，避免与 legal.db 行 id 冲突）")
    parser.add_argument("--out", required=True, help="输出 JSONL 路径")
    args = parser.parse_args()

    articles, skipped = split_articles(args.docx, args.law)
    with io.open(args.out, "w", encoding="utf-8", newline="\n") as f:
        for i, art in enumerate(articles, 1):
            f.write(json.dumps({
                "id": args.id_base + i,
                "law": args.law,
                "num": art["num"],
                "text": art["text"],
                "meta": {
                    "source": "flk.npc.gov.cn",
                    "source_url": args.source_url,
                    "publish": args.publish,
                    "effective": args.effective,
                    "docx": args.docx.replace("\\", "/"),
                },
            }, ensure_ascii=False) + "\n")
    print("已生成 %s：%d 条（丢弃标题行 %d，id %d-%d）"
          % (args.out, len(articles), skipped, args.id_base + 1, args.id_base + len(articles)))


if __name__ == "__main__":
    main()
