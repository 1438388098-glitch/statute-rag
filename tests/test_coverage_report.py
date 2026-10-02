# -*- coding: utf-8 -*-
"""coverage_report 覆盖率计算的单元测试（含 0 覆盖 / 全覆盖 / 重复法名边界）。"""
import io
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts.coverage_report import (
    classify_source, collect_corpus_stats, compute_coverage, extract_catalog_titles,
    main, normalize_title)


def law(title, zhuma_id, subject=u"测试科目", has_body=False):
    return {"title": title, "zhuma_id": zhuma_id, "subject": subject, "has_body": has_body}


class NormalizeTitleTest(unittest.TestCase):
    def test_strips_whitespace(self):
        self.assertEqual(normalize_title(u"中华人民共和国 刑法"), u"刑法")

    def test_strips_state_prefix(self):
        self.assertEqual(normalize_title(u"中华人民共和国民法典"), u"民法典")

    def test_keeps_short_form(self):
        self.assertEqual(normalize_title(u"民法典"), u"民法典")

    def test_keeps_non_state_title(self):
        name = u"最高人民法院关于审理走私刑事案件适用法律若干问题的解释"
        self.assertEqual(normalize_title(name), name)


class ClassifySourceTest(unittest.TestCase):
    def test_by_meta_source(self):
        self.assertEqual(classify_source({"id": 1, "meta": {"source": u"zhumavip.com 电子法条"}}),
                         u"竹马电子法条（zhumavip.com）")
        self.assertEqual(classify_source({"id": 1, "meta": {"source": u"flk.npc.gov.cn 国家法律法规数据库"}}),
                         u"flk 官方文件（v3）")

    def test_by_id_range_without_meta(self):
        self.assertEqual(classify_source({"id": 100}), u"公报源（v1，本地 legal-wisdom 库）")
        self.assertEqual(classify_source({"id": 910001}), u"官方 docx（v2 六部大法，flk）")
        self.assertEqual(classify_source({"id": 970005}), u"竹马电子法条（zhumavip.com）")
        self.assertEqual(classify_source({"id": 980005}), u"flk 官方文件（v3）")


class CoverageBoundaryTest(unittest.TestCase):
    def test_zero_coverage(self):
        cov = compute_coverage([u"另一部法"], [law(u"中华人民共和国刑法", 1), law(u"民法典", 2)])
        self.assertEqual((cov["unique_titles"], cov["covered"], cov["uncovered"]), (2, 0, 2))
        self.assertEqual(cov["rate"], 0.0)

    def test_full_coverage_with_prefix_alias(self):
        # 语料用全称，目录用简称，应视为同一部（命中）
        cov = compute_coverage([u"中华人民共和国民法典"], [law(u"民法典", 1)])
        self.assertEqual((cov["covered"], cov["rate"]), (1, 100.0))

    def test_partial_coverage_rate(self):
        cov = compute_coverage([u"刑法"], [law(u"刑法", 1), law(u"民法", 2),
                                         law(u"商法", 3), law(u"宪法", 4)])
        self.assertEqual(cov["covered"], 1)
        self.assertAlmostEqual(cov["rate"], 25.0)

    def test_duplicate_title_dedup_denominator(self):
        # 目录内同名字符串重复只算一部；不同空白变体也算一部（披露在 duplicate_titles）
        cov = compute_coverage([u"刑法"], [
            law(u"中华人民共和国立法法", 1), law(u"中华人民共和国立法法", 2),
            law(u"最高法解释", 3), law(u"最高法 解释", 4)])
        self.assertEqual(cov["unique_titles"], 3)  # 立法法×2 合并，最高法解释 2 个变体合并
        self.assertTrue(cov["duplicate_titles"])   # 披露


class CorpusStatsTest(unittest.TestCase):
    def test_multi_source_listed(self):
        rows = [
            {"id": 1, "law": u"中华人民共和国治安管理处罚法", "num": u"第一条", "text": u"a"},
            {"id": 970001, "law": u"中华人民共和国治安管理处罚法", "num": u"第一条", "text": u"b"},
            {"id": 2, "law": u"中华人民共和国刑法", "num": u"第一条", "text": u"c"},
        ]
        stats = collect_corpus_stats(rows)
        self.assertEqual(stats["rows"], 3)
        self.assertEqual(stats["laws"], 2)
        self.assertEqual(len(stats["multi_source"]), 1)
        self.assertEqual(stats["multi_source"][0]["sources"][u"公报源（v1，本地 legal-wisdom 库）"], 1)


class ExtractCatalogTest(unittest.TestCase):
    def test_tree_to_flat_titles(self):
        tree = [
            {"title": u"宪法", "type": 0, "children": [
                {"title": u"中华人民共和国宪法", "id": 1081, "type": 1,
                 "children": [{"title": u"序言", "id": 1082, "type": 4, "children": []}]},
                {"title": u"反分裂国家法", "id": 1087, "type": 4, "children": []},
            ]},
        ]
        out = extract_catalog_titles(tree)
        self.assertEqual(out["subjects"], [u"宪法"])
        self.assertEqual(len(out["laws"]), 2)
        by_id = dict((l["zhuma_id"], l) for l in out["laws"])
        self.assertTrue(by_id[1081]["has_body"])    # 有章节正文
        self.assertFalse(by_id[1087]["has_body"])   # 无正文
        self.assertEqual(by_id[1087]["subject"], u"宪法")


class MissingCatalogGracefulTest(unittest.TestCase):
    def test_missing_catalog_exits_zero(self):
        tmp = tempfile.mkdtemp()
        corpus = os.path.join(tmp, "corpus.jsonl")
        with io.open(corpus, "w", encoding="utf-8") as f:
            f.write(json.dumps({"id": 1, "law": u"刑法", "num": u"第一条", "text": u"x"},
                               ensure_ascii=False) + "\n")
        argv = sys.argv  # main() 直接读 sys.argv
        try:
            sys.argv = ["coverage_report.py", "--corpus", corpus,
                        "--catalog", os.path.join(tmp, "nope.json")]
            rc = main()
        finally:
            sys.argv = argv
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
