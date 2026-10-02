# -*- coding: utf-8 -*-
"""v1 条级重建相关测试：法名规范化 / 条号转换 / 逐条校验 / v4 组装与金标迁移。"""
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from scripts.rebuild_corpus_v4 import squeeze  # noqa: E402
from scripts.resource_from_clean_files import clean, cn2int, norm_title  # noqa: E402


class TestNormTitle(unittest.TestCase):
    def test_strips_date_and_extension(self):
        self.assertEqual(norm_title(u"2019-03-24_不动产登记暂行条例.docx"), u"不动产登记暂行条例")

    def test_strips_prefix_and_brackets(self):
        self.assertEqual(norm_title(u"中华人民共和国企业所得税法实施条例"), u"企业所得税法实施条例")
        self.assertEqual(norm_title(u"中华人民共和国民法典（第一编）"), u"民法典")

    def test_md_without_date(self):
        self.assertEqual(norm_title(u"民事诉讼法.md"), u"民事诉讼法")


class TestCn2int(unittest.TestCase):
    """条号转换曾在本项目里出过一次错（「二十」被算成 12），故锁住。"""

    def test_units(self):
        self.assertEqual(cn2int(u"三"), 3)

    def test_tens(self):
        self.assertEqual(cn2int(u"十"), 10)
        self.assertEqual(cn2int(u"二十"), 20)
        self.assertEqual(cn2int(u"三十五"), 35)

    def test_hundreds_thousands(self):
        self.assertEqual(cn2int(u"一百二十三"), 123)
        self.assertEqual(cn2int(u"一千一百六十四"), 1164)


class TestArticleCleanGate(unittest.TestCase):
    def _art(self, num, text):
        return {"num": num, "text": text}

    def test_accepts_complete_article(self):
        self.assertIsNone(clean(self._art(u"第一条", u"第一条 为了规范登记行为，制定本条例。")))

    def test_rejects_extreme_density(self):
        # 只抓最离谱的一批：短碎片密排（密度远高于干净行长 0.0625）
        inter = u"第一条 " + u" ".join([u"甲乙"] * 30) + u"。"
        self.assertIn(u"空格密度", clean(self._art(u"第一条", inter)))

    def test_density_gate_is_conservative(self):
        """诚实记录判据的局限：真实交错页块（约 20 字一行 → 密度≈0.045~0.05）
        与干净行分布重叠，**不会被这一门拦下**；拦它靠源件干净度与逐法条号单调。"""
        realistic = u"第一条 " + u" ".join([u"因产品存在缺陷造成买受人财产损害"] * 6) + u"。"
        self.assertLess(realistic.count(u" ") / float(len(realistic)), 0.065)
        self.assertIsNone(clean(self._art(u"第一条", realistic)))

    def test_rejects_non_sentence_ending(self):
        self.assertIn(u"句末标点", clean(self._art(u"第一条", u"第一条 本条没有写完")))

    def test_rejects_gazette_furniture(self):
        t = (u"第一条 为了规范相关行为，保护当事人合法权益，根据有关法律的规定，"
             u"制定本条例。中华人民共和国最高人民法院公报 本条正文结束。")
        self.assertIn(u"公报", clean(self._art(u"第一条", t)))


class TestRebuildEndToEnd(unittest.TestCase):
    """临时目录里跑一遍真实 CLI：页块行被换成条级行，金标按 law+num 迁移并校验 evidence。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="v4test_")
        self.base = os.path.join(self.tmp, "base.jsonl")
        self.repl = os.path.join(self.tmp, "repl.jsonl")
        self.gold = os.path.join(self.tmp, "gold.jsonl")
        self.out = os.path.join(self.tmp, "out.jsonl")
        self.gold_out = os.path.join(self.tmp, "gold_out.jsonl")
        with io.open(self.base, "w", encoding="utf-8", newline="\n") as fh:
            # 一部法的两行页块（v1 段：meta 为空且 id < 910000）+ 一部不受影响的法
            fh.write(json.dumps({"id": 70001, "law": u"测试条例", "num": u"第一条",
                                 "text": u"第一块 第一条 甲。 第二条 乙。", "meta": None},
                                ensure_ascii=False) + "\n")
            fh.write(json.dumps({"id": 70002, "law": u"测试条例", "num": u"第三条",
                                 "text": u"第二块 第三条 丙。", "meta": None},
                                ensure_ascii=False) + "\n")
            fh.write(json.dumps({"id": 70003, "law": u"别的法", "num": u"第一条",
                                 "text": u"别的法第一条 丁。", "meta": None}, ensure_ascii=False) + "\n")
        with io.open(self.repl, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps({"id": 990001, "law": u"测试条例", "num": u"第一条",
                                 "text": u"第一条 甲。", "meta": {"text_source": "clean-local-file"}},
                                ensure_ascii=False) + "\n")
            fh.write(json.dumps({"id": 990002, "law": u"测试条例", "num": u"第二条",
                                 "text": u"第二条 乙。", "meta": {"text_source": "clean-local-file"}},
                                ensure_ascii=False) + "\n")
        with io.open(self.gold, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps({"qid": "t001", "query": u"甲乙", "gold_id": 70001,
                                 "law": u"测试条例", "num": u"第一条",
                                 "gold_ids": [70001], "gold_laws": [u"测试条例 第二条"],
                                 "evidence": u"乙", "source_site": "test", "source_url": ""},
                                ensure_ascii=False) + "\n")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_replaces_pageblocks_and_remaps_gold(self):
        cmd = [sys.executable, os.path.join(ROOT, "scripts", "rebuild_corpus_v4.py"),
               "--base", self.base, "--replacement", self.repl, "--out", self.out,
               "--gold", self.gold, "--gold-out", self.gold_out,
               "--report", os.path.join(self.tmp, "rep.txt"), "--unrepaired", "keep"]
        subprocess.check_call(cmd, cwd=ROOT)
        rows = [json.loads(l) for l in io.open(self.out, encoding="utf-8") if l.strip()]
        laws = [r["law"] for r in rows]
        # 测试条例的两行页块被换掉 -> 只剩替换进来的 2 条；别的法原样保留
        self.assertEqual(laws.count(u"测试条例"), 2)
        self.assertIn(u"别的法", laws)
        self.assertEqual(len(rows), len(set(r["id"] for r in rows)))  # id 唯一
        # 替换行被统一重编号到 995000 段
        self.assertTrue(all(r["id"] >= 995000 for r in rows if r["law"] == u"测试条例"))
        g = json.loads(io.open(self.gold_out, encoding="utf-8").readline())
        # 金标按「法名 条号」迁到新行，且 evidence「乙」确实在新行文本里
        self.assertEqual(g["gold_ids"], [995002])
        self.assertTrue(g["remap"]["evidence_verified"])

    def test_drop_mode_removes_unrepaired_v1_rows(self):
        cmd = [sys.executable, os.path.join(ROOT, "scripts", "rebuild_corpus_v4.py"),
               "--base", self.base, "--replacement", self.repl, "--out", self.out,
               "--report", os.path.join(self.tmp, "rep2.txt"), "--unrepaired", "drop"]
        subprocess.check_call(cmd, cwd=ROOT)
        rows = [json.loads(l) for l in io.open(self.out, encoding="utf-8") if l.strip()]
        self.assertEqual([r["law"] for r in rows].count(u"别的法"), 0)  # 无干净源的 v1 行被丢弃


class TestSqueeze(unittest.TestCase):
    def test_squeeze_removes_whitespace(self):
        self.assertEqual(squeeze(u"卖淫、嫖娼的， 处十日 以上"), u"卖淫、嫖娼的，处十日以上")


if __name__ == "__main__":
    unittest.main()
