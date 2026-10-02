# -*- coding: utf-8 -*-
"""docx 条级切分单测：中文条号解析、单调递增约束、标题行丢弃、修正案后缀。"""
import os
import sys
import unittest
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts.docx_to_articles import article_num_label, cn_to_int, split_articles

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))


def make_docx(path, paragraphs):
    """构造最小 docx（zip + document.xml，仅够 zipfile/正则解析）。"""
    body = "".join(
        "<w:p><w:r><w:t>%s</w:t></w:r></w:p>" % p for p in paragraphs
    )
    xml = ('<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/'
           'wordprocessingml/2006/main"><w:body>%s</w:body></w:document>') % body
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/document.xml", xml)


class CnToIntTest(unittest.TestCase):
    def test_basic_and_composite(self):
        self.assertEqual(cn_to_int("一"), 1)
        self.assertEqual(cn_to_int("十"), 10)
        self.assertEqual(cn_to_int("十五"), 15)
        self.assertEqual(cn_to_int("一百零五"), 105)
        self.assertEqual(cn_to_int("二百六十六"), 266)
        self.assertEqual(cn_to_int("一千二百六十"), 1260)


class ArticleNumLabelTest(unittest.TestCase):
    def test_match_and_suffix(self):
        self.assertEqual(article_num_label("第一条　为了保护民事主体的合法权益"), ("一", ""))
        self.assertEqual(article_num_label("第二十五条之一 伪造货币的"), ("二十五", "之一"))
        self.assertIsNone(article_num_label("依照本法第一条的规定"))
        self.assertIsNone(article_num_label("（一）主动消除或者减轻违法后果"))


class SplitArticlesTest(unittest.TestCase):
    def test_split_with_monotonic_guard(self):
        path = os.path.join(os.path.dirname(__file__), "_tmp_docx.docx")
        try:
            make_docx(path, [
                "测试法", "第一章　总则",  # 标题行应丢弃
                "第一条　本法总则规定。",
                "（一）总则第一项；",      # 非条首，并入第一条
                "依照本法第一条执行。",    # 行内引用，并入第一条
                "第二条　本法分则规定。",
                "第三条　本法附则规定。",
                "第三条之一　修正案新增条款。",  # 之N 后缀允许
            ])
            articles, skipped = split_articles(path, "测试法")
            self.assertEqual([a["num"] for a in articles],
                             ["第一条", "第二条", "第三条", "第三条之一"])
            self.assertIn("（一）总则第一项", articles[0]["text"])
            self.assertIn("依照本法第一条执行", articles[0]["text"])
            self.assertEqual(skipped, 1)
        finally:
            if os.path.exists(path):
                os.remove(path)


if __name__ == "__main__":
    unittest.main()
