# -*- coding: utf-8 -*-
"""查询扩展单测：数字读法归一、同义扩展、hybrid 扩展路行为、词典完整性。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from statute_rag.query_expansion import (
    chinese_numeral, expand_query, load_synonyms, numeral_terms,
)
from statute_rag.retrieval import HybridRetriever, char_ngrams, query_grams

SYN = {"坐牢": ["服刑", "刑事责任"], "丢了": ["丢失"], "探视": ["会见"]}

EXP_CORPUS = [
    {"id": 1, "law": "监狱法", "num": "第六十九条",
     "text": "罪犯在监狱服刑期间，按照规定与亲属、监护人通话、会见。"},
    {"id": 2, "law": "测试法", "num": "第一条",
     "text": "当事人应当履行判决确定的义务。"},
]


class ChineseNumeralTest(unittest.TestCase):
    def test_standard_readings(self):
        self.assertEqual(chinese_numeral(10), "十")
        self.assertEqual(chinese_numeral(15), "十五")
        self.assertEqual(chinese_numeral(20), "二十")
        self.assertEqual(chinese_numeral(100), "一百")
        self.assertEqual(chinese_numeral(105), "一百零五")
        self.assertEqual(chinese_numeral(1000), "一千")
        self.assertEqual(chinese_numeral(1234), "一千二百三十四")
        self.assertEqual(chinese_numeral(0), "零")

    def test_out_of_range(self):
        with self.assertRaises(ValueError):
            chinese_numeral(10000)


class NumeralTermsTest(unittest.TestCase):
    def test_number_with_unit_converts(self):
        self.assertEqual(numeral_terms("索赔1000元"), ["一千元"])
        self.assertEqual(numeral_terms("10倍惩罚性赔偿"), ["十倍"])
        self.assertEqual(numeral_terms("第10条"), ["第十条"])

    def test_bare_number_kept(self):
        # 裸数字（编号/年份）不转换，避免错误读法
        self.assertEqual(numeral_terms("编号12345"), [])
        self.assertEqual(numeral_terms("没有数字"), [])


class ExpandQueryTest(unittest.TestCase):
    def test_synonym_appended_and_original_kept(self):
        out = expand_query("他坐牢了吗，东西丢了", SYN)
        self.assertTrue(out.startswith("他坐牢了吗，东西丢了"))
        self.assertIn("服刑", out)
        self.assertIn("丢失", out)

    def test_no_hit_returns_original(self):
        self.assertEqual(expand_query("今天天气不错", SYN), "今天天气不错")

    def test_max_terms_caps_extras(self):
        syn = {"坐牢": ["服刑", "刑罚", "刑事责任", "羁押", "关押", "拘役",
                        "有期徒刑", "无期徒刑", "死缓", "死刑", "缓刑"]}
        out = expand_query("坐牢", syn, max_terms=3)
        self.assertEqual(len(out.split()) - 1, 3)


class QueryGramsTest(unittest.TestCase):
    def test_grams_do_not_cross_segments(self):
        # 扩展查询带空格：gram 不得跨段，避免边界噪声
        self.assertEqual(query_grams("坐牢 服刑"), ["坐牢", "服刑"])
        self.assertEqual(char_ngrams("坐牢服刑"), ["坐牢", "牢服", "服刑"])


class LoadSynonymsTest(unittest.TestCase):
    def test_default_table_well_formed(self):
        syn = load_synonyms()
        self.assertGreater(len(syn), 100)  # 领域词典，非零星条目
        for key, values in syn.items():
            self.assertGreaterEqual(len(key), 2)
            self.assertTrue(values)
            for v in values:
                self.assertIsInstance(v, str)
                self.assertTrue(v)
            self.assertNotIn(key, values)  # 不自映射

    def test_known_entries_present(self):
        syn = load_synonyms()
        for key in ("坐牢", "探视", "社保", "房东", "噪音"):
            self.assertIn(key, syn)


class HybridExpansionTest(unittest.TestCase):
    def test_expansion_channel_lifts_colloquial_query(self):
        q = "坐牢的人可以去看望吗"
        without = HybridRetriever(EXP_CORPUS, use_expansion=False,
                                  synonyms=SYN).search(q, k=2)
        self.assertEqual([c["id"] for c in without], [])  # 无词法重叠 → 空结果
        with_ex = HybridRetriever(EXP_CORPUS, use_expansion=True,
                                  synonyms=SYN).search(q, k=2)
        self.assertEqual(with_ex[0]["id"], 1)  # 经「服刑/会见」命中目标条文

    def test_expansion_absent_when_no_hit(self):
        # 无词典命中时不新增检索路，行为与基线一致
        r = HybridRetriever(EXP_CORPUS, use_expansion=True,
                            synonyms=SYN).search("当事人应当履行义务", k=2)
        self.assertEqual(r[0]["id"], 2)

    def test_citation_structure_preserved(self):
        for cite in HybridRetriever(EXP_CORPUS, use_expansion=True,
                                    synonyms=SYN).search("坐牢", k=2):
            for field in ("id", "law", "num", "text", "score", "retriever"):
                self.assertIn(field, cite)


if __name__ == "__main__":
    unittest.main()
