# -*- coding: utf-8 -*-
"""corpus_audit 质量旗标判定的单元测试（每个旗标的边界）。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts.corpus_audit import (audit, is_gazette_furniture, narrow_column, row_flags)


def row(rid=1, law=u"测试法", num=u"第一条", text=u"正文", meta=None):
    r = {"id": rid, "law": law, "num": num, "text": text}
    if meta is not None:
        r["meta"] = meta
    return r


def flags_of(r, dup=()):
    return set(row_flags(r, set(dup)))


class EmptyTinyTest(unittest.TestCase):
    def test_tiny_flagged(self):
        self.assertIn("empty-or-tiny", flags_of(row(text=u"短")))

    def test_at_threshold_not_flagged(self):
        self.assertNotIn("empty-or-tiny", flags_of(row(text=u"字" * 10)))

    def test_blank_with_spaces_flagged(self):
        self.assertIn("empty-or-tiny", flags_of(row(text=u"   \n  ")))


class VeryLongTest(unittest.TestCase):
    def test_over_threshold_flagged(self):
        self.assertIn("very-long", flags_of(row(text=u"字" * 2001)))

    def test_at_threshold_not_flagged(self):
        self.assertNotIn("very-long", flags_of(row(text=u"字" * 2000)))


class WholeDocumentTest(unittest.TestCase):
    def test_by_num(self):
        self.assertIn("whole-document", flags_of(row(num=u"全文")))

    def test_by_unit(self):
        self.assertIn("whole-document", flags_of(row(meta={"unit": "whole-document"})))

    def test_normal_article_not_flagged(self):
        self.assertNotIn("whole-document", flags_of(row(meta={"unit": "article"})))


class NumMappedTest(unittest.TestCase):
    def test_mapped_flagged(self):
        self.assertIn("num-mapped", flags_of(row(num=u"第一条", meta={"num_origin": u"一、"})))

    def test_equal_not_flagged(self):
        self.assertNotIn("num-mapped", flags_of(row(num=u"第一条", meta={"num_origin": u"第一条"})))

    def test_absent_not_flagged(self):
        self.assertNotIn("num-mapped", flags_of(row()))


class DupLawNumTest(unittest.TestCase):
    def test_duplicate_flagged(self):
        key = (u"测试法", u"第一条")
        self.assertIn("dup-law-num", flags_of(row(), dup=[key]))

    def test_unique_not_flagged(self):
        self.assertNotIn("dup-law-num", flags_of(row()))


class PollutedInterleaveTest(unittest.TestCase):
    def test_helpers(self):
        self.assertTrue(is_gazette_furniture(u"全国人民代表大会常务委员会公报2025"))
        self.assertTrue(is_gazette_furniture(u"—７５４—"))
        self.assertFalse(is_gazette_furniture(u"普通条文文本"))
        self.assertTrue(narrow_column(u"短句一 短句二 短句三 短句四 短句五 短句六"))

    def test_gazette_narrow_flagged(self):
        text = u"全国人民代表大会常务委员会公报２０２５·４ 短句一 短句二 短句三 短句四 短句五"
        self.assertIn("polluted-interleave", flags_of(row(rid=1, text=text)))

    def test_gazette_wide_not_flagged(self):
        # 长句（非窄栏）即便有公报标记也不判为交错
        wide = u"全国人民代表大会常务委员会公报 " + u"这是一个很长的连续句子其长度明显超过窄栏阈值用于测试" * 3
        self.assertNotIn("polluted-interleave", flags_of(row(rid=1, text=wide)))

    def test_non_gazette_not_flagged(self):
        text = u"短句一 短句二 短句三 短句四 短句五 短句六"
        r = row(rid=980001, text=text, meta={"source": u"flk.npc.gov.cn 国家法律法规数据库"})
        self.assertNotIn("polluted-interleave", flags_of(r))


class NarrowNoFurnitureTest(unittest.TestCase):
    """第二风险带（低置信）：公报源 + 窄栏 + 无排版标记。"""

    NARROW = u"短句一 短句二 短句三 短句四 短句五 短句六"

    def test_narrow_without_furniture_flagged(self):
        f = flags_of(row(rid=1, text=self.NARROW))
        self.assertIn("narrow-no-furniture", f)
        self.assertNotIn("polluted-interleave", f)

    def test_narrow_with_furniture_is_main_band(self):
        text = u"全国人民代表大会常务委员会公报 " + self.NARROW
        f = flags_of(row(rid=1, text=text))
        self.assertIn("polluted-interleave", f)
        self.assertNotIn("narrow-no-furniture", f)

    def test_wide_without_furniture_neither(self):
        wide = u"这是很长的连续句子其长度明显超过窄栏阈值" * 3
        f = flags_of(row(rid=1, text=wide))
        self.assertNotIn("narrow-no-furniture", f)
        self.assertNotIn("polluted-interleave", f)

    def test_two_bands_are_disjoint(self):
        rows = [row(rid=1, text=self.NARROW),
                row(rid=2, text=u"全国人民代表大会常务委员会公报 " + self.NARROW)]
        rep = audit(rows)
        self.assertEqual(rep["flag_counts"].get("polluted-interleave"), 1)
        self.assertEqual(rep["flag_counts"].get("narrow-no-furniture"), 1)


class AuditAggregationTest(unittest.TestCase):
    def test_counts_and_review_lists(self):
        rows = [
            row(rid=1, law=u"甲法", num=u"第一条", text=u"字" * 10),
            row(rid=2, law=u"甲法", num=u"第一条", text=u"字" * 10),
            row(rid=3, law=u"乙法", num=u"全文", text=u"短", meta={"unit": "whole-document"}),
            row(rid=4, law=u"丙法", num=u"第一条", meta={"num_origin": u"一、"}, text=u"正常条文内容足够长的文本"),
        ]
        rep = audit(rows)
        self.assertEqual(rep["flag_counts"].get("dup-law-num"), 2)  # 两条同 (law,num)
        self.assertEqual(rep["flag_counts"].get("whole-document"), 1)
        self.assertEqual(rep["flag_counts"].get("num-mapped"), 1)
        self.assertEqual(rep["flag_counts"].get("empty-or-tiny"), 1)
        self.assertEqual(len(rep["dup_law_num_rows"]), 2)
        self.assertEqual(len(rep["num_mapped_rows"]), 1)
        self.assertEqual(len(rep["whole_document_rows"]), 1)


if __name__ == "__main__":
    unittest.main()
