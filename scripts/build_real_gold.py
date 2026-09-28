# -*- coding: utf-8 -*-
"""构建「真实问句金标」：问句来自网络真实法律问答（百度知道，URL 已记录），
金标条文由 LLM 在语料中定位并逐条核验（证据串必须逐字出现在该条文文本中）。

方法与诚实声明：
- 问句文本取自百度知道搜索结果列表的真实提问标题，来源 URL 逐条记录；
- 金标映射与证据核验由 LLM 完成：在语料中检索候选条文，阅读全文，
  从中摘出能回答问句的关键句（evidence）作为核验凭据；
  **人工法律复核尚未做**，属后续工作；
- 语料中双栏 PDF 解析的公报版法律存在文字交错（见 README「已知失败案例」），
  evidence 允许取「压缩空白后连续出现的片段」，构建脚本强制校验
  evidence 确实是所选条文文本（压缩空白后）的子串；
- 同一答案文本因分块重叠会出现在多个行内，每题最多保留证据占比最高的
  3 行作为 gold_ids（评测命中其中任一行即算命中）。

用法：py -3.13 scripts/build_real_gold.py --corpus data/corpus.jsonl --out gold/gold_real_38.jsonl
"""
import argparse
import io
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

SITE = "百度知道"
# 每题定义：question（真实问句原文）、url（提问页）、anchors（证据候选串，
# 构建时按出现长度取最长且在语料中能逐字找到的）、note（可选说明）
QUESTIONS = [
    # —— 治安管理处罚法（2025 修订，公报双栏版）——
    {"question": "嫖娼可以不拘留只罚款吗", "url": "http://zhidao.baidu.com/question/377476566913890684.html",
     "anchors": ["卖淫、嫖娼的，处十日以上十日以下拘留，可以并处一千元以下罚款", "卖淫、嫖娼的，处十日以上十日以下拘留"]},
    {"question": "嫖娼的处罚标准", "url": "http://zhidao.baidu.com/question/318492787336976404.html",
     "anchors": ["卖淫、嫖娼的，处十日以上十日以下拘留，可以并处一千元以下罚款", "卖淫、嫖娼的，处十日以上十日以下拘留"]},
    {"question": "三次吸毒会被拘留多久", "url": "http://zhidao.baidu.com/question/629967560498840172.html",
     "anchors": ["聚众、组织吸食、注射毒品的", "吸食、注射毒品的"]},
    {"question": "赌博被拘留正常几天?", "url": "http://zhidao.baidu.com/question/1055840583676999619.html",
     "anchors": ["或者参与赌博赌资较大的，处五日以下拘", "或者参与赌博赌资较大的"]},
    {"question": "邻居半夜噪音扰民可以报警吗", "url": "http://zhidao.baidu.com/question/885194592021533212.html",
     "anchors": ["违反关于社会生活噪声污染防治的法律法规规定，产生社会生活噪声", "违反关于社会生活噪声污染防治的法律法规规定"]},
    # —— 未成年人保护法 ——
    {"question": "未成年人沉迷网游的限制规定有哪些", "url": "http://zhidao.baidu.com/question/1748121746127700547.html",
     "anchors": ["设置相应的时间管理、权限管理、消费管理等"]},
    # —— 食品安全法 ——
    {"question": "10元过期食品怎么赔偿", "url": "http://zhidao.baidu.com/question/1969190474919598260.html",
     "anchors": ["价款十倍或者损失三倍的赔偿金"]},
    {"question": "5元过期食品怎么赔偿", "url": "http://zhidao.baidu.com/question/1552800526878834427.html",
     "anchors": ["价款十倍或者损失三倍的赔偿金"]},
    {"question": "去超市买过期食品可以直接要求索赔1000元吗", "url": "http://zhidao.baidu.com/question/702323863027287644.html",
     "anchors": ["价款十倍或者损失三倍的赔偿金"]},
    {"question": "超市卖过期食品需要承担什么责任", "url": "http://zhidao.baidu.com/question/318333192234850164.html",
     "anchors": ["标注虚假生产日期、保质期或者超过保质期的食品", "用超过保质期的食品原料、食品添加", "超过保质期的食品"]},
    {"question": "开餐饮店需要办理哪些证照？", "url": "http://zhidao.baidu.com/question/630014666062590412.html",
     "anchors": ["国家对食品生产经营实行许可"]},
    # —— 食品药品惩罚性赔偿解释 / 消保条例 ——
    {"question": "食品\"知假买假\"能否要求10倍惩罚性赔偿？", "url": "http://zhidao.baidu.com/question/502639332439376012.html",
     "anchors": ["在合理生活消费需要范围内"]},
    {"question": "明知是假货仍然购买，能否主张十倍赔偿？", "url": "http://zhidao.baidu.com/question/702648284063136204.html",
     "anchors": ["在合理生活消费需要范围内"]},
    {"question": "假货是退一赔三还是退一赔十", "url": "http://zhidao.baidu.com/question/694388361324593412.html",
     "anchors": ["经营者提供商品或者服务有欺诈行为的", "价款十倍或者损失三倍的赔偿金"]},
    # —— 预付式消费解释 ——
    {"question": "理发店、健身房跑路，办的卡能不能退，要怎么办？", "url": "http://zhidao.baidu.com/question/2214563881039781988.html",
     "anchors": ["不能按照合同约定兑付商品或者提供服务"]},
    {"question": "消费者能否解除健身房私教课、瑜伽课、美容等预付卡消费合同", "url": "http://zhidao.baidu.com/question/213522195874310405.html",
     "anchors": ["消费者请求解除预付式消费", "排除消费者依法解除合同或者请求"]},
    # —— 住房租赁条例 ——
    {"question": "租房押金不退怎么办", "url": "http://zhidao.baidu.com/question/376217381628690852.html",
     "anchors": ["出租人无正当理由不得扣减押金"]},
    {"question": "个人租房房东不退押金怎么处理最有效", "url": "http://zhidao.baidu.com/question/533218918288288165.html",
     "anchors": ["出租人无正当理由不得扣减押金"]},
    # —— 住房公积金管理条例 ——
    {"question": "住房公积金什么情况下可以提取？", "url": "http://zhidao.baidu.com/question/950895391344771452.html",
     "anchors": ["可以提取职工住房公积金账户内的存储余额"]},
    {"question": "租房提取公积金需要满足什么条件", "url": "http://zhidao.baidu.com/question/1770130075938213788.html",
     "anchors": ["可以提取职工住房公积金账户内的存储余额"]},
    # —— 涉彩礼纠纷规定 ——
    {"question": "彩礼是否可以退还，什么情况下能退", "url": "http://zhidao.baidu.com/question/2025479234950443708.html",
     "anchors": ["离婚时一方请求返还按照习俗给付的彩", "借婚姻索取财物，另一方要求返还"]},
    # —— 劳动争议解释（二）——
    {"question": "公司不给交社保离职后可要求经济补偿金吗？", "url": "http://zhidao.baidu.com/question/318658396284032884.html",
     "anchors": ["用人单位未依法缴纳社会保险费"]},
    {"question": "竞业限制补偿金的标准？", "url": "http://zhidao.baidu.com/question/597716741055740565.html",
     "anchors": ["竞业限制条款约定的竞业限制范围、地"]},
    {"question": "用人单位以何种标准向劳动者支付竞业限制补偿金？", "url": "http://zhidao.baidu.com/question/2024785437765705788.html",
     "anchors": ["竞业限制条款约定的竞业限制范围、地"]},
    # —— 拒执刑事解释 ——
    {"question": "失信被执行人会坐牢吗", "url": "http://zhidao.baidu.com/question/369766066051367372.html",
     "anchors": ["有能力执行而拒不执行，情节严重", "拒不执行判决、裁定罪处罚"]},
    # —— 学前教育法 ——
    {"question": "幼儿园老师用手打孩子犯法吗", "url": "http://zhidao.baidu.com/question/1908679673333381820.html",
     "anchors": ["因管理疏忽或者放任发生体罚或者变", "因管理疏忽或者放任发生体罚"]},
    # —— 建设工程质量管理条例 ——
    {"question": "商品房交付是不是必须取得竣工验收备案表", "url": "http://zhidao.baidu.com/question/317014786688723084.html",
     "anchors": ["建设工程竣工验收应当具备下列条件"]},
    # —— 政府信息公开条例 ——
    {"question": "政府信息公开接到后多少日回复", "url": "http://zhidao.baidu.com/question/759095683845005012.html",
     "anchors": ["应当自收到申请之日起20个工作日内予以答复"]},
    # —— 行政复议法 ——
    {"question": "行政复议的期限是多长", "url": "http://zhidao.baidu.com/question/371509932547182572.html",
     "anchors": ["之日起六十日内提出行政复议申"]},
    # —— 民事诉讼法 ——
    {"question": "法院立案的标准及条件是什么", "url": "http://zhidao.baidu.com/question/763794989089962364.html",
     "anchors": ["第一百二十二条起诉必须符合下列条件"]},
    # —— 仲裁法 ——
    {"question": "如果仲裁协议无效应如何处理", "url": "http://zhidao.baidu.com/question/275654863075949205.html",
     "anchors": ["但仲裁协议无效或者法律另有规定的除外"]},
    # —— 公司法 ——
    {"question": "公司减资，股东需对公司债务承担责任吗？", "url": "http://zhidao.baidu.com/question/1832806930479219468.html",
     "anchors": ["股东应当退还其收到的资金", "第二百二十四条公司减少注册资本"]},
    # —— 快递暂行条例 ——
    {"question": "快递丢了谁负责怎么赔偿", "url": "http://zhidao.baidu.com/question/274982826938210405.html",
     "anchors": ["对未保价的快件，依照民事法律的有关规定确定赔偿责任"]},
    # —— 人体器官捐献和移植条例 ——
    {"question": "器官捐献是否必须是自愿？", "url": "http://zhidao.baidu.com/question/946498565288292572.html",
     "anchors": ["自愿、无偿提供具有特定生理功能", "不得以任何形式买卖人体器官"]},
    # —— 监狱法 ——
    {"question": "坐牢的人可以随时去看望吗", "url": "http://zhidao.baidu.com/question/2130418697179005387.html",
     "anchors": ["可以与亲属、监护人通话、会见"]},
    {"question": "服刑人员家属探视规定", "url": "http://zhidao.baidu.com/question/533521410200439165.html",
     "anchors": ["可以与亲属、监护人通话、会见"]},
    # —— 保障农民工工资支付条例 ——
    {"question": "拖欠农民工工资怎么办", "url": "http://zhidao.baidu.com/question/144837517105977165.html",
     "anchors": ["任何单位和个人不得拖欠农民工工资"]},
    # —— 消防法 ——
    {"question": "电动车在楼道内充电是否属于违法行为", "url": "http://zhidao.baidu.com/question/375872700710101212.html",
     "anchors": ["占用、堵塞、封闭疏散通道、安全出口、消防车"]},
]

MAX_GOLD = 3  # 每题最多保留的金标行数


def main():
    parser = argparse.ArgumentParser(description="构建真实问句金标")
    parser.add_argument("--corpus", default="data/corpus.jsonl")
    parser.add_argument("--out", default="gold/gold_real_38.jsonl")
    parser.add_argument("--max-gold", type=int, default=MAX_GOLD)
    args = parser.parse_args()

    corpus = []
    with io.open(args.corpus, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                corpus.append(json.loads(line))
    for a in corpus:
        a["_norm"] = "".join((a.get("text") or "").split())
    print("语料条文数：", len(corpus))

    out, failed = [], []
    for i, spec in enumerate(QUESTIONS, start=1):
        evidence, gold_rows = None, []
        for anchor in spec["anchors"]:
            rows = [a for a in corpus if anchor in a["_norm"]]
            if rows:
                evidence = anchor
                # 证据占比 = 证据起点 / 行文本长度，越小代表该行越「围绕」答案
                rows.sort(key=lambda a: a["_norm"].index(anchor) / max(len(a["_norm"]), 1))
                gold_rows = rows[: args.max_gold]
                break
        if not gold_rows:
            failed.append(spec["question"])
            continue
        # 逐条核验：evidence 必须是每个金标行文本的子串
        for a in gold_rows:
            assert evidence in a["_norm"], (spec["question"], a["id"])
        primary = gold_rows[0]
        out.append({
            "qid": "r%03d" % i,
            # query：真实问句原文（口语、含疑问词），直接送给检索器
            "query": spec["question"],
            "question": spec["question"],
            "gold_id": primary["id"],
            "law": primary["law"],
            "num": primary["num"],
            "gold_ids": [a["id"] for a in gold_rows],
            "gold_laws": ["%s %s" % (a["law"], a["num"]) for a in gold_rows],
            "source_site": SITE,
            "source_url": spec["url"],
            "evidence": evidence,
        })

    with io.open(args.out, "w", encoding="utf-8") as f:
        for q in out:
            f.write(json.dumps(q, ensure_ascii=False) + "\n")
    print("已写出 %d 题金标：%s" % (len(out), args.out))
    if failed:
        print("未能在语料中核验到证据、已剔除 %d 题：" % len(failed))
        for q in failed:
            print("  -", q)


if __name__ == "__main__":
    main()
