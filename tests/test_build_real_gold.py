# -*- coding: utf-8 -*-
"""build_real_gold 多批次/证据核验行为的单元测试。"""
import io
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts.build_real_gold import build_gold, load_specs

CORPUS = [
    {"id": 1, "law": u"测试法", "num": u"第一条",
     "text": u"用人单位拖欠劳动报酬的，劳动者可以要求支付。"},
    {"id": 2, "law": u"测试法", "num": u"第二条",
     "text": u"这里也出现用人单位拖欠劳动报酬的表述，作为第二条。"},
    {"id": 3, "law": u"他法", "num": u"第一条", "text": u"无关内容。"},
]


class BuildGoldTest(unittest.TestCase):
    def test_evidence_verbatim_maps_to_rows(self):
        specs = [{"question": u"拖欠工资怎么办", "url": u"http://x/1.html",
                  "anchors": [u"用人单位拖欠劳动报酬的"]}]
        out, failed = build_gold(CORPUS, specs)
        self.assertEqual(failed, [])
        self.assertEqual(len(out), 1)
        row = out[0]
        self.assertEqual(row["qid"], u"r001")
        self.assertIn(1, row["gold_ids"])
        self.assertEqual(row["source_site"], u"百度知道")  # 默认站点
        self.assertIn(u"用人单位拖欠劳动报酬的", row["evidence"])

    def test_unverified_spec_skipped_and_reported(self):
        specs = [{"question": u"未核验问题", "url": u"http://x/2.html", "verified": False,
                  "anchors": [u"用人单位拖欠劳动报酬的"]}]
        out, failed = build_gold(CORPUS, specs)
        self.assertEqual(out, [])
        self.assertEqual(len(failed), 1)
        self.assertIn(u"未核验", failed[0][1])

    def test_anchor_missing_reported(self):
        specs = [{"question": u"找不到", "url": u"http://x/3.html", "anchors": [u"不存在的证据串"]}]
        out, failed = build_gold(CORPUS, specs)
        self.assertEqual(out, [])
        self.assertIn(u"未逐字命中", failed[0][1])

    def test_qid_prefix_and_source_site(self):
        specs = [{"question": u"q", "url": u"http://x/4.html", "source_site": u"找法网",
                  "anchors": [u"用人单位拖欠劳动报酬的"]}]
        out, _ = build_gold(CORPUS, specs, qid_prefix=u"v3")
        self.assertEqual(out[0]["qid"], u"v3001")
        self.assertEqual(out[0]["source_site"], u"找法网")

    def test_explicit_qid_wins(self):
        specs = [{"question": u"q", "url": u"http://x/5.html", "qid": u"custom-1",
                  "anchors": [u"用人单位拖欠劳动报酬的"]}]
        out, _ = build_gold(CORPUS, specs)
        self.assertEqual(out[0]["qid"], u"custom-1")

    def test_max_gold_cap(self):
        specs = [{"question": u"q", "url": u"http://x/6.html", "anchors": [u"用人单位拖欠劳动报酬的"]}]
        out, _ = build_gold(CORPUS, specs, max_gold=1)
        self.assertEqual(len(out[0]["gold_ids"]), 1)


class LoadSpecsTest(unittest.TestCase):
    def test_loads_jsonl(self):
        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, "spec.jsonl")
        with io.open(path, "w", encoding="utf-8") as f:
            f.write(json.dumps({"question": u"a", "url": u"u", "anchors": [u"x"]},
                               ensure_ascii=False) + "\n")
        specs = load_specs(path)
        self.assertEqual(len(specs), 1)

    def test_missing_question_raises(self):
        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, "bad.jsonl")
        with io.open(path, "w", encoding="utf-8") as f:
            f.write(json.dumps({"url": u"u", "anchors": [u"x"]}, ensure_ascii=False) + "\n")
        with self.assertRaises(SystemExit):
            load_specs(path)

    def test_verified_without_anchors_raises(self):
        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, "bad2.jsonl")
        with io.open(path, "w", encoding="utf-8") as f:
            f.write(json.dumps({"question": u"a", "url": u"u"}, ensure_ascii=False) + "\n")
        with self.assertRaises(SystemExit):
            load_specs(path)

    def test_unverified_candidate_without_anchors_ok(self):
        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, "cand.jsonl")
        with io.open(path, "w", encoding="utf-8") as f:
            f.write(json.dumps({"question": u"a", "url": u"u", "verified": False},
                               ensure_ascii=False) + "\n")
        specs = load_specs(path)
        self.assertEqual(len(specs), 1)


if __name__ == "__main__":
    unittest.main()
