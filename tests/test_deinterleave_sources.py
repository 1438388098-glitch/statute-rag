# -*- coding: utf-8 -*-
"""公报双栏还原与外部源补源测试。

锁住四件本轮新增、且**出错会静默污染语料**的事：
1. 页眉页脚/装饰线的判定（判错就把公报垃圾混进条文）；
2. 页内「先左栏后右栏」的股序（判错就把两条正文换位，读起来还像人话）；
3. 镜像法名的匹配键（把（二）配到（一）上会拿错整部法）；
4. 逐条门禁的两处误杀修复（短条密度、公式尾），以及零宽字符归一化。
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from scripts.deinterleave_gazette import deinterleave, is_garbage, pick_order  # noqa: E402
from scripts.lawrefbook_source import file_key, mirror_ok, refbook_key  # noqa: E402
from scripts.resource_from_clean_files import clean  # noqa: E402
from scripts.resource_local_remaining import (  # noqa: E402
    keep_article, normalize_line, num_sort_key, quality, whole_document)


class TestGazetteFurnitureGate(unittest.TestCase):
    """页眉页脚/装饰线判定：这些行不是条文，混进正文就是脏数据。"""

    def test_running_heads_are_junk(self):
        for line in (u"中华人民共和国最高人民法院公报",
                     u"全国人民代表大会常务委员会公报２０２４·１",
                     u"年第", u"期", u"2025·6", u"2024", u"- 17 -", u"—４—"):
            self.assertTrue(is_garbage(line), u"未判为页眉页脚: %r" % line)

    def test_cid_and_private_use_are_junk(self):
        self.assertTrue(is_garbage(u"(cid:2943)(cid:2943)(cid:2943)(cid:2943)"))
        self.assertTrue(is_garbage(u"\ue200\ue200\ue201"))

    def test_article_lines_are_not_junk(self):
        self.assertFalse(is_garbage(u"第六条　公司应当有自己的名称。公司名称"))
        self.assertFalse(is_garbage(u"第一条 非法使被监护人脱离监护，监护人请求赔偿的。"))

    def test_long_line_containing_press_name_is_kept(self):
        """正文里提到「公报」的长行不是页眉（页眉都很短）。"""
        line = (u"司法解释应当在《中华人民共和国最高人民法院公报》上刊登，"
                u"并自刊登之日起施行。")
        self.assertFalse(is_garbage(line))


class TestPickOrder(unittest.TestCase):
    """页内两股先后的裁决依据：条号必须单调不减。"""

    def test_left_column_first_when_numbers_ascend(self):
        a = [u"第一条 甲。", u"第二条 乙。"]
        b = [u"第三条 丙。", u"第四条 丁。"]
        first, second = pick_order(a, b, None)
        self.assertEqual(first, a)
        self.assertEqual(second, b)

    def test_swaps_when_streams_are_reversed(self):
        """a 股条号更大 → 说明 a 是右栏，真读序是 b 在前。"""
        a = [u"第五条 戊。", u"第六条 己。"]
        b = [u"第三条 庚。", u"第四条 辛。"]
        first, second = pick_order(a, b, None)
        self.assertEqual(first, b)
        self.assertEqual(second, a)

    def test_continuation_page_keeps_left_first(self):
        """本页左栏是上页条文的续文（无条首）→ 仍先左栏。"""
        a = [u"用等财产损失的，人民法院应予支持。"]
        b = [u"第五条 无民事行为能力人造成他人损害的。"]
        first, second = pick_order(a, b, 4)
        self.assertEqual(first, a)


class TestDeinterleave(unittest.TestCase):
    def test_two_column_page_read_left_then_right(self):
        content = u"\n".join([
            u"第一条 甲。", u"", u"第三条 丙。", u"",
            u"第二条 乙。", u"", u"第四条 丁。", u"",
        ])
        self.assertEqual(deinterleave(content),
                         [u"第一条 甲。", u"第二条 乙。", u"第三条 丙。", u"第四条 丁。"])

    def test_page_furniture_is_dropped_and_pages_kept_in_order(self):
        content = u"\n".join([
            u"第一条 甲。", u"", u"第三条 丙。", u"",
            u"中华人民共和国最高人民法院公报", u"", u"- 17 -", u"",
            u"第五条 戊。", u"", u"第七条 庚。", u"",
        ])
        # 公报页眉块把两页切开：第 1 页「左→右」，第 2 页「左→右」，条号仍单调
        self.assertEqual(deinterleave(content),
                         [u"第一条 甲。", u"第三条 丙。", u"第五条 戊。", u"第七条 庚。"])


class TestRefbookKey(unittest.TestCase):
    def test_parenthesised_versions_do_not_collide(self):
        one = refbook_key(u"最高人民法院关于适用《中华人民共和国民法典》婚姻家庭编的解释（一）")
        two = refbook_key(u"最高人民法院关于适用《中华人民共和国民法典》婚姻家庭编的解释（二）")
        self.assertNotEqual(one, two)

    def test_abbreviated_book_title_matches(self):
        corpus_name = u"最高人民法院关于适用《中华人民共和国民法典》婚姻家庭编的解释（二）"
        file_name = u"最高人民法院关于适用《民法典》婚姻家庭编的解释（二）(2025-01-15)"
        self.assertEqual(refbook_key(corpus_name), file_key(file_name)[0])
        self.assertEqual(file_key(file_name)[1], u"2025-01-15")

    def test_separator_variants_match(self):
        self.assertEqual(refbook_key(u"最高人民法院 最高人民检察院关于办理洗钱刑事案件适用法律若干问题的解释"),
                         refbook_key(u"最高人民法院、最高人民检察院关于办理洗钱刑事案件适用法律若干问题的解释"))


class TestMirrorGate(unittest.TestCase):
    def _q(self, nums, cov, dropped=0):
        return {"kept": [{"num": u"第%d条" % n, "text": u"第%d条 正文。" % n} for n in nums],
                "nums": nums, "mono": True, "complete": True, "cov": cov,
                "dropped": dropped, "soft": 0, "cid": 0, "arts": [None] * (len(nums) + dropped)}

    def test_requires_complete_numbering(self):
        """镜像文本按官方体例，缺号说明切条漏了 —— 不能当「补上了」发出去。"""
        q = self._q([1, 2, 3], 0.97)
        q["complete"] = False
        self.assertFalse(mirror_ok(q, 0.35))

    def test_coverage_floor_allows_appendix_laws(self):
        """带附表/附则的文书覆盖率天然低（税目税额表不在条文里）。"""
        self.assertTrue(mirror_ok(self._q([1, 2, 3], 0.41), 0.35))
        self.assertFalse(mirror_ok(self._q([1, 2, 3], 0.30), 0.35))

    def test_rejects_single_unit(self):
        self.assertFalse(mirror_ok(self._q([1], 0.99), 0.35))


class TestGateFalsePositiveFixes(unittest.TestCase):
    """本轮实测到的两处误杀：真条文被门禁拦下，导致整法缺条。"""

    def test_short_article_with_one_space_passes(self):
        # 真实案例：仲裁法第八条「第八条 仲裁应当遵循诚信原则。」曾被判「空格密度过高」
        self.assertIsNone(clean({"num": u"第八条", "text": u"第八条 仲裁应当遵循诚信原则。"}))

    def test_long_interleaved_line_still_rejected(self):
        inter = u"第一条 " + u" ".join([u"甲乙"] * 30) + u"。"
        self.assertIn(u"空格密度", clean({"num": u"第一条", "text": inter}))

    def test_formula_tail_is_soft_kept(self):
        art = {"num": u"第七十八条",
               "text": u"第七十八条 抵免限额＝中国境内、境外所得应纳税总额×来源于某国的应纳税所得额÷应纳税所得总额"}
        self.assertIn(u"句末标点", clean(art))
        self.assertEqual(keep_article(art), (True, None, True))

    def test_no_formula_tail_is_still_rejected(self):
        art = {"num": u"第九条", "text": u"第九条 这段文本没有句末标点收尾"}
        self.assertEqual(keep_article(art), (False, u"结尾非句末标点(尾)", False))

    def test_quality_reports_complete_numbering_after_fix(self):
        lines = [u"第一条 甲。", u"第二条 乙。", u"第三条 丙。"]
        q = quality(lines)
        self.assertTrue(q["complete"])
        self.assertEqual(q["dropped"], 0)

    def test_normalize_line_strips_zero_width(self):
        self.assertEqual(normalize_line(u"第八十三条　监事。\u200b\u200b"), u"第八十三条　监事。")


class TestWholeDocumentUnit(unittest.TestCase):
    """小体量批复没有条号结构：整篇作一个引用单位，但公报公告/装饰块要剔掉。"""

    LAW = u"最高人民法院关于基本医疗保险基金先行支付申请条件法律适用问题的批复"

    def test_extracts_title_and_body_drops_notice(self):
        lines = [
            u"\ue200\ue200\ue201",
            u"中华人民共和国最高人民法院",
            u"公 告",
            u"《最高人民法院关于基本医疗保险基金先行支付申请条件法律",
            u"适用问题的批复》已于 2025 年 11 月 24 日由最高人民法院审判委员",
            u"会第 1959 次会议通过，现予公布，自 2026 年 2 月 1 日起施行。",
            u"最高人民法院",
            u"关于基本医疗保险基金先行支付",
            u"申请条件法律适用问题的批复",
            u"法释〔2026〕1 号",
            u"安徽省高级人民法院：",
            u"你院《关于基本医疗保险基金先行支付申请条件法律适用问题的请示》收悉。经研究，批复如下："
            u"根据《中华人民共和国社会保险法》第三十条和《社会保险基金先行支付暂行办法》第二条、"
            u"第三条的规定，参加基本医疗保险的个人由于第三人的侵权行为造成伤病，医疗费用依法应当由"
            u"第三人负担的部分，第三人不支付或者无法确定第三人的，社会保险经办机构依法审核后应当按照"
            u"统筹地区基本医疗保险基金支付的规定先行支付相应部分的医疗费用。参保人依法享有的申请基本"
            u"医疗保险基金先行支付权利，不受在医疗费用结算时是否已自行支付医疗费用的影响。",
        ]
        unit = whole_document(lines, self.LAW)
        self.assertIsNotNone(unit)
        self.assertTrue(unit.startswith(u"最高人民法院"))
        self.assertIn(u"批复如下", unit)
        self.assertNotIn(u"现予公布", unit)          # 公报公告行不属于文书
        self.assertNotIn(u"\ue200", unit)            # 私用区装饰字符已剔除

    def test_rejects_when_text_too_long_or_short(self):
        self.assertIsNone(whole_document([u"最高人民法院关于某某问题的批复"] + [u"内容。" * 40] * 80, self.LAW))
        self.assertIsNone(whole_document([u"最高人民法院关于某某问题的批复", u"很短。"], self.LAW))

    def test_rejects_when_no_title_line(self):
        self.assertIsNone(whole_document([u"甲乙丙丁。" * 60], self.LAW))


class TestDeinterleaveDoesNotInventArticles(unittest.TestCase):
    """去掉页眉页脚后剩不下条文的，宁可为空，不能凑。"""

    def test_empty_content_yields_nothing(self):
        self.assertEqual(deinterleave(u""), [])
        self.assertEqual(deinterleave(u"\n\n\n"), [])


class TestNumSortKeyAndQualityWithZhiN(unittest.TestCase):
    """条号排序键（基础号, 之N号）：刑法整合版「之一/之二」条文的过门前提。"""

    def test_键序_基础条_之一_之二_下一条(self):
        self.assertLess(num_sort_key(u"第一百二十条"), num_sort_key(u"第一百二十条之一"))
        self.assertLess(num_sort_key(u"第一百二十条之一"), num_sort_key(u"第一百二十条之二"))
        self.assertLess(num_sort_key(u"第一百二十条之二"), num_sort_key(u"第一百二十一条"))

    def test_解析不了返回None(self):
        self.assertIsNone(num_sort_key(u"全文"))
        self.assertIsNone(num_sort_key(u"第X条"))

    def _quality(self, *nums):
        # nums 传完整条号（含「条」与「之N」），避免给「之一」再拼出一个「条」
        return quality([u"%s 内容%s。" % (n, u"正文" * 10) for n in nums])

    def test_普通文档与旧逻辑等价(self):
        q = self._quality(u"第一条", u"第二条", u"第三条")
        self.assertTrue(q["mono"])
        self.assertTrue(q["complete"])

    def test_之N文档判序通过(self):
        q = self._quality(u"第一条", u"第二条", u"第二条之一",
                          u"第二条之二", u"第三条")
        self.assertTrue(q["mono"])
        self.assertTrue(q["complete"])

    def test_之二缺之一判死(self):
        q = self._quality(u"第一条", u"第二条", u"第二条之二", u"第三条")
        self.assertFalse(q["complete"])

    def test_缺号判死(self):
        q = self._quality(u"第一条", u"第二条", u"第四条")
        self.assertFalse(q["complete"])
        self.assertTrue(q["mono"])


if __name__ == "__main__":
    unittest.main()
