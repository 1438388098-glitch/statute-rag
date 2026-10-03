# -*- coding: utf-8 -*-
"""修复真实问句金标里 9 处过时/垃圾条目（r001/r002/r012/r013/r022/r023/r024/
r025/r030）——版本对齐，非按题调参。

背景：这批金标 2026-09 建于旧语料（治安管理处罚法 2005 版、民诉法旧条号、
劳动争议解释（二）与食品药品解释的旧试行稿）。v5/v6 语料已换成现行文本，
(law,num) 机械迁移把部分条号对到了「同号不同义」的新条文上，另有一批
gold_laws 混入了不存在的条号（迁移垃圾，如 21 条的解释标着第四十八条）。
scripts/check_gold.py 结构检查 + evidence 逐字校验定位出这 9 处。

修复方式：把 (law,num) 与 evidence 对齐到语料内现行条文（逐字校验通过），
每行写入 gold_fix 审计字段。修复后必须过 scripts/check_gold.py。

重建 v6 金标后重跑本脚本即可复现：
  python scripts/rebuild_corpus_v4.py … --gold gold/gold_real_38_v5.jsonl …
  python scripts/fix_gold_real38_stale.py --corpus data/corpus_v6.jsonl \
      --gold gold/gold_real_38_v6.jsonl
"""
import argparse
import io
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

# qid → (新 gold_laws, 新 evidence)；条号取自语料内现行文本，逐字可校验
LAWS_STARE_2025 = u"中华人民共和国治安管理处罚法 第七十八条"
PIAO_EVIDENCE = u"卖淫、嫖娼的，处十日以上十五日以下拘留，可以并处五千元以下罚款；情节较轻的，处五日以下拘留或者一千元以下罚款"
FIXES = {
    u"r001": {
        u"gold_laws": [LAWS_STARE_2025],
        u"evidence": PIAO_EVIDENCE,
        u"reason": u"治安管理处罚法 2025-06-27 修订施行，evidence 原为旧法（2005）文本；2025 版卖淫嫖娼条款在第七十八条",
    },
    u"r002": {
        u"gold_laws": [LAWS_STARE_2025],
        u"evidence": PIAO_EVIDENCE,
        u"reason": u"同 r001",
    },
    u"r012": {
        u"gold_laws": [u"最高人民法院关于审理食品药品惩罚性赔偿纠纷案件适用法律若干问题的解释 第十二条"],
        u"evidence": u"在合理生活消费需要范围内",
        u"reason": u"原金标混入「第一百四十四条/第五十五条」两个不存在的条号（迁移垃圾），该解释仅十余条；知假买假十倍赔偿条款为第十二条",
    },
    u"r013": {
        u"gold_laws": [u"最高人民法院关于审理食品药品惩罚性赔偿纠纷案件适用法律若干问题的解释 第十二条"],
        u"evidence": u"在合理生活消费需要范围内",
        u"reason": u"同 r012",
    },
    u"r022": {
        u"gold_laws": [u"最高人民法院关于审理劳动争议案件适用法律问题的解释（二） 第十九条",
                       u"中华人民共和国劳动合同法 第三十八条",
                       u"中华人民共和国劳动合同法 第四十六条"],
        u"evidence": u"用人单位未依法缴纳社会保险费，劳动者根据劳动合同法第三十八条第一款第三项规定请求解除劳动合同、由用人单位支付经济补偿的，人民法院依法予以支持",
        u"reason": u"劳动争议解释（二）2025-09-01 施行正文仅 21 条，原金标「第四十八条」不存在；未缴社保经济补偿条款为第十九条",
    },
    u"r023": {
        u"gold_laws": [u"最高人民法院关于审理劳动争议案件适用法律问题的解释（二） 第十三条"],
        u"evidence": u"竞业限制条款约定的竞业限制范围、地",
        u"reason": u"原金标「第三十八条」不存在（该解释仅 21 条）；竞业限制补偿标准条款为第十三条",
    },
    u"r024": {
        u"gold_laws": [u"最高人民法院关于审理劳动争议案件适用法律问题的解释（二） 第十三条"],
        u"evidence": u"竞业限制条款约定的竞业限制范围、地",
        u"reason": u"同 r023",
    },
    u"r025": {
        u"gold_laws": [u"最高人民法院、最高人民检察院关于办理拒不执行判决、裁定刑事案件适用法律若干问题的解释 第一条",
                       u"最高人民法院、最高人民检察院关于办理拒不执行判决、裁定刑事案件适用法律若干问题的解释 第三条",
                       u"中华人民共和国刑法 第三百一十三条"],
        u"evidence": u"有能力执行而拒不执行，情节严重",
        u"reason": u"原金标把刑法的「第三百一十三条」挂在拒执罪解释名下（法名错挂）；改为解释第一条/第三条 + 刑法第三百一十三条",
    },
    u"r030": {
        u"gold_laws": [u"中华人民共和国民事诉讼法 第一百二十二条",
                       u"中华人民共和国民事诉讼法 第一百二十六条"],
        u"evidence": u"起诉必须符合下列条件",
        u"reason": u"民诉法 2023 修正后起诉条件为第一百二十二条（立案审查为第一百二十六条），原金标 127/126/121 为旧法条号",
    },
}


def main():
    ap = argparse.ArgumentParser(description=u"修复真实金标 4 道过时题")
    ap.add_argument("--corpus", default=os.path.join("data", "corpus_v6.jsonl"))
    ap.add_argument("--gold", default=os.path.join("gold", "gold_real_38_v6.jsonl"))
    args = ap.parse_args()

    by_id = {}
    with io.open(args.corpus, "r", encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            by_id[r["id"]] = r
    index = {}
    for r in by_id.values():
        index[(r["law"], r["num"])] = r["id"]

    rows = []
    with io.open(args.gold, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))

    fixed = 0
    for g in rows:
        fix = FIXES.get(g.get("qid"))
        if not fix:
            continue
        new_ids = []
        for pair in fix["gold_laws"]:
            law, _, num = pair.rpartition(u" ")
            assert (law, num) in index, u"条号对不在语料：%s" % pair
            new_ids.append(index[(law, num)])
        # 修复后的 evidence 必须逐字出现在主命中条文里（与 rebuild 的校验同规）
        primary = by_id[new_ids[0]]
        ev = u"".join(fix["evidence"].split())
        assert ev in u"".join(primary["text"].split()), \
            u"evidence 与主命中条文不逐字一致：%s" % g["qid"]
        g["gold_laws"] = fix["gold_laws"]
        g["evidence"] = fix["evidence"]
        g["gold_ids"] = new_ids[:3]
        g["gold_id"] = new_ids[0]
        g["remap"] = dict(g.get("remap") or {}, evidence_verified=True)
        g["gold_fix"] = {"reason": fix["reason"], "corpus": u"v6（现行文本）"}
        fixed += 1

    with io.open(args.gold, "w", encoding="utf-8", newline="\n") as f:
        for g in rows:
            f.write(json.dumps(g, ensure_ascii=False) + u"\n")
    print(u"修复 %d 题 → %s（随后请跑 scripts/check_gold.py 复核）" % (fixed, args.gold))


if __name__ == "__main__":
    main()
