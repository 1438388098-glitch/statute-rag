# -*- coding: utf-8 -*-
"""查询扩展：口语↔法言法语同义映射 + 阿拉伯数字中文读法归一。

通用机制（非按题调参）：
- 同义词表 statute_rag/synonyms.json 是领域词典：按法律领域批量收录
  「口语/缩略说法 → 法定表述」的一般性映射（坐牢→服刑、社保→社会保险、
  丢了→丢失、1000元→一千元……），词条与任何具体评测题目无关；
- 扩展方式单调可解释：原查询整体保留，把命中词的扩展表述追加到查询尾部，
  检索器看到的是「原查询 + 补充表述」而非替换（原查询信号永不丢失）；
- 机制有效性由两套金标共同约束：真实金标提升的同时，合成金标 Recall@5
  回退不得超过 2pt（docs/retrieval-improvement.md 记录逐轮数字）。
"""
import io
import json
import os
import re

SYNONYMS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "synonyms.json")

# 数字后接这些单位字（或前面是「第」）时才转换，避免把编号、年份读错场景
_NUM_UNIT_CHARS = set("元倍日天年月个人次件条号岁斤吨公里米页层楼周项台万")
_MAX_NUM = 99999

_CN_DIGITS = "零一二三四五六七八九"
_CN_UNITS = ["", "十", "百", "千"]


def load_synonyms(path=None):
    """加载同义映射表：{口语词: [法定表述, ...]}。校验词条形状。"""
    path = path or SYNONYMS_PATH
    with io.open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    mappings = data["mappings"] if isinstance(data, dict) and "mappings" in data else data
    for key, values in mappings.items():
        if len(key) < 2:
            raise ValueError("同义词表 key 须至少 2 字，避免单字误扩展：%r" % key)
        if not values or not all(isinstance(v, str) and v for v in values):
            raise ValueError("同义词表 value 须为非空字符串列表：%r" % key)
    return mappings


def chinese_numeral(num):
    """阿拉伯数字 → 中文数字读法（1..9999，法定文书读法）。

    10→十、15→十五、105→一百零五、1000→一千、1234→一千二百三十四。
    """
    if num < 0 or num > 9999:
        raise ValueError("仅支持 1..9999：%r" % num)
    if num == 0:
        return "零"
    digits = [int(c) for c in str(num)]
    parts = []
    zero_pending = False
    for idx, d in enumerate(digits):
        unit = _CN_UNITS[len(digits) - 1 - idx]
        if d == 0:
            zero_pending = bool(parts)
            continue
        if zero_pending:
            parts.append("零")
            zero_pending = False
        if d == 1 and unit == "十" and not parts:
            parts.append("十")  # 10..19 读「十X」而非「一十X」
        else:
            parts.append(_CN_DIGITS[d] + unit)
    return "".join(parts)


def numeral_terms(query):
    """从查询提取数字读法变体：10倍→十倍、1000元→一千元、第10条→第十条。

    仅转换「数字+单位字」或「第+数字」两类（法条与法定数额的书写惯例）；
    其余数字（编号、年份等）保持原样，避免产生错误读法。
    """
    terms = []
    seen = set()
    for m in re.finditer(r"(第)?([0-9]{1,5})([" + "".join(_NUM_UNIT_CHARS) + r"])?", query):
        prefix, digits, unit = m.group(1) or "", m.group(2), m.group(3) or ""
        if not prefix and not unit:
            continue  # 裸数字（编号/年份）不转
        num = int(digits)
        if num > 9999:
            continue
        term = (prefix or "") + chinese_numeral(num) + unit
        if term not in seen:
            seen.add(term)
            terms.append(term)
    return terms


def _match_terms(query, keys, max_key_len):
    """前向最长匹配扫描查询串，返回命中的词典 key 列表（按出现序去重）。"""
    hits = []
    seen = set()
    i = 0
    n = len(query)
    while i < n:
        matched = None
        for length in range(min(max_key_len, n - i), 1, -1):  # key 至少 2 字
            sub = query[i:i + length]
            if sub in keys:
                matched = sub
                break
        if matched:
            if matched not in seen:
                seen.add(matched)
                hits.append(matched)
            i += len(matched)
        else:
            i += 1
    return hits


def expand_query(query, synonyms=None, max_terms=8):
    """查询扩展：原查询保留，追加同义表述与数字读法变体（空格分隔）。

    扩展式（而非替换式）是刻意选择：原查询信号永不丢失，且消融测量中
    「替换式变体多路」无 Recall 增量、MRR 略降（docs/retrieval-improvement.md）。
    无任何命中时原样返回（调用方据 expand == query 判断是否新增检索路）。
    max_terms 限制追加词数，防止长尾扩展稀释查询重心。
    """
    synonyms = synonyms if synonyms is not None else load_synonyms()
    extras = []
    seen = set()
    keys = synonyms.keys()
    max_key_len = max((len(k) for k in keys), default=0)
    for key in _match_terms(query, keys, max_key_len):
        for value in synonyms[key]:
            if value not in seen:
                seen.add(value)
                extras.append(value)
    for term in numeral_terms(query):
        if term not in seen:
            seen.add(term)
            extras.append(term)
    extras = extras[:max_terms]
    if not extras:
        return query
    return query + " " + " ".join(extras)
