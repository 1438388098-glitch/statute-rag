# -*- coding: utf-8 -*-
"""合成金标生成：从语料自身抽取区分性短语，模板化生成检索问题。

诚实声明：这是**合成金标**——问题由模板从条文文本机械生成，不是真实
用户问题。它衡量的是「给定条文中的独特表述，能否把该条文检回来」，
即词法检索层面的指标；真实问句金标（人工/LLM 改写）是后续工作。

可复现性：随机采样使用固定 seed，同版本语料 + 同参数 → 同一份金标。
"""
import io
import json
import math
import random

from statute_rag.retrieval import char_ngrams

PHRASE_LEN = 6          # 题目短语长度（字符）
DEFAULT_SIZE = 200
DEFAULT_SEED = 20260918


def build_gram_df(corpus):
    """{gram: 含该 gram 的条文数}——短语文档频率的基础。"""
    gram_df = {}
    for item in corpus:
        for g in set(char_ngrams(item["text"])):
            gram_df[g] = gram_df.get(g, 0) + 1
    return gram_df


def build_gram_postings(corpus):
    """{gram: set(下标)}——短语唯一性检查用倒排，避免逐候选全库扫描。"""
    postings = {}
    for idx, item in enumerate(corpus):
        for g in set(char_ngrams(item["text"])):
            postings.setdefault(g, set()).add(idx)
    return postings


def phrase_df(phrase, postings):
    """包含该短语的条文数：短语的全部 gram 所在条文集的交集大小。

    条文含短语 ⇔ 含其全部（二元组）gram——集合交集给出精确 df。
    交集按集合大小升序（先小后大），让中间结果尽快收缩。
    """
    grams = char_ngrams(phrase)
    sets = [postings[g] for g in grams if g in postings]
    if len(sets) < len(grams) or not sets:
        return 0
    sets.sort(key=len)
    return len(set.intersection(*sets))


def candidate_phrases(text, gram_df, n_docs, phrase_len=PHRASE_LEN):
    """按平均 IDF 从高到低返回条文中所有候选短语。"""
    s = "".join((text or "").split())
    if len(s) < phrase_len:
        return []
    scores = {}
    for i in range(len(s) - phrase_len + 1):
        phrase = s[i:i + phrase_len]
        if phrase in scores:
            continue
        p_grams = char_ngrams(phrase)
        if not p_grams:
            continue
        idf = sum(math.log((n_docs + 1.0) / (gram_df.get(g, 0) + 1.0)) for g in p_grams) / len(p_grams)
        scores[phrase] = idf
    return sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))


def pick_unique_phrase(text, postings, gram_df, n_docs, phrase_len=PHRASE_LEN,
                       max_candidates=80):
    """挑选「全语料唯一」且 IDF 最高的短语；找不到唯一的就返回 None。

    为什么要求唯一：IDF 最高的短语在模板化法条里仍可能是跨条文复用的
    公式化表述（如「应当减轻或者免除处罚」），用它出题会让金标天然歧义，
    召回上限失真。唯一性校验（经倒排交集精确计数）让 200 题金标每题
    只有一个正确答案。

    max_candidates：每条条文最多尝试 IDF 前 N 个候选——语料巨大时防止
    在「没有任何唯一短语」的条文上做全候选扫描；被跳过的条文由采样池
    的其余条文补位，不影响凑齐目标题数。
    """
    tried = 0
    for phrase, _idf in candidate_phrases(text, gram_df, n_docs, phrase_len):
        if tried >= max_candidates:
            return None
        tried += 1
        if phrase_df(phrase, postings) == 1:
            return phrase
    return None


def make_gold(corpus, size=DEFAULT_SIZE, seed=DEFAULT_SEED, require_unique=True):
    """从语料生成合成金标。corpus 为 importer.load_corpus 的产物。

    返回 question dict 列表：{qid, query, question, gold_id, law, num}
    """
    gram_df = build_gram_df(corpus)
    n_docs = len(corpus)
    postings = build_gram_postings(corpus)
    rng = random.Random(seed)
    pool = list(corpus)
    rng.shuffle(pool)
    questions = []
    for item in pool:
        if len(questions) >= size:
            break
        if require_unique:
            phrase = pick_unique_phrase(item["text"], postings, gram_df, n_docs)
        else:
            cands = candidate_phrases(item["text"], gram_df, n_docs)
            phrase = cands[0][0] if cands else None
        if not phrase:
            continue
        questions.append({
            "qid": "q%03d" % (len(questions) + 1),
            # query：实际送给检索器的关键词式查询（模拟真实用户的短查询习惯）
            "query": phrase,
            # question：模板化的完整问句，仅作文档展示；真实问句金标属后续工作
            "question": u"关于「%s」的法律规定是哪一条？" % phrase,
            "gold_id": item["id"],
            "law": item["law"],
            "num": item["num"],
        })
    return questions


def save_gold(questions, path):
    with io.open(path, "w", encoding="utf-8") as f:
        for q in questions:
            f.write(json.dumps(q, ensure_ascii=False) + "\n")


def load_gold(path):
    out = []
    with io.open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out
