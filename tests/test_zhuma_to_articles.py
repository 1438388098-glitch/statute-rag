# -*- coding: utf-8 -*-
"""zhuma_to_articles 切条行为的单元测试（金样锁行为）。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts.zhuma_to_articles import split_chapter, strip_prefix
from scripts.docx_to_articles import best_effort_articles


class TestStripPrefix(unittest.TestCase):
    def test_strips_official_prefix(self):
        self.assertEqual(strip_prefix("中华人民共和国民法典"), "民法典")

    def test_keeps_short_title(self):
        self.assertEqual(strip_prefix("民法典"), "民法典")

    def test_keeps_non_state_law(self):
        self.assertEqual(strip_prefix("最高人民法院关于适用《中华人民共和国民法典》有关担保制度的解释"),
                         "最高人民法院关于适用《中华人民共和国民法典》有关担保制度的解释")


class TestSplitChapter(unittest.TestCase):
    def test_basic_articles_with_preamble(self):
        content = ("中华人民共和国宪法\n\n"
                   "（1982年12月4日通过 2018年修正）\n\n"
                   "第一章 总纲\n\n"
                   "第一条 中华人民共和国是工人阶级领导的、以工农联盟为基础的人民民主专政的社会主义国家。\n\n"
                   "第二条 中华人民共和国的一切权力属于人民。\n\n"
                   "人民依照法律规定，通过各种途径和形式，管理国家事务。")
        arts, skipped = split_chapter(content)
        self.assertEqual(skipped, 1)  # 「第一章 总纲」
        self.assertEqual([a["num"] for a in arts], ["第一条", "第二条"])
        self.assertTrue(arts[1]["text"].endswith("管理国家事务。"))

    def test_intra_article_reference_not_split(self):
        content = "第三条 有本法第五条规定情形的，适用本章规定。\n本法第五条所称情形，由国务院规定。"
        arts, _ = split_chapter(content)
        self.assertEqual(len(arts), 1)
        self.assertEqual(arts[0]["num"], "第三条")
        self.assertIn("由国务院规定", arts[0]["text"])

    def test_zhi_suffix_allows_same_number(self):
        # 竹马数据实测「之N」紧贴条号（无空格），与 flk docx 格式一致
        content = "第三条之一 基本条款。\n第三条之二 补充条款。\n第四条 后续条款。"
        arts, _ = split_chapter(content)
        self.assertEqual([a["num"] for a in arts], ["第三条之一", "第三条之二", "第四条"])

    def test_empty_chapter_returns_empty(self):
        arts, skipped = split_chapter("序言\n\n中国是世界上历史最悠久的国家之一。")
        self.assertEqual(arts, [])
        self.assertEqual(skipped, 0)  # 「序言」不是编章节标题

    def test_chapter_heading_inside_content_skipped(self):
        content = "第一条 总则性规定。\n第二章 经营者义务\n第八条 经营者应当保障商品质量。"
        arts, skipped = split_chapter(content)
        self.assertEqual(skipped, 1)
        self.assertEqual([a["num"] for a in arts], ["第一条", "第八条"])


class TestFallbackNumbering(unittest.TestCase):
    """通篇无「第X条」的文书：三道回退按体裁选编号。"""

    def test_list_numbering_maps_to_article(self):
        # 刑法修正案体裁：只有「一、二、」，实践中按「修正案第一条」引用
        paras = ["中华人民共和国刑法修正案（十一）", "一、将刑法第十七条修改为……",
                 "二、在刑法第一百三十三条之一后增加一条……"]
        arts, _, label = best_effort_articles(paras)
        self.assertEqual(label, "「一、」式编号")
        self.assertEqual([a["num"] for a in arts], ["第一条", "第二条"])
        self.assertEqual(arts[0]["num_origin"], "一、")

    def test_item_numbering_preferred_when_items_dominate(self):
        # 指导意见体裁：「一、」是小节标题，「1.」才是条目（引用单位）
        paras = ["一、总体要求", "1. 把握立法精神，严格公正办案。",
                 "2. 立足具体案情，依法准确认定。", "二、具体适用",
                 "3. 准确认定正当防卫的起因条件。", "4. 准确认定时间条件。"]
        arts, _, label = best_effort_articles(paras)
        self.assertEqual(label, "「N.」条目号")
        self.assertEqual([a["num"] for a in arts], ["第1条", "第2条", "第3条", "第4条"])
        self.assertEqual(arts[0]["text"][:2], "1.")

    def test_short_document_becomes_whole_unit(self):
        # 法律解释体裁：整篇一句话，无任何内部编号
        paras = ["全国人民代表大会常务委员会关于《中华人民共和国刑法》第三十条的解释",
                 "（2014年4月24日通过）", "对组织、策划、实施该危害社会行为的人依法追究刑事责任。",
                 "现予公告。"]
        arts, _, label = best_effort_articles(paras)
        self.assertEqual(label, "整篇作为一条")
        self.assertEqual(len(arts), 1)
        self.assertEqual(arts[0]["num"], "全文")
        self.assertIn("现予公告", arts[0]["text"])

    def test_empty_document_yields_nothing(self):
        arts, _, _ = best_effort_articles([])
        self.assertEqual(arts, [])


if __name__ == "__main__":
    unittest.main()
