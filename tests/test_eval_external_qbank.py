# -*- coding: utf-8 -*-
"""外部题库映射脚本的行为锁定：法名/条号归一化、主命中缺失必须报 None。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from scripts.eval_external_qbank import build_law_index, law_key, map_item, num_key


class LawNumKeyTest(unittest.TestCase):
    def test_law_key_去书名号与全角空格但保留括号(self):
        # NFKC 会把全角括号归一成半角：两边同归一，解释(一)/解释(二) 仍可区分
        self.assertEqual(law_key(u"最高人民法院关于适用《中华人民共和国民法典》侵权责任编的解释（一）"),
                         u"最高人民法院关于适用中华人民共和国民法典侵权责任编的解释(一)")
        self.assertNotEqual(law_key(u"解释（一）"), law_key(u"解释（二）"))

    def test_num_key_全角数字与零宽字符归一(self):
        self.assertEqual(num_key(u"第７８条"), u"第78条")
        self.assertEqual(num_key(u"第七十\u200b八条"), num_key(u"第七十八条"))
        self.assertEqual(num_key(u"第七十八条之一"), u"第七十八条之一")


class MapItemTest(unittest.TestCase):
    def setUp(self):
        self.corpus = [
            {"id": 1, "law": u"中华人民共和国民法典", "num": u"第一千一百六十五条"},
            {"id": 2, "law": u"中华人民共和国民法典", "num": u"第一千一百六十六条"},
            {"id": 3, "law": u"最高人民法院关于适用《中华人民共和国民法典》侵权责任编的解释（一）",
             "num": u"第十九条"},
        ]
        self.index = build_law_index(self.corpus)

    def _item(self, **kw):
        base = {u"qid": u"ext-001", u"query": u"q", u"expect_law": u"中华人民共和国民法典",
                u"expect_num": u"第一千一百六十五条", u"expect_alts": []}
        base.update(kw)
        return base

    def test_主命中与备选都映射成语料id(self):
        ids, laws, unmapped = map_item(self._item(
            expect_alts=[u"中华人民共和国民法典 第一千一百六十六条"]), self.index)
        self.assertEqual(ids, [1, 2])
        self.assertEqual(laws, [u"中华人民共和国民法典 第一千一百六十五条",
                                u"中华人民共和国民法典 第一千一百六十六条"])
        self.assertEqual(unmapped, [])

    def test_law不存在返回None(self):
        ids, _, _ = map_item(self._item(expect_law=u"中华人民共和国不存在的法"), self.index)
        self.assertIsNone(ids)

    def test_num不存在返回None(self):
        ids, _, _ = map_item(self._item(expect_num=u"第九百九十九条"), self.index)
        self.assertIsNone(ids)

    def test_备选未命中记入unmapped_alts不丢弃主命中(self):
        ids, _, unmapped = map_item(self._item(
            expect_alts=[u"中华人民共和国民法典 第九百九十九条"]), self.index)
        self.assertEqual(ids, [1])
        self.assertEqual(unmapped, [u"中华人民共和国民法典 第九百九十九条"])

    def test_书名号法名可匹配语料内带书名号的司法解释(self):
        ids, _, _ = map_item(self._item(
            expect_law=u"最高人民法院关于适用《中华人民共和国民法典》侵权责任编的解释（一）",
            expect_num=u"第十九条"), self.index)
        self.assertEqual(ids, [3])


if __name__ == "__main__":
    unittest.main()
