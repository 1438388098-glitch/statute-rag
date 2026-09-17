# -*- coding: utf-8 -*-
"""条文导入器：从 legal-wisdom 的 legal.db 导入结构化条文，并做语料质检。

为什么需要质检：源库来自 PDF 解析，抽样发现两类污染——
  1. ``(cid:xx)`` 字体映射残片（该处文本基本不可信）；
  2. 过短内容（多为解析残渣，无法作为检索单元）。
导入时过滤并输出统计，宁可少导入也不把坏文本放进索引。
"""
import io
import json
import os
import sqlite3

# 一条条文长度的下限：低于此值视为解析残渣而非可用条文
MIN_ARTICLE_LEN = 30

# 需要过滤的内容污染标记（PDF 字体映射失败时 PyMuPDF 会输出 (cid:nn)）
BAD_MARKERS = ("(cid:",)


def iter_raw_articles(db_path):
    """逐条产出 (article_id, law_title, article_num, content)。"""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT a.id, d.title, a.article_num, a.content "
        "FROM articles a JOIN documents d ON a.doc_id = d.id "
        "ORDER BY a.id"
    )
    for row in cursor.fetchall():
        yield row[0], row[1], row[2], row[3] or ""
    conn.close()


def is_clean(content):
    """单条质检：无污染标记、长度达标、去空白后仍有内容。"""
    if not content:
        return False
    compact = content.strip()
    if len(compact) < MIN_ARTICLE_LEN:
        return False
    for marker in BAD_MARKERS:
        if marker in compact:
            return False
    return True


def import_articles(db_path, out_jsonl):
    """导入全部干净条文到 JSONL，返回质检统计。

    输出行格式：{"id": 条文ID, "law": 法律名, "num": 条号, "text": 条文原文}
    """
    stats = {
        "total": 0, "imported": 0,
        "dropped_cid": 0, "dropped_short": 0, "dropped_empty": 0,
    }
    os.makedirs(os.path.dirname(os.path.abspath(out_jsonl)), exist_ok=True)
    with io.open(out_jsonl, "w", encoding="utf-8") as f:
        for article_id, law, num, content in iter_raw_articles(db_path):
            stats["total"] += 1
            if not content or not content.strip():
                stats["dropped_empty"] += 1
                continue
            if "(cid:" in content:
                stats["dropped_cid"] += 1
                continue
            if len(content.strip()) < MIN_ARTICLE_LEN:
                stats["dropped_short"] += 1
                continue
            stats["imported"] += 1
            f.write(json.dumps({
                "id": article_id, "law": law, "num": num,
                "text": " ".join(content.split()),
            }, ensure_ascii=False) + "\n")
    return stats


def load_corpus(jsonl_path):
    """读回 JSONL 语料，返回条文 dict 列表。"""
    items = []
    with io.open(jsonl_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                items.append(json.loads(line))
    return items
