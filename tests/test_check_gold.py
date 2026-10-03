# -*- coding: utf-8 -*-
"""金标校验器的行为锁定：结构错误必须报，evidence 未命中只警告。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from scripts.check_gold import check  # noqa: E402

CORPUS = [
    {"id": 1, "law": u"中华人民共和国刑法", "num": u"第一百三十三条之一",
     "text": u"第一百三十三条之一 在道路上驾驶机动车，处拘役，并处罚金。"},
    {"id": 2, "law": u"中华人民共和国刑法", "num": u"第二百八十七条之二",
     "text": u"第二百八十七条之二 明知他人利用信息网络实施犯罪，为其提供帮助的。"},
    {"id": 3, "law": u"最高人民法院关于审理劳动争议案件适用法律问题的解释（二）",
     "num": u"第十九条", "text": u"第十九条 用人单位未依法缴纳社会保险费的，人民法院依法予以支持。"},
]
BY_ID = {r["id"]: r for r in CORPUS}
LAW_NUMS = {}
for r in CORPUS:
    LAW_NUMS.setdefault(r["law"], set()).add(r["num"])


class CheckGoldTest(unittest.TestCase):
    def test_一致金标零报告(self):
        g = {"qid": "q1", "gold_id": 1, "gold_ids": [1],
             "gold_laws": [u"中华人民共和国刑法 第一百三十三条之一"],
             "evidence": u"在道路上驾驶机动车"}
        struct, ev_miss = check([g], BY_ID, LAW_NUMS)
        self.assertEqual((struct, ev_miss), ([], []))

    def test_不存在的条号是结构错误(self):
        g = {"qid": "q2", "gold_id": 3, "gold_ids": [3],
             "gold_laws": [u"最高人民法院关于审理劳动争议案件适用法律问题的解释（二） 第四十八条"]}
        struct, _ = check([g], BY_ID, LAW_NUMS)
        self.assertTrue(any(u"第四十八条" in s for s in struct))

    def test_id不在语料是结构错误(self):
        g = {"qid": "q3", "gold_id": 999, "gold_ids": [999],
             "gold_laws": [u"中华人民共和国刑法 第一百三十三条之一"]}
        struct, _ = check([g], BY_ID, LAW_NUMS)
        self.assertTrue(any(u"999" in s for s in struct))

    def test_laws与ids指向不同条文是结构错误(self):
        g = {"qid": "q4", "gold_id": 2, "gold_ids": [2],
             "gold_laws": [u"中华人民共和国刑法 第一百三十三条之一"]}
        struct, _ = check([g], BY_ID, LAW_NUMS)
        self.assertTrue(any(u"指向不同条文" in s for s in struct))

    def test_evidence未命中只警告不报错(self):
        g = {"qid": "q5", "gold_id": 1, "gold_ids": [1],
             "gold_laws": [u"中华人民共和国刑法 第一百三十三条之一"],
             "evidence": u"旧法已删改的文本"}
        struct, ev_miss = check([g], BY_ID, LAW_NUMS)
        self.assertEqual(struct, [])
        self.assertEqual(len(ev_miss), 1)

    def test_无gold_laws的合成金标跳过条号检查(self):
        g = {"qid": "q6", "gold_id": 2, "gold_ids": [2], "gold_laws": []}
        struct, ev_miss = check([g], BY_ID, LAW_NUMS)
        self.assertEqual((struct, ev_miss), ([], []))


if __name__ == "__main__":
    unittest.main()
