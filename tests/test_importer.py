# -*- coding: utf-8 -*-
"""importer 单测：临时 SQLite 上验证导入与质检过滤。"""
import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from statute_rag.importer import import_articles, is_clean, load_corpus


def make_db(path):
    conn = sqlite3.connect(path)
    cur = conn.cursor()
    cur.execute("CREATE TABLE documents (id INTEGER PRIMARY KEY, title TEXT)")
    cur.execute("CREATE TABLE articles (id INTEGER PRIMARY KEY, doc_id INTEGER, "
                "article_num TEXT, content TEXT)")
    cur.execute("INSERT INTO documents VALUES (1, '测试法')")
    rows = [
        (1, 1, "第一条", "为了测试导入器而设立的本条内容，长度超过三十个字，属于干净条文。"),
        (2, 1, "第二条", "损坏条文含有 (cid:123) 字体残片标记，应被过滤掉。"),
        (3, 1, "第三条", "太短"),  # 过短
        (4, 1, "第四条", None),     # 空内容
        (5, 1, "第五条", "另一条干净条文，同样超过三十个字的长度要求，用于验证正常导入路径。"),
    ]
    cur.executemany("INSERT INTO articles VALUES (?, ?, ?, ?)", rows)
    conn.commit()
    conn.close()
    return path


class ImporterTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = make_db(os.path.join(self.tmp, "t.db"))
        self.out = os.path.join(self.tmp, "corpus.jsonl")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_is_clean_rules(self):
        self.assertTrue(is_clean("这一条内容足够长，稳稳超过三十个字的长度下限要求，而且没有任何污染标记混在里面。"))
        self.assertFalse(is_clean("短内容"))
        self.assertFalse(is_clean("含 (cid:88) 污染的内容即使再长也不应该被导入索引，因为该处的字体映射已经失效。"))
        self.assertFalse(is_clean(""))
        self.assertFalse(is_clean(None))

    def test_import_stats_and_output(self):
        stats = import_articles(self.db, self.out)
        self.assertEqual(stats["total"], 5)
        self.assertEqual(stats["imported"], 2)
        self.assertEqual(stats["dropped_cid"], 1)
        self.assertEqual(stats["dropped_short"], 1)
        self.assertEqual(stats["dropped_empty"], 1)
        corpus = load_corpus(self.out)
        self.assertEqual(len(corpus), 2)
        self.assertEqual(corpus[0]["law"], "测试法")
        self.assertEqual(corpus[0]["num"], "第一条")
        # 空白被归一化
        self.assertNotIn("\n", corpus[0]["text"])

    def test_load_corpus_skips_blank_lines(self):
        import_articles(self.db, self.out)
        with io.open(self.out, "a", encoding="utf-8") as f:
            f.write("\n  \n")
        self.assertEqual(len(load_corpus(self.out)), 2)


if __name__ == "__main__":
    unittest.main()
