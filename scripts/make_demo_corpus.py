# -*- coding: utf-8 -*-
"""生成完全合成的演示语料：假条文 + 对应问句金标，让 run_eval.py 开箱跑通。

为什么需要它：仓库不含真实法条语料（见 README「边界」），外人没有 legal.db
就无法复现评测。本脚本用程序化模板拼装约 100 条**假条文**（章节条款随机组合、
条号与内容全部生成），配合 statute_rag.gold.make_gold 生成合成金标，输出到
demo_corpus/，使评测管线可以在零外部依赖（纯标准库、无模型/无 API）下走通。

诚实声明（与 gold.py 一致，且更进一步）：
- 条文文本**不含任何真实法律内容**，全部为程序生成的演示用假条文；
- 每条文本开头都带「合成演示条文」标记，法名前缀「合成」，JSONL 额外带
  "synthetic": true 字段；
- 演示语料上的评测分数**只验证管线可用**，不代表真实法条检索质量。

可重复性：固定随机种子；每条条文附带唯一事项代号（SY0001…），保证金标
生成所需的「全库唯一短语」在每条条文上都存在。
"""
import argparse
import io
import json
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from statute_rag.gold import make_gold, save_gold

DEFAULT_SEED = 20260928
DEFAULT_ARTICLES = 100
DEFAULT_GOLD_SIZE = 50

# 合成标记：出现在每条条文开头，任何引用输出都能一眼看出是演示数据
SYNTHETIC_MARK = u"本条为程序生成的合成演示条文，非真实法律文本。"

# 词库全部为自造词，不取自任何真实法律文本
LAW_NAMES = [u"合成示例法", u"演示管理条例", u"样例实施办法", u"虚拟规程", u"测试细则"]
SUBJECTS = [u"演示机关", u"样例机构", u"虚拟单位", u"测试主体", u"示例组织", u"模型当事人"]
SCENES = [u"演示活动期间", u"样例试点区域内", u"虚拟项目实施中", u"测试批次运转时", u"示例年度内"]
SHOULD_DO = [u"按期申报演示事项", u"保存样例台账", u"报送虚拟数据", u"公示测试结果", u"登记示例信息"]
MUST_NOT_DO = [u"伪造演示记录", u"转让样例凭证", u"泄露虚拟数据", u"挪用测试经费", u"虚构示例主体"]
TOPICS = [u"蓝箱管理", u"绿码申报", u"数据留痕", u"凭证核验", u"台账公示", u"区域协作", u"时段管控", u"名录更新"]
MEASURES = [u"责令限期整改", u"暂停演示资格", u"收回样例凭证", u"通报虚拟主管", u"记入测试档案"]

CN_DIGITS = u"零一二三四五六七八九"


def to_cn_num(n):
    """1-99 的中文数字（条号用），如 12 -> 十二、20 -> 二十。"""
    if n < 10:
        return CN_DIGITS[n]
    tens, ones = divmod(n, 10)
    part = u"十" if tens == 1 else CN_DIGITS[tens] + u"十"
    return part if ones == 0 else part + CN_DIGITS[ones]


def make_article(rng, law_name, art_idx, demo_idx):
    """拼装一条假条文。demo_idx 保证事项代号全库唯一。"""
    chapter = rng.randint(1, 6)
    amount_a = rng.randrange(500, 9500, 100)
    amount_b = amount_a * rng.randint(5, 20)
    text = (
        SYNTHETIC_MARK
        + u"{scene}，{subject}应当{should}，并确保{topic}全过程可回溯；"
          u"不得{must_not}。违反前款规定的，由演示主管机关{measure}，"
          u"并处{amount_a}元以上{amount_b}元以下罚款；情节严重的，"
          u"可以并处吊销演示凭证。本条事项代号SY{code:04d}。"
    ).format(
        scene=rng.choice(SCENES),
        subject=rng.choice(SUBJECTS),
        should=rng.choice(SHOULD_DO),
        topic=rng.choice(TOPICS),
        must_not=rng.choice(MUST_NOT_DO),
        measure=rng.choice(MEASURES),
        amount_a=amount_a,
        amount_b=amount_b,
        code=demo_idx,
    )
    return {
        "id": "demo-%04d" % demo_idx,
        "law": u"%s（合成演示%d）" % (law_name, LAW_NAMES.index(law_name) + 1),
        "num": u"第%s章第%s条" % (to_cn_num(chapter), to_cn_num(art_idx)),
        "text": " ".join(text.split()),
        "synthetic": True,
    }


def build_corpus(num_articles, seed):
    """生成 num_articles 条假条文，条号在各自法内连续、内容随机组合。"""
    rng = random.Random(seed)
    corpus = []
    for i in range(num_articles):
        law_name = LAW_NAMES[i % len(LAW_NAMES)]
        # 同一部法内条号从一连续编号；法名轮转使每部约 num/laws 条
        art_idx = i // len(LAW_NAMES) + 1
        corpus.append(make_article(rng, law_name, art_idx, i + 1))
    return corpus


def save_corpus(corpus, path):
    with io.open(path, "w", encoding="utf-8") as f:
        for item in corpus:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")


def self_check(corpus, gold):
    """产物格式断言：id 唯一、全部带合成标记、金标可被语料回答。"""
    ids = [item["id"] for item in corpus]
    assert len(ids) == len(set(ids)), "条文 id 存在重复"
    by_id = dict((item["id"], item) for item in corpus)
    for item in corpus:
        assert item.get("synthetic") is True, "条文缺少 synthetic 标记: %s" % item["id"]
        assert SYNTHETIC_MARK in item["text"], "条文缺少合成标记文本: %s" % item["id"]
        assert item["text"].strip() and item["law"] and item["num"], "字段为空: %s" % item["id"]
    assert gold, "金标为空"
    for q in gold:
        assert q["gold_id"] in by_id, "金标指向不存在的条文: %s" % q["gold_id"]
        compact = "".join(by_id[q["gold_id"]]["text"].split())
        assert "".join(q["query"].split()) in compact, "query 不是 gold 条文的子串: %s" % q["qid"]
    print("自检通过：%d 条条文，%d 题金标，id 唯一、全部含合成标记、query 均为 gold 条文子串"
          % (len(corpus), len(gold)))


def build(out_dir="demo_corpus", num_articles=DEFAULT_ARTICLES,
          gold_size=DEFAULT_GOLD_SIZE, seed=DEFAULT_SEED):
    """生成并落盘演示语料与金标，返回 (corpus, gold, corpus_path, gold_path)。

    main() 与单测（tests/test_app.py）共用这一条路径：演示语料是 .gitignore 的
    产物，单测若因它缺失而 skip，例数就随环境浮动，check_doc_numbers.py 的
    例数对账在 CI 上必然漂移。缺就现场生成，例数才是确定的。
    """
    corpus = build_corpus(num_articles, seed)
    gold = make_gold(corpus, size=gold_size, seed=seed)

    os.makedirs(out_dir, exist_ok=True)
    corpus_path = os.path.join(out_dir, "corpus.jsonl")
    gold_path = os.path.join(out_dir, "gold.jsonl")
    save_corpus(corpus, corpus_path)
    save_gold(gold, gold_path)
    return corpus, gold, corpus_path, gold_path


def main():
    parser = argparse.ArgumentParser(description=u"生成合成演示语料（假条文 + 金标）")
    parser.add_argument("--out-dir", default="demo_corpus", help=u"输出目录")
    parser.add_argument("--num-articles", type=int, default=DEFAULT_ARTICLES)
    parser.add_argument("--gold-size", type=int, default=DEFAULT_GOLD_SIZE)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args()

    corpus, gold, corpus_path, gold_path = build(
        args.out_dir, args.num_articles, args.gold_size, args.seed)

    self_check(corpus, gold)
    print("语料已写入：", corpus_path)
    print("金标已写入：", gold_path, "(seed=%d)" % args.seed)
    print(u"下一步：python scripts/run_eval.py --corpus %s --gold %s --out-dir %s"
          % (corpus_path, gold_path, args.out_dir))


if __name__ == "__main__":
    main()
