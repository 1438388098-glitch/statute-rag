English · [简体中文](./README.zh-CN.md)

# statute-rag · Article-level hybrid statute retrieval

A statute-retrieval pipeline that treats the **article** as the retrieval unit and **verifiable citations** as a hard constraint, with a **fully reproducible offline evaluation**. It is the retrieval foundation for statute question answering with forced article-level citations: structured article-level chunking → character-bigram BM25 (original query + synonym-expanded query) / LIKE multi-channel fusion (RRF) → forced article-level citations.

**Current version v0.1.1: the lexical retrieval trio, real evaluation numbers, and a query-expansion improvement for real questions (real-question Recall@5 26.3% → 44.7% with zero regression on the synthetic gold). A semantic vector channel is on the roadmap (below) and is not part of what this version claims.**

## Quick start (no real corpus required)

The repository does not ship real statute texts (see "Scope"). Without a legal.db you can still run the whole pipeline on a script-generated synthetic demo corpus — three commands, pure standard library, no model / API / GPU:

```bash
# 1) Generate the synthetic demo corpus: ~100 programmatically generated
#    fake articles + a synthetic gold set (fixed seed, reproducible)
python scripts/make_demo_corpus.py

# 2) Evaluate the retrieval trio on the demo corpus
python scripts/run_eval.py --corpus demo_corpus/corpus.jsonl --gold demo_corpus/gold.jsonl --out-dir demo_corpus

# 3) Search demo
python scripts/search_cli.py --corpus demo_corpus/corpus.jsonl "台账公示" --k 2
```

> Scores on the demo corpus **only verify that the pipeline runs end to end** (template-generated fake articles are trivially separable from each other); they say nothing about real-world retrieval quality. For the real evaluation numbers see "Evaluation" below.

## The problem

Legal QA / compliance scenarios do not ask retrieval to "find a related document". They require:

- given a statement or a keyword, **hit the exact article**;
- every result **must carry a verifiable source** (law name + article number + original text) — citing the wrong article is worse than answering "not found";
- retrieval quality must come with **reproducible numbers**, not "looks about right".

This project grew out of practice with [legal-wisdom-app](https://github.com/1438388098-glitch/legal-wisdom-app): under unicode61 tokenization, its SQLite FTS5 search degrades Chinese queries into substring/fuzzy matching (empirically tested, see `tests/test_search.py` in that repo) — no relevance scoring, no ranking. statute-rag builds an evaluation-backed retrieval base from zero.

## Scope (what this is not, stated up front)

- **Not a semantic RAG, for now**: no embeddings, no vector store. Character-bigram BM25 is lexical retrieval. v0.1.1 adds a lexical bridge — a colloquial↔statutory synonym dictionary plus query expansion (坐牢→服刑 "serving a prison term", 探视→会见 "visitation→meeting", 社保→社会保险 "social insurance", 1000元→一千元 numeral normalization) — lifting real-question Recall@5 from 26.3% to 44.7%; purely semantic paraphrases with no dictionary bridge still cannot be hit. The semantic channel needs a usable Chinese embedding model/API and belongs to v0.2.
- **Two gold sets**: the synthetic gold (unique phrase → keyword query) measures the lexical recall ceiling; the real-question gold v1 (38 questions from genuine web Q&A, LLM-verified, human legal review pending) measures real-question performance (hybrid 26.3% → 44.7%). See [docs/real-question-eval.md](docs/real-question-eval.md).
- **The corpus is not distributed with the repo**: statutes come from a local legal-wisdom database (263 laws, 14,212 clean articles); the repo contains code, tests, and evaluation artifacts only. Reproducing the real-corpus numbers requires your own corpus.
- Nothing here is legal advice; the authoritative text of any statute is its official publication.

## How it works

```
legal.db ──importer──> corpus JSONL (three-layer quality gate)
              │            · drops (cid:xx) font-mapping debris
              │            · drops <30-char parse residue / empty content
              │            · 14,344 → 14,212 articles (0.9% discarded)
              ▼
   Retrieval trio (unified Citation output: law + article no. + text + id)
   ├── LikeRetriever   exact substring baseline (mimics what unicode61
   │                   actually does to Chinese queries)
   ├── BM25Retriever   character-bigram BM25 (zero-dependency, no tokenizer)
   └── HybridRetriever multi-channel RRF: original-query BM25
                       + synonym-expanded-query BM25 (synonyms.json,
                         127 colloquial↔statutory entries + numeral
                         normalization, via query_expansion.py)
                       + LIKE (original query)
              ▼
   Evaluation (gold.py + eval_harness.py)
   · Gold: the highest-IDF 6-char phrase that is unique corpus-wide
     → keyword query, fixed seed
   · Metrics: Recall@5 / MRR
```

Key design decisions:

- **Unique-phrase constraint**: the first gold design took each article's "highest-IDF phrase"; in testing, 74% of such query phrases were reused across articles (statutes are formulaic), which distorted the recall ceiling. Switching to a "unique corpus-wide" constraint (exact df==1 counted via inverted-index intersection) leaves every question with exactly one correct answer.
- **Prefer importing less over importing bad**: two-column PDF parsing produced interleaved-column pollution (see failure cases below); the quality gate filters only deterministically identifiable corruption. Out-of-order detection remains an open problem.
- **Query expansion adds, never replaces (v0.1.1)**: real questions are colloquial (坐牢 "doing time", 看望 "visiting", 社保 "social insurance") while statutes use formal wording (服刑, 会见, 社会保险) — no lexical overlap. The fix is a data-driven domain dictionary (`statute_rag/synonyms.json`, 127 general-purpose entries) with append-style query expansion and RRF multi-channel fusion: **the original query is kept verbatim**, expansion becomes an extra retrieval channel (omitted automatically when nothing fires). Anti-overfitting discipline and the full per-round ablation (including mechanisms removed for zero gain) are documented in [docs/retrieval-improvement.md](docs/retrieval-improvement.md).

## Evaluation (real numbers, not fabricated)

**Two gold sets, two scopes, reported side by side** (the hybrid row shows the v0.1 baseline → v0.1.1 improvement):

| Retriever | Synthetic gold Recall@5 | Synthetic MRR | Real-question gold v1 Recall@5 | Real-question MRR |
|---|---|---|---|---|
| like (substring baseline) | 77.4% | 0.774 | 0.0% | 0.000 |
| bm25 (character bigram) | 96.6% | 0.954 | 26.3% | 0.180 |
| hybrid (v0.1 baseline: BM25+LIKE dual-channel RRF) | 98.9% | 0.984 | 26.3% (10/38) | 0.180 |
| **hybrid (v0.1.1: + synonym-expansion channel)** | **98.9% (zero regression)** | **0.984** | **44.7% (17/38, +18.4pt)** | **0.312** |

- **Synthetic gold scope**: questions are mechanically derived from phrases unique corpus-wide (seed=20260918, N=177), measuring the lexical recall ceiling of "given a distinctive phrasing, retrieve the article back". Gold at [gold/gold_synth_seed20260918.jsonl](gold/gold_synth_seed20260918.jsonl), report at [docs/eval_report.md](docs/eval_report.md). It also serves as the **holdout guard** for the query-expansion improvement: any real-gold gain is valid only if the synthetic gold drops by no more than 2pt — observed regression: 0.
- **Real-question gold v1 (LLM-verified; human legal review pending)**: 38 questions taken verbatim from genuine legal Q&A posts on Baidu Zhidao (source URL recorded per question); the raw colloquial question is sent to the retriever unchanged. The v0.1 baseline hybrid Recall@5 was **26.3%** — main causes: no lexical overlap between colloquial wording and statutory phrasing, intra-law competition among chunk rows, and the two-column PDF extraction pollution of gazette-style laws. v0.1.1 lifts it to **44.7%** via the generic synonym dictionary + query expansion; the 21 remaining misses (pure semantic equivalence, intra-/cross-law competition, extraction pollution) are listed as-is. Per-question details, failure analysis and coverage gaps: [docs/real-question-eval.md](docs/real-question-eval.md); gold at [gold/gold_real_38.jsonl](gold/gold_real_38.jsonl); improvement experiment log at [docs/retrieval-improvement.md](docs/retrieval-improvement.md).
- Reproduce (requires your own corpus `data/corpus.jsonl`):

```bash
# Synthetic gold
python scripts/run_eval.py --corpus data/corpus.jsonl --out-dir data
# Real-question gold
python scripts/build_real_gold.py --corpus data/corpus.jsonl --out data/gold_real_38.json
python scripts/run_eval.py --corpus data/corpus.jsonl --gold data/gold_real_38.json --out-dir data --gold-desc "real-question gold v1 (LLM-verified, human legal review pending)"
# Improvement ablation (baseline vs improved config, both gold sets)
python scripts/ablate_retrieval.py --corpus data/corpus.jsonl \
    --gold-real data/gold_real_38.json --gold-synth gold/gold_synth_seed20260918.jsonl
```

- 34 unit tests: `python -m unittest discover -s tests`
- 30-second demo:

```bash
python scripts/search_cli.py --db <your legal.db> "承诺生效时合同成立" --k 2
# [1] 最高人民法院关于适用《中华人民共和国民法典》合同编通则若干问题的解释 第三条 …
```

### Known failure cases

- **Out-of-order article pollution**: querying 正当防卫 ("justifiable defense") once hit an article of the《突发公共卫生事件应对法》— that PDF was parsed from two columns with the columns interleaved; the quality gate cannot detect the disorder (no `(cid:` debris, normal length), and text from another law happened to contain the query terms. Fix directions: disorder detection based on "high-frequency cross-law citation strings", or re-extracting high-risk documents with more reliable PDF settings and rebuilding the corpus. The real-question gold makes this visible again: on questions such as lost-parcel compensation and government-information reply deadlines, the gold rows themselves are broken by column interleaving — something even query expansion cannot bridge (see section 10 of [docs/real-question-eval.md](docs/real-question-eval.md)).
- **The lexical ceiling (partially raised by query expansion)**: the colloquial→statutory lexical bridge is now partly built by the synonym dictionary (real-question Recall@5 26.3% → 44.7%); the 21 remaining misses are dominated by pure semantic equivalence (paraphrases with no dictionary bridge), intra-/cross-law competition among similar articles, and extraction pollution — these still need the v0.2 vector semantic channel, not a bigger dictionary.
- **Synthetic gold is conservative**: the metric measures lexical recall of "distinctive phrasing → article"; real-question recall is far lower — measured at 26.3% (v0.1 baseline) and 44.7% (v0.1.1) with the real-question gold.

## Roadmap

- **v0.2**: Chinese embedding channel (interface abstraction, local/remote pluggable) + reranking → true semantic hybrid retrieval; real-question gold expansion (target 200 questions) + human legal review
- **v0.3**: article/clause/item multi-level chunking; statute version alignment (temporal validity); a "refuse to answer when retrieval fails" policy and hallucination-guardrail evaluation

## License

[MIT](LICENSE)
